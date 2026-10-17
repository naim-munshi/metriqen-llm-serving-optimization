package httpapi

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

const testProjectLeaseScope = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

type syntheticLeases struct {
	mu       sync.Mutex
	used     map[string]bool
	issued   int
	released int
	limit    int
}

func (s *syntheticLeases) serve(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("X-Metriqen-Gateway-Secret") != projectTestSecret {
		w.WriteHeader(http.StatusForbidden)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	switch r.URL.Path {
	case gatewayAuthPath:
		if r.Header.Get("Authorization") != "Bearer user-jwt" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		data, _ := io.ReadAll(io.LimitReader(r.Body, 512))
		if string(data) != `{"project_id":"`+projectA+`"}` {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		s.mu.Lock()
		if len(s.used) >= s.limit {
			s.mu.Unlock()
			w.Header().Set("Retry-After", "1")
			w.WriteHeader(http.StatusTooManyRequests)
			_, _ = io.WriteString(w, `{"detail":"active limit"}`)
			return
		}
		s.issued++
		id := fmt.Sprintf("%032x", s.issued)
		s.used[id] = true
		s.mu.Unlock()
		_, _ = io.WriteString(w, `{"authorized":true,"project_id":"`+projectA+
			`","lease":{"scope":"`+testProjectLeaseScope+`","permit":"`+id+`"}}`)
	case gatewayReleasePath:
		if r.Header.Get("Authorization") != "" ||
			r.Header.Get("Cookie") != "" ||
			r.Method != http.MethodPost {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		body, _ := io.ReadAll(io.LimitReader(r.Body, 512))
		s.mu.Lock()
		defer s.mu.Unlock()
		for id := range s.used {
			if strings.Contains(string(body), `"permit":"`+id+`"`) &&
				strings.Contains(string(body), `"scope":"`+testProjectLeaseScope+`"`) {
				delete(s.used, id)
				s.released++
				w.WriteHeader(http.StatusNoContent)
				return
			}
		}
		w.WriteHeader(http.StatusNotFound)
	default:
		w.WriteHeader(http.StatusNotFound)
	}
}

func leasedGateway(t *testing.T, upstream, control string) http.Handler {
	t.Helper()
	h, err := NewRouterWithInferenceAndSchedulerAndDeploymentAndLeases(
		upstream, "", "", "", control+gatewayAuthPath, projectTestSecret,
		"", "1")
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func TestDistributedProjectLeaseCapsConcurrentInferenceAcrossGateways(t *testing.T) {
	shared := &syntheticLeases{used: make(map[string]bool), limit: 2}
	control := httptest.NewServer(http.HandlerFunc(shared.serve))
	defer control.Close()

	started := make(chan struct{}, 8)
	unblock := make(chan struct{})
	var forward atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		forward.Add(1)
		started <- struct{}{}
		select {
		case <-unblock:
		case <-r.Context().Done():
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"ok"}}]}`)
	}))
	defer upstream.Close()
	defer func() {
		select {
		case <-unblock:
		default:
			close(unblock)
		}
	}()

	gateways := []http.Handler{
		leasedGateway(t, upstream.URL, control.URL),
		leasedGateway(t, upstream.URL, control.URL),
	}
	results := make(chan int, 2)
	for i := 0; i < 2; i++ {
		go func(n int) {
			w := httptest.NewRecorder()
			gateways[n].ServeHTTP(w, projectRequest("user-jwt", projectA))
			results <- w.Code
		}(i)
	}
	for i := 0; i < 2; i++ {
		select {
		case <-started:
		case <-time.After(4 * time.Second):
			t.Fatal("two eligible requests never reached active upstream")
		}
	}
	denied := httptest.NewRecorder()
	gateways[1].ServeHTTP(denied, projectRequest("user-jwt", projectA))
	if denied.Code != http.StatusTooManyRequests || denied.Header().Get("Retry-After") != "1" ||
		!strings.Contains(denied.Body.String(), "project_capacity_exceeded") ||
		forward.Load() != 2 {
		t.Fatalf("cross-replica distributed capacity bypass: %d %s", denied.Code, denied.Body)
	}

	close(unblock)
	for i := 0; i < 2; i++ {
		select {
		case code := <-results:
			if code != http.StatusOK {
				t.Fatalf("leased inference unexpectedly failed: %d", code)
			}
		case <-time.After(5 * time.Second):
			t.Fatal("authorized inference never released its permit")
		}
	}
	shared.mu.Lock()
	remaining, released := len(shared.used), shared.released
	shared.mu.Unlock()
	if remaining != 0 || released != 2 {
		t.Fatalf("permit leak after successful inference: active=%d released=%d", remaining, released)
	}
	recovered := httptest.NewRecorder()
	gateways[0].ServeHTTP(recovered, projectRequest("user-jwt", projectA))
	if recovered.Code != http.StatusOK || forward.Load() != 3 {
		t.Fatalf("capacity was not reusable after completion: %d %s", recovered.Code, recovered.Body)
	}
	shared.mu.Lock()
	defer shared.mu.Unlock()
	if len(shared.used) != 0 || shared.released != 3 {
		t.Fatal("completed inference retained an active permit")
	}
}

func TestDistributedPermitAlwaysReleasesOnUpstreamFailureAndCancellation(t *testing.T) {
	shared := &syntheticLeases{used: make(map[string]bool), limit: 1}
	control := httptest.NewServer(http.HandlerFunc(shared.serve))
	defer control.Close()

	var block atomic.Bool
	block.Store(false)
	started := make(chan struct{}, 1)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !block.Load() {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = io.WriteString(w, `{"error":"not ready"}`)
			return
		}
		started <- struct{}{}
		<-r.Context().Done()
	}))
	defer upstream.Close()
	h := leasedGateway(t, upstream.URL, control.URL)

	w := httptest.NewRecorder()
	h.ServeHTTP(w, projectRequest("user-jwt", projectA))
	if w.Code == http.StatusOK {
		t.Fatal("simulated runtime 503 was interpreted as successful inference")
	}
	shared.mu.Lock()
	n := len(shared.used)
	shared.mu.Unlock()
	if n != 0 {
		t.Fatal("upstream failure leaked project concurrency permit")
	}
	block.Store(true)
	ctx, cancel := context.WithCancel(context.Background())
	req := projectRequest("user-jwt", projectA).WithContext(ctx)
	finished := make(chan struct{})
	go func() {
		h.ServeHTTP(httptest.NewRecorder(), req)
		close(finished)
	}()
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		cancel()
		t.Fatal("cancellation test never reached Ollama")
	}
	cancel()
	select {
	case <-finished:
	case <-time.After(5 * time.Second):
		t.Fatal("canceled client did not release project permit")
	}
	shared.mu.Lock()
	defer shared.mu.Unlock()
	if len(shared.used) != 0 || shared.released != 2 {
		t.Fatalf("failed/canceled request permit leak: %+v", shared)
	}
}

func TestLeaseGuardRejectsMissingTamperedAndUnexpectedPermits(t *testing.T) {
	controlResponse := `{"authorized":true,"project_id":"`+projectA+`"}`
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, controlResponse)
	}))
	defer control.Close()
	var forwarded atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		forwarded.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"ok"}}]}`)
	}))
	defer upstream.Close()
	h := leasedGateway(t, upstream.URL, control.URL)
	for _, body := range []string{
		`{"authorized":true,"project_id":"` + projectA + `"}`,
		`{"authorized":true,"project_id":"` + projectA + `","lease":null}`,
		`{"authorized":true,"project_id":"` + projectA +
			`","lease":{"scope":"` + strings.Repeat("a", 64) + `","permit":"short"}}`,
		`{"authorized":true,"project_id":"` + projectA +
			`","lease":{"scope":"` + strings.Repeat("A", 64) +
			`","permit":"` + strings.Repeat("b", 32) + `"}}`,
	} {
		controlResponse = body
		w := httptest.NewRecorder()
		h.ServeHTTP(w, projectRequest("user-jwt", projectA))
		if w.Code != http.StatusServiceUnavailable {
			t.Fatalf("untrusted permit authorized inference: HTTP %d", w.Code)
		}
	}
	if forwarded.Load() != 0 {
		t.Fatal("incomplete lease proof forwarded prompts")
	}
	legacy, err := NewRouterWithInferenceAndSchedulerAndAuth(
		upstream.URL, "", "", "", control.URL+gatewayAuthPath, projectTestSecret)
	if err != nil {
		t.Fatal(err)
	}
	controlResponse = `{"authorized":true,"project_id":"` + projectA +
		`","lease":{"scope":"` + testProjectLeaseScope +
		`","permit":"` + strings.Repeat("a", 32) + `"}}`
	w := httptest.NewRecorder()
	legacy.ServeHTTP(w, projectRequest("user-jwt", projectA))
	if w.Code != http.StatusServiceUnavailable {
		t.Fatal("legacy gateway silently accepted partial project permit opt-in")
	}
	for _, invalid := range []string{"true", "01", "garbage", " "} {
		if _, err := newLeasedProjectAuthorizer(control.URL+gatewayAuthPath,
			projectTestSecret, "", invalid); err == nil {
			t.Fatal("unsafe lease mode accepted:", invalid)
		}
	}
	if _, err := newLeasedProjectAuthorizer("", "", "", "1"); err == nil {
		t.Fatal("distributed concurrency started without verified project authorization")
	}
}
