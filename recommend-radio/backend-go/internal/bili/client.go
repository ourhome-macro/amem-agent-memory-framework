package bili

import (
	"context"
	"crypto/md5"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"golang.org/x/sync/singleflight"

	"recommend-radio/backend-go/internal/platform/cache"
	"recommend-radio/backend-go/internal/platform/respond"
)

const (
	searchURL       = "https://api.bilibili.com/x/web-interface/search/type"
	videoInfoURL    = "https://api.bilibili.com/x/web-interface/view"
	playURL         = "https://api.bilibili.com/x/player/playurl"
	playerInfoURL   = "https://api.bilibili.com/x/player/wbi/v2"
	navURL          = "https://api.bilibili.com/x/web-interface/nav"
	replyMainURL    = "https://api.bilibili.com/x/v2/reply/main"
	spaceNavURL     = "https://api.bilibili.com/x/space/wbi/acc/info"
	spaceArcURL     = "https://api.bilibili.com/x/space/wbi/arc/search"
	defaultCacheTTL = 5 * time.Minute
	playerCacheTTL  = 1 * time.Minute
	playURLCacheTTL = 15 * time.Minute
	searchCacheTTL  = 30 * time.Second
	wbiCacheTTL     = 10 * time.Minute
)

var (
	bvidPattern = regexp.MustCompile(`(?i)^BV[0-9A-Za-z]+$`)
	urlPattern  = regexp.MustCompile(`(?i)bilibili\.com/video/(BV[0-9A-Za-z]+)`)
)

type Client struct {
	httpClient *http.Client
	cache      cache.JSONCache
	prefix     string
	cookieFunc func(context.Context) string
	group      singleflight.Group
}

func NewClient(httpClient *http.Client, jsonCache cache.JSONCache, prefix string, cookieFunc func(context.Context) string) *Client {
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 10 * time.Second}
	}
	if jsonCache == nil {
		jsonCache = cache.NoopJSONCache{}
	}
	if prefix == "" {
		prefix = "recommend-radio:bili"
	}
	return &Client{httpClient: httpClient, cache: jsonCache, prefix: prefix, cookieFunc: cookieFunc}
}

func (c *Client) ParseInput(input string) (string, bool) {
	value := strings.TrimSpace(input)
	if bvidPattern.MatchString(value) {
		return NormalizeBVID(value), true
	}
	if match := urlPattern.FindStringSubmatch(value); len(match) == 2 {
		return NormalizeBVID(match[1]), true
	}
	return "", false
}

func (c *Client) Search(ctx context.Context, keyword string, page int, pageSize int) ([]Track, error) {
	keyword = strings.TrimSpace(keyword)
	if keyword == "" {
		return nil, respond.BadRequest("keyword is required")
	}
	page = max(page, 1)
	pageSize = min(max(pageSize, 1), 50)
	key := c.cacheKey("search", keyword, strconv.Itoa(page), strconv.Itoa(pageSize))
	var tracks []Track
	if err := c.cached(ctx, key, searchCacheTTL, &tracks, func(ctx context.Context) (any, error) {
		payload, err := c.getBiliPayload(ctx, searchURL, url.Values{
			"search_type": {"video"},
			"keyword":     {keyword},
			"page":        {strconv.Itoa(page)},
			"page_size":   {strconv.Itoa(pageSize)},
		}, defaultHeaders())
		if err != nil {
			return nil, err
		}
		results, _ := payload.Data["result"].([]any)
		out := make([]Track, 0, len(results))
		for _, item := range results {
			if object, ok := item.(map[string]any); ok {
				if track, ok := normalizeSearchItem(object); ok {
					out = append(out, track)
				}
			}
		}
		return out, nil
	}); err != nil {
		return nil, err
	}
	return tracks, nil
}

func (c *Client) GetVideoDetail(ctx context.Context, bvid string) (VideoDetail, error) {
	normalized := NormalizeBVID(bvid)
	if !bvidPattern.MatchString(normalized) {
		return VideoDetail{}, respond.BadRequest("Invalid BVID or Bilibili video URL")
	}
	key := c.cacheKey("detail", c.cacheScope(ctx), normalized)
	var detail VideoDetail
	err := c.cached(ctx, key, defaultCacheTTL, &detail, func(ctx context.Context) (any, error) {
		data, err := c.videoDetailPayload(ctx, normalized)
		if err != nil {
			return nil, err
		}
		return normalizeVideoDetail(data)
	})
	return detail, err
}

