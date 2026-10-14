package httpapi

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestOptInQueueWaitsWithoutForwardingUntilCapacityIsReleased(t *testing.T) {
	entered := make(chan struct{}, maxConcurrent+1)
	release := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		entered <- struct{}{}
		select {
		case <-release:
		case <-r.Context().Done():
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"ok"}}]}`)
	}))
	defer upstream.Close()
	bridge, err := newInferenceBridge(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	bridge.admission = newAdmissionScheduler(maxConcurrent, 1)
	handler := http.HandlerFunc(bridge.serveChat)

	var started sync.WaitGroup
	started.Add(maxConcurrent)
	activeResults := make(chan int, maxConcurrent)
	for i := 0; i < maxConcurrent; i++ {
		go func() {
			started.Done()
			result := request(t, handler, validChat, "application/json")
			activeResults <- result.Code
		}()
	}
	started.Wait()
	for i := 0; i < maxConcurrent; i++ {
		select {
		case <-entered:
		case <-time.After(3 * time.Second):
			t.Fatal("upstream did not receive four active requests")
		}
	}
	queued := make(chan int, 1)
	go func() {
		result := request(t, handler, validChat, "application/json")
		queued <- result.Code
	}()
	waitForQueue(t, bridge.admission, 1)
	select {
	case <-entered:
		t.Fatal("pending request leaked upstream while queueing")
	default:
	}
	rejected := request(t, handler, validChat, "application/json")
	if rejected.Code != http.StatusTooManyRequests ||
		!strings.Contains(rejected.Body.String(), "capacity_exceeded") ||
		rejected.Header().Get("Retry-After") != "1" {
		t.Fatalf("expected bounded queue rejection, got %d: %s", rejected.Code, rejected.Body.String())
	}
	close(release)
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("queued request was not forwarded after capacity became free")
	}
	select {
	case status := <-queued:
		if status != http.StatusOK {
			t.Fatalf("queued request did not complete successfully: %d", status)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("queued request never completed")
	}
	for i := 0; i < maxConcurrent; i++ {
		select {
		case status := <-activeResults:
			if status != http.StatusOK {
				t.Fatalf("active request failed: %d", status)
			}
		case <-time.After(3 * time.Second):
			t.Fatal("active requests did not finish")
		}
	}
}

func TestCanceledHTTPQueuedRequestNeverForwardsPrompt(t *testing.T) {
	entered := make(chan struct{}, maxConcurrent+1)
	release := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		entered <- struct{}{}
		select {
		case <-release:
		case <-r.Context().Done():
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"ok"}}]}`)
	}))
	defer upstream.Close()
	bridge, err := newInferenceBridge(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	bridge.admission = newAdmissionScheduler(maxConcurrent, 1)
	handler := http.HandlerFunc(bridge.serveChat)
	active := make(chan struct{}, maxConcurrent)
	for i := 0; i < maxConcurrent; i++ {
		go func() {
			request(t, handler, validChat, "application/json")
			active <- struct{}{}
		}()
	}
	for i := 0; i < maxConcurrent; i++ {
		select {
		case <-entered:
		case <-time.After(3 * time.Second):
			t.Fatal("active upstream request did not start")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(validChat)).WithContext(ctx)
	req.Header.Set("Content-Type", "application/json")
	finished := make(chan struct{})
	go func() {
		handler.ServeHTTP(httptest.NewRecorder(), req)
		close(finished)
	}()
	waitForQueue(t, bridge.admission, 1)
	cancel()
	select {
	case <-finished:
	case <-time.After(3 * time.Second):
		t.Fatal("canceled queued request did not exit")
	}
	waitForQueue(t, bridge.admission, 0)
	close(release)
	for i := 0; i < maxConcurrent; i++ {
		select {
		case <-active:
		case <-time.After(3 * time.Second):
			t.Fatal("active work failed to finish")
		}
	}
	select {
	case <-entered:
		t.Fatal("canceled pending prompt was sent upstream")
	default:
	}
}

func TestQueueConstructorRejectsInvalidSettingBeforeInference(t *testing.T) {
	for _, raw := range []string{"-1", "33", "1,1", "0x20"} {
		if _, err := NewRouterWithInferenceAndScheduler("http://127.0.0.1:11434", "", raw); err == nil {
			t.Fatalf("invalid admission configuration accepted: %q", raw)
		}
	}
	if _, err := NewRouterWithInferenceAndScheduler("http://127.0.0.1:11434", "llama3.2:1b", "2"); err != nil {
		t.Fatalf("valid queue and allowlist config was rejected: %v", err)
	}
}
