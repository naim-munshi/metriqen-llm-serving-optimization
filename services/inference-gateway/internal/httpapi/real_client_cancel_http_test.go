package httpapi

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// Exercise a REAL HTTP/TCP client close against the Go gateway, not just a
// canceled httptest.NewRequest context. Both backend and gateway are
// ephemeral, loopback-only Go test servers with synthetic completions.
// This source-level result is not actual native Mac/Ollama acceptance.
func TestRealHTTPClientDisconnectReleasesGatewayAndUpstreamThenRecovers(t *testing.T) {
	for _, relayBuffer := range []string{"", "4096"} {
		name := "default_1024"
		if relayBuffer != "" {
			name = "optin_4096"
		}
		t.Run(name, func(t *testing.T) {
			t.Setenv("METRIQEN_SSE_RELAY_BUFFER_BYTES", relayBuffer)

			upstreamCanceled := make(chan struct{})
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Header.Get("Accept") == "text/event-stream" {
					w.Header().Set("Content-Type", "text/event-stream")
					_, _ = io.WriteString(w, "data: {\"choices\":[{\"delta\":{\"content\":\"first\"}}]}\n\n")
					w.(http.Flusher).Flush()
					// Do not terminate SSE itself. It must be the downstream
					// client's TCP close that cancels the upstream context.
					select {
					case <-r.Context().Done():
						close(upstreamCanceled)
					case <-time.After(5 * time.Second):
						t.Error("synthetic streaming upstream never received client cancellation")
					}
					return
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = io.WriteString(w, `{"model":"test-model","choices":[{"message":{"role":"assistant","content":"READY"}}]}`)
			}))
			defer upstream.Close()
			router := newGateway(t, upstream.URL)
			gateway := httptest.NewServer(router)
			defer gateway.Close()
			client := &http.Client{Timeout: 5 * time.Second}

			readMetrics := func(endpoint string, target any) {
				t.Helper()
				resp, err := client.Get(gateway.URL + endpoint)
				if err != nil {
					t.Fatal(err)
				}
				defer resp.Body.Close()
				if resp.StatusCode != http.StatusOK {
					t.Fatalf("%s returned HTTP %d", endpoint, resp.StatusCode)
				}
				if err := json.NewDecoder(resp.Body).Decode(target); err != nil {
					t.Fatal(err)
				}
			}
			var beforeInf inferenceOutcomeSnapshot
			var beforeAdm admissionMetrics
			readMetrics("/metrics/inference", &beforeInf)
			readMetrics("/metrics/admission", &beforeAdm)
			if beforeInf.AttemptsTotal != 0 || beforeAdm.ActiveRequests != 0 {
				t.Fatalf("ephemeral gateway not idle: %+v %+v", beforeInf, beforeAdm)
			}

			streamBody := `{"model":"test-model","messages":[{"role":"user","content":"synthetic"}],"stream":true,"max_tokens":32}`
			req, err := http.NewRequest(http.MethodPost, gateway.URL+"/v1/chat/completions", strings.NewReader(streamBody))
			if err != nil {
				t.Fatal(err)
			}
			req.Header.Set("Content-Type", "application/json")
			resp, err := client.Do(req)
			if err != nil {
				t.Fatal(err)
			}
			if resp.StatusCode != http.StatusOK || resp.Header.Get("Content-Type") != "text/event-stream" {
				resp.Body.Close()
				t.Fatalf("expected streaming 200, got HTTP %d", resp.StatusCode)
			}
			first, err := bufio.NewReader(resp.Body).ReadString('\n')
			if err != nil || !strings.Contains(first, "\"content\":\"first\"") {
				resp.Body.Close()
				t.Fatalf("missing first actual flushed SSE text delta: %v", err)
			}
			// Real downstream HTTP socket closed while upstream deliberately
			// remains open: this must reach gateway cancellation.
			if err := resp.Body.Close(); err != nil {
				t.Fatal(err)
			}
			select {
			case <-upstreamCanceled:
			case <-time.After(5 * time.Second):
				t.Fatal("Go upstream did not observe the real HTTP disconnect")
			}

			var afterInf inferenceOutcomeSnapshot
			var afterAdm admissionMetrics
			deadline := time.Now().Add(5 * time.Second)
			for {
				readMetrics("/metrics/inference", &afterInf)
				readMetrics("/metrics/admission", &afterAdm)
				if afterInf.ClientCanceledTotal == 1 && afterInf.ActiveAttempts == 0 &&
					afterAdm.ActiveRequests == 0 && afterAdm.WaitingRequests == 0 {
					break
				}
				if time.Now().After(deadline) {
					t.Fatalf("client cancellation/slot release not observed: %+v %+v", afterInf, afterAdm)
				}
				time.Sleep(10 * time.Millisecond)
			}
			if afterInf.SchemaVersion != 3 || afterAdm.SchemaVersion != 2 ||
				afterInf.AttemptsTotal != 1 || afterInf.DurationObservationsTotal != 1 ||
				afterInf.StreamDoneTotal != 0 || afterInf.StreamCleanEOFTotal != 0 ||
				afterInf.PreResponseFailureTotal != 0 || afterInf.PostResponseInterruptedTotal != 0 ||
				afterAdm.AdmittedImmediateTotal != 1 || afterAdm.AdmittedQueuedTotal != 0 ||
				afterAdm.RejectedFullTotal != 0 {
				t.Fatalf("canceled SSE was misclassified or leaked admission: %+v %+v", afterInf, afterAdm)
			}

			followup, err := http.NewRequest(http.MethodPost, gateway.URL+"/v1/chat/completions",
				strings.NewReader(`{"model":"test-model","messages":[{"role":"user","content":"synthetic"}],"stream":false,"max_tokens":16}`))
			if err != nil {
				t.Fatal(err)
			}
			followup.Header.Set("Content-Type", "application/json")
			recovered, err := client.Do(followup)
			if err != nil {
				t.Fatal(err)
			}
			defer recovered.Body.Close()
			var completion struct {
				Model   string `json:"model"`
				Choices []struct {
					Message struct {
						Content string `json:"content"`
					} `json:"message"`
				} `json:"choices"`
			}
			if recovered.StatusCode != http.StatusOK || json.NewDecoder(recovered.Body).Decode(&completion) != nil ||
				completion.Model != "test-model" || len(completion.Choices) != 1 ||
				completion.Choices[0].Message.Content != "READY" {
				t.Fatal("nonstream completion did not recover after TCP disconnect")
			}
			var finalInf inferenceOutcomeSnapshot
			var finalAdm admissionMetrics
			readMetrics("/metrics/inference", &finalInf)
			readMetrics("/metrics/admission", &finalAdm)
			if finalInf.AttemptsTotal != 2 || finalInf.ClientCanceledTotal != 1 ||
				finalInf.NonstreamCompleteTotal != 1 || finalInf.DurationObservationsTotal != 2 ||
				finalInf.ActiveAttempts != 0 || finalAdm.AdmittedImmediateTotal != 2 ||
				finalAdm.ActiveRequests != 0 || finalAdm.WaitingRequests != 0 {
				t.Fatalf("gateway did not account for exactly 1 canceled and 1 recovered request: %s", fmt.Sprint(finalInf, finalAdm))
			}
		})
	}
}