func (c *Client) ResolveCID(ctx context.Context, bvid string, cid *int64) (string, int64, error) {
	detail, err := c.GetVideoDetail(ctx, bvid)
	if err != nil {
		return "", 0, err
	}
	if cid != nil {
		for _, page := range detail.Pages {
			if page.CID != nil && *page.CID == *cid {
				return detail.Track.BVID, *cid, nil
			}
		}
		return "", 0, respond.BadRequest("cid does not belong to bvid")
	}
	if detail.Track.CID != nil && *detail.Track.CID > 0 {
		return detail.Track.BVID, *detail.Track.CID, nil
	}
	if len(detail.Pages) > 0 && detail.Pages[0].CID != nil {
		return detail.Track.BVID, *detail.Pages[0].CID, nil
	}
	return "", 0, respond.BadRequest("cid is required")
}

func (c *Client) GetPlayURL(ctx context.Context, bvid string, cid int64, quality string) (AudioStreamInfo, error) {
	return c.playURL(ctx, bvid, cid, quality, false)
}

func (c *Client) RefreshPlayURL(ctx context.Context, bvid string, cid int64, quality string) (AudioStreamInfo, error) {
	return c.playURL(ctx, bvid, cid, quality, true)
}

func (c *Client) playURL(ctx context.Context, bvid string, cid int64, quality string, force bool) (AudioStreamInfo, error) {
	normalized := NormalizeBVID(bvid)
	if cid <= 0 {
		return AudioStreamInfo{}, respond.BadRequest("cid is required")
	}
	quality = normalizeQuality(quality)
	key := c.cacheKey("playurl", c.cacheScope(ctx), normalized, strconv.FormatInt(cid, 10), quality)
	var info AudioStreamInfo
	load := func(ctx context.Context) (any, error) {
		params := url.Values{
			"bvid":  {normalized},
			"cid":   {strconv.FormatInt(cid, 10)},
			"qn":    {"16"},
			"fnval": {"16"},
			"fnver": {"0"},
			"fourk": {"0"},
		}
		payload, err := c.getBiliPayload(ctx, playURL, params, c.authHeaders(ctx, normalized))
		if err != nil {
			return nil, err
		}
		return normalizeAudioInfo(payload.Data, quality)
	}
	var err error
	if force {
		value, loadErr := load(ctx)
		if loadErr != nil {
			return AudioStreamInfo{}, loadErr
		}
		_ = c.cache.SetJSON(ctx, key, value, playURLCacheTTL)
		payload, _ := json.Marshal(value)
		err = json.Unmarshal(payload, &info)
	} else {
		err = c.cached(ctx, key, playURLCacheTTL, &info, load)
	}
	return info, err
}

func (c *Client) GetCoverInfo(ctx context.Context, bvid string, cid *int64) (CoverInfo, error) {
	detail, err := c.GetVideoDetail(ctx, bvid)
	if err != nil {
		return nil, err
	}
	pages := make([]map[string]any, 0, len(detail.Pages))
	var pageCover any
	for _, page := range detail.Pages {
		row := map[string]any{
			"cid":       derefInt64(page.CID),
			"page":      derefInt(page.Page),
			"pageTitle": derefString(page.PageTitle),
			"cover":     page.Cover,
		}
		pages = append(pages, row)
		if cid != nil && page.CID != nil && *page.CID == *cid {
			pageCover = page.Cover
		}
	}
	return CoverInfo{
		"bvid":       detail.Track.BVID,
		"cid":        optionalInt64(cid),
		"cover":      detail.Track.Cover,
		"videoCover": detail.Track.Cover,
		"pageCover":  pageCover,
		"pages":      pages,
	}, nil
}

