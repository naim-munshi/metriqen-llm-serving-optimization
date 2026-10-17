package httpapi

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

type syntheticOutputBudget struct {
	mu           sync.Mutex
	active       map[string]bool
	spent        map[string]bool
	nextID       int
	used         int
	limit        int
	reservations int
	releases     int
	returnError  bool
}

func (s *syntheticOutputBudget) control(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("X-Metriqen-Gateway-Secret") != projectTestSecret ||
		r.Header.Get("Cookie") != "" {
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
		body, _ := io.ReadAll(io.LimitReader(r.Body, 512))
		if string(body) != fmt.Sprintf(`{"project_id":"%s"}`, projectA) {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		s.mu.Lock()
		s.nextID++
		permit := fmt.Sprintf("%032x", s.nextID)
		s.active[permit] = true
		s.mu.Unlock()
		payload := map[string]any{
			"authorized":                  true,
			"project_id":                  projectA,
			"output_reservation_required": true,
			"lease":                       map[string]string{"scope": testProjectLeaseScope, "permit": permit},
		}
		_ = json.NewEncoder(w).Encode(payload)
	case "/internal/gateway/reserve-output":
		if r.Header.Get("Authorization") != "" {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		var body struct {
			Scope     string `json:"scope"`
			Permit    string `json:"permit"`
			MaxTokens int    `json:"max_tokens"`
		}
		if json.NewDecoder(r.Body).Decode(&body) != nil ||
			body.Scope != testProjectLeaseScope || body.MaxTokens < 1 ||
			body.MaxTokens > maxOutputTokens {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		s.mu.Lock()
		defer s.mu.Unlock()
		if s.returnError {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		if !s.active[body.Permit] || s.spent[body.Permit] {
			w.WriteHeader(http.StatusConflict)
			return
		}
		if s.used+body.MaxTokens > s.limit {
			w.Header().Set("Retry-After", "60")
			w.WriteHeader(http.StatusTooManyRequests)
			return
		}
		s.used += body.MaxTokens
		s.spent[body.Permit] = true
		s.reservations++
		w.WriteHeader(http.StatusNoContent)
	case gatewayReleasePath:
		if r.Header.Get("Authorization") != "" {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		var body struct {
			Scope  string `json:"scope"`
			Permit string `json:"permit"`
		}
		if json.NewDecoder(r.Body).Decode(&body) != nil ||
			body.Scope != testProjectLeaseScope {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		s.mu.Lock()
		if s.active[body.Permit] {
			delete(s.active, body.Permit)
			s.releases++
		}
		s.mu.Unlock()
		w.WriteHeader(http.StatusNoContent)
	default:
		w.WriteHeader(http.StatusNotFound)
	}
}

func budgetGateway(t *testing.T, upstream, control string) http.Handler {
	t.Helper()
	h, err := NewRouterWithInferenceAndSchedulerAndDeploymentAndBudget(
		upstream, "", "", "", control+gatewayAuthPath, projectTestSecret,
		"", "1", "1")
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func outputBudgetRequest(body string) *http.Request {
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Authorization", "Bearer user-jwt")
	r.Header.Set("X-Metriqen-Project-ID", projectA)
	return r
}

func TestOutputReservationBeforeForwardingAcrossGatewayReplicas(t *testing.T) {
	backend := &syntheticOutputBudget{active: map[string]bool{}, spent: map[string]bool{}, limit: 768}
	control := httptest.NewServer(http.HandlerFunc(backend.control))
	defer control.Close()
	var forwarded atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		forwarded.Add(1)
		if r.Header.Get("Authorization") != "" || r.Header.Get("X-Metriqen-Gateway-Secret") != "" ||
			r.Header.Get("X-Metriqen-Project-ID") != "" {
			t.Error("trusted service or user token leaked to Ollama")
		}
		var body struct {
			MaxTokens int `json:"max_tokens"`
		}
		if json.NewDecoder(r.Body).Decode(&body) != nil || body.MaxTokens != 256 {
			t.Error("wrong validated cap forwarded to model")
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"ok"}}]}`)
	}))
	defer upstream.Close()
	gateways := []http.Handler{
		budgetGateway(t, upstream.URL, control.URL),
		budgetGateway(t, upstream.URL, control.URL),
	}
	for i := 0; i < 4; i++ {
		recorder := httptest.NewRecorder()
		gateways[i%2].ServeHTTP(recorder, outputBudgetRequest(validChat))
		expected := http.StatusOK
		if i == 3 {
			expected = http.StatusTooManyRequests
			if recorder.Header().Get("Retry-After") != "60" {
				t.Fatal("missing fixed 60s project budget retry")
			}
		}
		if recorder.Code != expected {
			t.Fatalf("request %d got %d expected %d %s", i, recorder.Code, expected, recorder.Body)
		}
	}
	backend.mu.Lock()
	defer backend.mu.Unlock()
	if forwarded.Load() != 3 || backend.used != 768 ||
		backend.reservations != 3 || backend.releases != 4 ||
		len(backend.active) != 0 {
		t.Fatalf("output budget bypass or permit leak: %+v forwarded=%d", backend, forwarded.Load())
	}
}

func TestOutputReservationNoRefundOnUpstreamErrorAndValidatedCap(t *testing.T) {
	backend := &syntheticOutputBudget{active: map[string]bool{}, spent: map[string]bool{}, limit: 2048}
	control := httptest.NewServer(http.HandlerFunc(backend.control))
	defer control.Close()
	var upstreamCalls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer upstream.Close()
	gateway := budgetGateway(t, upstream.URL, control.URL)
	body := `{"model":"test-model","messages":[{"role":"user","content":"synthetic"}],"max_tokens":2048}`
	recorder := httptest.NewRecorder()
	gateway.ServeHTTP(recorder, outputBudgetRequest(body))
	if recorder.Code == http.StatusOK {
		t.Fatal("upstream 503 unexpectedly returned success")
	}
	second := httptest.NewRecorder()
	gateway.ServeHTTP(second, outputBudgetRequest(validChat))
	if second.Code != http.StatusTooManyRequests || upstreamCalls.Load() != 1 {
		t.Fatalf("error refunded reserved token cap: %d %s", second.Code, second.Body)
	}
	malformed := httptest.NewRecorder()
	gateway.ServeHTTP(malformed, outputBudgetRequest(
		`{"model":"test-model","messages":[{"role":"user","content":"synthetic"}],"max_tokens":"256"}`))
	if malformed.Code != http.StatusBadRequest {
		t.Fatalf("malformed cap bypassed JSON validation: %d", malformed.Code)
	}
	backend.mu.Lock()
	defer backend.mu.Unlock()
	if backend.used != 2048 || backend.reservations != 1 ||
		backend.releases != 3 || len(backend.active) != 0 {
		t.Fatalf("failed, denied or invalid input retained active leases: %+v", backend)
	}
}

func TestOutputReservationFailsClosedOnControllerMismatchAndRedisOutage(t *testing.T) {
	backend := &syntheticOutputBudget{active: map[string]bool{}, spent: map[string]bool{}, limit: 2048, returnError: true}
	control := httptest.NewServer(http.HandlerFunc(backend.control))
	defer control.Close()
	var forwarded atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		forwarded.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"ok"}}]}`)
	}))
	defer upstream.Close()
	budgeted := budgetGateway(t, upstream.URL, control.URL)
	w := httptest.NewRecorder()
	budgeted.ServeHTTP(w, outputBudgetRequest(validChat))
	if w.Code != http.StatusServiceUnavailable || forwarded.Load() != 0 {
		t.Fatal("Redis outage allowed inference")
	}
	backend.mu.Lock()
	if backend.releases != 1 || len(backend.active) != 0 {
		t.Fatal("failed reservation retained project lease")
	}
	backend.mu.Unlock()

	legacy, err := NewRouterWithInferenceAndSchedulerAndDeploymentAndLeases(
		upstream.URL, "", "", "", control.URL+gatewayAuthPath,
		projectTestSecret, "", "1")
	if err != nil {
		t.Fatal(err)
	}
	w = httptest.NewRecorder()
	legacy.ServeHTTP(w, outputBudgetRequest(validChat))
	if w.Code != http.StatusServiceUnavailable || forwarded.Load() != 0 {
		t.Fatal("partially enabled controller bypassed mandatory reservation")
	}
	for _, bad := range []string{"yes", "01", "True", " "} {
		if _, err := newBudgetedProjectAuthorizer(
			control.URL+gatewayAuthPath, projectTestSecret, "", "1", bad); err == nil {
			t.Fatal("unsafe budget mode accepted", bad)
		}
	}
	if _, err := newBudgetedProjectAuthorizer(
		control.URL+gatewayAuthPath, projectTestSecret, "", "", "1"); err == nil {
		t.Fatal("output reservations allowed without in-flight leases")
	}
}

