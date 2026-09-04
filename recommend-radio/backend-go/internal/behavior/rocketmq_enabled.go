//go:build rocketmq

package behavior

import (
	"context"

	rmq "github.com/apache/rocketmq-clients/golang/v5"
)

type RocketMQPublisher struct {
	producer rmq.Producer
}

func NewRocketMQPublisher(endpoint string, topic string) (Publisher, error) {
	producer, err := rmq.NewProducer(&rmq.Config{Endpoint: endpoint}, rmq.WithTopics(topic))
	if err != nil {
		return nil, err
	}
	if err := producer.Start(); err != nil {
		return nil, err
	}
	return &RocketMQPublisher{producer: producer}, nil
}

func (p *RocketMQPublisher) Publish(ctx context.Context, topic string, eventID string, payload []byte) error {
	msg := &rmq.Message{Topic: topic, Body: payload}
	msg.SetKeys(eventID)
	_, err := p.producer.Send(ctx, msg)
	return err
}

func (p *RocketMQPublisher) Close() error {
	return p.producer.GracefulStop()
}