func (c *Client) GetVideoIntro(ctx context.Context, bvid string, cid *int64) (TrackIntro, error) {
	payload, err := c.videoDetailPayload(ctx, NormalizeBVID(bvid))
	if err != nil {
		return nil, err
	}
	owner, _ := payload["owner"].(map[string]any)
	stat, _ := payload["stat"].(map[string]any)
	pages, _ := payload["pages"].([]any)
	outPages := make([]map[string]any, 0, len(pages))
	for _, item := range pages {
		page, ok := item.(map[string]any)
		if !ok {
			continue
		}
		outPages = append(outPages, map[string]any{
			"cid":      int64FromAny(page["cid"]),
			"page":     intFromAny(page["page"]),
			"title":    stringFromAny(page["part"]),
			"duration": intFromAny(page["duration"]),
		})
	}
	return TrackIntro{
		"bvid":        NormalizeBVID(bvid),
		"cid":         optionalInt64(cid),
		"title":       stringFromAny(payload["title"]),
		"description": stringFromAny(payload["desc"]),
		"dynamic":     stringFromAny(payload["dynamic"]),
		"owner": map[string]any{
			"mid":  int64FromAny(owner["mid"]),
			"name": stringFromAny(owner["name"]),
			"face": stringFromAny(owner["face"]),
		},
		"publishedAt": stringFromAny(payload["pubdate"]),
		"stats": map[string]any{
			"view":     intFromAny(stat["view"]),
			"danmaku":  intFromAny(stat["danmaku"]),
			"reply":    intFromAny(stat["reply"]),
			"favorite": intFromAny(stat["favorite"]),
			"coin":     intFromAny(stat["coin"]),
			"share":    intFromAny(stat["share"]),
			"like":     intFromAny(stat["like"]),
		},
		"pages": outPages,
	}, nil
}

func (c *Client) GetTrackSubtitles(ctx context.Context, bvid string, cid *int64) (TrackSubtitles, error) {
	resolvedBVID, resolvedCID, err := c.ResolveCID(ctx, bvid, cid)
	if err != nil {
		return nil, err
	}
	key := c.cacheKey("player", c.cacheScope(ctx), resolvedBVID, strconv.FormatInt(resolvedCID, 10))
	var data map[string]any
	if err := c.cached(ctx, key, playerCacheTTL, &data, func(ctx context.Context) (any, error) {
		return c.playerInfoPayload(ctx, resolvedBVID, resolvedCID)
	}); err != nil {
		return nil, err
	}
	subtitle, _ := data["subtitle"].(map[string]any)
	raw, _ := subtitle["subtitles"].([]any)
	out := make([]map[string]any, 0, len(raw))
	for _, item := range raw {
		row, ok := item.(map[string]any)
		if !ok {
			continue
		}
		out = append(out, map[string]any{
			"id":        intFromAny(row["id"]),
			"lan":       stringFromAny(row["lan"]),
			"lanDoc":    stringFromAny(row["lan_doc"]),
			"url":       normalizeCover(stringFromAny(row["subtitle_url"])),
			"authorMid": int64FromAny(row["author_mid"]),
			"type":      intFromAny(row["type"]),
		})
	}
	return TrackSubtitles{"bvid": resolvedBVID, "cid": resolvedCID, "needLogin": false, "subtitles": out, "activeSubtitleId": nil, "lines": []any{}}, nil
}

func (c *Client) GetTrackChapters(ctx context.Context, bvid string, cid *int64) (TrackChapters, error) {
	resolvedBVID, resolvedCID, err := c.ResolveCID(ctx, bvid, cid)
	if err != nil {
		return nil, err
	}
	data, err := c.playerInfoPayload(ctx, resolvedBVID, resolvedCID)
	if err != nil {
		return nil, err
	}
	viewPoints, _ := data["view_points"].([]any)
	chapters := make([]map[string]any, 0, len(viewPoints))
	for _, item := range viewPoints {
		row, ok := item.(map[string]any)
		if !ok {
			continue
		}
		chapters = append(chapters, map[string]any{
			"from":  floatFromAny(row["from"]),
			"to":    floatFromAny(row["to"]),
			"title": stringFromAny(row["content"]),
			"cover": normalizeCover(stringFromAny(row["imgUrl"])),
		})
	}
	return TrackChapters{"bvid": resolvedBVID, "cid": resolvedCID, "chapters": chapters}, nil
}

