package app

import (
	"context"
	"database/sql"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"recommend-radio/backend-go/internal/amem"
	"recommend-radio/backend-go/internal/auth"
	"recommend-radio/backend-go/internal/behavior"
	"recommend-radio/backend-go/internal/bili"
	"recommend-radio/backend-go/internal/library"
	"recommend-radio/backend-go/internal/platform/cache"
	"recommend-radio/backend-go/internal/platform/config"
	"recommend-radio/backend-go/internal/platform/database"
	"recommend-radio/backend-go/internal/platform/model"
	"recommend-radio/backend-go/internal/platform/respond"
	"recommend-radio/backend-go/internal/playback"
	"recommend-radio/backend-go/internal/recommendation"
)

type Runtime struct {
	cfg        config.Config
	logger     *slog.Logger
	db         *gorm.DB
	sqlDB      *sql.DB
	cache      cache.JSONCache
	publisher  behavior.Publisher
	dispatcher *behavior.Dispatcher
	consumers  *behavior.ConsumerSet
	router     *gin.Engine

	bili       bili.Gateway
	biliAuth   *auth.Service
	library    *library.Service
	playback   *playback.Service
	behavior   *behavior.Service
	recommend  *recommendation.Service
	amemClient amem.Client

	shutdown context.CancelFunc
}

func NewRuntime(cfg config.Config, logger *slog.Logger) (*Runtime, error) {
	gin.SetMode(gin.ReleaseMode)
	db, sqlDB, err := database.Open(cfg.MySQLDSN)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := database.RunMigrations(ctx, db); err != nil {
		return nil, err
	}
	jsonCache := cache.NewRedis(cfg.RedisAddr, cfg.RedisPassword, cfg.RedisDB)
	libraryService := library.NewService(db)
	if err := libraryService.EnsureLegacyUser(ctx); err != nil {
		return nil, err
	}
	authService := auth.NewService(db, &http.Client{Timeout: cfg.BiliTimeout})
	biliClient := bili.NewClient(&http.Client{Timeout: cfg.BiliTimeout}, jsonCache, cfg.BiliCachePrefix, func(requestCtx context.Context) string {
		cookie, _ := authService.CookieHeader(requestCtx, model.LegacyOwnerUserID)
		return cookie
	})
	playbackService := playback.NewService(biliClient, cfg.MaxMediaStreams, cfg.StreamTimeout)
	amemClient, err := amem.NewClient(cfg.AMEMTransport, cfg.AMEMBaseURL, cfg.AMEMGRPCAddr, cfg.AMEMTimeout)
	if err != nil {
		return nil, err
	}
	recommendService := recommendation.NewServiceWithConfig(db, biliClient, libraryService, amemClient, recommendation.Config{
		EmbeddingEnabled:        cfg.RecommendEmbeddingEnabled,
		EmbeddingBaseURL:        cfg.RecommendEmbeddingBaseURL,
		EmbeddingAPIKeyEnv:      cfg.RecommendEmbeddingAPIKeyEnv,
		EmbeddingModel:          cfg.RecommendEmbeddingModel,
		EmbeddingDimensions:     cfg.RecommendEmbeddingDimensions,
		EmbeddingTimeout:        cfg.RecommendEmbeddingTimeout,
		EmbeddingCacheTTL:       cfg.RecommendEmbeddingCacheTTL,
		PrefilterMode:           cfg.RecommendPrefilterMode,
		PositivePassThreshold:   cfg.RecommendPrefilterPositivePass,
		PositiveRejectThreshold: cfg.RecommendPrefilterPositiveReject,
		NegativeRejectThreshold: cfg.RecommendPrefilterNegativeReject,
		NegativePassMax:         cfg.RecommendPrefilterNegativePassMax,
		LLMEvaluatorEnabled:     cfg.RecommendLLMEvaluatorEnabled,
	}, jsonCache, nil)
	behaviorService := behavior.NewService(db, libraryService, cfg.OutboxTopic)
	var publisher behavior.Publisher = behavior.NoopPublisher{}
	if cfg.RocketMQEnabled {
		publisher, err = behavior.NewRocketMQPublisher(cfg.RocketMQEndpoint, cfg.OutboxTopic)
		if err != nil {
			return nil, err
		}
	}
	runCtx, shutdown := context.WithCancel(context.Background())
	dispatcher := behavior.NewDispatcher(db, publisher, logger, cfg.OutboxWorkers)
	dispatcher.Start(runCtx)
	consumerProcessor := behavior.NewConsumerProcessor(db, amemClient)
	consumers, err := behavior.NewRocketMQConsumerSet(behavior.ConsumerConfig{
		Enabled: cfg.RocketMQConsumersEnabled, Endpoint: cfg.RocketMQEndpoint,
		Topic: cfg.OutboxTopic, Workers: cfg.ConsumerWorkers, DLQThreshold: cfg.ConsumerDLQThreshold,
	}, consumerProcessor, logger)
	if err != nil {
		shutdown()
		return nil, err
	}
	if err := consumers.Start(); err != nil {
		shutdown()
		return nil, err
	}
	runtime := &Runtime{
		cfg: cfg, logger: logger, db: db, sqlDB: sqlDB, cache: jsonCache,
		publisher: publisher, dispatcher: dispatcher, bili: biliClient,
		biliAuth: authService, library: libraryService, playback: playbackService, behavior: behaviorService,
		recommend: recommendService, amemClient: amemClient, consumers: consumers, shutdown: shutdown,
	}
	runtime.router = runtime.buildRouter()
	return runtime, nil
}

