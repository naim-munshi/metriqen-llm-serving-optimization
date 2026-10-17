package httpapi

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

func TestProviderUsageParsingNeverTreatsMalformedOrAboveCapAsPlausible(t *testing.T) {
	tests := []struct {
		name, usage string
		kind        providerUsageKind
		prompt      uint64
		completion  uint64
		total       uint64
	}{
		{"missing", "", providerUsageMissing, 0, 0, 0},
		{"null", "null", providerUsageMalformed, 0, 0, 0},
		{"array", "[1,2,3]", providerUsageMalformed, 0, 0, 0},
		{"string", `"PRIVATE_TOKEN"`, providerUsageMalformed, 0, 0, 0},
		{"empty object", "{}", providerUsageMalformed, 0, 0, 0},
		{"missing total", `{"prompt_tokens":3,"completion_tokens":2}`, providerUsageMalformed, 0, 0, 0},
		{"missing prompt", `{"completion_tokens":2,"total_tokens":2}`, providerUsageMalformed, 0, 0, 0},
		{"string numbers", `{"prompt_tokens":"3","completion_tokens":2,"total_tokens":5}`, providerUsageMalformed, 0, 0, 0},
		{"floating point", `{"prompt_tokens":3.0,"completion_tokens":2,"total_tokens":5}`, providerUsageMalformed, 0, 0, 0},
		{"exponent", `{"prompt_tokens":3e0,"completion_tokens":2,"total_tokens":5}`, providerUsageMalformed, 0, 0, 0},
		{"null prompt count", `{"prompt_tokens":null,"completion_tokens":2,"total_tokens":2}`, providerUsageMalformed, 0, 0, 0},
		{"null completion count", `{"prompt_tokens":1,"completion_tokens":null,"total_tokens":1}`, providerUsageMalformed, 0, 0, 0},
		{"null total count", `{"prompt_tokens":1,"completion_tokens":2,"total_tokens":null}`, providerUsageMalformed, 0, 0, 0},
		{"negative", `{"prompt_tokens":-1,"completion_tokens":2,"total_tokens":1}`, providerUsageMalformed, 0, 0, 0},
		{"boolean", `{"prompt_tokens":true,"completion_tokens":2,"total_tokens":5}`, providerUsageMalformed, 0, 0, 0},
		{"overflow integer", `{"prompt_tokens":99999999999999999999999,"completion_tokens":2,"total_tokens":5}`, providerUsageMalformed, 0, 0, 0},
		{"total mismatch", `{"prompt_tokens":3,"completion_tokens":2,"total_tokens":100}`, providerUsageInconsistent, 0, 0, 0},
		{"implausible prompt", `{"prompt_tokens":1000001,"completion_tokens":2,"total_tokens":1000003}`, providerUsageInconsistent, 0, 0, 0},
		{"implausible completion", `{"prompt_tokens":3,"completion_tokens":2049,"total_tokens":2052}`, providerUsageInconsistent, 0, 0, 0},
		{"above requested cap", `{"prompt_tokens":3,"completion_tokens":257,"total_tokens":260}`, providerUsageExceedsCap, 0, 0, 0},
		{"zero counts allowed", `{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}`, providerUsagePlausible, 0, 0, 0},
		{"plausible counts", `{"prompt_tokens":17,"completion_tokens":7,"total_tokens":24}`, providerUsagePlausible, 17, 7, 24},
		{"extra usage metadata ignored", `{"prompt_tokens":17,"completion_tokens":7,"total_tokens":24,"completion_tokens_details":{"reasoning_tokens":9999},"private":"PRIVATE_TOKEN"}`, providerUsagePlausible, 17, 7, 24},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			usage := classifyProviderUsage(json.RawMessage(tc.usage), 256)
			if usage.kind != tc.kind || usage.promptTokens != tc.prompt ||
				usage.completionTokens != tc.completion || usage.totalTokens != tc.total {
				t.Fatalf("unexpected self-report: %+v expected kind=%d", usage, tc.kind)
			}
		})
	}
	if usage := classifyProviderUsage(json.RawMessage(`{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}`), 0); usage.kind != providerUsageMalformed {
		t.Fatalf("invalid internal cap accepted: %+v", usage)
	}
}

func readUsageSnapshot(t *testing.T, handler http.Handler) (providerUsageSnapshot, string) {
	t.Helper()
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/metrics/usage", nil))
	if w.Code != http.StatusOK || w.Header().Get("Cache-Control") != "no-store" ||
		w.Header().Get("Content-Type") != "application/json" {
		t.Fatalf("invalid usage metrics headers: status=%d headers=%v", w.Code, w.Header())
	}
	var metrics providerUsageSnapshot
	if err := json.Unmarshal(w.Body.Bytes(), &metrics); err != nil {
		t.Fatal(err)
	}
	return metrics, w.Body.String()
}

