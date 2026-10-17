package httpapi

import (
	"bytes"
	"encoding/json"
)

// sseProviderUsageTracker is a constant-memory (per request) observer of
// already-forwarded bytes. It does not alter the upstream SSE stream, replay
// token generation, persist model text, or ever produce billable usage.
//
// Only a bounded, single-line final usage event of the form
//   data: {"choices":[],"usage":{"prompt_tokens":...,...}}
//
// followed by a standalone data: [DONE] event and clean EOF is considered
// internally plausible provider-reported usage. The provider can still lie.
// Chunk boundaries, LF/CRLF, comments and unrelated ordinary events are safe.
const maxSSEUsageLineBytes = 4096

type sseProviderUsageTracker struct {
	line       [maxSSEUsageLineBytes]byte
	lineLen    int
	lastCR     bool
	data       [maxSSEUsageLineBytes]byte
	dataLen    int
	dataLines  int
	tooLong    bool
	seenUsage  bool
	invalidSeq bool
	done       bool
	observed   providerUsageObservation
	outputCap  int
}

func newSSEProviderUsageTracker(outputCap int) sseProviderUsageTracker {
	return sseProviderUsageTracker{
		outputCap: outputCap,
		observed:  providerUsageObservation{kind: providerUsageMissing},
	}
}

func (s *sseProviderUsageTracker) observe(part []byte) {
	for _, ch := range part {
		if s.lastCR {
			s.lastCR = false
			if ch == '\n' {
				continue
			}
		}
		switch ch {
		case '\r':
			s.finishLine()
			s.lastCR = true
		case '\n':
			s.finishLine()
		default:
			if s.lineLen < len(s.line) {
				s.line[s.lineLen] = ch
			}
			s.lineLen++
		}
	}
}

func (s *sseProviderUsageTracker) finishLine() {
	if s.lineLen == 0 {
		s.finishEvent()
		return
	}
	length := min(s.lineLen, len(s.line))
	view := s.line[:length]
	if bytes.Equal(view, []byte("data")) ||
		bytes.HasPrefix(view, []byte("data:")) {
		s.dataLines++
		if s.dataLines == 1 {
			start := 4
			if len(view) > 4 && view[4] == ':' {
				start++
			}
			if len(view) > start && view[start] == ' ' {
				start++
			}
			s.dataLen = len(view) - start
			copy(s.data[:], view[start:])
			if s.lineLen > len(s.line) {
				s.tooLong = true
			}
		}
	}
	s.lineLen = 0
}

func (s *sseProviderUsageTracker) finishEvent() {
	if s.dataLines == 0 {
		return // Comment-only or heartbeat events are not usage evidence.
	}
	if s.dataLines != 1 || s.tooLong {
		// A multi-line or oversized event is not proof of reported usage.
		// Following a potential usage event it also invalidates sequencing.
		if s.seenUsage || s.done {
			s.invalidSeq = true
		}
		s.clearEvent()
		return
	}
	content := s.data[:s.dataLen]
	if bytes.Equal(content, []byte("[DONE]")) {
		if s.done {
			s.invalidSeq = true
		}
		s.done = true
		s.clearEvent()
		return
	}
	if s.done || s.seenUsage {
		s.invalidSeq = true
	}
	// Most stream events are ordinary deltas, containing no usage field.
	// Do not parse, store, or allocate their private message content.
	if !bytes.Contains(content, []byte(`"usage"`)) {
		s.clearEvent()
		return
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(content, &fields); err != nil || fields == nil {
		// A literal "usage" marker in malformed content is ambiguous,
		// never acceptable as a real provider usage observation.
		s.seenUsage = true
		s.observed.kind = providerUsageMalformed
		s.clearEvent()
		return
	}
	raw, present := fields["usage"]
	if !present {
		s.clearEvent() // E.g. the word usage inside a model message.
		return
	}
	if s.seenUsage {
		s.invalidSeq = true
	}
	s.seenUsage = true
	var choices []json.RawMessage
	if err := json.Unmarshal(fields["choices"], &choices); err != nil ||
		choices == nil || len(choices) != 0 {
		s.observed.kind = providerUsageMalformed
	} else {
		s.observed = classifyProviderUsage(raw, s.outputCap)
	}
	s.clearEvent()
}

func (s *sseProviderUsageTracker) clearEvent() {
	s.dataLines = 0
	s.dataLen = 0
	s.tooLong = false
}

// result is meaningful ONLY at clean network EOF with a standalone terminal
// marker from the independent SSE framing tracker. Interrupted/missing-DONE
// streams cannot contribute to provider-reported token sums.
func (s *sseProviderUsageTracker) result(complete bool) providerUsageObservation {
	if !complete {
		return providerUsageObservation{kind: providerUsageUnavailable}
	}
	if s.invalidSeq {
		return providerUsageObservation{kind: providerUsageMalformed}
	}
	if !s.seenUsage {
		return providerUsageObservation{kind: providerUsageMissing}
	}
	return s.observed
}
