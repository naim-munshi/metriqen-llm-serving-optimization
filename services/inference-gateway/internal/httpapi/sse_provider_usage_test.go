package httpapi

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

const reportedStreamUsage = `{"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":4,"total_tokens":13}}`

func inspectUsageSSE(payload string, step, cap int) (providerUsageObservation, bool) {
	var terminal sseTerminalTracker
	usage := newSSEProviderUsageTracker(cap)
	for i := 0; i < len(payload); i += step {
		end := min(i+step, len(payload))
		bytes := []byte(payload[i:end])
		terminal.observe(bytes)
		usage.observe(bytes)
	}
	done := terminal.completed()
	return usage.result(done), done
}

func TestSSEProviderUsageBoundedParsingAtEveryChunkBoundary(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		expected   providerUsageKind
		done       bool
	}{
		{"valid", "data: " + reportedStreamUsage + "\n\ndata: [DONE]\n\n", providerUsagePlausible, true},
		{"CRLF", ": heartbeat\r\n\r\ndata: " + reportedStreamUsage + "\r\n\r\n: ok\r\n\r\ndata:[DONE]\r\n\r\n", providerUsagePlausible, true},
		{"CR only", "data: " + reportedStreamUsage + "\r\rdata: [DONE]\r\r", providerUsagePlausible, true},
		{"zero count", "data: {\"choices\":[],\"usage\":{\"prompt_tokens\":0,\"completion_tokens\":0,\"total_tokens\":0}}\n\ndata: [DONE]\n\n", providerUsagePlausible, true},
		{"no usage", "data: {\"choices\":[{\"delta\":{\"content\":\"hello\"}}]}\n\ndata: [DONE]\n\n", providerUsageMissing, true},
		{"no choices", "data: {\"usage\":{\"prompt_tokens\":9,\"completion_tokens\":4,\"total_tokens\":13}}\n\ndata: [DONE]\n\n", providerUsageMalformed, true},
		{"nonempty choices", "data: {\"choices\":[{}],\"usage\":{\"prompt_tokens\":9,\"completion_tokens\":4,\"total_tokens\":13}}\n\ndata: [DONE]\n\n", providerUsageMalformed, true},
		{"malformed usage", "data: {\"choices\":[],\"usage\":{\"prompt_tokens\":\"9\",\"completion_tokens\":4,\"total_tokens\":13}}\n\ndata: [DONE]\n\n", providerUsageMalformed, true},
		{"invalid total", "data: {\"choices\":[],\"usage\":{\"prompt_tokens\":9,\"completion_tokens\":4,\"total_tokens\":11}}\n\ndata: [DONE]\n\n", providerUsageInconsistent, true},
		{"above cap", "data: {\"choices\":[],\"usage\":{\"prompt_tokens\":9,\"completion_tokens\":257,\"total_tokens\":266}}\n\ndata: [DONE]\n\n", providerUsageExceedsCap, true},
		{"duplicate usage", "data: " + reportedStreamUsage + "\n\ndata: " + reportedStreamUsage + "\n\ndata: [DONE]\n\n", providerUsageMalformed, true},
		{"usage followed by data", "data: " + reportedStreamUsage + "\n\ndata: {\"choices\":[{\"delta\":{\"content\":\"late\"}}]}\n\ndata: [DONE]\n\n", providerUsageMalformed, true},
		{"invalid usage JSON", "data: {\"usage\": invalid}\n\ndata: [DONE]\n\n", providerUsageMalformed, true},
		{"multiline data event", "data: " + reportedStreamUsage + "\ndata: {\"extra\":1}\n\ndata: [DONE]\n\n", providerUsageMissing, true},
		{"oversized data event", "data: " + reportedStreamUsage + strings.Repeat(" ", maxSSEUsageLineBytes+10) + "\n\ndata: [DONE]\n\n", providerUsageMissing, true},
		{"missing DONE", "data: " + reportedStreamUsage + "\n\n", providerUsageUnavailable, false},
		{"incomplete DONE", "data: " + reportedStreamUsage + "\n\ndata: [DONE]\n", providerUsageUnavailable, false},
		{"spoof marker in text", "data: {\"choices\":[{\"delta\":{\"content\":\"[DONE] and usage\"}}]}\n\n", providerUsageUnavailable, false},
		{"after DONE extra data", "data: " + reportedStreamUsage + "\n\ndata: [DONE]\n\ndata: trailing\n\n", providerUsageUnavailable, false},
		{"duplicate terminal", "data: " + reportedStreamUsage + "\n\ndata: [DONE]\n\ndata: [DONE]\n\n", providerUsageUnavailable, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			for _, step := range []int{1, 2, 3, 7, 31, 1024} {
				result, done := inspectUsageSSE(tc.body, step, defaultMaxTokens)
				if result.kind != tc.expected || done != tc.done {
					t.Fatalf("step=%d got kind=%d done=%v want kind=%d done=%v", step,
						result.kind, done, tc.expected, tc.done)
				}
				if result.kind == providerUsagePlausible &&
					!strings.Contains(tc.name, "zero") &&
					(result.promptTokens != 9 || result.completionTokens != 4 || result.totalTokens != 13) {
					t.Fatalf("step=%d incorrect aggregate counts: %+v", step, result)
				}
			}
		})
	}
}