func TestCanonicalValidatedOutputCapMatchesForwardedJSON(t *testing.T) {
	for _, tc := range []struct {
		body string
		want int
	}{
		{`{"model":"test-model","messages":[{"role":"user","content":"a"}]}`, 256},
		{`{"model":"test-model","messages":[{"role":"user","content":"a"}],"max_tokens":1}`, 1},
		{`{"model":"test-model","messages":[{"role":"user","content":"a"}],"max_tokens":2048}`, 2048},
		{`{"model":"test-model","messages":[{"role":"user","content":"a"}],"max_tokens":32,"max_tokens":512}`, 512},
		{`{"model":"test-model","messages":[{"role":"user","content":"a"}],"max_tokens":512,"max_tokens":4}`, 4},
	} {
		body, _, _, cap, err := validateChatRequestWithBudget([]byte(tc.body))
		if err != nil || cap != tc.want {
			t.Fatalf("validated output cap mismatch: want=%d got=%d err=%v", tc.want, cap, err)
		}
		var forwarded struct {
			MaxTokens int `json:"max_tokens"`
		}
		if json.Unmarshal(body, &forwarded) != nil || forwarded.MaxTokens != cap {
			t.Fatal("reserved cap differs from the literal model bound")
		}
	}
	for _, raw := range []string{"null", "true", "0", "-1", "2049", "1.0", `"256"`, "256.5", "[]"} {
		input := fmt.Sprintf(`{"model":"test-model","messages":[{"role":"user","content":"a"}],"max_tokens":%s}`, raw)
		if _, _, _, _, err := validateChatRequestWithBudget([]byte(input)); err == nil {
			t.Fatalf("noncanonical token cap accepted: %q", raw)
		}
	}
}
