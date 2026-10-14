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
	"testing"
	"time"
)

func readInferenceMetrics(t *testing.T, handler http.Handler) (inferenceOutcomeSnapshot, string) {
	t.Helper()
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/metrics/inference", nil))
	if w.Code != http.StatusOK || w.Header().Get("Cache-Control") != "no-store" ||
		w.Header().Get("Content-Type") != "application/json" {
		t.Fatalf("invalid inference metrics response: %d %+v", w.Code, w.Header())
	}
	var snapshot inferenceOutcomeSnapshot
	if err := json.Unmarshal(w.Body.Bytes(), &snapshot); err != nil {
		t.Fatal(err)
	}
	return snapshot, w.Body.String()
}

func assertOutcomeReconciled(t *testing.T, state inferenceOutcomeSnapshot) {
	t.Helper()
	terminal := state.NonstreamCompleteTotal + state.StreamCleanEOFTotal +
		state.PreResponseFailureTotal + state.PostResponseInterruptedTotal + state.ClientCanceledTotal
	if state.AttemptsTotal != state.ActiveAttempts+terminal {
		t.Fatalf("inference outcome count mismatch: %+v", state)
	}
}

func TestInferenceOutcomesRemainZeroForInvalidRejectedAndHealthRequests(t *testing.T) {
	handler, err := NewRouterWithInferenceAndScheduler("http://127.0.0.1:11434", "approved-model", "0")
	if err != nil {
		t.Fatal(err)
	}
	for _, body := range []string{
		"{not-json",
		validChat, // Disallowed model (403), no admitted attempt.
	} {
		r := request(t, handler, body, "application/json")
		if r.Code != http.StatusBadRequest && r.Code != http.StatusForbidden {
			t.Fatalf("unexpected rejected HTTP %d", r.Code)
		}
	}
	m, raw := readInferenceMetrics(t, handler)
	if m.SchemaVersion != 1 || m.AttemptsTotal != 0 || m.ActiveAttempts != 0 {
		t.Fatalf("rejected requests counted as admitted inference: %+v", m)
	}
	for _, private := range []string{"prompt", "model", "user", "token", "PRIVATE", "address", "127.0.0.1"} {
		if strings.Contains(strings.ToLower(raw), strings.ToLower(private)) {
			t.Fatalf("metrics contain unbounded or private label %q: %s", private, raw)
		}
	}
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/metrics/inference", nil))
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("metrics endpoint accepts writes: HTTP %d", w.Code)
	}
}

func TestInferenceOutcomesCountExactlyOneTerminalResult(t *testing.T) {
	for _, tc := range []struct {
		name, upstreamType, upstreamBody string
		upstreamStatus                   int
		stream                           bool
		expectedCode                      int
		nonstream, streamEOF, pre, post   uint64
	}{
		{"nonstream complete", "application/json", `{"choices":[{"message":{"role":"assistant","content":"ok"}}]}`, 200, false, 200, 1, 0, 0, 0},
		{"stream complete marker", "text/event-stream", "data: [DONE]\n\n", 200, true, 200, 0, 1, 0, 0},
		{"stream empty EOF not certified", "text/event-stream", "data: {\"incomplete\":true}\n\n", 200, true, 200, 0, 1, 0, 0},
		{"upstream rejected", "application/json", `{"secret":"PRIVATE_UPSTREAM"}`, 429, false, 429, 0, 0, 1, 0},
		{"upstream 503", "application/json", `{"secret":"PRIVATE_UPSTREAM"}`, 503, false, 502, 0, 0, 1, 0},
		{"invalid completion", "application/json", `{"choices":[]}`, 200, false, 502, 0, 0, 1, 0},
		{"wrong response type", "text/plain", "PRIVATE_UPSTREAM", 200, false, 502, 0, 0, 1, 0},
		{"oversized stream", "text/event-stream", strings.Repeat("x", maxStreamedBytes+64), 200, true, 200, 0, 0, 0, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", tc.upstreamType)
				w.WriteHeader(tc.upstreamStatus)
				_, _ = io.WriteString(w, tc.upstreamBody)
			}))
			defer upstream.Close()
			handler := newGateway(t, upstream.URL)
			body := validChat
			if tc.stream {
				body = `{"model":"test","messages":[{"role":"user","content":"fixture"}],"stream":true}`
			}
			result := request(t, handler, body, "application/json")
			if result.Code != tc.expectedCode {
				t.Fatalf("got HTTP %d, want %d", result.Code, tc.expectedCode)
			}
			m, raw := readInferenceMetrics(t, handler)
			if m.AttemptsTotal != 1 || m.ActiveAttempts != 0 ||
				m.NonstreamCompleteTotal != tc.nonstream || m.StreamCleanEOFTotal != tc.streamEOF ||
				m.PreResponseFailureTotal != tc.pre || m.PostResponseInterruptedTotal != tc.post ||
				m.ClientCanceledTotal != 0 {
				t.Fatalf("wrong result for %s: %+v", tc.name, m)
			}
			assertOutcomeReconciled(t, m)
			if strings.Contains(raw, "PRIVATE_UPSTREAM") {
				t.Fatal("raw upstream data leaked to outcome metrics")
			}
		})
	}
}

