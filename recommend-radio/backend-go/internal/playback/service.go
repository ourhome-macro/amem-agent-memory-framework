package playback

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"golang.org/x/sync/semaphore"

	"recommend-radio/backend-go/internal/bili"
	"recommend-radio/backend-go/internal/platform/respond"
)

const (
	copyBufferSize  = 64 * 1024
	streamUserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

var expiredStreamStatuses = map[int]bool{http.StatusUnauthorized: true, http.StatusForbidden: true, http.StatusGone: true}

type Service struct {
	bili          bili.Gateway
	httpClient    *http.Client
	streamSlots   *semaphore.Weighted
	streamTimeout time.Duration
}

func NewService(gateway bili.Gateway, maxStreams int64, streamTimeout time.Duration) *Service {
	if maxStreams <= 0 {
		maxStreams = 32
	}
	if streamTimeout <= 0 {
		streamTimeout = 5 * time.Minute
	}
	return &Service{
		bili: gateway,
		httpClient: &http.Client{
			Timeout: streamTimeout,
			Transport: &http.Transport{
				MaxIdleConns:        64,
				MaxIdleConnsPerHost: 32,
				IdleConnTimeout:     90 * time.Second,
			},
		},
		streamSlots:   semaphore.NewWeighted(maxStreams),
		streamTimeout: streamTimeout,
	}
}

func (s *Service) StreamInfo(ctx context.Context, bvid string, cid *int64, quality string) (map[string]any, error) {
	resolvedBVID, resolvedCID, err := s.bili.ResolveCID(ctx, bvid, cid)
	if err != nil {
		return nil, err
	}
	info, err := s.bili.GetPlayURL(ctx, resolvedBVID, resolvedCID, quality)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"url":                     fmt.Sprintf("/api/tracks/%s/%d/stream?quality=%s", resolvedBVID, resolvedCID, info.Quality),
		"streamUrl":               fmt.Sprintf("/api/tracks/%s/%d/stream?quality=%s", resolvedBVID, resolvedCID, info.Quality),
		"relativeUrl":             fmt.Sprintf("/api/tracks/%s/%d/stream?quality=%s", resolvedBVID, resolvedCID, info.Quality),
		"audio":                   info,
		"bvid":                    resolvedBVID,
		"cid":                     resolvedCID,
		"quality":                 info.Quality,
		"actualQuality":           info.ActualQuality,
		"availableAudioQualities": info.AvailableAudioQualities,
	}, nil
}

func (s *Service) Proxy(c *gin.Context, bvid string, cid *int64, quality string) error {
	reqCtx, cancel := context.WithTimeout(c.Request.Context(), s.streamTimeout)
	defer cancel()

	acquired := make(chan error, 1)
	go func() {
		acquired <- s.streamSlots.Acquire(reqCtx, 1)
	}()

	select {
	case err := <-acquired:
		if err != nil {
			if errors.Is(err, context.Canceled) {
				return err
			}
			return respond.TooManyRequests("too many active media streams")
		}
	case <-reqCtx.Done():
		return reqCtx.Err()
	}
	defer s.streamSlots.Release(1)

	resolvedBVID, resolvedCID, err := s.bili.ResolveCID(reqCtx, bvid, cid)
	if err != nil {
		return err
	}
	info, err := s.bili.GetPlayURL(reqCtx, resolvedBVID, resolvedCID, quality)
	if err != nil {
		return err
	}
	upstream, refreshed, err := s.openUpstream(reqCtx, resolvedBVID, resolvedCID, quality, info, c.GetHeader("Range"))
	if err != nil {
		return err
	}
	defer upstream.Body.Close()

	for key, values := range upstream.Header {
		if proxyHeaderAllowed(key) {
			for _, value := range values {
				c.Writer.Header().Add(key, value)
			}
		}
	}
	c.Writer.Header().Set("Accept-Ranges", "bytes")
	if c.Writer.Header().Get("Content-Type") == "" || c.Writer.Header().Get("Content-Type") == "application/octet-stream" {
		c.Writer.Header().Set("Content-Type", "audio/mp4")
	}
	c.Writer.Header().Set("Server-Timing", fmt.Sprintf("bili_stream_refreshed;desc=\"%t\"", refreshed))
	c.Status(upstream.StatusCode)

	buffer := make([]byte, copyBufferSize)
	_, copyErr := io.CopyBuffer(c.Writer, upstream.Body, buffer)
	if copyErr != nil && reqCtx.Err() != nil {
		return reqCtx.Err()
	}
	return copyErr
}

func (s *Service) openUpstream(ctx context.Context, bvid string, cid int64, quality string, info bili.AudioStreamInfo, rangeHeader string) (*http.Response, bool, error) {
	upstream, status, err := s.tryOpen(ctx, bvid, info, rangeHeader)
	if upstream != nil {
		return upstream, false, nil
	}
	if expiredStreamStatuses[status] {
		refreshed, refreshErr := s.bili.RefreshPlayURL(ctx, bvid, cid, quality)
		if refreshErr != nil {
			return nil, false, refreshErr
		}
		upstream, status, err = s.tryOpen(ctx, bvid, refreshed, rangeHeader)
		if upstream != nil {
			return upstream, true, nil
		}
	}
	if err != nil {
		return nil, false, err
	}
	if status > 0 {
		return nil, false, respond.Upstream("Audio upstream HTTP " + strconv.Itoa(status))
	}
	return nil, false, respond.Upstream("No usable audio upstream URL")
}

func (s *Service) tryOpen(ctx context.Context, bvid string, info bili.AudioStreamInfo, rangeHeader string) (*http.Response, int, error) {
	var lastStatus int
	var lastErr error
	for _, candidate := range candidateURLs(info) {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, candidate, nil)
		if err != nil {
			lastErr = err
			continue
		}
		req.Header.Set("User-Agent", streamUserAgent)
		req.Header.Set("Referer", "https://www.bilibili.com/video/"+bili.NormalizeBVID(bvid))
		req.Header.Set("Origin", "https://www.bilibili.com")
		req.Header.Set("Accept", "*/*")
		req.Header.Set("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
		if strings.TrimSpace(rangeHeader) != "" {
			req.Header.Set("Range", rangeHeader)
		}
		resp, err := s.httpClient.Do(req)
		if err != nil {
			lastErr = err
			continue
		}
		lastErr = nil
		lastStatus = resp.StatusCode
		if (resp.StatusCode >= 200 && resp.StatusCode < 300) || resp.StatusCode == http.StatusPartialContent {
			return resp, resp.StatusCode, nil
		}
		resp.Body.Close()
	}
	return nil, lastStatus, lastErr
}

func candidateURLs(info bili.AudioStreamInfo) []string {
	seen := map[string]bool{}
	out := make([]string, 0, 1+len(info.BackupURLs))
	for _, value := range append([]string{info.URL}, info.BackupURLs...) {
		candidate := strings.TrimSpace(value)
		if candidate != "" && !seen[candidate] {
			seen[candidate] = true
			out = append(out, candidate)
		}
	}
	return out
}

func proxyHeaderAllowed(key string) bool {
	switch strings.ToLower(key) {
	case "content-type", "content-length", "content-range", "accept-ranges", "content-encoding", "etag", "last-modified":
		return true
	default:
		return false
	}
}
