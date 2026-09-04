//go:build rocketmq

package behavior

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	rmq "github.com/apache/rocketmq-clients/golang/v5"
)

type rocketMQConsumerRunner struct {
	consumer rmq.PushConsumer
}

func NewRocketMQConsumerSet(cfg ConsumerConfig, processor *ConsumerProcessor, logger *slog.Logger) (*ConsumerSet, error) {
	cfg, err := cfg.normalized()
	if err != nil {
		return nil, err
	}
	if !cfg.Enabled {
		return NewConsumerSet(), nil
	}
	if processor == nil {
		return nil, fmt.Errorf("consumer processor is required")
	}
	if logger == nil {
		logger = slog.Default()
	}
	runners := make([]ConsumerRunner, 0, 3)
	for _, consumerName := range []string{AMEMConsumerName, RecommendationConsumerName, MetricsConsumerName} {
		runner, err := newRocketMQConsumerRunner(cfg, consumerName, processor, logger)
		if err != nil {
			return nil, err
		}
		runners = append(runners, runner)
	}
	return NewConsumerSet(runners...), nil
}

func newRocketMQConsumerRunner(cfg ConsumerConfig, consumerName string, processor *ConsumerProcessor, logger *slog.Logger) (*rocketMQConsumerRunner, error) {
	consumer, err := rmq.NewPushConsumer(
		&rmq.Config{Endpoint: cfg.Endpoint, ConsumerGroup: consumerName},
		rmq.WithPushAwaitDuration(5*time.Second),
		rmq.WithPushSubscriptionExpressions(map[string]*rmq.FilterExpression{cfg.Topic: rmq.SUB_ALL}),
		rmq.WithPushConsumptionThreadCount(int32(cfg.Workers)),
		rmq.WithPushMaxCacheMessageCount(int32(cfg.Workers*64)),
		rmq.WithPushMessageListener(&rmq.FuncMessageListener{
			Consume: func(mv *rmq.MessageView) rmq.ConsumerResult {
				ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
				defer cancel()
				if err := processor.Process(ctx, consumerName, mv.GetTopic(), mv.GetBody()); err != nil {
					attempt := int(mv.GetDeliveryAttempt())
					logger.Warn("rocketmq consume failed", "consumer", consumerName, "topic", mv.GetTopic(), "message_id", mv.GetMessageId(), "attempt", attempt, "error", err)
					if attempt >= cfg.DLQThreshold {
						if dlqErr := processor.RecordDLQ(context.Background(), consumerName, mv.GetTopic(), mv.GetMessageId(), attempt, mv.GetBody(), err); dlqErr != nil {
							logger.Error("record consumer dlq failed", "consumer", consumerName, "message_id", mv.GetMessageId(), "error", dlqErr)
							return rmq.FAILURE
						}
						return rmq.SUCCESS
					}
					return rmq.FAILURE
				}
				return rmq.SUCCESS
			},
		}),
	)
	if err != nil {
		return nil, err
	}
	return &rocketMQConsumerRunner{consumer: consumer}, nil
}

func (r *rocketMQConsumerRunner) Start() error {
	if r == nil || r.consumer == nil {
		return nil
	}
	return r.consumer.Start()
}

func (r *rocketMQConsumerRunner) Close() error {
	if r == nil || r.consumer == nil {
		return nil
	}
	return r.consumer.GracefulStop()
}
