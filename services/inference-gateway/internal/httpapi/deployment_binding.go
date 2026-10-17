package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"regexp"
	"strings"
	"time"
)

// A model binding can only enter the request context after the pinned control
// API returns an exact positive decision. Client headers never create it.
type deploymentBindingKey struct{}

type deploymentBinding struct {
	modelTag string
	digest   string
}

var canonicalModelTag = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,62}:[A-Za-z0-9][A-Za-z0-9._-]{0,62}$`)

func validModelTag(tag string) bool {
	return len(tag) <= maxModelNameSize && canonicalModelTag.MatchString(tag)
}

// A per-request local Ollama /api/ps check blocks stopped/unloaded/retagged
// revisions before forwarding prompts. No external proxies or redirects are
// possible through the existing explicitly pinned upstream HTTP client.
//
// This only proves momentary local runtime identity. Tag retagging between
// this probe and inference is still a TOCTOU risk without immutable artifacts.
func (bridge *inferenceBridge) deploymentModelLoaded(
	ctx context.Context, selected deploymentBinding,
) (bool, error) {
	if bridge == nil || bridge.endpoint == "" || bridge.client == nil {
		return false, errors.New("inference runtime not configured")
	}
	if !validModelTag(selected.modelTag) ||
		!canonicalDigest.MatchString(selected.digest) {
		return false, errors.New("invalid trusted model revision")
	}
	probeCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	path := strings.TrimSuffix(bridge.endpoint, "/v1/chat/completions") + "/api/ps"
	req, err := http.NewRequestWithContext(probeCtx, http.MethodGet, path, nil)
	if err != nil {
		return false, err
	}
	req.Header.Set("Accept", "application/json")
	resp, err := bridge.client.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return false, errors.New("local model status unavailable")
	}
	mediaType, _, err := mime.ParseMediaType(resp.Header.Get("Content-Type"))
	if err != nil || mediaType != "application/json" {
		return false, errors.New("invalid local model status response")
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, 262145))
	if err != nil || len(data) > 262144 {
		return false, errors.New("invalid or oversized model status")
	}
	var result struct {
		Models []struct {
			Name   string `json:"name"`
			Model  string `json:"model"`
			Digest string `json:"digest"`
		} `json:"models"`
	}
	if err := json.Unmarshal(data, &result); err != nil ||
		result.Models == nil || len(result.Models) > 256 {
		return false, errors.New("invalid loaded model list")
	}
	found := false
	for _, item := range result.Models {
		name := item.Name
		if name == "" {
			name = item.Model
		}
		if name != selected.modelTag {
			continue
		}
		if found { // Ambiguous duplicate entries are never positive proof.
			return false, errors.New("ambiguous local model identity")
		}
		found = true
		if item.Digest != selected.digest {
			return false, nil
		}
	}
	return found, nil
}
