package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"time"
	"syscall"
	"testing"
)

type fakeRoundTripper func(*http.Request) (*http.Response, error)

func (f fakeRoundTripper) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func upstreamSnapshotFromHandler(t *testing.T, handler http.Handler) (upstreamFailureSnapshot, string) {
	t.Helper()
	resp := httptest.NewRecorder()
	handler.ServeHTTP(resp, httptest.NewRequest(http.MethodGet, "/metrics/upstream", nil))
	if resp.Code != http.StatusOK || resp.Header().Get("Content-Type") != "application/json" ||
		resp.Header().Get("Cache-Control") != "no-store" {
		t.Fatalf("invalid upstream metrics route: HTTP %d, headers=%v", resp.Code, resp.Header())
	}
	var result upstreamFailureSnapshot
	if err := json.Unmarshal(resp.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	return result, resp.Body.String()
}

func TestUpstreamMetricsReadOnlyAndNoPrivateAttributes(t *testing.T) {
	handler, err := NewRouterWithInferenceAndScheduler("", "", "2")
	if err != nil {
		t.Fatal(err)
	}
	initial, body := upstreamSnapshotFromHandler(t, handler)
	if initial.SchemaVersion != 1 || initial.ConnectionRefusedTotal != 0 ||
		initial.TransportErrorsTotal != 0 || initial.TimeoutsTotal != 0 {
		t.Fatalf("nonzero initial upstream counters: %+v", initial)
	}
	for _, forbidden := range []string{"prompt", "message", "model", "user", "address", "url", "authorization", "raw_error"} {
		if strings.Contains(strings.ToLower(body), forbidden) {
			t.Fatalf("metrics contain private attribute %q: %s", forbidden, body)
		}
	}
	resp := httptest.NewRecorder()
	handler.ServeHTTP(resp, httptest.NewRequest(http.MethodPost, "/metrics/upstream", nil))
	if resp.Code != http.StatusMethodNotAllowed {
		t.Fatalf("metrics must not accept POST: %d", resp.Code)
	}
}

func TestUpstreamTransportErrorsUseTypedClassification(t *testing.T) {
	for _, tc := range []struct {
		name string
		failure error
		refused, transport, timeout uint64
	}{
		{"connection refused", syscall.ECONNREFUSED, 1, 0, 0},
		{"deadline", context.DeadlineExceeded, 0, 0, 1},
		{"other", errors.New("private upstream details must not escape"), 0, 1, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			bridge, err := newInferenceBridge("http://127.0.0.1:11434")
			if err != nil {
				t.Fatal(err)
			}
			bridge.client = &http.Client{Transport: fakeRoundTripper(func(*http.Request) (*http.Response, error) {
				return nil, tc.failure
			})}
			got := request(t, http.HandlerFunc(bridge.serveChat), validChat, "application/json")
			if got.Code != http.StatusBadGateway || strings.Contains(got.Body.String(), "private") {
				t.Fatalf("unsafe transport error: %d %q", got.Code, got.Body.String())
			}
			s := bridge.upstreamFailures.snapshot()
			if s.ConnectionRefusedTotal != tc.refused || s.TransportErrorsTotal != tc.transport ||
				s.TimeoutsTotal != tc.timeout {
				t.Fatalf("misclassified transport: %+v", s)
			}
		})
	}
}

func TestUpstreamStatusProtocolAndResponseSizeCounters(t *testing.T) {
	for _, tc := range []struct {
		name string
		status int
		contentType, body string
		rejected, other, protocol, tooLarge uint64
	}{
		{"rejected", 429, "text/plain", "private rejection", 1, 0, 0, 0},
		{"unavailable", 503, "text/plain", "private error", 0, 1, 0, 0},
		{"redirect", 302, "text/plain", "private location", 0, 1, 0, 0},
		{"bad content type", 200, "text/plain", "private", 0, 0, 1, 0},
		{"invalid json", 200, "application/json", "{private", 0, 0, 1, 0},
		{"oversized", 200, "application/json", strings.Repeat("x", maxNonStreamingResponseBytes+1), 0, 0, 0, 1},
		{"valid", 200, "application/json", "{\"choices\":[{\"message\":{\"content\":\"ok\"}}]}", 0, 0, 0, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				w.Header().Set("Content-Type", tc.contentType)
				if tc.status == 302 {
					w.Header().Set("Location", "http://unsafe.example.invalid/secret")
				}
				w.WriteHeader(tc.status)
				_, _ = io.WriteString(w, tc.body)
			}))
			defer upstream.Close()
			handler := newGateway(t, upstream.URL)
			res := request(t, handler, validChat, "application/json")
			if strings.Contains(strings.ToLower(res.Body.String()), "private") ||
				strings.Contains(res.Body.String(), "unsafe.example.invalid") {
				t.Fatalf("upstream leaked to client: %q", res.Body.String())
			}
			s, raw := upstreamSnapshotFromHandler(t, handler)
			if s.HTTPRejectedTotal != tc.rejected || s.HTTPOtherStatusTotal != tc.other ||
				s.ProtocolErrorsTotal != tc.protocol || s.ResponseTooLargeTotal != tc.tooLarge ||
				s.ConnectionRefusedTotal != 0 || s.TransportErrorsTotal != 0 || s.TimeoutsTotal != 0 {
				t.Fatalf("wrong metrics for %s: %+v", tc.name, s)
			}
			if strings.Contains(strings.ToLower(raw), "private") {
				t.Fatalf("upstream details leaked to metrics: %q", raw)
			}
		})
	}
}