func (r *Runtime) Router() *gin.Engine {
	return r.router
}

func (r *Runtime) Shutdown(ctx context.Context) {
	if r.shutdown != nil {
		r.shutdown()
	}
	done := make(chan struct{})
	go func() {
		r.dispatcher.Stop()
		close(done)
	}()
	select {
	case <-done:
	case <-ctx.Done():
	}
	if r.consumers != nil {
		_ = r.consumers.Close()
	}
}

func (r *Runtime) Close() {
	if r.publisher != nil {
		_ = r.publisher.Close()
	}
	if r.cache != nil {
		_ = r.cache.Close()
	}
	if closer, ok := r.amemClient.(interface{ Close() error }); ok {
		_ = closer.Close()
	}
	if r.sqlDB != nil {
		_ = r.sqlDB.Close()
	}
}

func (r *Runtime) buildRouter() *gin.Engine {
	router := gin.New()
	router.Use(gin.Recovery())
	router.Use(cors.New(cors.Config{
		AllowOrigins:     []string{"http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:5173"},
		AllowMethods:     []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
		AllowHeaders:     []string{"Origin", "Content-Type", "Accept", "X-CSRF-Token", "Range"},
		ExposeHeaders:    []string{"Content-Length", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified"},
		AllowCredentials: true,
	}))

	router.GET("/health/live", func(c *gin.Context) { respond.OK(c, map[string]any{"status": "live"}) })
	router.GET("/health/ready", func(c *gin.Context) {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 2*time.Second)
		defer cancel()
		if err := database.Ping(ctx, r.sqlDB); err != nil {
			respond.Error(c, err)
			return
		}
		respond.OK(c, map[string]any{"status": "ready"})
	})
	router.GET("/api/session/me", r.sessionMe)
	router.GET("/api/session/login", func(c *gin.Context) { c.Redirect(http.StatusFound, "/") })
	router.GET("/api/session/callback", func(c *gin.Context) { c.Redirect(http.StatusFound, "/") })
	router.POST("/api/session/logout", func(c *gin.Context) { respond.NoContent(c) })

	router.GET("/api/search", r.searchTracks)
	router.GET("/api/images/proxy", r.proxyImage)
	router.GET("/api/tracks/resolve", r.resolveTrackInput)
	router.GET("/api/tracks/:bvid", r.trackDetail)
	router.GET("/api/tracks/:bvid/cover", r.trackCover)
	router.GET("/api/tracks/:bvid/:cid/cover", r.trackCover)
	router.GET("/api/tracks/:bvid/intro", r.trackIntro)
	router.GET("/api/tracks/:bvid/:cid/intro", r.trackIntro)
	router.GET("/api/tracks/:bvid/subtitles", r.trackSubtitles)
	router.GET("/api/tracks/:bvid/:cid/subtitles", r.trackSubtitles)
	router.GET("/api/tracks/:bvid/chapters", r.trackChapters)
	router.GET("/api/tracks/:bvid/:cid/chapters", r.trackChapters)
	router.GET("/api/tracks/:bvid/comments", r.trackComments)
	router.GET("/api/tracks/:bvid/:cid/comments", r.trackComments)
	router.GET("/api/tracks/:bvid/stream-info", r.streamInfo)
	router.GET("/api/tracks/:bvid/:cid/stream-info", r.streamInfo)
	router.GET("/api/tracks/:bvid/stream", r.streamTrack)
	router.GET("/api/tracks/:bvid/:cid/stream", r.streamTrack)
	router.GET("/api/video/info/:bvid", r.videoInfoLegacy)
	router.GET("/api/video/audio/:bvid/:cid", r.streamInfo)
	router.GET("/api/stream/:bvid", r.streamTrack)
	router.GET("/api/player/status", func(c *gin.Context) { respond.OK(c, map[string]any{"status": "idle"}) })
	router.GET("/api/player/queue", r.getQueue)
	router.PUT("/api/player/queue", r.putQueue)
	router.DELETE("/api/player/queue", r.clearQueue)
	router.POST("/api/player/stop", func(c *gin.Context) { respond.NoContent(c) })
	router.GET("/api/stream/stats", func(c *gin.Context) { respond.OK(c, map[string]any{"total_bytes": 0, "session_bytes": 0}) })
	router.POST("/api/stream/stats/reset", func(c *gin.Context) { respond.NoContent(c) })

	router.GET("/api/library/recent", r.listRecent)
	router.POST("/api/library/recent", r.addRecent)
	router.DELETE("/api/library/recent", r.clearRecent)
	router.DELETE("/api/library/recent/:bvid", r.removeRecent)
	router.GET("/api/library/likes", r.listLikes)
	router.POST("/api/library/likes/:bvid", r.addLike)
	router.DELETE("/api/library/likes/:bvid", r.removeLike)
	router.GET("/api/library/reviews/:bvid", r.getReview)
	router.GET("/api/library/reviews/:bvid/:cid", r.getReview)
	router.PUT("/api/library/reviews/:bvid", r.saveReview)
	router.PUT("/api/library/reviews/:bvid/:cid", r.saveReview)
	router.DELETE("/api/library/reviews/:bvid", r.deleteReview)
	router.DELETE("/api/library/reviews/:bvid/:cid", r.deleteReview)
	router.GET("/api/library/playlists", r.listPlaylists)
	router.POST("/api/library/playlists", r.createPlaylist)
	router.GET("/api/library/playlists/:playlistID", r.getPlaylist)
	router.PATCH("/api/library/playlists/:playlistID", r.updatePlaylist)
	router.DELETE("/api/library/playlists/:playlistID", r.deletePlaylist)
	router.PUT("/api/library/playlists/:playlistID/items", r.replacePlaylistItems)
	router.POST("/api/library/playlists/import/favorite", r.notImplementedOK)
	router.POST("/api/library/playlists/:playlistID/import/favorite", r.notImplementedOK)

	router.POST("/api/playback/events", r.recordPlaybackEvent)
	router.GET("/api/playback/recent", r.playbackRecent)
	router.GET("/api/playback/resume/:trackID", func(c *gin.Context) {
		respond.OK(c, map[string]any{"trackId": c.Param("trackID"), "positionMs": 0, "listenMs": 0, "completed": false})
	})
	router.GET("/api/recommendations", r.listRecommendations)
	router.GET("/api/recommendations/debug/latest", r.latestRecommendationTrace)
	router.POST("/api/recommendations/events", r.recordRecommendationEvent)
	router.GET("/api/profile/music", r.musicProfile)
	router.POST("/api/profile/music/backfill", func(c *gin.Context) { respond.OK(c, map[string]any{"recorded": 0, "memoryIds": []string{}}) })
	router.POST("/api/profile/music/statement", r.recordMusicProfileStatement)

	router.GET("/api/auth/status", r.authStatus)
	router.POST("/api/auth/status/refresh", r.authStatus)
	router.POST("/api/auth/qrcode", r.createBiliQRCode)
	router.POST("/api/auth/qrcode/status", r.pollBiliQRCode)
	router.GET("/api/auth/profile", r.biliAuthProfile)
	router.POST("/api/auth/profile/refresh", r.biliAuthProfile)
	router.POST("/api/auth/logout", r.logoutBili)
	router.GET("/api/bili/users/:mid/profile", r.biliUserProfile)
	router.GET("/api/bili/users/:mid/tracks", r.biliUserTracks)
	router.GET("/api/bili/favorites", func(c *gin.Context) { respond.OK(c, map[string]any{"folders": []any{}}) })
	router.GET("/api/bili/favorites/:mediaID/tracks", func(c *gin.Context) { respond.OK(c, map[string]any{"tracks": []any{}}) })
	router.POST("/api/analysis/events", r.recordAnalysisEvent)
	router.GET("/api/settings", r.getSettings)
	router.PATCH("/api/settings", r.updateSettings)
	router.GET("/api/settings/audio-quality", r.getAudioQuality)
	router.PATCH("/api/settings/audio-quality", r.updateAudioQuality)
	router.GET("/api/admin/stats/summary", func(c *gin.Context) {
		respond.OK(c, map[string]any{"range": c.DefaultQuery("range", "7d"), "users": map[string]int{"total": 1}})
	})
	router.GET("/api/admin/users", func(c *gin.Context) {
		respond.OK(c, map[string]any{"items": []any{}, "total": 0, "page": 1, "pageSize": 20})
	})
	router.POST("/api/admin/genshin", func(c *gin.Context) { respond.OK(c, map[string]any{"id": currentUserID(c), "role": "admin"}) })
	router.PATCH("/api/admin/users/:userID/role", func(c *gin.Context) { respond.OK(c, map[string]any{"id": c.Param("userID"), "role": "user"}) })
	router.NoRoute(r.compatLegacyNoRoute)
	return router
}

