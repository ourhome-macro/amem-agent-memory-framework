package behavior

import "fmt"

type ConsumerConfig struct {
	Enabled      bool
	Endpoint     string
	Topic        string
	Workers      int
	DLQThreshold int
}

type ConsumerRunner interface {
	Start() error
	Close() error
}

type ConsumerSet struct {
	runners []ConsumerRunner
}

func NewConsumerSet(runners ...ConsumerRunner) *ConsumerSet {
	return &ConsumerSet{runners: runners}
}

func (s *ConsumerSet) Start() error {
	if s == nil {
		return nil
	}
	for i, runner := range s.runners {
		if runner == nil {
			continue
		}
		if err := runner.Start(); err != nil {
			for j := i - 1; j >= 0; j-- {
				_ = s.runners[j].Close()
			}
			return err
		}
	}
	return nil
}

func (s *ConsumerSet) Close() error {
	if s == nil {
		return nil
	}
	var first error
	for i := len(s.runners) - 1; i >= 0; i-- {
		if s.runners[i] == nil {
			continue
		}
		if err := s.runners[i].Close(); err != nil && first == nil {
			first = err
		}
	}
	return first
}

func (c ConsumerConfig) normalized() (ConsumerConfig, error) {
	if !c.Enabled {
		return c, nil
	}
	if c.Endpoint == "" {
		return c, fmt.Errorf("rocketmq endpoint is required")
	}
	if c.Topic == "" {
		c.Topic = "radio_behavior_v1"
	}
	if c.Workers <= 0 {
		c.Workers = 4
	}
	if c.DLQThreshold <= 0 {
		c.DLQThreshold = 16
	}
	return c, nil
}
