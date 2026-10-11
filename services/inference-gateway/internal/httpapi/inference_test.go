package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

const validChat = `{"model":"test-model","messages":[{"role":"user","content":"hello"}]}`

func newGateway(t *testing.T, upstream string) http.Handler {
	t.Helper()
	handler, err := NewRouterWithInference(upstream)
	if err != nil {
		t.Fatal(err)
	}
	return handler
}

func request(t *testing.T, handler http.Handler, body, contentType string) *httptest.ResponseRecorder {
	t.Helper()
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	r.Header.Set("Content-Type", contentType)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, r)
	return w
}

func TestForwardsNonStreamingCompletionAndSanitizesHeaders(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			t.Errorf("unexpected upstream route: %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" || r.Header.Get("X-Upstream-Host") != "" {
			t.Error("untrusted client headers leaked to upstream")
		}
		if r.Header.Get("Accept") != "application/json" {
			t.Errorf("unexpected Accept: %s", r.Header.Get("Accept"))
		}
		var body map[string]json.RawMessage
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		if string(body["max_tokens"]) != "256" {
			t.Errorf("missing default token bound: %s", body["max_tokens"])
		}
		if _, ok := body["messages"]; !ok {
			t.Error("messages were not forwarded")
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"hello there"}}]}`)
	}))
	defer upstream.Close()

	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(validChat))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Authorization", "Bearer never-forward")
	r.Header.Set("Cookie", "session=sensitive")
	r.Header.Set("X-Upstream-Host", "malicious")
	w := httptest.NewRecorder()
	newGateway(t, upstream.URL).ServeHTTP(w, r)
	if w.Code != 200 || !strings.Contains(w.Body.String(), "hello there") {
		t.Fatalf("bad response: %d %s", w.Code, w.Body.String())
	}
	if w.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("inference responses must not be cached")
	}
}

func TestStreamingFlushesFirstEventBeforeCompletion(t *testing.T) {
	release := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Accept") != "text/event-stream" {
			t.Errorf("bad Accept: %s", r.Header.Get("Accept"))
		}
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = fmt.Fprint(w, "data: {\"token\":\"first\"}\n\n")
		w.(http.Flusher).Flush()
		select {
		case <-release:
		case <-r.Context().Done():
			return
		}
		_, _ = fmt.Fprint(w, "data: [DONE]\n\n")
		w.(http.Flusher).Flush()
	}))
	defer upstream.Close()
	defer close(release)

	gateway := httptest.NewServer(newGateway(t, upstream.URL))
	defer gateway.Close()
	client := &http.Client{Timeout: 3 * time.Second}
	r, _ := http.NewRequest(http.MethodPost, gateway.URL+"/v1/chat/completions", strings.NewReader(`{"model":"test-model","messages":[{"role":"user","content":"hi"}],"stream":true}`))
	r.Header.Set("Content-Type", "application/json")
	response, err := client.Do(r)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 200 || response.Header.Get("Content-Type") != "text/event-stream" {
		t.Fatalf("unexpected SSE response: %d %s", response.StatusCode, response.Header.Get("Content-Type"))
	}
	chunk := make([]byte, 64)
	n, err := response.Body.Read(chunk)
	if err != nil || !strings.Contains(string(chunk[:n]), "first") {
		t.Fatalf("first SSE token not flushed: %d %q, %v", n, chunk[:n], err)
	}
}

func TestInvalidRequestsRejectedWithoutCallingUpstream(t *testing.T) {
	calls := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.WriteHeader(200)
	}))
	defer upstream.Close()
	handler := newGateway(t, upstream.URL)
	tests := []struct {
		name, body, contentType string
		wantStatus              int
	}{
		{"invalid media type", validChat, "text/plain", 415},
		{"invalid JSON", `{`, "application/json", 400},
		{"no model", `{"messages":[{"role":"user","content":"hi"}]}`, "application/json", 400},
		{"empty messages", `{"model":"m","messages":[]}`, "application/json", 400},
		{"missing role", `{"model":"m","messages":[{"content":"hi"}]}`, "application/json", 400},
		{"missing content", `{"model":"m","messages":[{"role":"user"}]}`, "application/json", 400},
		{"not stream bool", `{"model":"m","messages":[{"role":"user","content":"hi"}],"stream":"yes"}`, "application/json", 400},
		{"too many tokens", `{"model":"m","messages":[{"role":"user","content":"hi"}],"max_tokens":4096}`, "application/json", 400},
		{"fractional tokens", `{"model":"m","messages":[{"role":"user","content":"hi"}],"max_tokens":1.5}`, "application/json", 400},
		{"oversized body", validChat + strings.Repeat(" ", maxRequestBytes), "application/json", 413},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			w := request(t, handler, tc.body, tc.contentType)
			if w.Code != tc.wantStatus {
				t.Fatalf("got %d, want %d: %s", w.Code, tc.wantStatus, w.Body.String())
			}
		})
	}
	if calls != 0 {
		t.Errorf("invalid requests reached inference upstream %d times", calls)
	}
}

func TestUpstreamErrorsAndRedirectsNeverLeakResponseBody(t *testing.T) {
	for _, tc := range []struct {
		upstreamStatus, expectedStatus int
	}{
		{400, 400}, {404, 404}, {429, 429}, {500, 502}, {302, 502},
	} {
		t.Run(fmt.Sprintf("%d", tc.upstreamStatus), func(t *testing.T) {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Location", "http://attacker.example.test/collect")
				w.WriteHeader(tc.upstreamStatus)
				_, _ = io.WriteString(w, "sensitive upstream internals")
			}))
			defer upstream.Close()
			w := request(t, newGateway(t, upstream.URL), validChat, "application/json")
			if w.Code != tc.expectedStatus || strings.Contains(w.Body.String(), "sensitive") {
				t.Fatalf("got %d, body %q", w.Code, w.Body.String())
			}
		})
	}
}

func TestProtocolMismatchRejected(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"content":"not SSE"}`)
	}))
	defer upstream.Close()
	w := request(t, newGateway(t, upstream.URL), `{"model":"m","messages":[{"role":"user","content":"hi"}],"stream":true}`, "application/json")
	if w.Code != 502 {
		t.Fatalf("expected upstream protocol error, got %d", w.Code)
	}
}

