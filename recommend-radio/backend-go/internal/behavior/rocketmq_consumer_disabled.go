//go:build !rocketmq

package behavior

import (
	"fmt"
	"log/slog"
)

func NewRocketMQConsumerSet(cfg ConsumerConfig, _ *ConsumerProcessor, _ *slog.Logger) (*ConsumerSet, error) {
	if cfg.Enabled {
		return nil, fmt.Errorf("rocketmq consumers require building with -tags rocketmq")
	}
	return NewConsumerSet(), nil
}
