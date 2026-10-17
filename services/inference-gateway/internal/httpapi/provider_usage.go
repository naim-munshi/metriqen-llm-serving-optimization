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
	reportedPrompt        uint64
	reportedCompletion    uint64
	reportedTotal         uint64
}

type providerUsageSnapshot struct {
	SchemaVersion                     int    `json:"schema_version"`
	ClassifiedTerminalAttemptsTotal   uint64 `json:"classified_terminal_attempts_total"`
	NonstreamProviderPlausibleTotal   uint64 `json:"nonstream_provider_plausible_total"`
	NonstreamProviderMissingTotal     uint64 `json:"nonstream_provider_missing_total"`
	NonstreamProviderMalformedTotal   uint64 `json:"nonstream_provider_malformed_total"`
	NonstreamProviderInconsistentTotal uint64 `json:"nonstream_provider_inconsistent_total"`
	NonstreamProviderAboveCapTotal    uint64 `json:"nonstream_provider_above_cap_total"`
	NonstreamNoCompletionTotal        uint64 `json:"nonstream_no_completion_total"`
	StreamNotMeasuredTotal            uint64 `json:"stream_not_measured_total"`
	ProviderPromptTokensReportedTotal uint64 `json:"provider_prompt_tokens_reported_total"`
	ProviderOutputTokensReportedTotal uint64 `json:"provider_output_tokens_reported_total"`
	ProviderAllTokensReportedTotal    uint64 `json:"provider_all_tokens_reported_total"`
}

func (c *providerUsageCounters) record(stream bool, outcome inferenceOutcome, observed providerUsageObservation) {
	c.mu.Lock()
	defer c.mu.Unlock()

	c.classified++
	switch {
	case stream:
		// Even a clean SSE [DONE] is not proof of token-usage metadata.
		c.streamNotMeasured++
	case outcome != inferenceNonstreamComplete:
		// A request that failed, was canceled, or could not be delivered
		// cannot contribute trusted successful-completion usage counts.
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
		SchemaVersion:                     1,
		ClassifiedTerminalAttemptsTotal:   c.classified,
		NonstreamProviderPlausibleTotal:   c.nonstreamPlausible,
		NonstreamProviderMissingTotal:     c.nonstreamMissing,
		NonstreamProviderMalformedTotal:   c.nonstreamMalformed,
		NonstreamProviderInconsistentTotal: c.nonstreamInconsistent,
		NonstreamProviderAboveCapTotal:    c.nonstreamExceedsCap,
		NonstreamNoCompletionTotal:        c.nonstreamUnsuccessful,
		StreamNotMeasuredTotal:            c.streamNotMeasured,
		ProviderPromptTokensReportedTotal: c.reportedPrompt,
		ProviderOutputTokensReportedTotal: c.reportedCompletion,
		ProviderAllTokensReportedTotal:    c.reportedTotal,
	}
}
