package behavior

import (
	"context"
)

type Publisher interface {
	Publish(ctx context.Context, topic string, eventID string, payload []byte) error
	Close() error
}

type NoopPublisher struct{}

func (NoopPublisher) Publish(context.Context, string, string, []byte) error { return nil }
func (NoopPublisher) Close() error                                          { return nil }