func (r *Runtime) compatLegacyNoRoute(c *gin.Context) {
	if c.Request.Method == http.MethodPost {
		prefix := "/api/library/playlists/"
		if rest, ok := strings.CutPrefix(c.Request.URL.Path, prefix); ok {
			parts := strings.Split(rest, "/")
			if len(parts) == 2 && parts[0] != "" {
				c.Params = append(c.Params, gin.Param{Key: "playlistID", Value: parts[0]})
				switch parts[1] {
				case "items:preview":
					r.previewPlaylistItems(c)
					return
				case "items:batch":
					r.addPlaylistItems(c)
					return
				}
			}
		}
	}
	respond.Error(c, respond.NotFound("route not found"))
}

func (r *Runtime) sessionMe(c *gin.Context) {
	status, _ := r.biliAuth.Status(c.Request.Context(), currentUserID(c), false)
	respond.OK(c, map[string]any{
		"authenticated": true,
		"user":          map[string]any{"id": currentUserID(c), "displayName": "Legacy Owner", "role": "admin"},
		"csrfToken":     "go-backend-dev",
		"oidcEnabled":   false,
		"biliConnected": status.IsLoggedIn,
	})
}

func (r *Runtime) searchTracks(c *gin.Context) {
	keyword := c.Query("keyword")
	page, _ := strconv.Atoi(c.DefaultQuery("page", "1"))
	pageSize, _ := strconv.Atoi(c.DefaultQuery("pageSize", c.DefaultQuery("page_size", "20")))
	tracks, err := r.bili.Search(c.Request.Context(), keyword, page, pageSize)
	if err != nil {
		respond.Error(c, err)
		return
	}
	r.emitBehaviorLog(c.Request.Context(), currentUserID(c), "search.performed", "", "search", map[string]any{
		"keyword": keyword, "page": page, "pageSize": pageSize, "resultCount": len(tracks), "source": "bili",
	})
	respond.OK(c, map[string]any{"tracks": tracks, "page": page, "pageSize": pageSize})
}

