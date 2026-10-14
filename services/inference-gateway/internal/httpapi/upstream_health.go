package httpapi

import (
	"context"
	"encoding/json"
	"io"
	"mime"
	"net/http"
	"strings"
	"time"
)

const (
	upstreamHealthTimeout   = 2 * time.Second
	maxHealthResponseBytes = 4096
)

// upstreamHealth is a narrow, on-demand Ollama HTTP API reachability probe.
// It does not load a model, generate tokens, or establish model readiness.
type upstreamHealth struct {
	Status string `json:"status"`
	Probe  string `json:"probe"`
}

func (bridge *inferenceBridge) serveUpstreamHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	writeHealth := func(statusCode int, status string) {
		w.WriteHeader(statusCode)
		_ = json.NewEncoder(w).Encode(upstreamHealth{
			Status: status,
			Probe:  "ollama_api_version",
		})
	}

	if bridge.endpoint == "" {
		writeHealth(http.StatusServiceUnavailable, "disabled")
		return
	}

	// The origin is validated during gateway configuration. No HTTP
	// request parameter, client header, or user-supplied URL is forwarded.
	origin := strings.TrimSuffix(bridge.endpoint, "/v1/chat/completions")
	ctx, cancel := context.WithTimeout(r.Context(), upstreamHealthTimeout)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, origin+"/api/version", nil)
	if err != nil {
		writeHealth(http.StatusServiceUnavailable, "unreachable")
		return
	}

	// The existing loopback client disables redirects and proxy forwarding.
	response, err := bridge.client.Do(request)
	if err != nil {
		writeHealth(http.StatusServiceUnavailable, "unreachable")
		return
	}
	defer response.Body.Close()

	if response.StatusCode != http.StatusOK {
		writeHealth(http.StatusServiceUnavailable, "unreachable")
		return
	}
	mediaType, _, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if err != nil || mediaType != "application/json" {
		writeHealth(http.StatusServiceUnavailable, "unreachable")
		return
	}
	raw, err := io.ReadAll(io.LimitReader(response.Body, maxHealthResponseBytes+1))
	if err != nil || len(raw) > maxHealthResponseBytes {
		writeHealth(http.StatusServiceUnavailable, "unreachable")
		return
	}
	var version struct {
		Version string `json:"version"`
	}
	if json.Unmarshal(raw, &version) != nil || strings.TrimSpace(version.Version) == "" {
		writeHealth(http.StatusServiceUnavailable, "unreachable")
		return
	}
	writeHealth(http.StatusOK, "reachable")
}
