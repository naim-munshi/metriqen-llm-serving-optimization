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
	return NewRouterWithInferenceAndModels(localUpstreamURL, "")
}

// NewRouterWithInferenceAndModels filters requested model tags only when an
// explicit allowlist is supplied. It does not authenticate the caller or pin
// Ollama model digests; the listener must still remain local-only.
func NewRouterWithInferenceAndModels(localUpstreamURL, rawAllowedModels string) (http.Handler, error) {
	return NewRouterWithInferenceAndScheduler(localUpstreamURL, rawAllowedModels, "")
}

// NewRouterWithInferenceAndScheduler optionally enables a bounded FIFO wait
// queue. Zero/empty queue depth preserves the original immediate-429 behavior.
// This is a process-local admission control, NOT GPU batching or multi-tenant
// resource scheduling; all inference remains restricted to loopback.
func NewRouterWithInferenceAndScheduler(localUpstreamURL, rawAllowedModels, rawQueueDepth string) (http.Handler, error) {
	allowed, err := parseAllowedModels(rawAllowedModels)
	if err != nil {
		return nil, err
	}
	queueDepth, err := parseQueueDepth(rawQueueDepth)
	if err != nil {
		return nil, err
	}
	bridge, err := newInferenceBridge(localUpstreamURL)
	if err != nil {
		return nil, err
	}
	bridge.allowedModels = allowed
	bridge.admission = newAdmissionScheduler(maxConcurrent, queueDepth)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health/live", health("ok"))
	mux.HandleFunc("GET /health/ready", health("ready"))
	// Local-only JSON diagnostic endpoint. No model/request identifiers,
	// prompt text, or personally identifying labels leave the scheduler.
	mux.HandleFunc("GET /metrics/admission", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(bridge.admission.snapshot())
	})
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