func (r *Runtime) proxyImage(c *gin.Context) {
	target := c.Query("url")
	if target == "" {
		respond.Error(c, respond.BadRequest("url is required"))
		return
	}
	req, err := http.NewRequestWithContext(c.Request.Context(), http.MethodGet, target, nil)
	if err != nil {
		respond.Error(c, err)
		return
	}
	req.Header.Set("Referer", "https://www.bilibili.com/")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		respond.Error(c, err)
		return
	}
	defer resp.Body.Close()
	for _, header := range []string{"Content-Type", "Content-Length", "ETag", "Last-Modified"} {
		if value := resp.Header.Get(header); value != "" {
			c.Header(header, value)
		}
	}
	c.Status(resp.StatusCode)
	_, _ = io.Copy(c.Writer, resp.Body)
}

func (r *Runtime) resolveTrackInput(c *gin.Context) {
	bvid, ok := r.bili.ParseInput(c.Query("input"))
	if !ok {
		respond.Error(c, respond.BadRequest("Invalid BVID or Bilibili video URL"))
		return
	}
	r.trackDetailByBVID(c, bvid)
}

func (r *Runtime) trackDetail(c *gin.Context) { r.trackDetailByBVID(c, c.Param("bvid")) }
func (r *Runtime) trackDetailByBVID(c *gin.Context, bvid string) {
	detail, err := r.bili.GetVideoDetail(c.Request.Context(), bvid)
	if err != nil {
		respond.Error(c, err)
		return
	}
	_ = r.library.UpsertTrack(c.Request.Context(), nil, detail.Track)
	for _, page := range detail.Pages {
		_ = r.library.UpsertTrack(c.Request.Context(), nil, page)
	}
	respond.OK(c, detail)
}

func (r *Runtime) trackCover(c *gin.Context) {
	cid := cidFromParam(c)
	data, err := r.bili.GetCoverInfo(c.Request.Context(), c.Param("bvid"), cid)
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, data)
}

func (r *Runtime) trackIntro(c *gin.Context) {
	data, err := r.bili.GetVideoIntro(c.Request.Context(), c.Param("bvid"), cidFromParam(c))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, data)
}

func (r *Runtime) trackSubtitles(c *gin.Context) {
	data, err := r.bili.GetTrackSubtitles(c.Request.Context(), c.Param("bvid"), cidFromParam(c))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, data)
}

func (r *Runtime) trackChapters(c *gin.Context) {
	data, err := r.bili.GetTrackChapters(c.Request.Context(), c.Param("bvid"), cidFromParam(c))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, data)
}

