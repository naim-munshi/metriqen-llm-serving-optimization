package httpapi

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

// A valid Redis lease can be followed by an incompatible auth decision.
// Rejected requests must return that permit instead of waiting for crash TTL.
func TestRejectedControlDecisionReleasesValidIssuedLeaseExactlyOnce(t *testing.T) {
	lease := `"lease":{"scope":"` + testProjectLeaseScope + `","permit":"` +
		strings.Repeat("b", 32) + `"}`
	tests := []struct {
		name, mode, decision string
	}{
		{"project_mismatch", "", `{"authorized":true,"project_id":"` + projectB + `",` + lease + `}`},
		{"unknown_field", "", `{"authorized":true,"project_id":"` + projectA + `",` + lease + `,"extra":true}`},
		{"trailing_json", "", `{"authorized":true,"project_id":"` + projectA + `",` + lease + `}{}`},
		{"budget_mismatch", "", `{"authorized":true,"project_id":"` + projectA + `",` + lease + `,"output_reservation_required":true}`},
		{"strict_project_mismatch", "1", `{"authorized":true,"project_id":"` + projectB +
			`","deployment":"assistant","model_tag":"llama3.2:1b","model_digest":"` +
			strings.Repeat("a", 64) + `","generation":1,` + lease + `}`},
		{"strict_deployment_mismatch", "1", `{"authorized":true,"project_id":"` + projectA +
			`","deployment":"wrong","model_tag":"llama3.2:1b","model_digest":"` +
			strings.Repeat("a", 64) + `","generation":1,` + lease + `}`},
		{"strict_trailing_json", "1", `{"authorized":true,"project_id":"` + projectA +
			`","deployment":"assistant","model_tag":"llama3.2:1b","model_digest":"` +
			strings.Repeat("a", 64) + `","generation":1,` + lease + `}{}`},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			var released, forwarded atomic.Int32
			control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				if req.Header.Get("X-Metriqen-Gateway-Secret") != projectTestSecret {
					t.Error("gateway service secret missing")
					http.Error(w, "forbidden", http.StatusForbidden)
					return
				}
				switch req.URL.Path {
				case gatewayAuthPath:
					w.Header().Set("Content-Type", "application/json")
					_, _ = io.WriteString(w, tc.decision)
				case gatewayReleasePath:
					if req.Header.Get("Authorization") != "" || req.Header.Get("Cookie") != "" {
						t.Error("release leaked user credentials")
					}
					body, err := io.ReadAll(io.LimitReader(req.Body, 512))
					if err != nil || !strings.Contains(string(body), `"permit":"`+
						strings.Repeat("b", 32)+`"`) ||
						!strings.Contains(string(body), `"scope":"`+testProjectLeaseScope+`"`) {
						t.Errorf("invalid permit release payload: %q", string(body))
						http.Error(w, "invalid", http.StatusBadRequest)
						return
					}
					released.Add(1)
					w.WriteHeader(http.StatusNoContent)
				default:
					http.NotFound(w, req)
				}
			}))
			defer control.Close()
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				forwarded.Add(1)
				http.Error(w, "must not forward", http.StatusInternalServerError)
			}))
			defer upstream.Close()

			handler, err := NewRouterWithInferenceAndSchedulerAndDeploymentAndLeases(
				upstream.URL, "", "", "", control.URL+gatewayAuthPath, projectTestSecret,
				tc.mode, "1")
			if err != nil {
				t.Fatal(err)
			}
			request := projectRequest("user-jwt", projectA)
			if tc.mode == "1" {
				request.Header.Set("X-Metriqen-Deployment", "assistant")
			}
			w := httptest.NewRecorder()
			handler.ServeHTTP(w, request)
			if w.Code != http.StatusServiceUnavailable {
				t.Fatalf("untrusted decision HTTP %d want 503: %s", w.Code, w.Body)
			}
			if forwarded.Load() != 0 {
				t.Fatal("untrusted decision forwarded an inference prompt")
			}
			if released.Load() != 1 {
				t.Fatalf("issued permit must be returned exactly once, got %d", released.Load())
			}
		})
	}
}
