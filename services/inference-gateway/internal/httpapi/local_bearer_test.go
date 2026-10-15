package httpapi

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

const localTestBearerToken = "metriqen-test-only-0123456789abcdef0123456789abcdef"

func TestLocalBearerTokenBlocksPromptBeforeAdmission(t *testing.T) {
	var forwarded atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		forwarded.Add(1)
		if r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" {
			t.Error("client credentials forwarded upstream")
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"ok"}}]}`)
	}))
	defer upstream.Close()
	handler, err := NewRouterWithInferenceAndSchedulerAndToken(upstream.URL, "", "2", localTestBearerToken)
	if err != nil {
		t.Fatal(err)
	}
	tests := []struct {
		name   string
		header []string
	}{
		{"missing", nil},
		{"wrong", []string{"Bearer " + strings.Repeat("x", len(localTestBearerToken))}},
		{"short", []string{"Bearer x"}},
		{"wrong scheme", []string{"Basic " + localTestBearerToken}},
		{"lowercase scheme", []string{"bearer " + localTestBearerToken}},
		{"duplicated", []string{"Bearer " + localTestBearerToken, "Bearer " + localTestBearerToken}},
		{"comma-separated", []string{"Bearer " + localTestBearerToken + ",Bearer " + localTestBearerToken}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader("{malformed"))
			r.Header.Set("Content-Type", "application/json")
			for _, header := range tc.header {
				r.Header.Add("Authorization", header)
			}
			w := httptest.NewRecorder()
			handler.ServeHTTP(w, r)
			if w.Code != http.StatusUnauthorized ||
				!strings.Contains(w.Body.String(), "authentication_required") ||
				w.Header().Get("WWW-Authenticate") != `Bearer realm="metriqen-local"` ||
				w.Header().Get("Cache-Control") != "no-store" {
				t.Fatalf("missing fail-closed auth: HTTP %d body %s", w.Code, w.Body.String())
			}
			if strings.Contains(w.Body.String(), localTestBearerToken) {
				t.Fatal("configured secret exposed by HTTP error")
			}
		})
	}
	admission, _ := readAdmissionMetrics(t, handler)
	outcomes, _ := readInferenceMetrics(t, handler)
	if forwarded.Load() != 0 || admission.ActiveRequests != 0 ||
		admission.WaitingRequests != 0 || admission.AdmittedImmediateTotal != 0 ||
		admission.AdmittedQueuedTotal != 0 || outcomes.AttemptsTotal != 0 {
		t.Fatalf("unauthorized requests reached inference: forwarded=%d admission=%+v outcomes=%+v", forwarded.Load(), admission, outcomes)
	}

	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(validChat))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+localTestBearerToken)
	req.Header.Set("Cookie", "PRIVATE_COOKIE_MUST_NOT_FORWARD")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK || forwarded.Load() != 1 {
		t.Fatalf("valid token was not accepted: HTTP %d upstream calls %d", w.Code, forwarded.Load())
	}
	outcomes, raw := readInferenceMetrics(t, handler)
	if outcomes.AttemptsTotal != 1 || outcomes.NonstreamCompleteTotal != 1 ||
		strings.Contains(raw, localTestBearerToken) {
		t.Fatalf("authorized attempt accounting or privacy failed: %+v", outcomes)
	}
}

func TestLocalBearerTokenStrictStartupValidation(t *testing.T) {
	invalid := []string{
		"x", strings.Repeat("x", 31), strings.Repeat("x", 257),
		strings.Repeat("x", 31) + " ", strings.Repeat("x", 31) + "\n",
		strings.Repeat("x", 31) + "\t", strings.Repeat("x", 31) + ",",
		strings.Repeat("x", 31) + "é", strings.Repeat("x", 31) + "\"",
	}
	for _, token := range invalid {
		if _, err := NewRouterWithInferenceAndSchedulerAndToken("http://127.0.0.1:11434", "", "", token); err == nil {
			t.Fatal("invalid shared secret accepted")
		}
	}
	if _, err := NewRouterWithInferenceAndSchedulerAndToken("", "", "", localTestBearerToken); err == nil {
		t.Fatal("configured bearer secret without upstream must fail startup")
	}
	if _, err := NewRouterWithInferenceAndSchedulerAndToken("  ", "", "", localTestBearerToken); err == nil {
		t.Fatal("whitespace-only upstream must fail with token configured")
	}
	if _, err := NewRouterWithInferenceAndSchedulerAndToken("http://127.0.0.1:11434", "", "", localTestBearerToken); err != nil {
		t.Fatal("valid secret rejected:", err)
	}
}

func TestLocalBearerTokenPreservesDefaultLoopbackContract(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"ok"}}]}`)
	}))
	defer upstream.Close()
	handler, err := NewRouterWithInferenceAndSchedulerAndToken(upstream.URL, "", "", "")
	if err != nil {
		t.Fatal(err)
	}
	if result := request(t, handler, validChat, "application/json"); result.Code != http.StatusOK {
		t.Fatalf("token opt-out unexpectedly changed default: %d", result.Code)
	}
}

func TestLocalBearerTokenDoesNotBlockReadOnlyDiagnostics(t *testing.T) {
	handler, err := NewRouterWithInferenceAndSchedulerAndToken("http://127.0.0.1:11434", "", "", localTestBearerToken)
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"/health/ready", "/metrics/admission", "/metrics/upstream", "/metrics/inference", "/console"} {
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
		if w.Code != http.StatusOK {
			t.Fatalf("token unexpectedly gated local diagnostic %q: %d", path, w.Code)
		}
	}
}