func TestUnreachableUpstreamReturns502(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	url := upstream.URL
	upstream.Close()
	w := request(t, newGateway(t, url), validChat, "application/json")
	if w.Code != 502 {
		t.Fatalf("got %d; want 502", w.Code)
	}
}

func TestConcurrencySaturationReturns429AndRecovers(t *testing.T) {
	entered := make(chan struct{}, maxConcurrent)
	release := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		entered <- struct{}{}
		select {
		case <-release:
		case <-r.Context().Done():
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[]}`)
	}))
	defer upstream.Close()
	handler := newGateway(t, upstream.URL)
	var workers sync.WaitGroup
	for i := 0; i < maxConcurrent; i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			w := request(t, handler, validChat, "application/json")
			if w.Code != 200 {
				t.Errorf("running request failed: %d", w.Code)
			}
		}()
	}
	for i := 0; i < maxConcurrent; i++ {
		select {
		case <-entered:
		case <-time.After(2 * time.Second):
			t.Fatal("upstream did not receive expected concurrency")
		}
	}
	rejected := request(t, handler, validChat, "application/json")
	if rejected.Code != 429 || rejected.Header().Get("Retry-After") != "1" {
		t.Fatalf("expected overload rejection, got %d", rejected.Code)
	}
	close(release)
	workers.Wait()
	if got := request(t, handler, validChat, "application/json"); got.Code != 200 {
		t.Fatalf("concurrency capacity was not released: %d", got.Code)
	}
}

func TestClientCancellationStopsUpstream(t *testing.T) {
	started := make(chan struct{})
	canceled := make(chan struct{})
	release := make(chan struct{})
	defer close(release)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = fmt.Fprint(w, "data: {\"token\":\"first\"}\n\n")
		w.(http.Flusher).Flush()
		close(started)
		select {
		case <-r.Context().Done():
			close(canceled)
		case <-release:
			return
		}
	}))
	defer upstream.Close()

	ctx, cancel := context.WithCancel(context.Background())
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"model":"test-model","messages":[{"role":"user","content":"hi"}],"stream":true}`)).WithContext(ctx)
	r.Header.Set("Content-Type", "application/json")
	handler := newGateway(t, upstream.URL)
	finished := make(chan struct{})
	go func() {
		handler.ServeHTTP(httptest.NewRecorder(), r)
		close(finished)
	}()
	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("upstream never started")
	}
	cancel()
	select {
	case <-canceled:
	case <-time.After(2 * time.Second):
		t.Fatal("upstream did not observe cancellation")
	}
	select {
	case <-finished:
	case <-time.After(2 * time.Second):
		t.Fatal("gateway did not release canceled request")
	}
}

func TestRejectsUnsafeUpstreamOrigins(t *testing.T) {
	for _, raw := range []string{
		"https://127.0.0.1:11434", "http://example.com:8080", "http://169.254.169.254:80",
		"http://127.0.0.1:11434/admin", "http://alice:secret@127.0.0.1:11434",
		"http://127.0.0.1:11434?target=evil", "http://127.0.0.1:11434/#fragment",
		"http://127.0.0.1", "not-a-url", "http://localhost:99999",
	} {
		if _, err := NewRouterWithInference(raw); err == nil {
			t.Errorf("unsafe configuration accepted: %q", raw)
		}
	}
	if _, err := NewRouterWithInference("http://localhost:11434"); err != nil {
		t.Errorf("expected local origin: %v", err)
	}
}