func (r *Runtime) trackComments(c *gin.Context) {
	page, _ := strconv.Atoi(c.DefaultQuery("page", "1"))
	pageSize, _ := strconv.Atoi(c.DefaultQuery("pageSize", c.DefaultQuery("page_size", "20")))
	data, err := r.bili.GetTrackComments(c.Request.Context(), c.Param("bvid"), page, pageSize)
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, data)
}

func (r *Runtime) streamInfo(c *gin.Context) {
	data, err := r.playback.StreamInfo(c.Request.Context(), c.Param("bvid"), cidFromParam(c), c.DefaultQuery("quality", "auto"))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, data)
}

func (r *Runtime) streamTrack(c *gin.Context) {
	if err := r.playback.Proxy(c, c.Param("bvid"), cidFromParam(c), c.DefaultQuery("quality", "auto")); err != nil && !strings.Contains(err.Error(), "context canceled") {
		respond.Error(c, err)
	}
}

func (r *Runtime) videoInfoLegacy(c *gin.Context) {
	detail, err := r.bili.GetVideoDetail(c.Request.Context(), c.Param("bvid"))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, detail.Track)
}

func (r *Runtime) listRecent(c *gin.Context) {
	limit, _ := strconv.Atoi(c.DefaultQuery("limit", "100"))
	tracks, err := r.library.ListRecent(c.Request.Context(), currentUserID(c), limit)
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, map[string]any{"tracks": tracks})
}

func (r *Runtime) addRecent(c *gin.Context) {
	var payload map[string]any
	if err := c.ShouldBindJSON(&payload); err != nil {
		respond.Error(c, respond.BadRequest("invalid JSON"))
		return
	}
	track := trackFromMap(payload)
	err := r.library.AddRecent(c.Request.Context(), currentUserID(c), track, intAny(payload["positionMs"]), intAny(payload["listenMs"]), boolAny(payload["completed"]))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.NoContent(c)
}

func (r *Runtime) clearRecent(c *gin.Context) {
	if err := r.library.ClearRecent(c.Request.Context(), currentUserID(c)); err != nil {
		respond.Error(c, err)
		return
	}
	respond.NoContent(c)
}
func (r *Runtime) removeRecent(c *gin.Context) {
	if err := r.library.RemoveRecent(c.Request.Context(), currentUserID(c), c.Param("bvid"), cidFromParam(c)); err != nil {
		respond.Error(c, err)
		return
	}
	respond.NoContent(c)
}

func (r *Runtime) listLikes(c *gin.Context) {
	tracks, err := r.library.ListLikes(c.Request.Context(), currentUserID(c))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, map[string]any{"tracks": tracks})
}

func (r *Runtime) addLike(c *gin.Context) {
	track, err := bindTrack(c)
	if err != nil {
		respond.Error(c, err)
		return
	}
	if track.BVID == "" {
		track.BVID = c.Param("bvid")
	}
	if err := r.library.AddLike(c.Request.Context(), currentUserID(c), track); err != nil {
		respond.Error(c, err)
		return
	}
	respond.NoContent(c)
}

func (r *Runtime) removeLike(c *gin.Context) {
	removed, err := r.library.RemoveLike(c.Request.Context(), currentUserID(c), c.Param("bvid"), cidFromParam(c))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, map[string]any{"bvid": c.Param("bvid"), "cid": cidFromParam(c), "removed": removed})
}

func (r *Runtime) getReview(c *gin.Context) {
	review, err := r.library.GetReview(c.Request.Context(), currentUserID(c), c.Param("bvid"), cidFromParam(c))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, map[string]any{"review": review})
}

func (r *Runtime) saveReview(c *gin.Context) {
	var payload map[string]any
	if err := c.ShouldBindJSON(&payload); err != nil {
		respond.Error(c, respond.BadRequest("invalid JSON"))
		return
	}
	track := trackFromMap(payload)
	if track.BVID == "" {
		track.BVID = c.Param("bvid")
	}
	if cid := cidFromParam(c); cid != nil {
		track.CID = cid
	}
	track.TrackID = bili.MakeTrackID(track.BVID, track.CID)
	review, err := r.library.SaveReview(c.Request.Context(), currentUserID(c), track, intAny(payload["rating"]), stringAny(payload["mood"]), stringAny(payload["note"]))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, map[string]any{"review": review})
}

func (r *Runtime) deleteReview(c *gin.Context) {
	if err := r.library.DeleteReview(c.Request.Context(), currentUserID(c), c.Param("bvid"), cidFromParam(c)); err != nil {
		respond.Error(c, err)
		return
	}
	respond.NoContent(c)
}

func (r *Runtime) listPlaylists(c *gin.Context) {
	playlists, err := r.library.ListPlaylists(c.Request.Context(), currentUserID(c))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, map[string]any{"playlists": playlists})
}

