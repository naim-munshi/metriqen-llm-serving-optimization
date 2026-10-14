package httpapi

import (
	"context"
	"errors"
)

// admissionCounters is always accessed under admissionScheduler.mu. Only
// aggregate outcomes are retained; no model tags, prompts, or user IDs.
type admissionCounters struct {
	immediate uint64
	queued    uint64
	full      uint64
	timeout   uint64
	canceled  uint64
}

// Admission metrics are a bounded, process-local diagnostic snapshot.
// No per-request attributes or unbounded labels are exported.
type admissionMetrics struct {
	SchemaVersion          int    `json:"schema_version"`
	ActiveRequests         int    `json:"active_requests"`
	WaitingRequests        int    `json:"waiting_requests"`
	MaxActiveRequests      int    `json:"max_active_requests"`
	MaxWaitingRequests     int    `json:"max_waiting_requests"`
	AdmittedImmediateTotal uint64 `json:"admitted_immediate_total"`
	AdmittedQueuedTotal    uint64 `json:"admitted_queued_total"`
	RejectedFullTotal      uint64 `json:"rejected_full_total"`
	QueueTimeoutTotal      uint64 `json:"queue_timeout_total"`
	QueueCanceledTotal     uint64 `json:"queue_canceled_total"`
}

func (s *admissionScheduler) snapshot() admissionMetrics {
	s.mu.Lock()
	defer s.mu.Unlock()
	return admissionMetrics{
		SchemaVersion:          1,
		ActiveRequests:         s.active,
		WaitingRequests:        len(s.waiting),
		MaxActiveRequests:      s.maxActive,
		MaxWaitingRequests:     s.maxQueued,
		AdmittedImmediateTotal: s.counters.immediate,
		AdmittedQueuedTotal:    s.counters.queued,
		RejectedFullTotal:      s.counters.full,
		QueueTimeoutTotal:      s.counters.timeout,
		QueueCanceledTotal:     s.counters.canceled,
	}
}

// recordQueuedOutcome is called exactly once after a request has entered
// the FIFO queue. It tracks outcomes of admission, not upstream completions.
func (s *admissionScheduler) recordQueuedOutcome(err error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	switch {
	case err == nil:
		s.counters.queued++
	case errors.Is(err, context.DeadlineExceeded):
		s.counters.timeout++
	default:
		s.counters.canceled++
	}
}
