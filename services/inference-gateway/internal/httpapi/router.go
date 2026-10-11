// Package httpapi serves process health checks and a local-only inference bridge.
package httpapi

import (
	"encoding/json"
	"net/http"
)

// NewRouter remains useful for health-only tests and safe default startup.
func NewRouter() http.Handler {
	handler, _ := NewRouterWithInference("")
	return handler
}

// NewRouterWithInference never accepts upstream URLs from HTTP requests.
// Non-loopback listener deployment needs authentication before use.
func NewRouterWithInference(localUpstreamURL string) (http.Handler, error) {
	bridge, err := newInferenceBridge(localUpstreamURL)
	if err != nil {
		return nil, err
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health/live", health("ok"))
	mux.HandleFunc("GET /health/ready", health("ready"))
	mux.HandleFunc("POST /v1/chat/completions", bridge.serveChat)

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		mux.ServeHTTP(w, r)
	}), nil
}

func health(status string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(struct {
			Service string `json:"service"`
			Status  string `json:"status"`
		}{Service: "metriqen-inference-gateway", Status: status})
	}
}