func (r *Runtime) createPlaylist(c *gin.Context) {
	var payload map[string]any
	if err := c.ShouldBindJSON(&payload); err != nil {
		respond.Error(c, respond.BadRequest("invalid JSON"))
		return
	}
	playlist, err := r.library.CreatePlaylist(c.Request.Context(), currentUserID(c), stringAny(payload["name"]), tracksFromAny(payload["tracks"]), stringAny(payload["sourceType"]), nil)
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, playlist)
}

func (r *Runtime) getPlaylist(c *gin.Context) {
	playlist, err := r.library.GetPlaylist(c.Request.Context(), currentUserID(c), c.Param("playlistID"))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, playlist)
}
func (r *Runtime) updatePlaylist(c *gin.Context) {
	var p map[string]any
	_ = c.ShouldBindJSON(&p)
	playlist, err := r.library.UpdatePlaylist(c.Request.Context(), currentUserID(c), c.Param("playlistID"), stringAny(p["name"]))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, playlist)
}
func (r *Runtime) deletePlaylist(c *gin.Context) {
	if err := r.library.DeletePlaylist(c.Request.Context(), currentUserID(c), c.Param("playlistID")); err != nil {
		respond.Error(c, err)
		return
	}
	respond.NoContent(c)
}
func (r *Runtime) previewPlaylistItems(c *gin.Context) {
	var p map[string]any
	_ = c.ShouldBindJSON(&p)
	respond.OK(c, map[string]any{"tracks": tracksFromAny(p["tracks"])})
}
func (r *Runtime) addPlaylistItems(c *gin.Context) {
	var p map[string]any
	_ = c.ShouldBindJSON(&p)
	if err := r.library.AddPlaylistItems(c.Request.Context(), currentUserID(c), c.Param("playlistID"), tracksFromAny(p["tracks"])); err != nil {
		respond.Error(c, err)
		return
	}
	respond.NoContent(c)
}
func (r *Runtime) replacePlaylistItems(c *gin.Context) {
	var p map[string]any
	_ = c.ShouldBindJSON(&p)
	playlist, err := r.library.ReplacePlaylistItems(c.Request.Context(), currentUserID(c), c.Param("playlistID"), tracksFromAny(p["tracks"]))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, map[string]any{"playlist": playlist})
}

func (r *Runtime) getQueue(c *gin.Context) {
	q, err := r.library.GetQueue(c.Request.Context(), currentUserID(c))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, q)
}
func (r *Runtime) putQueue(c *gin.Context) {
	var p map[string]any
	_ = c.ShouldBindJSON(&p)
	q, err := r.library.SaveQueue(c.Request.Context(), currentUserID(c), tracksFromAny(p["queue"]), intAny(p["currentIndex"]), stringAny(p["playMode"]))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, q)
}
func (r *Runtime) clearQueue(c *gin.Context) {
	q, err := r.library.SaveQueue(c.Request.Context(), currentUserID(c), nil, -1, "order")
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, q)
}

func (r *Runtime) recordPlaybackEvent(c *gin.Context) {
	var p map[string]any
	if err := c.ShouldBindJSON(&p); err != nil {
		respond.Error(c, respond.BadRequest("invalid JSON"))
		return
	}
	eventID := stringAny(p["eventId"])
	if eventID == "" {
		eventID = stringAny(p["sessionId"]) + ":" + stringAny(p["trackId"]) + ":" + stringAny(p["event"])
	}
	result, err := r.behavior.Record(c.Request.Context(), currentUserID(c), behavior.EventInput{EventID: eventID, Type: stringAny(p["event"]), TrackID: stringAny(p["trackId"]), Scene: "playback", Payload: p})
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.Accepted(c, result)
}

