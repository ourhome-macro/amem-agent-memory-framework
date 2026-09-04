package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"recommend-radio/backend-go/internal/app"
	"recommend-radio/backend-go/internal/platform/config"
)

func main() {
	cfg := config.Load()
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: cfg.LogLevel}))

	runtime, err := app.NewRuntime(cfg, logger)
	if err != nil {
		logger.Error("runtime init failed", "error", err)
		os.Exit(1)
	}
	defer runtime.Close()

	server := &http.Server{
		Addr:              cfg.HTTPAddr,
		Handler:           runtime.Router(),
		ReadHeaderTimeout: 10 * time.Second,
	}

	go func() {
		logger.Info("recommend-radio go backend listening", "addr", cfg.HTTPAddr)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("http server failed", "error", err)
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop

	ctx, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
	defer cancel()
	runtime.Shutdown(ctx)
	if err := server.Shutdown(ctx); err != nil {
		logger.Error("http shutdown failed", "error", err)
	}
}
