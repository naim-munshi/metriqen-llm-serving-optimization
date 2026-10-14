package httpapi

import (
	"strings"
	"testing"
)

// The parser must handle arbitrary Go net/http chunking and cannot rely on
// newline-aligned reads or unbounded response buffering.
func TestSSETerminalTrackerChunkBoundariesAndSpoofing(t *testing.T) {
	for _, tc := range []struct {
		name, payload string
		complete      bool
	}{
		{"done", "data: [DONE]\n\n", true},
		{"no space", "data:[DONE]\n\n", true},
		{"CRLF", "data: [DONE]\r\n\r\n", true},
		{"bare CR", "data: [DONE]\r\r", true},
		{"comment before", ": ready\n\ndata: [DONE]\n\n", true},
		{"data before", "data: {\"choices\":[]}\n\ndata: [DONE]\n\n", true},
		{"extra data in DONE event", "data: x\ndata: [DONE]\n\n", false},
		{"missing event boundary", "data: [DONE]\n", false},
		{"unterminated line", "data: [DONE]", false},
		{"prefix only", "data: [DONE] extra\n\n", false},
		{"JSON string masquerade", "data: {\"content\":\"[DONE]\"}\n\n", false},
		{"comment masquerade", ":data: [DONE]\n\n", false},
		{"field name masquerade", "metadata: [DONE]\n\n", false},
		{"late data after marker", "data: [DONE]\n\ndata: trailing\n\n", false},
		{"late partial data", "data: [DONE]\n\ndata: trailing", false},
		{"duplicate marker", "data: [DONE]\n\ndata: [DONE]\n\n", false},
		{"no events", "", false},
		{"plain event", "data: {}\n\n", false},
		{"huge event bounded memory", "data: " + strings.Repeat("x", 90000) + "\n\ndata: [DONE]\n\n", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			for _, size := range []int{1, 2, 3, 7, 1024} {
				var tracker sseTerminalTracker
				for i := 0; i < len(tc.payload); i += size {
					end := min(i+size, len(tc.payload))
					tracker.observe([]byte(tc.payload[i:end]))
				}
				if got := tracker.completed(); got != tc.complete {
					t.Errorf("chunkSize=%d got %v want %v", size, got, tc.complete)
				}
			}
		})
	}
}