func (r *Runtime) playbackRecent(c *gin.Context) { r.listRecent(c) }
func (r *Runtime) listRecommendations(c *gin.Context) {
	limit, _ := strconv.Atoi(c.DefaultQuery("limit", "8"))
	userID := currentUserID(c)
	scene := c.DefaultQuery("scene", "home")
	data, err := r.recommend.List(c.Request.Context(), userID, scene, limit)
	if err != nil {
		respond.Error(c, err)
		return
	}
	items := recommendationItems(data["items"])
	traceID := stringAny(data["debugTraceId"])
	r.emitBehaviorLog(c.Request.Context(), userID, "recommendation.generated", "", scene, map[string]any{
		"traceId": traceID, "limit": limit, "resultCount": len(items), "items": items, "source": "agent_search",
	})
	r.emitBehaviorLog(c.Request.Context(), userID, "recommendation.exposed", "", scene, map[string]any{
		"traceId": traceID, "itemCount": len(items), "items": items, "source": "agent_search",
	})
	if telemetry, ok := data["telemetry"].(map[string]any); ok {
		if intents, ok := telemetry["searchIntents"]; ok {
			r.emitBehaviorLog(c.Request.Context(), userID, "search_intent.yield_updated", "", scene, map[string]any{
				"traceId": traceID, "searchIntents": intents, "source": "agent_search",
			})
		}
		if decisions, ok := telemetry["prefilterDecisions"]; ok {
			r.emitBehaviorLog(c.Request.Context(), userID, "candidate.prefiltered", "", scene, map[string]any{
				"traceId": traceID, "decisions": decisions, "source": "agent_search",
			})
		}
	}
	respond.OK(c, data)
}
func (r *Runtime) latestRecommendationTrace(c *gin.Context) {
	data, err := r.recommend.LatestTrace(c.Request.Context(), currentUserID(c), c.DefaultQuery("scene", "home"))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, data)
}
func (r *Runtime) recordRecommendationEvent(c *gin.Context) {
	var p map[string]any
	_ = c.ShouldBindJSON(&p)
	userID := currentUserID(c)
	data, err := r.recommend.RecordEvent(c.Request.Context(), userID, p)
	if err != nil {
		respond.Error(c, err)
		return
	}
	event := stringAny(data["event"])
	if _, err := r.recordBehaviorFromPayload(c.Request.Context(), userID, event, p); err != nil {
		respond.Error(c, err)
		return
	}
	respond.Accepted(c, data)
}
func (r *Runtime) musicProfile(c *gin.Context) {
	profile, _ := r.amemClient.MusicProfile(c.Request.Context(), currentUserID(c), c.DefaultQuery("scene", "home"))
	respond.OK(c, map[string]any{"scene": c.DefaultQuery("scene", "home"), "profile": profile, "profileTraceId": "", "memories": []any{}, "summary": map[string]any{}})
}

func (r *Runtime) recordMusicProfileStatement(c *gin.Context) {
	var p map[string]any
	_ = c.ShouldBindJSON(&p)
	if p == nil {
		p = map[string]any{}
	}
	userID := currentUserID(c)
	scene := stringAny(p["scene"])
	if scene == "" {
		scene = c.DefaultQuery("scene", "home")
	}
	description := stringAny(p["description"])
	profile, _ := p["profile"].(map[string]any)
	if description == "" && len(profile) == 0 {
		description = defaultMandopopProfileDescription()
		profile = defaultMandopopProfile()
	}
	if description == "" {
		description = defaultMandopopProfileDescription()
	}
	if len(profile) == 0 {
		profile = defaultMandopopProfile()
	}
	result, err := r.amemClient.RecordProfileStatement(c.Request.Context(), userID, scene, description, profile, stringAny(first(p, "source", "profileSource")))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, result)
}

func defaultMandopopProfileDescription() string {
	return "用户的音乐人格偏爱华语流行乐，尤其喜欢周杰伦式旋律型华语流行、经典华语金曲、华语R&B、温暖人声、清晰旋律线和有情绪表达的歌词。适合推荐怀旧、治愈、夜晚、通勤和工作背景播放的高音质歌单，探索范围应围绕中文流行音乐展开。"
}

func defaultMandopopProfile() map[string]any {
	return map[string]any{
		"positive_topics": map[string]float64{
			"华语流行乐": 0.96, "周杰伦": 0.88, "经典华语金曲": 0.86,
			"华语R&B": 0.82, "治愈系华语": 0.78, "高音质歌单": 0.72,
		},
		"negative_topics": map[string]float64{
			"随机泛音乐": 0.42, "低相关游戏混剪": 0.35,
		},
		"preferred_uploaders": map[string]float64{},
		"mood_weights": map[string]float64{
			"怀旧": 0.86, "治愈": 0.78, "夜晚": 0.72, "通勤": 0.68,
		},
		"recent_intents": []string{
			"华语流行 音乐", "周杰伦 华语 流行", "华语R&B 歌单", "经典华语金曲", "治愈系 华语 歌单",
		},
		"same_uploader_limit": 2,
		"exploration_ratio":   0.18,
		"confidence":          0.9,
	}
}