func (c *Client) GetTrackComments(ctx context.Context, bvid string, page int, pageSize int) (TrackComments, error) {
	detail, err := c.videoDetailPayload(ctx, NormalizeBVID(bvid))
	if err != nil {
		return nil, err
	}
	aid := int64FromAny(detail["aid"])
	page = max(page, 1)
	pageSize = min(max(pageSize, 1), 50)
	payload, err := c.getBiliPayload(ctx, replyMainURL, url.Values{
		"type": {"1"}, "oid": {strconv.FormatInt(aid, 10)}, "mode": {"3"},
		"next": {strconv.Itoa(page - 1)}, "ps": {strconv.Itoa(pageSize)},
	}, c.authHeaders(ctx, NormalizeBVID(bvid)))
	if err != nil {
		return nil, err
	}
	replies, _ := payload.Data["replies"].([]any)
	comments := make([]map[string]any, 0, len(replies))
	for _, item := range replies {
		row, ok := item.(map[string]any)
		if !ok {
			continue
		}
		member, _ := row["member"].(map[string]any)
		content, _ := row["content"].(map[string]any)
		comments = append(comments, map[string]any{
			"id": stringFromAny(row["rpid_str"]),
			"author": map[string]any{
				"mid":    int64FromAny(member["mid"]),
				"name":   stringFromAny(member["uname"]),
				"avatar": normalizeCover(stringFromAny(member["avatar"])),
			},
			"message":    stringFromAny(content["message"]),
			"like":       intFromAny(row["like"]),
			"replyCount": intFromAny(row["rcount"]),
			"createdAt":  int64FromAny(row["ctime"]),
		})
	}
	cursor, _ := payload.Data["cursor"].(map[string]any)
	return TrackComments{"bvid": NormalizeBVID(bvid), "aid": aid, "page": page, "pageSize": pageSize, "total": intFromAny(cursor["all_count"]), "hasMore": boolFromAny(cursor["is_end"]) == false, "comments": comments}, nil
}

func (c *Client) ListUserTracks(ctx context.Context, mid int64, page int, pageSize int, order string) (UpTracksResult, error) {
	page = max(page, 1)
	pageSize = min(max(pageSize, 1), 50)
	if order != "click" {
		order = "pubdate"
	}
	profile, _ := c.getSpaceProfile(ctx, mid)
	payload, err := c.spaceWBIGet(ctx, spaceArcURL, url.Values{
		"mid": {strconv.FormatInt(mid, 10)}, "pn": {strconv.Itoa(page)},
		"ps": {strconv.Itoa(pageSize)}, "order": {order},
	})
	if err != nil {
		return UpTracksResult{}, err
	}
	list, _ := payload.Data["list"].(map[string]any)
	vlist, _ := list["vlist"].([]any)
	tracks := make([]Track, 0, len(vlist))
	for _, item := range vlist {
		if row, ok := item.(map[string]any); ok {
			if track, ok := normalizeSpaceArchive(row, profile); ok {
				tracks = append(tracks, track)
			}
		}
	}
	return UpTracksResult{MID: mid, Page: page, PageSize: pageSize, Order: order, Total: intFromAny(list["count"]), HasMore: len(tracks) == pageSize, Profile: profile, Tracks: tracks}, nil
}

func (c *Client) videoDetailPayload(ctx context.Context, bvid string) (map[string]any, error) {
	payload, err := c.getBiliPayload(ctx, videoInfoURL, url.Values{"bvid": {NormalizeBVID(bvid)}}, c.authHeaders(ctx, bvid))
	if err != nil {
		return nil, err
	}
	return payload.Data, nil
}

func (c *Client) playerInfoPayload(ctx context.Context, bvid string, cid int64) (map[string]any, error) {
	detail, err := c.videoDetailPayload(ctx, bvid)
	if err != nil {
		return nil, err
	}
	aid := int64FromAny(detail["aid"])
	payload, err := c.getBiliPayload(ctx, playerInfoURL, url.Values{
		"aid": {strconv.FormatInt(aid, 10)}, "bvid": {NormalizeBVID(bvid)}, "cid": {strconv.FormatInt(cid, 10)},
	}, c.authHeaders(ctx, bvid))
	if err != nil {
		return nil, err
	}
	return payload.Data, nil
}

type biliPayload struct {
	Code    int            `json:"code"`
	Message string         `json:"message"`
	Data    map[string]any `json:"data"`
}

