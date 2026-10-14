package httpapi

import "sync"

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

type inferenceOutcomeCounters struct {
	mu                  sync.Mutex
	attempts            uint64
	active              uint64
	nonstreamComplete   uint64
	streamCleanEOF      uint64
	preResponseFailure  uint64
	postResponseFailure uint64
	canceled            uint64
}

// These counters are local to one process and reset on gateway restart.
// "nonstream_complete" is the successful delivery of validated JSON to
// http.ResponseWriter, not a model quality guarantee. "stream_clean_eof"
// means only that upstream ended with io.EOF; it does NOT verify SSE [DONE].
type inferenceOutcomeSnapshot struct {
	SchemaVersion                int    `json:"schema_version"`
	AttemptsTotal                uint64 `json:"attempts_total"`
	ActiveAttempts               uint64 `json:"active_attempts"`
	NonstreamCompleteTotal       uint64 `json:"nonstream_complete_total"`
	StreamCleanEOFTotal          uint64 `json:"stream_clean_eof_total"`
	PreResponseFailureTotal      uint64 `json:"pre_response_failure_total"`
	PostResponseInterruptedTotal uint64 `json:"post_response_interrupted_total"`
	ClientCanceledTotal          uint64 `json:"client_canceled_total"`
}

func (c *inferenceOutcomeCounters) start() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.attempts++
	c.active++
}

func (c *inferenceOutcomeCounters) finish(outcome inferenceOutcome) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.active == 0 {
		panic("inference outcome recorded without an active attempt")
	}
	c.active--
	switch outcome {
	case inferenceNonstreamComplete:
		c.nonstreamComplete++
	case inferenceStreamCleanEOF:
		c.streamCleanEOF++
	case inferencePostResponseInterrupted:
		c.postResponseFailure++
	case inferenceClientCanceled:
		c.canceled++
	default:
		c.preResponseFailure++
	}
}

func (c *inferenceOutcomeCounters) snapshot() inferenceOutcomeSnapshot {
	c.mu.Lock()
	defer c.mu.Unlock()
	return inferenceOutcomeSnapshot{
		SchemaVersion:                1,
		AttemptsTotal:                c.attempts,
		ActiveAttempts:               c.active,
		NonstreamCompleteTotal:       c.nonstreamComplete,
		StreamCleanEOFTotal:          c.streamCleanEOF,
		PreResponseFailureTotal:      c.preResponseFailure,
		PostResponseInterruptedTotal: c.postResponseFailure,
		ClientCanceledTotal:          c.canceled,
	}
}
