package httpapi

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// Synthetic CPU-only input: no real model, network latency or GPU. The
// benchmark copies exactly the same SSE bytes and observes both trackers.
func syntheticRelaySSE() []byte {
	var body strings.Builder
	for i := 0; i < 64; i++ {
		body.WriteString("data: {\"choices\":[{\"delta\":{\"content\":\"")
		body.WriteString(strings.Repeat("synthetic-safe-token-", 10))
		body.WriteString("\"}}]}\n\n")
	}
	body.WriteString("data: " + reportedStreamUsage + "\n\n")
	body.WriteString("data: [DONE]\n\n")
	return []byte(body.String())
}

func syntheticRelayReadPass(input []byte, readSize int) (int, bool, providerUsageObservation, error) {
	buffer := make([]byte, readSize)
	reader := bytes.NewReader(input)
	var terminal sseTerminalTracker
	usage := newSSEProviderUsageTracker(defaultMaxTokens)
	reads := 0
	for {
		n, err := reader.Read(buffer)
		if n > 0 {
			reads++
			// Observe only the exact forwarded span; no prompt or user data stored.
			_, _ = io.Discard.Write(buffer[:n])
			terminal.observe(buffer[:n])
			usage.observe(buffer[:n])
		}
		if err == io.EOF {
			return reads, terminal.completed(), usage.result(terminal.completed()), nil
		}
		if err != nil {
			return reads, false, providerUsageObservation{}, err
		}
	}
}

func TestSSERelayCandidateOptInPreservesLegacyDefault(t *testing.T) {
	t.Setenv("METRIQEN_SSE_RELAY_BUFFER_BYTES", "")
	bridge, err := newInferenceBridge("http://127.0.0.1:11434")
	if err != nil || bridge.sseRelayReadBufferBytes != 1024 {
		t.Fatalf("default changed: %v, bridge=%+v", err, bridge)
	}
	for _, value := range []string{"4096", "1024", "0", "-1", "4096 ", "8192", "04", "bad"} {
		got, err := parseSSERelayReadBufferBytes(value)
		if value == "4096" {
			if err != nil || got != 4096 {
				t.Fatalf("candidate opt-in not accepted: value=%q got=%d err=%v", value, got, err)
			}
			continue
		}
		if err == nil {
			t.Fatalf("invalid or unreviewed opt-in accepted: %q => %d", value, got)
		}
	}
	t.Setenv("METRIQEN_SSE_RELAY_BUFFER_BYTES", "4096")
	bridge, err = newInferenceBridge("http://127.0.0.1:11434")
	if err != nil || bridge.sseRelayReadBufferBytes != 4096 {
		t.Fatalf("opt-in candidate not applied: %v, bridge=%+v", err, bridge)
	}
	t.Setenv("METRIQEN_SSE_RELAY_BUFFER_BYTES", "8192")
	if _, err := newInferenceBridge("http://127.0.0.1:11434"); err == nil {
		t.Fatal("invalid operator configuration must fail at startup")
	}
}

func TestStreamingRelayLargerReadChunksPreserveSSEAndReportedUsage(t *testing.T) {
	if candidateSSERelayReadBufferBytes != 4096 {
		t.Fatalf("expected 4 KiB bounded candidate chunk, got %d", candidateSSERelayReadBufferBytes)
	}
	payload := syntheticRelaySSE()
	smallReads, smallDone, smallUsage, err := syntheticRelayReadPass(payload, 1024)
	if err != nil || !smallDone || smallUsage.kind != providerUsagePlausible {
		t.Fatalf("bad baseline synthetic framing %v %+v", err, smallUsage)
	}
	for _, size := range []int{1, 3, 127, 1024, candidateSSERelayReadBufferBytes, 8192} {
		reads, done, usage, err := syntheticRelayReadPass(payload, size)
		if err != nil || !done || usage.kind != providerUsagePlausible ||
			usage.promptTokens != smallUsage.promptTokens ||
			usage.completionTokens != smallUsage.completionTokens ||
			usage.totalTokens != smallUsage.totalTokens {
			t.Fatalf("read size %d changed framing/usage: reads=%d done=%v %+v err=%v",
				size, reads, done, usage, err)
		}
		if size == candidateSSERelayReadBufferBytes && reads >= smallReads {
			t.Fatalf("larger candidate did not reduce synthetic buffered read cycles %d vs %d",
				reads, smallReads)
		}
	}
}

func TestStreamingRelayLargerReadBufferPreservesHTTPAndAccounting(t *testing.T) {
	t.Setenv("METRIQEN_SSE_RELAY_BUFFER_BYTES", "4096")
	payload := syntheticRelaySSE()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write(payload)
	}))
	defer upstream.Close()

	router := newGateway(t, upstream.URL)
	w := request(t, router,
		"{\"model\":\"test-model\",\"messages\":[{\"role\":\"user\",\"content\":\"synthetic\"}],\"stream\":true}",
		"application/json")
	if w.Code != http.StatusOK || !bytes.Equal(w.Body.Bytes(), payload) {
		t.Fatalf("relay changed buffered SSE payload: status=%d, length=%d, expected=%d",
			w.Code, w.Body.Len(), len(payload))
	}
	snapshot, _ := readUsageSnapshot(t, router)
	outcomes, _ := readInferenceMetrics(t, router)
	if outcomes.StreamDoneTotal != 1 || outcomes.StreamCleanEOFTotal != 1 ||
		snapshot.StreamProviderPlausibleTotal != 1 ||
		snapshot.ProviderOutputTokensReportedTotal != 4 {
		t.Fatalf("relay changed terminal/usage accounting: %+v, %+v", outcomes, snapshot)
	}
}

// BenchmarkSSEChunkedRelayParsing is a **synthetic CPU microbenchmark** of
// request stream copy + two telemetry observers, NOT model throughput/TTFT.
func BenchmarkSSEChunkedRelayParsing(b *testing.B) {
	payload := syntheticRelaySSE()
	for _, tc := range []struct {
		name     string
		readSize int
	}{
		{"baseline_1024", 1024},
		{"candidate_4096", candidateSSERelayReadBufferBytes},
	} {
		b.Run(tc.name, func(b *testing.B) {
			b.ReportAllocs()
			b.SetBytes(int64(len(payload)))
			for i := 0; i < b.N; i++ {
				_, done, usage, err := syntheticRelayReadPass(payload, tc.readSize)
				if err != nil || !done || usage.kind != providerUsagePlausible {
					b.Fatal("synthetic SSE parsing changed outcome")
				}
			}
		})
	}
}