func (c *Client) getBiliPayload(ctx context.Context, endpoint string, params url.Values, headers map[string]string) (biliPayload, error) {
	requestURL := endpoint
	if len(params) > 0 {
		requestURL += "?" + params.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, requestURL, nil)
	if err != nil {
		return biliPayload{}, err
	}
	for key, value := range headers {
		req.Header.Set(key, value)
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return biliPayload{}, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return biliPayload{}, err
	}
	if resp.StatusCode >= 400 {
		return biliPayload{}, respond.Upstream(fmt.Sprintf("Bilibili HTTP %d", resp.StatusCode))
	}
	var payload biliPayload
	if err := json.Unmarshal(body, &payload); err != nil {
		return biliPayload{}, respond.Upstream("Bilibili returned invalid JSON")
	}
	if payload.Code != 0 {
		return biliPayload{}, respond.Upstream(firstNonEmpty(payload.Message, "Bilibili API failed"))
	}
	if payload.Data == nil {
		payload.Data = map[string]any{}
	}
	return payload, nil
}

func (c *Client) cached(ctx context.Context, key string, ttl time.Duration, target any, loader func(context.Context) (any, error)) error {
	if err := c.cache.GetJSON(ctx, key, target); err == nil {
		return nil
	}
	value, err, _ := c.group.Do(key, func() (any, error) {
		if err := c.cache.GetJSON(ctx, key, target); err == nil {
			return target, nil
		}
		value, err := loader(ctx)
		if err != nil {
			return nil, err
		}
		_ = c.cache.SetJSON(ctx, key, value, ttl)
		return value, nil
	})
	if err != nil {
		return err
	}
	payload, err := json.Marshal(value)
	if err != nil {
		return err
	}
	return json.Unmarshal(payload, target)
}

func (c *Client) getSpaceProfile(ctx context.Context, mid int64) (UserProfile, error) {
	payload, err := c.spaceWBIGet(ctx, spaceNavURL, url.Values{"mid": {strconv.FormatInt(mid, 10)}})
	if err != nil {
		return UserProfile{MID: mid}, err
	}
	return UserProfile{
		MID:   int64FromAny(payload.Data["mid"]),
		Name:  stringFromAny(payload.Data["name"]),
		Face:  normalizeCover(stringFromAny(payload.Data["face"])),
		Sign:  stringFromAny(payload.Data["sign"]),
		Level: intFromAny(payload.Data["level"]),
	}, nil
}

func (c *Client) spaceWBIGet(ctx context.Context, endpoint string, params url.Values) (biliPayload, error) {
	keys, err := c.wbiKeys(ctx)
	if err != nil {
		return biliPayload{}, err
	}
	signed := signWBI(params, keys.ImgKey, keys.SubKey, time.Now().Unix())
	return c.getBiliPayload(ctx, endpoint, signed, c.authHeaders(ctx, ""))
}

type wbiKeyPair struct {
	ImgKey string `json:"imgKey"`
	SubKey string `json:"subKey"`
}

func (c *Client) wbiKeys(ctx context.Context) (wbiKeyPair, error) {
	key := c.cacheKey("wbi", c.cacheScope(ctx))
	var pair wbiKeyPair
	err := c.cached(ctx, key, wbiCacheTTL, &pair, func(ctx context.Context) (any, error) {
		payload, err := c.getBiliPayload(ctx, navURL, nil, c.authHeaders(ctx, ""))
		if err != nil {
			return nil, err
		}
		wbiImg, _ := payload.Data["wbi_img"].(map[string]any)
		return wbiKeyPair{
			ImgKey: extractWBIKey(stringFromAny(wbiImg["img_url"])),
			SubKey: extractWBIKey(stringFromAny(wbiImg["sub_url"])),
		}, nil
	})
	return pair, err
}

func (c *Client) authHeaders(ctx context.Context, bvid string) map[string]string {
	headers := videoHeaders(bvid)
	if c.cookieFunc != nil {
		if cookie := strings.TrimSpace(c.cookieFunc(ctx)); cookie != "" {
			headers["Cookie"] = cookie
		}
	}
	return headers
}