func assertUsagePartition(t *testing.T, s providerUsageSnapshot) {
	t.Helper()
	total := s.NonstreamProviderPlausibleTotal + s.NonstreamProviderMissingTotal +
		s.NonstreamProviderMalformedTotal + s.NonstreamProviderInconsistentTotal +
		s.NonstreamProviderAboveCapTotal + s.NonstreamNoCompletionTotal +
		s.StreamNotMeasuredTotal + s.StreamProviderPlausibleTotal
	if total != s.ClassifiedTerminalAttemptsTotal {
		t.Fatalf("nonexclusive /metrics/usage partition: %+v", s)
	}
	if s.StreamNotMeasuredTotal != s.StreamProviderMissingTotal+
		s.StreamProviderMalformedTotal+s.StreamProviderInconsistentTotal+
		s.StreamProviderAboveCapTotal+s.StreamIncompleteTotal {
		t.Fatalf("stream missing/invalid/incomplete partition incorrect: %+v", s)
	}
	if s.ProviderPromptTokensReportedTotal+s.ProviderOutputTokensReportedTotal != s.ProviderAllTokensReportedTotal {
		t.Fatalf("provider token totals violate reported sum: %+v", s)
	}
}

func TestProviderUsageMetricsClassifyNonstreamAfterSuccessfulWrite(t *testing.T) {
	const privateMarker = "PRIVATE_PROVIDER_CONTENT_SHOULD_NEVER_APPEAR"
	for _, tc := range []struct {
		name, usage string
		wantKind    providerUsageKind
	}{
		{"consistent", `{"prompt_tokens":6,"completion_tokens":2,"total_tokens":8}`, providerUsagePlausible},
		{"omitted", "", providerUsageMissing},
		{"malformed", `{"prompt_tokens":"6","completion_tokens":2,"total_tokens":8}`, providerUsageMalformed},
		{"inconsistent", `{"prompt_tokens":6,"completion_tokens":2,"total_tokens":7}`, providerUsageInconsistent},
		{"above cap", `{"prompt_tokens":6,"completion_tokens":257,"total_tokens":263}`, providerUsageExceedsCap},
	} {
		t.Run(tc.name, func(t *testing.T) {
			usageField := ""
			if tc.usage != "" {
				usageField = `,"usage":` + tc.usage
			}
			responseBody := `{"choices":[{"message":{"content":"` + privateMarker +
				`"}}]` + usageField + `}`
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				_, _ = io.WriteString(w, responseBody)
			}))
			defer upstream.Close()
			gateway := newGateway(t, upstream.URL)
			idle, _ := readUsageSnapshot(t, gateway)
			if idle.SchemaVersion != 2 || idle.ClassifiedTerminalAttemptsTotal != 0 {
				t.Fatalf("unexpected idle usage snapshot: %+v", idle)
			}
			reply := request(t, gateway, validChat, "application/json")
			if reply.Code != 200 || !strings.Contains(reply.Body.String(), privateMarker) {
				t.Fatalf("telemetry modified valid inference response: %d", reply.Code)
			}
			usage, raw := readUsageSnapshot(t, gateway)
			if usage.ClassifiedTerminalAttemptsTotal != 1 {
				t.Fatalf("missing observation for completed inference: %+v", usage)
			}
			assertUsagePartition(t, usage)
			switch tc.wantKind {
			case providerUsagePlausible:
				if usage.NonstreamProviderPlausibleTotal != 1 ||
					usage.ProviderPromptTokensReportedTotal != 6 ||
					usage.ProviderOutputTokensReportedTotal != 2 ||
					usage.ProviderAllTokensReportedTotal != 8 {
					t.Fatalf("valid self-reported counts lost: %+v", usage)
				}
			case providerUsageMissing:
				if usage.NonstreamProviderMissingTotal != 1 {
					t.Fatalf("missing report not classified: %+v", usage)
				}
			case providerUsageMalformed:
				if usage.NonstreamProviderMalformedTotal != 1 {
					t.Fatalf("invalid report wrongly accepted: %+v", usage)
				}
			case providerUsageInconsistent:
				if usage.NonstreamProviderInconsistentTotal != 1 {
					t.Fatalf("incorrect total wrongly accepted: %+v", usage)
				}
			case providerUsageExceedsCap:
				if usage.NonstreamProviderAboveCapTotal != 1 {
					t.Fatalf("over-cap report wrongly accepted: %+v", usage)
				}
			}
			if tc.wantKind != providerUsagePlausible && (usage.ProviderPromptTokensReportedTotal != 0 ||
				usage.ProviderOutputTokensReportedTotal != 0 ||
				usage.ProviderAllTokensReportedTotal != 0) {
				t.Fatalf("unreliable upstream claims contaminated provider totals: %+v", usage)
			}
			for _, forbidden := range []string{privateMarker, "test-model", "PRIVATE_TOKEN", "project_id"} {
				if strings.Contains(raw, forbidden) {
					t.Fatalf("high-cardinality/private field leaked in metrics: %q", forbidden)
				}
			}
		})
	}
}