type failsOnWrite struct {
	httptest.ResponseRecorder
}

func (w *failsOnWrite) Write([]byte) (int, error) {
	return 0, errors.New("client write failed")
}

func TestInferenceOutcomesDistinguishDownstreamFailureAfterHeaders(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"ok"}}]}`)
	}))
	defer upstream.Close()
	bridge, err := newInferenceBridge(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(validChat))
	req.Header.Set("Content-Type", "application/json")
	w := &failsOnWrite{}
	bridge.serveChat(w, req)
	s := bridge.outcomes.snapshot()
	if s.AttemptsTotal != 1 || s.PostResponseInterruptedTotal != 1 || s.NonstreamCompleteTotal != 0 {
		t.Fatalf("failed downstream write counted as success: %+v", s)
	}
	assertOutcomeReconciled(t, s)
}

func TestInferenceOutcomesClientCancellation(t *testing.T) {
	started := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: {\"choices\":[]}\n\n")
		w.(http.Flusher).Flush()
		close(started)
		<-r.Context().Done()
	}))
	defer upstream.Close()
	bridge, err := newInferenceBridge(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(
		`{"model":"test","messages":[{"role":"user","content":"x"}],"stream":true}`)).WithContext(ctx)
	req.Header.Set("Content-Type", "application/json")
	finished := make(chan struct{})
	go func() {
		bridge.serveChat(httptest.NewRecorder(), req)
		close(finished)
	}()
	select {
	case <-started:
	case <-time.After(3 * time.Second):
		t.Fatal("upstream did not start stream")
	}
	cancel()
	select {
	case <-finished:
	case <-time.After(3 * time.Second):
		t.Fatal("cancellation did not release inference")
	}
	s := bridge.outcomes.snapshot()
	if s.AttemptsTotal != 1 || s.ClientCanceledTotal != 1 ||
		s.ActiveAttempts != 0 || s.StreamCleanEOFTotal != 0 {
		t.Fatalf("canceled inference classified as completed: %+v", s)
	}
	assertOutcomeReconciled(t, s)
}

func TestInferenceOutcomesConcurrentSnapshotAndTerminalInvariants(t *testing.T) {
	entered := make(chan struct{}, maxConcurrent)
	release := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		entered <- struct{}{}
		<-release
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"ok"}}]}`)
	}))
	defer upstream.Close()
	handler := newGateway(t, upstream.URL)
	var workers sync.WaitGroup
	workers.Add(maxConcurrent)
	for i := 0; i < maxConcurrent; i++ {
		go func() {
			defer workers.Done()
			resp := request(t, handler, validChat, "application/json")
			if resp.Code != http.StatusOK {
				t.Errorf("concurrent request failed: %d", resp.Code)
			}
		}()
	}
	for i := 0; i < maxConcurrent; i++ {
		select {
		case <-entered:
		case <-time.After(3 * time.Second):
			t.Fatal("could not saturate concurrency")
		}
	}
	busy, _ := readInferenceMetrics(t, handler)
	if busy.AttemptsTotal != maxConcurrent || busy.ActiveAttempts != maxConcurrent {
		t.Fatalf("active inference gauge incorrect: %+v", busy)
	}
	assertOutcomeReconciled(t, busy)
	if r := request(t, handler, validChat, "application/json"); r.Code != http.StatusTooManyRequests {
		t.Fatalf("admission rejection changed: HTTP %d", r.Code)
	}
	stillBusy, _ := readInferenceMetrics(t, handler)
	if stillBusy.AttemptsTotal != maxConcurrent || stillBusy.ActiveAttempts != maxConcurrent {
		t.Fatalf("admission 429 counted as inference attempt: %+v", stillBusy)
	}
	close(release)
	workers.Wait()
	done, _ := readInferenceMetrics(t, handler)
	if done.ActiveAttempts != 0 || done.NonstreamCompleteTotal != maxConcurrent {
		t.Fatalf("completed attempts not recorded: %+v", done)
	}
	assertOutcomeReconciled(t, done)
}

func TestInferenceOutcomesRecordCanceledPreHeadersWithoutUpstreamFailure(t *testing.T) {
	bridge, err := newInferenceBridge("http://127.0.0.1:11434")
	if err != nil {
		t.Fatal(err)
	}
	entered := make(chan struct{})
	bridge.client = &http.Client{Transport: fakeRoundTripper(func(req *http.Request) (*http.Response, error) {
		close(entered)
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
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("inference did not begin")
	}
	cancel()
	select {
	case <-finished:
	case <-time.After(3 * time.Second):
		t.Fatal("canceled attempt did not finish")
	}
	outcomes := bridge.outcomes.snapshot()
	if outcomes.ClientCanceledTotal != 1 || outcomes.PreResponseFailureTotal != 0 {
		t.Fatalf("client cancel counted as server failure: %+v", outcomes)
	}
	assertOutcomeReconciled(t, outcomes)
}
