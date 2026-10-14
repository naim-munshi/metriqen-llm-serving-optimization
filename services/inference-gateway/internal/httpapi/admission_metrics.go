package httpapi

import (
	"context"
	"errors"
	"time"
)

// Fixed histogram buckets are cumulative-free counts, not p50/p95 estimates.
// The ninth bucket covers durations greater than 5 seconds.
var queueWaitUpperBoundsMs = [8]int{10, 50, 100, 250, 500, 1000, 2000, 5000}

// admissionCounters is always accessed under admissionScheduler.mu. Only
// aggregate outcomes are retained; no model tags, prompts, or user IDs.
type admissionCounters struct {
	immediate         uint64
	queued            uint64
	full              uint64
	timeout           uint64
	canceled          uint64
	queueWaitSamples  uint64
	queueWaitTotalUs  uint64
	queueWaitBuckets  [9]uint64
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
	QueueWaitObservationsTotal uint64     `json:"queue_wait_observations_total"`
	QueueWaitDurationTotalUs   uint64     `json:"queue_wait_duration_total_us"`
	QueueWaitUpperBoundsMs     [8]int     `json:"queue_wait_bucket_upper_bounds_ms"`
	QueueWaitBucketCounts      [9]uint64  `json:"queue_wait_bucket_counts"`
}

func (s *admissionScheduler) snapshot() admissionMetrics {
	s.mu.Lock()
	defer s.mu.Unlock()
	return admissionMetrics{
		SchemaVersion:          2,
		ActiveRequests:         s.active,
		WaitingRequests:        len(s.waiting),
		MaxActiveRequests:      s.maxActive,
		MaxWaitingRequests:     s.maxQueued,
		AdmittedImmediateTotal: s.counters.immediate,
		AdmittedQueuedTotal:    s.counters.queued,
		RejectedFullTotal:      s.counters.full,
		QueueTimeoutTotal:      s.counters.timeout,
		QueueCanceledTotal:     s.counters.canceled,
		QueueWaitObservationsTotal: s.counters.queueWaitSamples,
		QueueWaitDurationTotalUs:   s.counters.queueWaitTotalUs,
		QueueWaitUpperBoundsMs:     queueWaitUpperBoundsMs,
		QueueWaitBucketCounts:      s.counters.queueWaitBuckets,
	}
}

// recordQueuedOutcome is called exactly once after a request has entered
// the FIFO queue. It tracks outcomes of admission, not upstream completions.
func (s *admissionScheduler) recordQueuedOutcome(err error, enqueuedAt time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	// Use monotonic elapsed time; every request that entered the FIFO gets
	// exactly one sample, regardless of successful admission or cancellation.
	// Immediate admissions and full-queue rejections are not observations.
	elapsed := time.Since(enqueuedAt)
	if elapsed < 0 {
		elapsed = 0
	}
	us := uint64(elapsed / time.Microsecond)
	s.counters.queueWaitSamples++
	s.counters.queueWaitTotalUs += us
	bucket := len(queueWaitUpperBoundsMs)
	for i, boundMs := range queueWaitUpperBoundsMs {
		if elapsed <= time.Duration(boundMs)*time.Millisecond {
			bucket = i
			break
		}
	}
	s.counters.queueWaitBuckets[bucket]++

	switch {
	case err == nil:
		s.counters.queued++
	case errors.Is(err, context.DeadlineExceeded):
		s.counters.timeout++
	default:
		s.counters.canceled++
	}
}
