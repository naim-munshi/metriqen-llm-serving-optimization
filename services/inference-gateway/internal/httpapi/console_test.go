package httpapi

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestLocalOperationsConsoleServesBundledAssetsWithStrictCSP(t *testing.T) {
	handler, err := NewRouterWithInferenceAndScheduler("http://127.0.0.1:11434", "", "2")
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		path, mediaType, sentinel string
	}{
		{"/console", "text/html; charset=utf-8", "Operations <span>Overview</span>"},
		{"/console/app.css", "text/css; charset=utf-8", ".health-grid"},
		{"/console/app.js", "text/javascript; charset=utf-8", "stream_missing_done_total"},
	} {
		t.Run(tc.path, func(t *testing.T) {
			result := httptest.NewRecorder()
			handler.ServeHTTP(result, httptest.NewRequest(http.MethodGet, tc.path, nil))
			if result.Code != http.StatusOK ||
				result.Header().Get("Content-Type") != tc.mediaType ||
				!strings.Contains(result.Body.String(), tc.sentinel) {
				t.Fatalf("unexpected console asset %s: HTTP %d type=%q", tc.path,
					result.Code, result.Header().Get("Content-Type"))
			}
			if result.Header().Get("Cache-Control") != "no-store" ||
				result.Header().Get("X-Content-Type-Options") != "nosniff" ||
				result.Header().Get("X-Frame-Options") != "DENY" ||
				result.Header().Get("Referrer-Policy") != "no-referrer" {
				t.Fatalf("missing console security headers for %s: %v", tc.path, result.Header())
			}
			csp := result.Header().Get("Content-Security-Policy")
			for _, directive := range []string{"default-src 'none'", "script-src 'self'",
				"style-src 'self'", "connect-src 'self'", "frame-ancestors 'none'",
				"object-src 'none'", "base-uri 'none'"} {
				if !strings.Contains(csp, directive) {
					t.Fatalf("missing CSP directive %s: %s", directive, csp)
				}
			}
			if strings.Contains(csp, "unsafe-inline") || strings.Contains(csp, "unsafe-eval") ||
				strings.Contains(csp, "http:") || strings.Contains(csp, "https:") {
				t.Fatalf("console CSP overly permissive: %q", csp)
			}
			if strings.Contains(result.Body.String(), "PRIVATE_DEMO_PROMPT") ||
				strings.Contains(result.Body.String(), "PRIVATE_UPSTREAM") {
				t.Fatal("console leaked private request contents")
			}
		})
	}
}

func TestOperationsConsoleUnavailableInHealthOnlyDeployments(t *testing.T) {
	handler := NewRouter()
	for _, path := range []string{"/console", "/console/app.js", "/console/app.css"} {
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
		if w.Code != http.StatusNotFound {
			t.Fatalf("health-only service unexpectedly serves %s: %d", path, w.Code)
		}
	}
}

func TestOperationsConsoleReadOnlyAndExactPaths(t *testing.T) {
	handler := newGateway(t, "http://127.0.0.1:11434")
	for _, path := range []string{"/console", "/console/app.css", "/console/app.js"} {
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest(http.MethodPost, path, nil))
		if w.Code != http.StatusMethodNotAllowed {
			t.Fatalf("console route %s accepted POST: %d", path, w.Code)
		}
	}
	for _, path := range []string{"/console/anything", "/console/debug", "/console/private.json"} {
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
		if w.Code != http.StatusNotFound {
			t.Fatalf("unexpected dynamic console route %s: %d", path, w.Code)
		}
	}
}

func TestOperationsConsoleDoesNotConsumeAdmissionOrOutcomes(t *testing.T) {
	handler := newGateway(t, "http://127.0.0.1:11434")
	for _, path := range []string{"/console", "/console/app.js", "/console/app.css"} {
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
		if w.Code != http.StatusOK {
			t.Fatalf("dashboard request failed %s: %d", path, w.Code)
		}
	}
	admission, _ := readAdmissionMetrics(t, handler)
	inference, _ := readInferenceMetrics(t, handler)
	if admission.AdmittedImmediateTotal != 0 || admission.ActiveRequests != 0 ||
		inference.AttemptsTotal != 0 || inference.ActiveAttempts != 0 {
		t.Fatalf("dashboard assets consumed inference admission: %+v %+v", admission, inference)
	}
}

func TestPortfolioReviewerJourneyUsesOnlyFixedLocalLinksAndSafeUsageMetrics(t *testing.T) {
	handler := newGateway(t, "http://127.0.0.1:11434")
	request := func(path string) *httptest.ResponseRecorder {
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
		if w.Code != http.StatusOK {
			t.Fatalf("unexpected %s response: %d", path, w.Code)
		}
		return w
	}
	html := request("/console").Body.String()
	script := request("/console/app.js").Body.String()
	css := request("/console/app.css").Body.String()
	for _, fragment := range []string{
		`id="usage"`, `id="usage-state"`, `id="usage-prompt"`,
		`id="usage-output"`, `id="usage-total"`,
		`id="usage-nonstream"`, `id="usage-stream"`,
		`id="usage-unmeasured"`, `id="reviewer"`,
		`href="http://127.0.0.1:8000/console/models"`,
		`href="http://127.0.0.1:8000/console/account"`,
		`href="#admission"`, `rel="noopener noreferrer"`,
		"not a shared authenticated session", "not a bill",
	} {
		if !strings.Contains(html, fragment) {
			t.Fatalf("missing exact reviewer/usage HTML invariant %q", fragment)
		}
	}
	for _, fragment := range []string{
		`usage: "/metrics/usage"`, "data?.schema_version === 2",
		"Number.isSafeInteger", "textContent = String(value)",
		`credentials: "omit"`, `redirect: "error"`,
		"Provider usage unavailable or unsupported schema",
		"provider_all_tokens_reported_total",
		"stream_not_measured_total",
	} {
		if !strings.Contains(script, fragment) {
			t.Fatalf("missing safe usage client behavior %q", fragment)
		}
	}
	if strings.Contains(script, "innerHTML") || strings.Contains(script, "localStorage") ||
		strings.Contains(script, "sessionStorage") || strings.Contains(script, "127.0.0.1:8000") ||
		strings.Contains(script, "Authorization") {
		t.Fatal("telemetry browser script gained a risky cross-origin, secret or HTML operation")
	}
	for _, fragment := range []string{".usage-grid", ".reviewer-steps", ".step-content", "@media(max-width:600px)"} {
		if !strings.Contains(css, fragment) {
			t.Fatalf("missing responsive portfolio styling %q", fragment)
		}
	}

	// Read-only provider metrics are anonymous, idle and not inference traffic.
	m := request("/metrics/usage")
	if !strings.Contains(m.Body.String(), `"schema_version":2`) ||
		!strings.Contains(m.Body.String(), `"provider_all_tokens_reported_total":0`) {
		t.Fatalf("provider metrics schema unexpectedly changed: %s", m.Body)
	}
	admission, _ := readAdmissionMetrics(t, handler)
	inference, _ := readInferenceMetrics(t, handler)
	if admission.AdmittedImmediateTotal != 0 || inference.AttemptsTotal != 0 {
		t.Fatal("reviewer console route or usage metrics unexpectedly generated inference")
	}

	// These routes do not become writable or publicly served by enabling the reviewer UI.
	for _, path := range []string{"/console", "/console/app.js", "/metrics/usage"} {
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest(http.MethodPost, path, nil))
		if w.Code != http.StatusMethodNotAllowed {
			t.Fatalf("unexpected POST capability on %s: %d", path, w.Code)
		}
	}
}
