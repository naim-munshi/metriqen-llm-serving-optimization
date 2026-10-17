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
	return NewRouterWithInferenceAndSchedulerAndToken(localUpstreamURL, rawAllowedModels, rawQueueDepth, "")
}

// NewRouterWithInferenceAndSchedulerAndToken optionally requires a single
// operator-configured bearer token on the loopback chat inference endpoint.
// This is local shared-secret defense-in-depth, not production authentication.
func NewRouterWithInferenceAndSchedulerAndToken(localUpstreamURL, rawAllowedModels, rawQueueDepth, rawToken string) (http.Handler, error) {
	return NewRouterWithInferenceAndSchedulerAndAuth(localUpstreamURL, rawAllowedModels, rawQueueDepth, rawToken, "", "")
}

// NewRouterWithInferenceAndSchedulerAndAuth optionally requires per-request,
// database-authoritative project grants via a pinned loopback control API.
// It is mutually exclusive with the legacy local shared-bearer mode.
func NewRouterWithInferenceAndSchedulerAndAuth(localUpstreamURL, rawAllowedModels, rawQueueDepth, rawToken, rawProjectAuthURL, rawProjectAuthSecret string) (http.Handler, error) {
	return NewRouterWithInferenceAndSchedulerAndDeploymentAuth(
		localUpstreamURL, rawAllowedModels, rawQueueDepth, rawToken,
		rawProjectAuthURL, rawProjectAuthSecret, "")
}

// The strict deployment mode is opt-in, requires the project authorization
// seam, and verifies the SQL-authorized model digest before forwarding.
func NewRouterWithInferenceAndSchedulerAndDeploymentAuth(localUpstreamURL, rawAllowedModels, rawQueueDepth, rawToken, rawProjectAuthURL, rawProjectAuthSecret, rawDeploymentMode string) (http.Handler, error) {
	return NewRouterWithInferenceAndSchedulerAndDeploymentAndLeases(
		localUpstreamURL, rawAllowedModels, rawQueueDepth, rawToken,
		rawProjectAuthURL, rawProjectAuthSecret, rawDeploymentMode, "")
}

// Distributed active-project admission is guarded and separately opt-in.
// Legacy localhost router constructors keep their unchanged behavior.
func NewRouterWithInferenceAndSchedulerAndDeploymentAndLeases(localUpstreamURL, rawAllowedModels, rawQueueDepth, rawToken, rawProjectAuthURL, rawProjectAuthSecret, rawDeploymentMode, rawLeaseMode string) (http.Handler, error) {
	token, err := validateLocalBearerToken(rawToken)
	if err != nil {
		return nil, err
	}
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
	if token != "" && bridge.endpoint == "" {
		return nil, errBearerRequiresInference
	}
	projectAuth, err := newLeasedProjectAuthorizer(rawProjectAuthURL, rawProjectAuthSecret, rawDeploymentMode, rawLeaseMode)
	if err != nil {
		return nil, err
	}
	if projectAuth != nil && (token != "" || bridge.endpoint == "") {
		return nil, errBearerRequiresInference
	}
	bridge.allowedModels = allowed
	bridge.admission = newAdmissionScheduler(maxConcurrent, queueDepth)
	mux := http.NewServeMux()
	// The read-only operations console is part of loopback-only inference
	// development, never the externally bound health-only Compose service.
	if bridge.endpoint != "" {
		registerLocalConsole(mux)
	}
	mux.HandleFunc("GET /health/live", health("ok"))
	mux.HandleFunc("GET /health/ready", health("ready"))
	// On-demand dependency reachability is independent of process readiness.
	mux.HandleFunc("GET /health/upstream", bridge.serveUpstreamHealth)
	// Local-only JSON diagnostic endpoint. No model/request identifiers,
	// prompt text, or personally identifying labels leave the scheduler.
	mux.HandleFunc("GET /metrics/admission", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(bridge.admission.snapshot())
	})
	// Separate aggregate error counters from admission decisions. Read-only,
	// process-local; no model names, addresses, or upstream error content.
	mux.HandleFunc("GET /metrics/upstream", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(bridge.upstreamFailures.snapshot())
	})
	// Tracks admitted inference attempt outcomes independently from scheduling
	// and from upstream transport failure causes. No prompts or model labels.
	mux.HandleFunc("GET /metrics/inference", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(bridge.outcomes.snapshot())
	})
	mux.HandleFunc("POST /v1/chat/completions", projectAuth.protect(protectLocalInference(token, bridge.serveChat)))

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