func (r *Runtime) authStatus(c *gin.Context) {
	status, err := r.biliAuth.Status(c.Request.Context(), currentUserID(c), c.Request.Method == http.MethodPost || c.Query("refresh") == "true")
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, status)
}
func (r *Runtime) createBiliQRCode(c *gin.Context) {
	qr, err := r.biliAuth.CreateQRCode(c.Request.Context(), currentUserID(c))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, qr)
}
func (r *Runtime) pollBiliQRCode(c *gin.Context) {
	var payload map[string]any
	if err := c.ShouldBindJSON(&payload); err != nil {
		respond.Error(c, respond.BadRequest("invalid JSON"))
		return
	}
	status, err := r.biliAuth.PollQRCode(c.Request.Context(), currentUserID(c), stringAny(payload["qrcodeKey"]))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, status)
}
func (r *Runtime) biliAuthProfile(c *gin.Context) {
	profile, err := r.biliAuth.Profile(c.Request.Context(), currentUserID(c), c.Request.Method == http.MethodPost || c.Query("refresh") == "true")
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, profile)
}
func (r *Runtime) logoutBili(c *gin.Context) {
	result, err := r.biliAuth.Logout(c.Request.Context(), currentUserID(c))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, result)
}
func (r *Runtime) biliUserProfile(c *gin.Context) {
	mid, _ := strconv.ParseInt(c.Param("mid"), 10, 64)
	result, err := r.bili.ListUserTracks(c.Request.Context(), mid, 1, 1, "pubdate")
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, result.Profile)
}
func (r *Runtime) biliUserTracks(c *gin.Context) {
	mid, _ := strconv.ParseInt(c.Param("mid"), 10, 64)
	page, _ := strconv.Atoi(c.DefaultQuery("page", "1"))
	pageSize, _ := strconv.Atoi(c.DefaultQuery("pageSize", "20"))
	result, err := r.bili.ListUserTracks(c.Request.Context(), mid, page, pageSize, c.DefaultQuery("order", "pubdate"))
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.OK(c, result)
}
func (r *Runtime) recordAnalysisEvent(c *gin.Context) {
	var p map[string]any
	_ = c.ShouldBindJSON(&p)
	_, err := r.behavior.Record(c.Request.Context(), currentUserID(c), behavior.EventInput{EventID: time.Now().Format(time.RFC3339Nano), Type: "play", Scene: "analysis", Payload: p})
	if err != nil {
		respond.Error(c, err)
		return
	}
	respond.Accepted(c, map[string]any{"accepted": true})
}
func (r *Runtime) getSettings(c *gin.Context) {
	respond.OK(c, map[string]any{"audioQualityPreference": "auto", "playbackSpeed": 1})
}
func (r *Runtime) updateSettings(c *gin.Context)     { r.getSettings(c) }
func (r *Runtime) getAudioQuality(c *gin.Context)    { respond.OK(c, map[string]any{"value": "auto"}) }
func (r *Runtime) updateAudioQuality(c *gin.Context) { r.getAudioQuality(c) }
func (r *Runtime) notImplementedOK(c *gin.Context) {
	respond.OK(c, map[string]any{"implemented": false})
}

func (r *Runtime) emitBehaviorLog(ctx context.Context, userID string, event string, trackID string, scene string, payload map[string]any) {
	payload = payloadWithTrack(payload, trackID)
	if scene != "" && stringAny(payload["scene"]) == "" {
		payload["scene"] = scene
	}
	if _, err := r.recordBehaviorFromPayload(ctx, userID, event, payload); err != nil && r.logger != nil {
		r.logger.Warn("behavior log emit failed", "event", event, "user_id", userID, "error", err)
	}
}

func (r *Runtime) recordBehaviorFromPayload(ctx context.Context, userID string, event string, payload map[string]any) (map[string]any, error) {
	if payload == nil {
		payload = map[string]any{}
	}
	eventID := stringAny(first(payload, "event_id", "eventId"))
	if eventID == "" {
		eventID = fmt.Sprintf("%s:%s:%d", event, userID, time.Now().UnixNano())
	}
	scene := stringAny(payload["scene"])
	trackID := stringAny(first(payload, "trackId", "track_id"))
	if scene == "" {
		scene = "default"
	}
	payload["event_id"] = eventID
	payload["event"] = event
	payload["scene"] = scene
	payload["userId"] = userID
	return r.behavior.Record(ctx, userID, behavior.EventInput{
		EventID: eventID, Type: event, TrackID: trackID, Scene: scene, Payload: payload,
	})
}

func payloadWithTrack(payload map[string]any, trackID string) map[string]any {
	if payload == nil {
		payload = map[string]any{}
	}
	if trackID != "" && stringAny(first(payload, "trackId", "track_id")) == "" {
		payload["trackId"] = trackID
	}
	return payload
}

func recommendationItems(value any) []map[string]any {
	rawItems, ok := value.([]map[string]any)
	if ok {
		return rawItems
	}
	items, ok := value.([]any)
	if !ok {
		return []map[string]any{}
	}
	out := make([]map[string]any, 0, len(items))
	for _, item := range items {
		if row, ok := item.(map[string]any); ok {
			out = append(out, row)
		}
	}
	return out
}

func currentUserID(c *gin.Context) string {
	if userID := strings.TrimSpace(c.GetHeader("X-User-ID")); userID != "" {
		return userID
	}
	return model.LegacyOwnerUserID
}

func cidFromParam(c *gin.Context) *int64 {
	raw := c.Param("cid")
	if raw == "" {
		raw = c.Query("cid")
	}
	if raw == "" {
		return nil
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		return nil
	}
	return &value
}
