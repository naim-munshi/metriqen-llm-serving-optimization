package httpapi

import (
	"sync"
	"time"
)

// Inference attempt outcomes are separate from admission decisions and
// transport-failure reasons. Exactly one terminal outcome is recorded for
// each request that has acquired an active gateway slot. Rejected or invalid
// requests are intentionally excluded from these totals.
type inferenceOutcome uint8

const (
	inferencePreResponseFailure inferenceOutcome = iota
	inferenceNonstreamComplete
	inferenceStreamCleanEOF
	inferencePostResponseInterrupted
	inferenceClientCanceled
)

// Disjoint latency buckets in milliseconds; the final overflow bin holds
// strictly more than 30 seconds. No private request labels are retained.
var inferenceDurationUpperBoundsMs = [8]int{100, 250, 500, 1000, 2500, 5000, 10000, 30000}

type inferenceOutcomeCounters struct {
	mu                  sync.Mutex
	attempts            uint64
	active              uint64
	nonstreamComplete   uint64
	streamCleanEOF      uint64
	streamDone          uint64
	streamMissingDone   uint64
	preResponseFailure  uint64
	postResponseFailure uint64
	canceled            uint64
	durationObservations uint64
	durationTotalUs     uint64
	durationBuckets     [9]uint64
}

// These counters are local to one process and reset on gateway restart.
// Schema v2 adds a mutually exclusive breakdown of stream_clean_eof_total:
// stream_done_total versus stream_missing_done_total. The former means a
// standalone SSE terminal event was observed, NOT JSON-event validation.
// Schema v3 extends v2 with a bounded, fixed-bin duration histogram.
// Duration begins AFTER admission, so it excludes FIFO queue wait.
// It includes upstream transport, model response and downstream writing.
type inferenceOutcomeSnapshot struct {
	SchemaVersion                int       `json:"schema_version"`
	AttemptsTotal                   uint64 `json:"attempts_total"`
	ActiveAttempts                  uint64 `json:"active_attempts"`
	NonstreamCompleteTotal          uint64 `json:"nonstream_complete_total"`
	StreamCleanEOFTotal             uint64 `json:"stream_clean_eof_total"`
	StreamDoneTotal                 uint64 `json:"stream_done_total"`
	StreamMissingDoneTotal          uint64 `json:"stream_missing_done_total"`
	PreResponseFailureTotal         uint64 `json:"pre_response_failure_total"`
	PostResponseInterruptedTotal    uint64 `json:"post_response_interrupted_total"`
	ClientCanceledTotal          uint64    `json:"client_canceled_total"`
	DurationObservationsTotal    uint64    `json:"duration_observations_total"`
	DurationTotalUs              uint64    `json:"duration_total_us"`
	DurationBucketUpperBoundsMs  [8]int    `json:"duration_bucket_upper_bounds_ms"`
	DurationBucketCounts         [9]uint64 `json:"duration_bucket_counts"`
}

func (c *inferenceOutcomeCounters) start() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.attempts++
	c.active++
}

func (c *inferenceOutcomeCounters) finish(outcome inferenceOutcome, done bool, elapsed time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.active == 0 {
		panic("inference outcome recorded without an active attempt")
	}
	c.active--
	if elapsed < 0 {
		elapsed = 0
	}
	c.durationObservations++
	c.durationTotalUs += uint64(elapsed / time.Microsecond)
	c.durationBuckets[inferenceDurationBucket(elapsed)]++
	switch outcome {
	case inferenceNonstreamComplete:
		c.nonstreamComplete++
	case inferenceStreamCleanEOF:
		c.streamCleanEOF++
		if done {
			c.streamDone++
		} else {
			c.streamMissingDone++
		}
	case inferencePostResponseInterrupted:
		c.postResponseFailure++
	case inferenceClientCanceled:
		c.canceled++
	default:
		c.preResponseFailure++
	}
}

func inferenceDurationBucket(elapsed time.Duration) int {
	for i, upperMs := range inferenceDurationUpperBoundsMs {
		if elapsed <= time.Duration(upperMs)*time.Millisecond {
			return i
		}
	}
	return len(inferenceDurationUpperBoundsMs)
}

func (c *inferenceOutcomeCounters) snapshot() inferenceOutcomeSnapshot {
	c.mu.Lock()
	defer c.mu.Unlock()
	return inferenceOutcomeSnapshot{
		SchemaVersion:                3,
		AttemptsTotal:                c.attempts,
		ActiveAttempts:               c.active,
		NonstreamCompleteTotal:       c.nonstreamComplete,
		StreamCleanEOFTotal:          c.streamCleanEOF,
		StreamDoneTotal:              c.streamDone,
		StreamMissingDoneTotal:       c.streamMissingDone,
		PreResponseFailureTotal:      c.preResponseFailure,
		PostResponseInterruptedTotal: c.postResponseFailure,
		ClientCanceledTotal:          c.canceled,
		DurationObservationsTotal:    c.durationObservations,
		DurationTotalUs:              c.durationTotalUs,
		DurationBucketUpperBoundsMs:  inferenceDurationUpperBoundsMs,
		DurationBucketCounts:         c.durationBuckets,
	}
}
