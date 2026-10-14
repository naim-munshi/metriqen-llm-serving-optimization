package httpapi

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestLocalExactModelAllowlistRejectsBeforeUpstream(t *testing.T) {
	calls := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"ok"}}]}`)
	}))
	defer upstream.Close()

	handler, err := NewRouterWithInferenceAndModels(upstream.URL, "test-model,another/model:v1")
	if err != nil {
		t.Fatal(err)
	}
	ok := request(t, handler, validChat, "application/json")
	if ok.Code != 200 || calls != 1 {
		t.Fatalf("permitted model was not forwarded: status=%d calls=%d", ok.Code, calls)
	}
	disallowed := request(t, handler,
		`{"model":"other-model","messages":[{"role":"user","content":"PRIVATE_PROMPT"}]}`,
		"application/json")
	if disallowed.Code != http.StatusForbidden || !strings.Contains(disallowed.Body.String(), "model_not_allowed") {
		t.Fatalf("unexpected rejection: %d %s", disallowed.Code, disallowed.Body.String())
	}
	if calls != 1 {
		t.Fatalf("disallowed model reached upstream; calls=%d", calls)
	}
	if strings.Contains(disallowed.Body.String(), "PRIVATE_PROMPT") ||
		strings.Contains(disallowed.Body.String(), "test-model") {
		t.Fatal("rejection leaked prompt or list of allowed models")
	}
	caseMismatch := request(t, handler,
		`{"model":"Test-model","messages":[{"role":"user","content":"hi"}],"stream":true}`,
		"application/json")
	if caseMismatch.Code != http.StatusForbidden || calls != 1 {
		t.Fatalf("model allowlist must be case-sensitive; status=%d calls=%d", caseMismatch.Code, calls)
	}
}

func TestLocalModelAllowlistMalformedConfigurationFailsClosed(t *testing.T) {
	bad := []string{
		" ", ",", "test-model,", ",test-model", " test-model",
		"test-model ", "test-model, test-other",
		"test-model,test-model", "test model", "test\nmodel",
		"test@model", "😺", strings.Repeat("m", 129),
		strings.Repeat("x,", 32) + "z",
	}
	for _, raw := range bad {
		t.Run(raw, func(t *testing.T) {
			if _, err := NewRouterWithInferenceAndModels("http://127.0.0.1:11434", raw); err == nil {
				t.Fatalf("bad allowlist %q was accepted", raw)
			}
		})
	}
}

func TestEmptyLocalModelAllowlistPreservesDefaultBehavior(t *testing.T) {
	allowed, err := parseAllowedModels("")
	if err != nil || allowed != nil {
		t.Fatalf("default unrestricted model tag should remain unchanged: %v", err)
	}
	allowed, err = parseAllowedModels("llama3.2:1b,nomic-embed-text:latest")
	if err != nil || len(allowed) != 2 {
		t.Fatalf("valid multi-model list rejected: %v", err)
	}
	if _, ok := allowed["llama3.2:1b"]; !ok {
		t.Fatal("expected llama model tag missing")
	}
}

func TestLocalModelAllowlistConfiguredWithoutUpstreamCannotEnableInference(t *testing.T) {
	handler, err := NewRouterWithInferenceAndModels("", "llama3.2:1b")
	if err != nil {
		t.Fatal(err)
	}
	res := request(t, handler, validChat, "application/json")
	if res.Code != http.StatusServiceUnavailable {
		t.Fatalf("disabled upstream must remain disabled: %d", res.Code)
	}
}
