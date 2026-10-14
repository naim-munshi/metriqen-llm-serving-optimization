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
