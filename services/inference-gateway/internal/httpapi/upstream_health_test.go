package httpapi

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func probeHealth(t *testing.T, handler http.Handler) (int, upstreamHealth, string) {
	t.Helper()
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/health/upstream", nil))
	if w.Header().Get("Cache-Control") != "no-store" ||
		w.Header().Get("X-Content-Type-Options") != "nosniff" ||
		w.Header().Get("Content-Type") != "application/json" {
		t.Fatalf("insecure probe headers: %+v", w.Header())
	}
	var body upstreamHealth
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	return w.Code, body, w.Body.String()
}

func TestUpstreamHealthProbeSeparatesReachabilityFromGatewayReadiness(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Method != http.MethodGet || r.URL.Path != "/api/version" ||
			r.URL.RawQuery != "" || r.Header.Get("Authorization") != "" ||
			r.Header.Get("Cookie") != "" {
			t.Errorf("unexpected upstream probe: %s %s", r.Method, r.URL.String())
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"version":"0.30.8"}`)
	}))
	defer upstream.Close()

	handler, err := NewRouterWithInferenceAndScheduler(upstream.URL, "", "2")
	if err != nil {
		t.Fatal(err)
	}
	status, body, raw := probeHealth(t, handler)
	if status != http.StatusOK || body.Status != "reachable" ||
		body.Probe != "ollama_api_version" || calls.Load() != 1 {
		t.Fatalf("unexpected health check: HTTP %d %+v", status, body)
	}
	if strings.Contains(raw, "0.30.8") || strings.Contains(raw, upstream.URL) {
		t.Fatal("upstream details leaked in health response")
	}

	// Health probes never reserve inference admission capacity and do
	// not alter failure metrics, even when actively checking the API.
	snapshot, _ := readAdmissionMetrics(t, handler)
	if snapshot.ActiveRequests != 0 || snapshot.WaitingRequests != 0 ||
		snapshot.AdmittedImmediateTotal != 0 {
		t.Fatalf("health probe occupied scheduler capacity: %+v", snapshot)
	}
	failures, _ := upstreamSnapshotFromHandler(t, handler)
	if failures.ConnectionRefusedTotal != 0 || failures.TransportErrorsTotal != 0 ||
		failures.HTTPRejectedTotal != 0 || failures.ProtocolErrorsTotal != 0 {
		t.Fatalf("health probe unexpectedly counted as inference: %+v", failures)
	}

	ready := httptest.NewRecorder()
	handler.ServeHTTP(ready, httptest.NewRequest(http.MethodGet, "/health/ready", nil))
	if ready.Code != http.StatusOK {
		t.Fatalf("existing process readiness contract changed: %d", ready.Code)
	}
}

func TestUpstreamHealthFailsClosedForInvalidResponses(t *testing.T) {
	for _, tc := range []struct {
		name string
		status int
		contentType string
		body string
	}{
		{"http error", 503, "application/json", `{"version":"secret"}`},
		{"html", 200, "text/html", "<b>private</b>"},
		{"malformed", 200, "application/json", "private invalid JSON"},
		{"missing version", 200, "application/json", "{}"},
		{"empty version", 200, "application/json", `{"version":"  "}`},
		{"oversized", 200, "application/json", strings.Repeat("x", maxHealthResponseBytes+1)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", tc.contentType)
				w.WriteHeader(tc.status)
				_, _ = io.WriteString(w, tc.body)
			}))
			defer upstream.Close()
			handler := newGateway(t, upstream.URL)
			status, result, raw := probeHealth(t, handler)
			if status != http.StatusServiceUnavailable || result.Status != "unreachable" ||
				strings.Contains(raw, "private") || strings.Contains(raw, "secret") {
				t.Fatalf("invalid health response: HTTP %d %+v", status, result)
			}
		})
	}
}

func TestUpstreamHealthDoesNotFollowRedirects(t *testing.T) {
	var followed atomic.Bool
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		followed.Store(true)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"version":"not ollama"}`)
	}))
	defer target.Close()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Redirect(w, &http.Request{}, target.URL, http.StatusFound)
	}))
	defer upstream.Close()
	status, result, _ := probeHealth(t, newGateway(t, upstream.URL))
	if status != http.StatusServiceUnavailable || result.Status != "unreachable" || followed.Load() {
		t.Fatalf("redirect bypassed failure-closed probe: HTTP %d %+v", status, result)
	}
}

func TestUpstreamHealthDisabledAndUnreachable(t *testing.T) {
	disabled, err := NewRouterWithInference("")
	if err != nil {
		t.Fatal(err)
	}
	status, result, _ := probeHealth(t, disabled)
	if status != http.StatusServiceUnavailable || result.Status != "disabled" {
		t.Fatalf("disabled upstream misreported: HTTP %d %+v", status, result)
	}

	upstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	address := upstream.URL
	upstream.Close()
	status, result, _ = probeHealth(t, newGateway(t, address))
	if status != http.StatusServiceUnavailable || result.Status != "unreachable" {
		t.Fatalf("connection refusal misreported: HTTP %d %+v", status, result)
	}
}

type blockingHealthTransport struct{}

func (blockingHealthTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	<-req.Context().Done()
	return nil, req.Context().Err()
}

func TestUpstreamHealthProbeHasBoundedDeadline(t *testing.T) {
	bridge, err := newInferenceBridge("http://127.0.0.1:11434")
	if err != nil {
		t.Fatal(err)
	}
	bridge.client = &http.Client{Transport: blockingHealthTransport{}}
	started := time.Now()
	w := httptest.NewRecorder()
	bridge.serveUpstreamHealth(w, httptest.NewRequest(http.MethodGet, "/health/upstream", nil))
	if time.Since(started) < upstreamHealthTimeout ||
		time.Since(started) > upstreamHealthTimeout+3*time.Second ||
		w.Code != http.StatusServiceUnavailable {
		t.Fatalf("timeout violated: elapsed=%s HTTP %d", time.Since(started), w.Code)
	}
}

func TestUpstreamHealthIsReadOnly(t *testing.T) {
	handler, err := NewRouterWithInference("")
	if err != nil {
		t.Fatal(err)
	}
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/health/upstream", nil))
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("health probe must reject POST, got %d", w.Code)
	}
}

// Explicitly exercise canceled caller context, not a background probe.
func TestUpstreamHealthRespectsCanceledRequest(t *testing.T) {
	bridge, err := newInferenceBridge("http://127.0.0.1:11434")
	if err != nil {
		t.Fatal(err)
	}
	bridge.client = &http.Client{Transport: blockingHealthTransport{}}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	req := httptest.NewRequest(http.MethodGet, "/health/upstream", nil).WithContext(ctx)
	started := time.Now()
	w := httptest.NewRecorder()
	bridge.serveUpstreamHealth(w, req)
	if time.Since(started) > time.Second || w.Code != http.StatusServiceUnavailable {
		t.Fatalf("canceled probe was not bounded: %d", w.Code)
	}
}