func (c *Client) cacheScope(ctx context.Context) string {
	if c.cookieFunc == nil {
		return "guest"
	}
	cookie := strings.TrimSpace(c.cookieFunc(ctx))
	if cookie == "" {
		return "guest"
	}
	sum := md5.Sum([]byte(cookie))
	return hex.EncodeToString(sum[:])[:16]
}

func (c *Client) cacheKey(parts ...string) string {
	clean := make([]string, 0, len(parts)+1)
	clean = append(clean, c.prefix)
	for _, part := range parts {
		clean = append(clean, strings.ReplaceAll(strings.ToLower(strings.TrimSpace(part)), " ", "_"))
	}
	return strings.Join(clean, ":")
}

func normalizeVideoDetail(data map[string]any) (VideoDetail, error) {
	bvid := NormalizeBVID(stringFromAny(data["bvid"]))
	cid := int64FromAny(data["cid"])
	owner, _ := data["owner"].(map[string]any)
	stat, _ := data["stat"].(map[string]any)
	page := 1
	track := Track{
		TrackID:   MakeTrackID(bvid, &cid),
		BVID:      bvid,
		CID:       &cid,
		Title:     stringFromAny(data["title"]),
		Owner:     stringFromAny(owner["name"]),
		OwnerMID:  ptrInt64(int64FromAny(owner["mid"])),
		Cover:     normalizeCover(stringFromAny(data["pic"])),
		Duration:  intFromAny(data["duration"]),
		PlayCount: int64FromAny(stat["view"]),
		Page:      &page,
		Source:    "bili",
		UpdatedAt: time.Now().UTC(),
	}
	pagesRaw, _ := data["pages"].([]any)
	pages := make([]Track, 0, len(pagesRaw))
	for _, item := range pagesRaw {
		row, ok := item.(map[string]any)
		if !ok {
			continue
		}
		pageCID := int64FromAny(row["cid"])
		pageNo := intFromAny(row["page"])
		pageTitle := stringFromAny(row["part"])
		pages = append(pages, Track{
			TrackID:   MakeTrackID(bvid, &pageCID),
			BVID:      bvid,
			CID:       &pageCID,
			Title:     firstNonEmpty(pageTitle, track.Title),
			Owner:     track.Owner,
			OwnerMID:  track.OwnerMID,
			Cover:     track.Cover,
			Duration:  intFromAny(row["duration"]),
			PlayCount: track.PlayCount,
			Page:      &pageNo,
			PageTitle: &pageTitle,
			Source:    "bili",
			UpdatedAt: time.Now().UTC(),
		})
	}
	if len(pages) == 0 {
		pages = []Track{track}
	}
	return VideoDetail{Track: track, Pages: pages}, nil
}

func normalizeSearchItem(item map[string]any) (Track, bool) {
	bvid := NormalizeBVID(stringFromAny(item["bvid"]))
	if !bvidPattern.MatchString(bvid) {
		return Track{}, false
	}
	return Track{
		TrackID:   MakeTrackID(bvid, nil),
		BVID:      bvid,
		Title:     stripHTML(stringFromAny(item["title"])),
		Owner:     stringFromAny(item["author"]),
		OwnerMID:  ptrInt64(int64FromAny(item["mid"])),
		Cover:     normalizeCover(stringFromAny(item["pic"])),
		Duration:  durationFromAny(item["duration"]),
		PlayCount: int64FromAny(item["play"]),
		Source:    "bili-search",
		UpdatedAt: time.Now().UTC(),
	}, true
}

func normalizeSpaceArchive(item map[string]any, profile UserProfile) (Track, bool) {
	bvid := NormalizeBVID(stringFromAny(item["bvid"]))
	if !bvidPattern.MatchString(bvid) {
		return Track{}, false
	}
	return Track{
		TrackID:   MakeTrackID(bvid, nil),
		BVID:      bvid,
		Title:     stringFromAny(item["title"]),
		Owner:     profile.Name,
		OwnerMID:  ptrInt64(profile.MID),
		Cover:     normalizeCover(stringFromAny(item["pic"])),
		Duration:  intFromAny(item["length"]),
		PlayCount: int64FromAny(item["play"]),
		Source:    "bili-space",
		UpdatedAt: time.Now().UTC(),
	}, true
}

