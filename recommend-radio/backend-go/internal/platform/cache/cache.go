package cache

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/redis/go-redis/v9"
)

var ErrMiss = errors.New("cache miss")

type JSONCache interface {
	GetJSON(ctx context.Context, key string, target any) error
	SetJSON(ctx context.Context, key string, value any, ttl time.Duration) error
	Close() error
}

type RedisJSONCache struct {
	client *redis.Client
}

func NewRedis(addr string, password string, db int) *RedisJSONCache {
	return &RedisJSONCache{
		client: redis.NewClient(&redis.Options{
			Addr:     addr,
			Password: password,
			DB:       db,
		}),
	}
}

func (c *RedisJSONCache) GetJSON(ctx context.Context, key string, target any) error {
	payload, err := c.client.Get(ctx, key).Bytes()
	if errors.Is(err, redis.Nil) {
		return ErrMiss
	}
	if err != nil {
		return err
	}
	return json.Unmarshal(payload, target)
}

func (c *RedisJSONCache) SetJSON(ctx context.Context, key string, value any, ttl time.Duration) error {
	payload, err := json.Marshal(value)
	if err != nil {
		return err
	}
	return c.client.Set(ctx, key, payload, ttl).Err()
}

func (c *RedisJSONCache) Close() error {
	return c.client.Close()
}

type NoopJSONCache struct{}

func (NoopJSONCache) GetJSON(context.Context, string, any) error { return ErrMiss }
func (NoopJSONCache) SetJSON(context.Context, string, any, time.Duration) error {
	return nil
}
func (NoopJSONCache) Close() error { return nil }