func TestProviderUsageNoFalseSuccessForStreamFailureOrWriterError(t *testing.T) {
	t.Run("upstream error", func(t *testing.T) {
		upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = io.WriteString(w, "PRIVATE_UPSTREAM_ERROR")
		}))
		defer upstream.Close()
		handler := newGateway(t, upstream.URL)
		w := request(t, handler, validChat, "application/json")
		if w.Code == 200 {
			t.Fatal("upstream failure considered successful completion")
		}
		s, raw := readUsageSnapshot(t, handler)
		assertUsagePartition(t, s)
		if s.NonstreamNoCompletionTotal != 1 || s.NonstreamProviderPlausibleTotal != 0 ||
			strings.Contains(raw, "PRIVATE_UPSTREAM_ERROR") {
			t.Fatalf("pre-response failure counted as usage: %+v", s)
		}
	})

	t.Run("downstream writer failed", func(t *testing.T) {
		upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"PRIVATE"}}],"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}`)
		}))
		defer upstream.Close()
		bridge, err := newInferenceBridge(upstream.URL)
		if err != nil {
			t.Fatal(err)
		}
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(validChat))
		req.Header.Set("Content-Type", "application/json")
		bridge.serveChat(&failsOnWrite{}, req)
		s := bridge.usage.snapshot()
		assertUsagePartition(t, s)
		if s.NonstreamNoCompletionTotal != 1 || s.ProviderAllTokensReportedTotal != 0 {
			t.Fatalf("downstream failure wrongly counted as measured usage: %+v", s)
		}
	})

	t.Run("stream with fake usage", func(t *testing.T) {
		upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = io.WriteString(w, "data: {\"usage\":{\"prompt_tokens\":1,\"completion_tokens\":1,\"total_tokens\":2}}\n\ndata: [DONE]\n\n")
		}))
		defer upstream.Close()
		handler := newGateway(t, upstream.URL)
		stream := `{"model":"test-model","messages":[{"role":"user","content":"hello"}],"stream":true}`
		w := request(t, handler, stream, "application/json")
		if w.Code != http.StatusOK {
			t.Fatalf("unexpected streaming HTTP %d", w.Code)
		}
		s, raw := readUsageSnapshot(t, handler)
		assertUsagePartition(t, s)
		if s.StreamNotMeasuredTotal != 1 || s.ProviderAllTokensReportedTotal != 0 ||
			strings.Contains(raw, "test-model") {
			t.Fatalf("stream claimed unverified usage: %+v", s)
		}
	})
}

func TestProviderUsageCountersConcurrentSnapshotsAreRaceSafe(t *testing.T) {
	var counters providerUsageCounters
	const workers = 12
	const perWorker = 200
	var wg sync.WaitGroup
	wg.Add(workers)
	for i := 0; i < workers; i++ {
		go func() {
			defer wg.Done()
			for j := 0; j < perWorker; j++ {
				counters.record(false, inferenceNonstreamComplete, providerUsageObservation{
					kind: providerUsagePlausible, promptTokens: 3, completionTokens: 2, totalTokens: 5,
				})
				if j%3 == 0 {
					assert := counters.snapshot()
					if assert.ProviderAllTokensReportedTotal > assert.ClassifiedTerminalAttemptsTotal*5 {
						t.Error("provider usage exceeded observed samples")
					}
				}
			}
		}()
	}
	wg.Wait()
	s := counters.snapshot()
	assertUsagePartition(t, s)
	if s.ClassifiedTerminalAttemptsTotal != workers*perWorker ||
		s.NonstreamProviderPlausibleTotal != workers*perWorker ||
		s.ProviderAllTokensReportedTotal != workers*perWorker*5 {
		t.Fatalf("wrong concurrency totals: %+v", s)
	}
}
