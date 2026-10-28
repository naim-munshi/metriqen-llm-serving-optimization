package httpapi

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// A real operator saw a 502 while native Ollama was unavailable.
// This synthetic upstream test exercises the *body and headers* as well,
// without shutting down a running model or contacting a Mac. It must never
// be claimed as independent real-Mac HTTP 502 attestation.
func TestGatewayDoesNotExposeNon2xxUpstreamErrorBodies(t *testing.T) {
	const secretBody = "PRIVATE_UPSTREAM_TOKEN_AND_PROMPT_9C72814"
	const secretHeader = "PRIVATE_UPSTREAM_DEBUG_HEADER_C6149A"
	cases := []struct {
		upstreamCode int
		gatewayCode  int
		expectedCode string
	}{
		{http.StatusBadRequest, http.StatusBadRequest, "upstream_rejected"},
		{http.StatusNotFound, http.StatusNotFound, "upstream_rejected"},
		{http.StatusTooManyRequests, http.StatusTooManyRequests, "upstream_rejected"},
		{http.StatusInternalServerError, http.StatusBadGateway, "upstream_unavailable"},
		{http.StatusServiceUnavailable, http.StatusBadGateway, "upstream_unavailable"},
	}
	for _, stream := range []bool{false, true} {
		for _, tc := range cases {
			t.Run(fmt.Sprintf("stream_%t_upstream_%d", stream, tc.upstreamCode), func(t *testing.T) {
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.Header().Set("X-Internal-Diagnostic", secretHeader)
					w.WriteHeader(tc.upstreamCode)
					_, _ = io.WriteString(w, `{"error":"`+secretBody+`","debug":"`+secretHeader+`"}`)
				}))
				defer upstream.Close()

				input := `{"model":"test-model","messages":[{"role":"user","content":"probe"}],"max_tokens":16,"stream":`
				if stream {
					input += "true}"
				} else {
					input += "false}"
				}
				result := request(t, newGateway(t, upstream.URL), input, "application/json")
				if result.Code != tc.gatewayCode {
					t.Fatalf("got HTTP %d instead of %d", result.Code, tc.gatewayCode)
				}
				var response struct {
					Error struct {
						Code    string `json:"code"`
						Message string `json:"message"`
					} `json:"error"`
				}
				if err := json.Unmarshal(result.Body.Bytes(), &response); err != nil {
					t.Fatal("gateway must return valid sanitized JSON")
				}
				if response.Error.Code != tc.expectedCode || response.Error.Message == "" {
					t.Fatalf("wrong public error classification: %+v", response.Error)
				}
				for _, secret := range []string{secretBody, secretHeader, "PRIVATE_UPSTREAM_"} {
					if strings.Contains(result.Body.String(), secret) {
						t.Fatal("gateway exposed upstream error body")
					}
					for name, values := range result.Header() {
						if strings.Contains(name, secret) || strings.Contains(strings.Join(values, ","), secret) {
							t.Fatal("gateway exposed upstream diagnostic header")
						}
					}
				}
				if ct := result.Header().Get("Content-Type"); ct != "application/json" {
					t.Fatalf("expected JSON error, got %q", ct)
				}
				if result.Header().Get("X-Internal-Diagnostic") != "" {
					t.Fatal("upstream diagnostic header was forwarded")
				}
				if result.Header().Get("Cache-Control") != "no-store" {
					t.Fatal("error responses must be non-cacheable")
				}
			})
		}
	}
}

func TestGatewayUnreachableLocalUpstreamReturnsSanitized502(t *testing.T) {
	// Close the synthetic loopback listener *before* sending the request.
	// No real Ollama process is involved.
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("request should never reach the already-closed upstream")
	}))
	upstreamURL := upstream.URL
	upstream.Close()

	result := request(t, newGateway(t, upstreamURL), validChat, "application/json")
	if result.Code != http.StatusBadGateway {
		t.Fatalf("got HTTP %d; expected 502 for unavailable upstream", result.Code)
	}
	var response struct {
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal(result.Body.Bytes(), &response); err != nil {
		t.Fatal("unavailable-upstream response must be JSON")
	}
	if response.Error.Code != "upstream_unavailable" ||
		response.Error.Message != "Local inference upstream unavailable" {
		t.Fatalf("unexpected public transport-error body: %+v", response.Error)
	}
	for _, leak := range []string{upstreamURL, "127.0.0.1:", "connection refused", "dial tcp"} {
		if strings.Contains(strings.ToLower(result.Body.String()), strings.ToLower(leak)) {
			t.Fatal("gateway exposed backend network details")
		}
	}
}
