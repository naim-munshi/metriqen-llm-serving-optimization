package httpapi

// sseTerminalTracker identifies a standalone SSE `data: [DONE]` event at a
// complete event boundary. It observes bytes already forwarded by the
// gateway; it neither buffers a generated response nor validates JSON data
// events. Memory use is constant, even for an arbitrarily long data line.
// A terminal marker is not accepted when followed by another data event.
type sseTerminalTracker struct {
	prefix       [32]byte
	lineLen      int
	lastCR       bool
	dataLines    int
	doneLine     bool
	doneEvent    bool
	dataAfterEnd bool
}

func (s *sseTerminalTracker) observe(part []byte) {
	for _, b := range part {
		if s.lastCR {
			s.lastCR = false
			if b == '\n' {
				continue // Treat CRLF as one separator, including across reads.
			}
		}
		switch b {
		case '\r':
			s.finishLine()
			s.lastCR = true
		case '\n':
			s.finishLine()
		default:
			if s.lineLen < len(s.prefix) {
				s.prefix[s.lineLen] = b
			}
			s.lineLen++
		}
	}
}

func (s *sseTerminalTracker) finishLine() {
	if s.lineLen == 0 {
		if s.dataLines > 0 {
			if s.doneEvent {
				s.dataAfterEnd = true
			} else if s.dataLines == 1 && s.doneLine {
				s.doneEvent = true
			}
		}
		s.dataLines = 0
		s.doneLine = false
		return
	}
	view := s.prefix[:min(s.lineLen, len(s.prefix))]
	if len(view) >= 4 && string(view[:4]) == "data" {
		if s.lineLen == 4 || (len(view) > 4 && view[4] == ':') {
			s.dataLines++
			if s.dataLines == 1 && s.lineLen <= len(s.prefix) {
				value := view[4:]
				if len(value) > 0 && value[0] == ':' {
					value = value[1:]
				}
				if len(value) > 0 && value[0] == ' ' {
					value = value[1:]
				}
				s.doneLine = string(value) == "[DONE]"
			} else {
				s.doneLine = false
			}
		}
	}
	s.lineLen = 0
}

func (s *sseTerminalTracker) completed() bool {
	return s.doneEvent && !s.dataAfterEnd && s.lineLen == 0 && s.dataLines == 0
}