func TestSSEProviderUsageNoMultiMegabyteBufferOrCrossRequestState(t *testing.T) {
	var s = newSSEProviderUsageTracker(defaultMaxTokens)
	var terminal sseTerminalTracker
	veryLong := "data: " + strings.Repeat("PRIVATE_MODEL_OUTPUT", 10000) +
		"\n\ndata: " + reportedStreamUsage + "\n\ndata: [DONE]\n\n"
	for i := 0; i < len(veryLong); i += 7 {
		end := min(i+7, len(veryLong))
		b := []byte(veryLong[i:end])
		s.observe(b)
		terminal.observe(b)
	}
	got := s.result(terminal.completed())
	if got.kind != providerUsagePlausible || got.totalTokens != 13 {
		t.Fatalf("large unrelated delta event denied later valid usage: %+v", got)
	}
	if len(s.line) != maxSSEUsageLineBytes || len(s.data) != maxSSEUsageLineBytes {
		t.Fatal("SSE usage tracker can grow unbounded")
	}
	if next, _ := inspectUsageSSE("data: [DONE]\n\n", 1, defaultMaxTokens); next.kind != providerUsageMissing {
		t.Fatal("provider usage leaked from an earlier request")
	}
}

func TestStreamingReportedUsageHTTPMetricsAndLegacyUnmeasuredSemantics(t *testing.T) {
	for _, tc := range []struct {
		name, sse string
		kind      providerUsageKind
		done      bool
	}{
		{"accepted self report", "data: " + reportedStreamUsage + "\n\ndata: [DONE]\n\n", providerUsagePlausible, true},
		{"missing usage", "data: {\"choices\":[]}\n\ndata: [DONE]\n\n", providerUsageMissing, true},
		{"contradictory total", "data: {\"choices\":[],\"usage\":{\"prompt_tokens\":9,\"completion_tokens\":4,\"total_tokens\":12}}\n\ndata: [DONE]\n\n", providerUsageInconsistent, true},
		{"malformed usage", "data: {\"choices\":[],\"usage\":{\"prompt_tokens\":\"9\",\"completion_tokens\":4,\"total_tokens\":13}}\n\ndata: [DONE]\n\n", providerUsageMalformed, true},
		{"above validated cap", "data: {\"choices\":[],\"usage\":{\"prompt_tokens\":9,\"completion_tokens\":300,\"total_tokens\":309}}\n\ndata: [DONE]\n\n", providerUsageExceedsCap, true},
		{"missing terminal", "data: " + reportedStreamUsage + "\n\n", providerUsageUnavailable, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "text/event-stream")
				_, _ = io.WriteString(w, tc.sse)
			}))
			defer upstream.Close()
			handler := newGateway(t, upstream.URL)
			body := `{"model":"test-model","messages":[{"role":"user","content":"PRIVATE_USER_TEXT"}],"stream":true}`
			reply := request(t, handler, body, "application/json")
			if reply.Code != http.StatusOK || reply.Body.String() != tc.sse {
				t.Fatalf("telemetry modified upstream stream: status=%d body=%q", reply.Code, reply.Body)
			}
			s, raw := readUsageSnapshot(t, handler)
			inference, _ := readInferenceMetrics(t, handler)
			if s.SchemaVersion != 2 || s.ClassifiedTerminalAttemptsTotal != 1 ||
				inference.StreamDoneTotal != uint64(boolToInt(tc.done)) {
				t.Fatalf("stream metrics diverged: %+v outcome=%+v", s, inference)
			}
			assertUsagePartition(t, s)
			if tc.kind == providerUsagePlausible {
				if s.StreamProviderPlausibleTotal != 1 || s.StreamNotMeasuredTotal != 0 ||
					s.ProviderPromptTokensReportedTotal != 9 ||
					s.ProviderOutputTokensReportedTotal != 4 || s.ProviderAllTokensReportedTotal != 13 {
					t.Fatalf("missing plausible, explicitly untrusted usage: %+v", s)
				}
			} else {
				if s.ProviderAllTokensReportedTotal != 0 || s.StreamNotMeasuredTotal != 1 {
					t.Fatalf("invalid or missing stream usage was counted: %+v", s)
				}
				switch tc.kind {
				case providerUsageMissing:
					if s.StreamProviderMissingTotal != 1 {
						t.Fatalf("no missing stream usage: %+v", s)
					}
				case providerUsageMalformed:
					if s.StreamProviderMalformedTotal != 1 {
						t.Fatalf("no malformed stream usage: %+v", s)
					}
				case providerUsageInconsistent:
					if s.StreamProviderInconsistentTotal != 1 {
						t.Fatalf("no inconsistent stream usage: %+v", s)
					}
				case providerUsageExceedsCap:
					if s.StreamProviderAboveCapTotal != 1 {
						t.Fatalf("no above-cap stream usage: %+v", s)
					}
				case providerUsageUnavailable:
					if s.StreamIncompleteTotal != 1 {
						t.Fatalf("uncertified stream falsely measured: %+v", s)
					}
				}
			}
			for _, secret := range []string{"PRIVATE_USER_TEXT", "test-model", "project_id", "PRIVATE_MODEL_OUTPUT"} {
				if strings.Contains(raw, secret) {
					t.Fatalf("sensitive model/request field leaked into metrics: %q", secret)
				}
			}
		})
	}
}

