package httpapi

import "sync"

// Provider usage is self-reported. Values are NOT independently verified,
// enforceable token generation counts, budget debits, or billable usage.
type providerUsageKind uint8

const (
	providerUsageUnavailable providerUsageKind = iota
	providerUsageMissing
	providerUsageMalformed
	providerUsageInconsistent
	providerUsageExceedsCap
	providerUsagePlausible
)

const maxReportedPromptTokens int64 = 1_000_000

type providerUsageObservation struct {
	kind             providerUsageKind
	promptTokens     uint64
	completionTokens uint64
	totalTokens      uint64
}

// A process-local, cardinality-bounded view of provider-reported numbers.
// Prompts, model names, tokens, project IDs and raw JSON never enter state.
// Every completed admitted inference attempt is classified exactly once.
type providerUsageCounters struct {
	mu                    sync.Mutex
	classified            uint64
	nonstreamPlausible    uint64
	nonstreamMissing      uint64
	nonstreamMalformed    uint64
	nonstreamInconsistent uint64
	nonstreamExceedsCap   uint64
	nonstreamUnsuccessful uint64
	streamNotMeasured     uint64
	streamPlausible       uint64
	streamMissing         uint64
	streamMalformed       uint64
	streamInconsistent    uint64
	streamExceedsCap      uint64
	streamIncomplete      uint64
	reportedPrompt        uint64
	reportedCompletion    uint64
	reportedTotal         uint64
}

type providerUsageSnapshot struct {
	SchemaVersion                      int    `json:"schema_version"`
	ClassifiedTerminalAttemptsTotal    uint64 `json:"classified_terminal_attempts_total"`
	NonstreamProviderPlausibleTotal    uint64 `json:"nonstream_provider_plausible_total"`
	NonstreamProviderMissingTotal      uint64 `json:"nonstream_provider_missing_total"`
	NonstreamProviderMalformedTotal    uint64 `json:"nonstream_provider_malformed_total"`
	NonstreamProviderInconsistentTotal uint64 `json:"nonstream_provider_inconsistent_total"`
	NonstreamProviderAboveCapTotal     uint64 `json:"nonstream_provider_above_cap_total"`
	NonstreamNoCompletionTotal         uint64 `json:"nonstream_no_completion_total"`
	StreamNotMeasuredTotal             uint64 `json:"stream_not_measured_total"`
	StreamProviderPlausibleTotal       uint64 `json:"stream_provider_plausible_total"`
	StreamProviderMissingTotal         uint64 `json:"stream_provider_missing_total"`
	StreamProviderMalformedTotal       uint64 `json:"stream_provider_malformed_total"`
	StreamProviderInconsistentTotal    uint64 `json:"stream_provider_inconsistent_total"`
	StreamProviderAboveCapTotal        uint64 `json:"stream_provider_above_cap_total"`
	StreamIncompleteTotal              uint64 `json:"stream_incomplete_total"`
	ProviderPromptTokensReportedTotal  uint64 `json:"provider_prompt_tokens_reported_total"`
	ProviderOutputTokensReportedTotal  uint64 `json:"provider_output_tokens_reported_total"`
	ProviderAllTokensReportedTotal     uint64 `json:"provider_all_tokens_reported_total"`
}

// Keep the old internal method for non-stream synthetic callers; a streaming
// observation needs an explicit, independently confirmed terminal marker.
func (c *providerUsageCounters) record(stream bool, outcome inferenceOutcome, observed providerUsageObservation) {
	c.recordWithStreamResult(stream, outcome, false, observed)
}

func (c *providerUsageCounters) recordWithStreamResult(stream bool, outcome inferenceOutcome, done bool, observed providerUsageObservation) {
	c.mu.Lock()
	defer c.mu.Unlock()

	c.classified++
	switch {
	case stream:
		if outcome != inferenceStreamCleanEOF || !done {
			// Includes canceled/interrupted streams, clean EOF without
			// [DONE], and fake usage in a body with no terminal proof.
			c.streamIncomplete++
			c.streamNotMeasured++
			return
		}
		switch observed.kind {
		case providerUsagePlausible:
			c.streamPlausible++
			c.reportedPrompt += observed.promptTokens
			c.reportedCompletion += observed.completionTokens
			c.reportedTotal += observed.totalTokens
		case providerUsageMalformed:
			c.streamMalformed++
			c.streamNotMeasured++
		case providerUsageInconsistent:
			c.streamInconsistent++
			c.streamNotMeasured++
		case providerUsageExceedsCap:
			c.streamExceedsCap++
			c.streamNotMeasured++
		default:
			c.streamMissing++
			c.streamNotMeasured++
		}
	case outcome != inferenceNonstreamComplete:
		c.nonstreamUnsuccessful++
	default:
		switch observed.kind {
		case providerUsagePlausible:
			c.nonstreamPlausible++
			c.reportedPrompt += observed.promptTokens
			c.reportedCompletion += observed.completionTokens
			c.reportedTotal += observed.totalTokens
		case providerUsageMissing:
			c.nonstreamMissing++
		case providerUsageInconsistent:
			c.nonstreamInconsistent++
		case providerUsageExceedsCap:
			c.nonstreamExceedsCap++
		default:
			c.nonstreamMalformed++
		}
	}
}

func (c *providerUsageCounters) snapshot() providerUsageSnapshot {
	c.mu.Lock()
	defer c.mu.Unlock()
	return providerUsageSnapshot{
		SchemaVersion:                      2,
		ClassifiedTerminalAttemptsTotal:    c.classified,
		NonstreamProviderPlausibleTotal:    c.nonstreamPlausible,
		NonstreamProviderMissingTotal:      c.nonstreamMissing,
		NonstreamProviderMalformedTotal:    c.nonstreamMalformed,
		NonstreamProviderInconsistentTotal: c.nonstreamInconsistent,
		NonstreamProviderAboveCapTotal:     c.nonstreamExceedsCap,
		NonstreamNoCompletionTotal:         c.nonstreamUnsuccessful,
		StreamNotMeasuredTotal:             c.streamNotMeasured,
		StreamProviderPlausibleTotal:       c.streamPlausible,
		StreamProviderMissingTotal:         c.streamMissing,
		StreamProviderMalformedTotal:       c.streamMalformed,
		StreamProviderInconsistentTotal:    c.streamInconsistent,
		StreamProviderAboveCapTotal:        c.streamExceedsCap,
		StreamIncompleteTotal:              c.streamIncomplete,
		ProviderPromptTokensReportedTotal:  c.reportedPrompt,
		ProviderOutputTokensReportedTotal:  c.reportedCompletion,
		ProviderAllTokensReportedTotal:     c.reportedTotal,
	}
}
