package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHealthRoutes(t *testing.T) {
	cases := []struct {
		path   string
		status string
	}{
		{"/health/live", "ok"},
		{"/health/ready", "ready"},
	}
	for _, tc := range cases {
		t.Run(tc.path, func(t *testing.T) {
			r := httptest.NewRequest(http.MethodGet, tc.path, nil)
			w := httptest.NewRecorder()
			NewRouter().ServeHTTP(w, r)
			if w.Code != http.StatusOK {
				t.Fatalf("got HTTP %d; want 200", w.Code)
			}
			if w.Header().Get("Cache-Control") != "no-store" {
				t.Fatal("health endpoints must not be cached")
			}
			var body struct{ Service, Status string }
			if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			if body.Service != "metriqen-inference-gateway" || body.Status != tc.status {
				t.Fatalf("unexpected health body: %+v", body)
			}
		})
	}
}

func TestInferenceDisabledByDefault(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	w := httptest.NewRecorder()
	NewRouter().ServeHTTP(w, r)
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("got HTTP %d; want 503", w.Code)
	}
}

func TestHealthDoesNotAcceptPost(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/health/live", nil)
	w := httptest.NewRecorder()
	NewRouter().ServeHTTP(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("got HTTP %d; want 405", w.Code)
	}
}