func TestCanceledUpstreamAttemptIsNotRecordedAsFailure(t *testing.T) {
	bridge, err := newInferenceBridge("http://127.0.0.1:11434")
	if err != nil {
		t.Fatal(err)
	}
	started := make(chan struct{})
	bridge.client = &http.Client{Transport: fakeRoundTripper(func(req *http.Request) (*http.Response, error) {
		close(started)
		<-req.Context().Done()
		return nil, req.Context().Err()
	})}
	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(validChat)).WithContext(ctx)
	req.Header.Set("Content-Type", "application/json")
	finished := make(chan struct{})
	go func() {
		bridge.serveChat(httptest.NewRecorder(), req)
		close(finished)
	}()
	select {
	case <-started:
	case <-time.After(3 * time.Second):
		t.Fatal("mock transport did not start")
	}
	cancel()
	select {
	case <-finished:
	case <-time.After(3 * time.Second):
		t.Fatal("canceling upstream did not finish")
	}
	s := bridge.upstreamFailures.snapshot()
	if s.TransportErrorsTotal != 0 || s.TimeoutsTotal != 0 || s.ConnectionRefusedTotal != 0 {
		t.Fatalf("client canceled request counted as upstream failure: %+v", s)
	}
}

type readErrorReader struct{}

func (readErrorReader) Read([]byte) (int, error) {
	return 0, errors.New("private upstream streaming error")
}

func TestUpstreamStreamingFailureCounters(t *testing.T) {
	for _, tc := range []struct {
		name string
		body io.ReadCloser
		wantReadErrors, wantSizeLimit uint64
	}{
		{"read failure", io.NopCloser(readErrorReader{}), 1, 0},
		{"size limit", io.NopCloser(strings.NewReader(strings.Repeat("x", maxStreamedBytes+1024))), 0, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			bridge, err := newInferenceBridge("http://127.0.0.1:11434")
			if err != nil {
				t.Fatal(err)
			}
			bridge.client = &http.Client{Transport: fakeRoundTripper(func(*http.Request) (*http.Response, error) {
				return &http.Response{
					StatusCode: http.StatusOK,
					Header: http.Header{"Content-Type": []string{"text/event-stream"}},
					Body: tc.body,
				}, nil
			})}
			res := request(t, http.HandlerFunc(bridge.serveChat),
				"{\"model\":\"test\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"stream\":true}",
				"application/json")
			if res.Code != http.StatusOK {
				t.Fatalf("stream should already have HTTP 200: %d", res.Code)
			}
			s := bridge.upstreamFailures.snapshot()
			if s.StreamReadErrorsTotal != tc.wantReadErrors || s.StreamSizeLimitTotal != tc.wantSizeLimit {
				t.Fatalf("stream failure not captured: %+v", s)
			}
		})
	}
}

func TestUpstreamMetricsConcurrentRecording(t *testing.T) {
	m := &upstreamFailureCounters{}
	const n = 100
	var wg sync.WaitGroup
	for _, kind := range []upstreamFailureKind{upstreamConnectionRefused, upstreamTransportError, upstreamTimeout} {
		for i := 0; i < n; i++ {
			wg.Add(1)
			go func(k upstreamFailureKind) {
				defer wg.Done()
				m.record(k)
				_ = m.snapshot()
			}(kind)
		}
	}
	wg.Wait()
	s := m.snapshot()
	if s.ConnectionRefusedTotal != n || s.TransportErrorsTotal != n || s.TimeoutsTotal != n {
		t.Fatalf("concurrent increments were lost: %+v", s)
	}
}
