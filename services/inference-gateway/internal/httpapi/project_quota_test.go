package httpapi

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

func TestQuota429IsPropagatedWithoutReadingPromptOrUsingAdmission(t *testing.T) {
	var calls atomic.Int32
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		if n > 2 {
			// The gateway must not trust a service-supplied Retry-After.
			w.Header().Set("Retry-After", "999999")
			w.WriteHeader(http.StatusTooManyRequests)
			_, _ = io.WriteString(w, `{"detail":"project quota exceeded"}`)
			return
		}
		_, _ = io.WriteString(w, `{"authorized":true,"project_id":"`+projectA+`"}`)
	}))
	defer control.Close()

	var forward atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		forward.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"ok"}}]}`)
	}))
	defer upstream.Close()

	instances := make([]http.Handler, 2)
	for i := range instances {
		h, err := NewRouterWithInferenceAndSchedulerAndAuth(
			upstream.URL, "", "", "", control.URL+gatewayAuthPath, projectTestSecret)
		if err != nil {
			t.Fatal(err)
		}
		instances[i] = h
	}
	for i := 0; i < 3; i++ {
		r := projectRequest("user-jwt", projectA)
		if i == 2 {
			// The quota must reject before malformed prompt validation.
			r = httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader("{malformed"))
			r.Header.Set("Content-Type", "application/json")
			r.Header.Set("Authorization", "Bearer user-jwt")
			r.Header.Set("X-Metriqen-Project-ID", projectA)
		}
		w := httptest.NewRecorder()
		instances[i%2].ServeHTTP(w, r)
		if i < 2 && w.Code != http.StatusOK {
			t.Fatalf("authorized call %d failed with HTTP %d: %s", i, w.Code, w.Body)
		}
		if i == 2 && (w.Code != http.StatusTooManyRequests ||
			w.Header().Get("Retry-After") != "60" ||
			!strings.Contains(w.Body.String(), "project_quota_exceeded")) {
			t.Fatalf("quota denial did not propagate 429 with bounded delay: %d %s", w.Code, w.Body)
		}
	}
	if forward.Load() != 2 || calls.Load() != 3 {
		t.Fatalf("quota bypass across gateways: forwarded %d checked %d", forward.Load(), calls.Load())
	}
	for _, h := range instances {
		metrics, _ := readAdmissionMetrics(t, h)
		if metrics.AdmittedImmediateTotal != 1 {
			t.Fatalf("rejected request entered admission: %+v", metrics)
		}
	}
}

func TestQuotaCapAcrossConcurrentIndependentGatewayReplicas(t *testing.T) {
	const quotaLimit = 7
	var checked atomic.Int32
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := checked.Add(1)
		w.Header().Set("Content-Type", "application/json")
		if n > quotaLimit {
			w.WriteHeader(http.StatusTooManyRequests)
			_, _ = io.WriteString(w, `{"detail":"quota"}`)
			return
		}
		_, _ = io.WriteString(w, `{"authorized":true,"project_id":"`+projectA+`"}`)
	}))
	defer control.Close()
	var forwarded atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		forwarded.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"ok"}}]}`)
	}))
	defer upstream.Close()

	replicas := make([]http.Handler, 2)
	for i := range replicas {
		h, err := NewRouterWithInferenceAndSchedulerAndAuth(
			upstream.URL, "", "4", "", control.URL+gatewayAuthPath, projectTestSecret)
		if err != nil {
			t.Fatal(err)
		}
		replicas[i] = h
	}
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			w := httptest.NewRecorder()
			replicas[index%2].ServeHTTP(w, projectRequest("user-jwt", projectA))
			if w.Code != http.StatusOK && w.Code != http.StatusTooManyRequests {
				t.Errorf("unexpected gateway denial: HTTP %d: %s", w.Code, w.Body)
			}
		}(i)
	}
	wg.Wait()
	if checked.Load() != 50 {
		t.Fatalf("requests skipped centralized project authorization: %d", checked.Load())
	}
	if forwarded.Load() > quotaLimit || forwarded.Load() == 0 {
		t.Fatalf("cross-replica quota bypass: %d upstream calls", forwarded.Load())
	}
}