func boolToInt(v bool) int {
	if v {
		return 1
	}
	return 0
}

func TestStreamingUsageConcurrentSnapshotsAreRaceSafe(t *testing.T) {
	var counters providerUsageCounters
	const workers = 16
	const perWorker = 50
	var wg sync.WaitGroup
	wg.Add(workers)
	for i := 0; i < workers; i++ {
		go func() {
			defer wg.Done()
			for j := 0; j < perWorker; j++ {
				counters.recordWithStreamResult(true, inferenceStreamCleanEOF, true,
					providerUsageObservation{kind: providerUsagePlausible, promptTokens: 9,
						completionTokens: 4, totalTokens: 13})
				if j%3 == 0 {
					_ = counters.snapshot()
				}
			}
		}()
	}
	wg.Wait()
	result := counters.snapshot()
	assertUsagePartition(t, result)
	if result.StreamProviderPlausibleTotal != workers*perWorker ||
		result.ClassifiedTerminalAttemptsTotal != workers*perWorker ||
		result.ProviderAllTokensReportedTotal != workers*perWorker*13 {
		t.Fatalf("concurrent usage counters inconsistent: %+v", result)
	}
}

func TestSSEUsageNeverBecomesBillableWhenWriterFails(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: "+reportedStreamUsage+"\n\ndata: [DONE]\n\n")
	}))
	defer upstream.Close()
	bridge, err := newInferenceBridge(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions",
		strings.NewReader(`{"model":"test","messages":[{"role":"user","content":"hello"}],"stream":true}`))
	req.Header.Set("Content-Type", "application/json")
	bridge.serveChat(&failsOnWrite{}, req)
	s := bridge.usage.snapshot()
	assertUsagePartition(t, s)
	if s.StreamProviderPlausibleTotal != 0 || s.StreamIncompleteTotal != 1 ||
		s.ProviderAllTokensReportedTotal != 0 {
		t.Fatalf("failed downstream write credited stream token usage: %+v", s)
	}
}

func TestSSEMalformedMetadataNeverPersisted(t *testing.T) {
	var tracker = newSSEProviderUsageTracker(defaultMaxTokens)
	for _, part := range bytes.Split([]byte("data: {\"choices\":[],\"usage\":{\"prompt_tokens\":1,\"completion_tokens\":null,\"total_tokens\":1},\"secret\":\"PRIVATE_DATA\"}\n\ndata: [DONE]\n\n"), []byte{}) {
		tracker.observe(part)
	}
	if result := tracker.result(true); result.kind != providerUsageMalformed {
		t.Fatalf("malformed model metadata became plausible: %+v", result)
	}
}