func normalizeAudioInfo(data map[string]any, requestedQuality string) (AudioStreamInfo, error) {
	dash, _ := data["dash"].(map[string]any)
	rawAudio, _ := dash["audio"].([]any)
	if len(rawAudio) == 0 {
		return AudioStreamInfo{}, respond.Upstream("No audio stream available")
	}
	streams := make([]map[string]any, 0, len(rawAudio))
	for _, item := range rawAudio {
		if row, ok := item.(map[string]any); ok {
			streams = append(streams, row)
		}
	}
	selected := selectAudioStream(streams, requestedQuality)
	streamID := intFromAny(selected["id"])
	bitrate := int64FromAny(selected["bandwidth"])
	actual := qualityLabel(streamID, bitrate)
	backupRaw, _ := selected["backupUrl"].([]any)
	if len(backupRaw) == 0 {
		backupRaw, _ = selected["backup_url"].([]any)
	}
	backup := make([]string, 0, len(backupRaw))
	for _, item := range backupRaw {
		if value := stringFromAny(item); value != "" {
			backup = append(backup, value)
		}
	}
	return AudioStreamInfo{
		URL:                     stringFromAny(selected["baseUrl"]),
		BackupURLs:              backup,
		Duration:                intFromAny(data["timelength"]) / 1000,
		Bitrate:                 bitrate,
		SampleRate:              44100,
		SampleRateCompat:        44100,
		Channels:                2,
		Quality:                 requestedQuality,
		ActualQuality:           actual,
		ActualQualityCompat:     actual,
		Codec:                   codecLabel(stringFromAny(selected["codecs"])),
		Fallback:                requestedQuality != "auto" && requestedQuality != actual,
		StreamID:                streamID,
		AvailableAudioQualities: availableQualities(streams),
	}, nil
}

func NormalizeBVID(bvid string) string {
	value := strings.TrimSpace(bvid)
	if len(value) >= 2 && strings.EqualFold(value[:2], "bv") {
		return "BV" + value[2:]
	}
	return value
}

func MakeTrackID(bvid string, cid *int64) string {
	normalized := NormalizeBVID(bvid)
	if cid == nil {
		return "bili:" + normalized
	}
	return fmt.Sprintf("bili:%s:cid:%d", normalized, *cid)
}

func defaultHeaders() map[string]string {
	return map[string]string{
		"User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
		"Accept":          "application/json,text/plain,*/*",
		"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
	}
}

func videoHeaders(bvid string) map[string]string {
	headers := defaultHeaders()
	headers["Referer"] = "https://www.bilibili.com/video/" + NormalizeBVID(bvid)
	return headers
}

var htmlTag = regexp.MustCompile(`<[^>]*>`)

func stripHTML(value string) string {
	return htmlTag.ReplaceAllString(value, "")
}

func normalizeCover(value string) string {
	if strings.HasPrefix(value, "//") {
		return "https:" + value
	}
	return value
}

var qualityOrder = map[string][]int{
	"64k":   {30216, 30232, 30280, 30250, 30251},
	"132k":  {30232, 30216, 30280, 30250, 30251},
	"192k":  {30280, 30232, 30216, 30250, 30251},
	"dolby": {30250, 30280, 30232, 30216, 30251},
	"hires": {30251, 30280, 30232, 30216, 30250},
}

func normalizeQuality(quality string) string {
	switch strings.ToLower(strings.TrimSpace(quality)) {
	case "standard":
		return "132k"
	case "high":
		return "192k"
	case "64k", "132k", "192k", "dolby", "hires":
		return strings.ToLower(strings.TrimSpace(quality))
	default:
		return "auto"
	}
}

func selectAudioStream(streams []map[string]any, quality string) map[string]any {
	if quality != "auto" {
		byID := map[int]map[string]any{}
		for _, stream := range streams {
			byID[intFromAny(stream["id"])] = stream
		}
		for _, id := range qualityOrder[quality] {
			if stream, ok := byID[id]; ok {
				return stream
			}
		}
	}
	sort.SliceStable(streams, func(i, j int) bool {
		return int64FromAny(streams[i]["bandwidth"]) > int64FromAny(streams[j]["bandwidth"])
	})
	return streams[0]
}

