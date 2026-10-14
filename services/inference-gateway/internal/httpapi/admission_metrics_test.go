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

func TestAdmissionMetricsTrackImmediateQueueAndRejections(t *testing.T) {
	s := newAdmissionScheduler(1, 1)
	initial := s.snapshot()
	if initial.SchemaVersion != 1 || initial.MaxActiveRequests != 1 || initial.MaxWaitingRequests != 1 {
		t.Fatalf("invalid initial snapshot: %+v", initial)
	}
	if err := s.acquire(context.Background()); err != nil {
		t.Fatal(err)
	}
	first := s.snapshot()
	if first.ActiveRequests != 1 || first.AdmittedImmediateTotal != 1 || first.WaitingRequests != 0 {
		t.Fatalf("immediate admission not counted: %+v", first)
	}

	queued := make(chan error, 1)
	go func() { queued <- s.acquire(context.Background()) }()
	waitForQueue(t, s, 1)
	if err := s.acquire(context.Background()); !errors.Is(err, errAdmissionFull) {
		t.Fatalf("expected full queue, got %v", err)
	}
	blocked := s.snapshot()
	if blocked.ActiveRequests != 1 || blocked.WaitingRequests != 1 ||
		blocked.AdmittedImmediateTotal != 1 || blocked.RejectedFullTotal != 1 ||
		blocked.AdmittedQueuedTotal != 0 {
		t.Fatalf("incorrect queued snapshot: %+v", blocked)
	}

	s.release()
	select {
	case err := <-queued:
		if err != nil {
			t.Fatalf("queued admission failed: %v", err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("queue handoff did not complete")
	}
	granted := s.snapshot()
	if granted.ActiveRequests != 1 || granted.WaitingRequests != 0 ||
		granted.AdmittedQueuedTotal != 1 {
		t.Fatalf("handoff not counted: %+v", granted)
	}
	s.release()
	final := s.snapshot()
	if final.ActiveRequests != 0 || final.WaitingRequests != 0 ||
		final.AdmittedImmediateTotal != 1 || final.AdmittedQueuedTotal != 1 ||
		final.RejectedFullTotal != 1 {
		t.Fatalf("admission counters or gauges leaked: %+v", final)
	}
}

func TestAdmissionMetricsTrackTimeoutAndCancellation(t *testing.T) {
	s := newAdmissionScheduler(1, 2)
	if err := s.acquire(context.Background()); err != nil {
		t.Fatal(err)
	}
	timeout, stop := context.WithTimeout(context.Background(), 25*time.Millisecond)
	defer stop()
	if err := s.acquire(timeout); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected deadline exceeded, got %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- s.acquire(ctx) }()
	waitForQueue(t, s, 1)
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("expected canceled waiter, got %v", err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("canceled waiter remained blocked")
	}
	snapshot := s.snapshot()
	if snapshot.ActiveRequests != 1 || snapshot.WaitingRequests != 0 ||
		snapshot.QueueTimeoutTotal != 1 || snapshot.QueueCanceledTotal != 1 ||
		snapshot.AdmittedQueuedTotal != 0 {
		t.Fatalf("failed waiters counted incorrectly: %+v", snapshot)
	}
	s.release()
}

func readAdmissionMetrics(t *testing.T, handler http.Handler) (admissionMetrics, *httptest.ResponseRecorder) {
	t.Helper()
	resp := httptest.NewRecorder()
	handler.ServeHTTP(resp, httptest.NewRequest(http.MethodGet, "/metrics/admission", nil))
	if resp.Code != http.StatusOK || resp.Header().Get("Cache-Control") != "no-store" ||
		resp.Header().Get("Content-Type") != "application/json" {
		t.Fatalf("invalid metrics HTTP contract: %d %+v", resp.Code, resp.Header())
	}
	var snapshot admissionMetrics
	if err := json.Unmarshal(resp.Body.Bytes(), &snapshot); err != nil {
		t.Fatal(err)
	}
	return snapshot, resp
}

func TestAdmissionMetricsRouteIsBoundedAndReadOnly(t *testing.T) {
	handler, err := NewRouterWithInferenceAndScheduler("", "", "2")
	if err != nil {
		t.Fatal(err)
	}
	snapshot, response := readAdmissionMetrics(t, handler)
	if snapshot.SchemaVersion != 1 || snapshot.MaxActiveRequests != maxConcurrent ||
		snapshot.MaxWaitingRequests != 2 || snapshot.ActiveRequests != 0 ||
		snapshot.WaitingRequests != 0 {
		t.Fatalf("unexpected initial metrics: %+v", snapshot)
	}
	for _, unexpected := range []string{"prompt", "model", "message", "user_id", "authorization"} {
		if strings.Contains(strings.ToLower(response.Body.String()), unexpected) {
			t.Fatalf("private attribute leaked through metrics: %q", unexpected)
		}
	}
	post := httptest.NewRecorder()
	handler.ServeHTTP(post, httptest.NewRequest(http.MethodPost, "/metrics/admission", nil))
	if post.Code != http.StatusMethodNotAllowed {
		t.Fatalf("metrics must not accept POST: %d", post.Code)
	}
}

func TestAdmissionMetricsObserveRealGatewayBackpressure(t *testing.T) {
	started := make(chan struct{}, maxConcurrent+1)
	release := make(chan struct{})
	var releaseOnce sync.Once
	unblock := func() { releaseOnce.Do(func() { close(release) }) }
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started <- struct{}{}
		select {
		case <-release:
		case <-r.Context().Done():
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"ok"}}]}`)
	}))
	defer upstream.Close()
	defer unblock()

	handler, err := NewRouterWithInferenceAndScheduler(upstream.URL, "", "1")
	if err != nil {
		t.Fatal(err)
	}
	completed := make(chan int, maxConcurrent+1)
	for i := 0; i < maxConcurrent; i++ {
		go func() {
			completed <- request(t, handler, validChat, "application/json").Code
		}()
	}
	for i := 0; i < maxConcurrent; i++ {
		select {
		case <-started:
		case <-time.After(3 * time.Second):
			t.Fatal("active inference request did not reach upstream")
		}
	}
	go func() {
		completed <- request(t, handler, validChat, "application/json").Code
	}()
	deadline := time.After(3 * time.Second)
	for {
		state, _ := readAdmissionMetrics(t, handler)
		if state.WaitingRequests == 1 {
			break
		}
		select {
		case <-deadline:
			t.Fatalf("no queued request was observable: %+v", state)
		case <-time.After(time.Millisecond):
		}
	}
	if resp := request(t, handler, validChat, "application/json"); resp.Code != http.StatusTooManyRequests {
		t.Fatalf("expected overload rejection, got %d", resp.Code)
	}
	queuedState, _ := readAdmissionMetrics(t, handler)
	if queuedState.ActiveRequests != maxConcurrent || queuedState.WaitingRequests != 1 ||
		queuedState.RejectedFullTotal != 1 || queuedState.AdmittedImmediateTotal != uint64(maxConcurrent) {
		t.Fatalf("bad saturated gateway state: %+v", queuedState)
	}
	unblock()
	for i := 0; i < maxConcurrent+1; i++ {
		select {
		case status := <-completed:
			if status != http.StatusOK {
				t.Fatalf("admitted request status %d", status)
			}
		case <-time.After(3 * time.Second):
			t.Fatal("request did not complete after capacity was released")
		}
	}
	final, _ := readAdmissionMetrics(t, handler)
	if final.ActiveRequests != 0 || final.WaitingRequests != 0 ||
		final.AdmittedImmediateTotal != uint64(maxConcurrent) || final.AdmittedQueuedTotal != 1 ||
		final.RejectedFullTotal != 1 {
		t.Fatalf("gateway admission state not reconciled: %+v", final)
	}
}
