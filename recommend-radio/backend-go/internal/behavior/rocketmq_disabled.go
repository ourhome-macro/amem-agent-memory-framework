//go:build !rocketmq

package behavior

import "fmt"

func NewRocketMQPublisher(endpoint string, topic string) (Publisher, error) {
	return nil, fmt.Errorf("RocketMQ publisher requires the rocketmq build tag; endpoint=%s topic=%s", endpoint, topic)
}