func availableQualities(streams []map[string]any) []string {
	ids := map[int]bool{}
	for _, stream := range streams {
		ids[intFromAny(stream["id"])] = true
	}
	out := []string{"auto"}
	for _, item := range []struct {
		label string
		id    int
	}{{"64k", 30216}, {"132k", 30232}, {"192k", 30280}, {"dolby", 30250}, {"hires", 30251}} {
		if ids[item.id] {
			out = append(out, item.label)
		}
	}
	return out
}

func qualityLabel(streamID int, bitrate int64) string {
	switch streamID {
	case 30216:
		return "64k"
	case 30232:
		return "132k"
	case 30280:
		return "192k"
	case 30250:
		return "dolby"
	case 30251:
		return "hires"
	default:
		if bitrate >= 160000 {
			return "192k"
		}
		if bitrate >= 96000 {
			return "132k"
		}
		return "64k"
	}
}

func codecLabel(value string) string {
	if strings.Contains(strings.ToLower(value), "mp4a") || value == "" {
		return "aac"
	}
	return strings.ToLower(value)
}

var wbiMixinKeyEncTab = []int{46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52}

func signWBI(params url.Values, imgKey string, subKey string, timestamp int64) url.Values {
	raw := imgKey + subKey
	mixin := strings.Builder{}
	for _, index := range wbiMixinKeyEncTab {
		if index < len(raw) {
			mixin.WriteByte(raw[index])
		}
		if mixin.Len() == 32 {
			break
		}
	}
	signed := url.Values{}
	keys := make([]string, 0, len(params))
	for key := range params {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		if key == "w_rid" || key == "wts" {
			continue
		}
		signed.Set(key, strings.NewReplacer("!", "", "'", "", "(", "", ")", "", "*", "").Replace(params.Get(key)))
	}
	signed.Set("wts", strconv.FormatInt(timestamp, 10))
	query := signed.Encode()
	sum := md5.Sum([]byte(query + mixin.String()))
	signed.Set("w_rid", hex.EncodeToString(sum[:]))
	return signed
}

func extractWBIKey(rawURL string) string {
	path, err := url.Parse(rawURL)
	if err != nil {
		return ""
	}
	name := path.Path[strings.LastIndex(path.Path, "/")+1:]
	return strings.TrimSuffix(name, ".png")
}

func stringFromAny(value any) string {
	switch typed := value.(type) {
	case string:
		return typed
	case json.Number:
		return typed.String()
	case float64:
		return strconv.FormatInt(int64(typed), 10)
	case int64:
		return strconv.FormatInt(typed, 10)
	case int:
		return strconv.Itoa(typed)
	default:
		return ""
	}
}

func intFromAny(value any) int {
	return int(int64FromAny(value))
}

func int64FromAny(value any) int64 {
	switch typed := value.(type) {
	case int64:
		return typed
	case int:
		return int64(typed)
	case float64:
		return int64(typed)
	case json.Number:
		out, _ := typed.Int64()
		return out
	case string:
		out, _ := strconv.ParseInt(typed, 10, 64)
		return out
	default:
		return 0
	}
}

func floatFromAny(value any) float64 {
	switch typed := value.(type) {
	case float64:
		return typed
	case int:
		return float64(typed)
	case int64:
		return float64(typed)
	case json.Number:
		out, _ := typed.Float64()
		return out
	default:
		return 0
	}
}

func boolFromAny(value any) bool {
	switch typed := value.(type) {
	case bool:
		return typed
	default:
		return false
	}
}

func durationFromAny(value any) int {
	raw := stringFromAny(value)
	if strings.Contains(raw, ":") {
		parts := strings.Split(raw, ":")
		total := 0
		for _, part := range parts {
			number, _ := strconv.Atoi(part)
			total = total*60 + number
		}
		return total
	}
	return intFromAny(value)
}

func ptrInt64(value int64) *int64 {
	if value == 0 {
		return nil
	}
	return &value
}

func derefInt64(value *int64) int64 {
	if value == nil {
		return 0
	}
	return *value
}

func derefInt(value *int) int {
	if value == nil {
		return 0
	}
	return *value
}

func derefString(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

func optionalInt64(value *int64) any {
	if value == nil {
		return nil
	}
	return *value
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}
