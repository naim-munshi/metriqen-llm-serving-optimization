package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const (
	maxRequestBytes  = 1 << 20
	maxMessages      = 32
	maxModelNameSize = 128
	maxOutputTokens  = 2048
	defaultMaxTokens = 256
	maxConcurrent    = 4
	maxStreamedBytes = 8 << 20 // Per-request streaming response byte budget.
	requestDeadline  = 120 * time.Second
)

// inferenceBridge deliberately has no API authentication. It MUST remain on a
// loopback-only development listener until authentication and quotas are wired.
type inferenceBridge struct {
	endpoint         string
	client           *http.Client
	admission        *admissionScheduler
	allowedModels    map[string]struct{}
	upstreamFailures *upstreamFailureCounters
}

func newInferenceBridge(rawURL string) (*inferenceBridge, error) {
	bridge := &inferenceBridge{admission: newAdmissionScheduler(maxConcurrent, 0), upstreamFailures: &upstreamFailureCounters{}}
	if strings.TrimSpace(rawURL) == "" {
		return bridge, nil // disabled unless explicitly configured
	}
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Scheme != "http" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || parsed.Path != "" || parsed.RawPath != "" {
		return nil, errors.New("upstream must be an HTTP origin without path or credentials")
	}
	if parsed.Hostname() != "localhost" && parsed.Hostname() != "127.0.0.1" && parsed.Hostname() != "::1" && parsed.Hostname() != "ollama" {
		return nil, errors.New("only local development inference upstreams are supported")
	}
	port, err := strconv.Atoi(parsed.Port())
	if err != nil || port <= 0 || port > 65535 {
		return nil, errors.New("upstream must have a valid explicit port")
	}
	// Route never accepts a user-provided URL. Redirects and environment proxies
	// are disabled to avoid forwarding private prompt content elsewhere.
	bridge.endpoint = parsed.String() + "/v1/chat/completions"
	bridge.client = &http.Client{
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse },
		Transport: &http.Transport{
			Proxy:                 nil,
			DialContext:           (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
			MaxIdleConnsPerHost:   maxConcurrent,
			ResponseHeaderTimeout: 30 * time.Second,
			IdleConnTimeout:       30 * time.Second,
		},
	}
	return bridge, nil
}

func (bridge *inferenceBridge) serveChat(w http.ResponseWriter, r *http.Request) {
	if bridge.endpoint == "" {
		writeError(w, http.StatusServiceUnavailable, "inference_disabled", "Configure a local inference upstream first")
		return
	}
	mediaType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || mediaType != "application/json" {
		writeError(w, http.StatusUnsupportedMediaType, "invalid_content_type", "Content-Type must be application/json")
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, maxRequestBytes+1))
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", "Unable to read request")
		return
	}
	if len(body) > maxRequestBytes {
		writeError(w, http.StatusRequestEntityTooLarge, "request_too_large", "Request exceeds 1 MiB")
		return
	}
	validated, streaming, err := validateChatRequest(body)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	if len(bridge.allowedModels) > 0 {
		// The request has already passed bounded JSON/model validation. Read
		// only its model field and reject disallowed tags before taking capacity
		// or sending prompt content to an upstream runtime.
		var fields map[string]json.RawMessage
		var requestedModel string
		if err := json.Unmarshal(validated, &fields); err != nil {
			writeError(w, http.StatusBadRequest, "invalid_request", "Invalid model selection")
			return
		}
		if err := json.Unmarshal(fields["model"], &requestedModel); err != nil {
			writeError(w, http.StatusBadRequest, "invalid_request", "Invalid model selection")
			return
		}
		if _, allowed := bridge.allowedModels[requestedModel]; !allowed {
			writeError(w, http.StatusForbidden, "model_not_allowed", "Model is not allowed by this local gateway")
			return
		}
	}

	// The default queue depth is zero: preserve immediate HTTP 429 on
	// saturation. Positive opt-in depths wait briefly, first-in-first-out.
	admissionCtx := r.Context()
	if bridge.admission.maxQueued > 0 {
		var cancelWait context.CancelFunc
		admissionCtx, cancelWait = context.WithTimeout(r.Context(), queueWaitTimeout)
		defer cancelWait()
	}
	if err := bridge.admission.acquire(admissionCtx); err != nil {
		switch {
		case errors.Is(err, context.Canceled):
			return // Disconnected while queued; no upstream request sent.
		case errors.Is(err, context.DeadlineExceeded):
			w.Header().Set("Retry-After", "1")
			writeError(w, http.StatusTooManyRequests, "queue_wait_timeout", "Local inference queue wait expired")
		default:
			w.Header().Set("Retry-After", "1")
			writeError(w, http.StatusTooManyRequests, "capacity_exceeded", "Local inference concurrency limit reached")
		}
		return
	}
	defer bridge.admission.release()

	ctx, cancel := context.WithTimeout(r.Context(), requestDeadline)
	defer cancel()
	outbound, err := http.NewRequestWithContext(ctx, http.MethodPost, bridge.endpoint, bytes.NewReader(validated))
	if err != nil {
		writeError(w, http.StatusBadGateway, "upstream_unavailable", "Cannot create inference request")
		return
	}
	outbound.Header.Set("Content-Type", "application/json")
	if streaming {
		outbound.Header.Set("Accept", "text/event-stream")
	} else {
		outbound.Header.Set("Accept", "application/json")
	}
	// Never forward Authorization, Cookie, Host, or arbitrary client headers.
	response, err := bridge.client.Do(outbound)
	if err != nil {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			bridge.upstreamFailures.record(upstreamTimeout)
			writeError(w, http.StatusGatewayTimeout, "inference_timeout", "Inference request timed out")
		} else if errors.Is(ctx.Err(), context.Canceled) {
			return // Client disconnected; do not write further.
		} else {
			bridge.upstreamFailures.record(classifyUpstreamTransport(err))
			writeError(w, http.StatusBadGateway, "upstream_unavailable", "Local inference upstream unavailable")
		}
		return
	}
	defer response.Body.Close()

	if response.StatusCode < 200 || response.StatusCode >= 300 {
		switch response.StatusCode {
		case http.StatusBadRequest, http.StatusNotFound, http.StatusTooManyRequests:
			bridge.upstreamFailures.record(upstreamHTTPRejected)
			writeError(w, response.StatusCode, "upstream_rejected", "Inference runtime rejected the request")
		default:
			bridge.upstreamFailures.record(upstreamHTTPError)
			writeError(w, http.StatusBadGateway, "upstream_unavailable", "Inference runtime returned an error")
		}
		return
	}
	contentType, _, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if err != nil || (streaming && contentType != "text/event-stream") || (!streaming && contentType != "application/json") {
		bridge.upstreamFailures.record(upstreamProtocolError)
		writeError(w, http.StatusBadGateway, "upstream_protocol", "Inference runtime returned an unexpected response format")
		return
	}

	// Validate bounded JSON before sending HTTP 200; malformed or oversized
	// upstream output must not be classified as a successful completion.
	if !streaming {
		completion, err := readValidatedCompletion(response.Body)
		if err != nil {
			if errors.Is(ctx.Err(), context.DeadlineExceeded) {
				bridge.upstreamFailures.record(upstreamTimeout)
				writeError(w, http.StatusGatewayTimeout, "inference_timeout", "Inference request timed out")
			} else if errors.Is(ctx.Err(), context.Canceled) {
				return
			} else if errors.Is(err, errChatCompletionTooLarge) {
				bridge.upstreamFailures.record(upstreamResponseTooLarge)
				writeError(w, http.StatusBadGateway, "upstream_response_too_large", "Inference response exceeded the size limit")
			} else {
				bridge.upstreamFailures.record(upstreamProtocolError)
				writeError(w, http.StatusBadGateway, "upstream_protocol", "Inference runtime returned an invalid completion")
			}
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(completion)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)

	// Do not buffer SSE until completion; flush after each read and honor
	// cancellation through the upstream request context.
	flusher, ok := w.(http.Flusher)
	if !ok {
		return
	}
	buffer := make([]byte, 1024)
	forwarded := 0
	for {
		n, readErr := response.Body.Read(buffer)
		if n > 0 {
			// The HTTP 200 streaming headers are already sent. Terminate an
			// oversized stream rather than forwarding unbounded upstream data.
			// Clients must reject streams without a completed [DONE] event.
			if forwarded+n > maxStreamedBytes {
				bridge.upstreamFailures.record(upstreamStreamTooLarge)
				return
			}
			forwarded += n
			if _, err := w.Write(buffer[:n]); err != nil {
				return
			}
			flusher.Flush()
		}
		if readErr != nil {
			if errors.Is(ctx.Err(), context.DeadlineExceeded) {
				bridge.upstreamFailures.record(upstreamTimeout)
			} else if !errors.Is(ctx.Err(), context.Canceled) && !errors.Is(readErr, io.EOF) {
				bridge.upstreamFailures.record(upstreamStreamReadError)
			}
			return
		}
	}
}

func validateChatRequest(body []byte) ([]byte, bool, error) {
	var payload map[string]json.RawMessage
	if err := json.Unmarshal(body, &payload); err != nil || payload == nil {
		return nil, false, errors.New("Request must be a JSON object")
	}
	var model string
	if err := json.Unmarshal(payload["model"], &model); err != nil || strings.TrimSpace(model) == "" || len(model) > maxModelNameSize {
		return nil, false, errors.New("A non-empty model name is required")
	}
	var messages []json.RawMessage
	if err := json.Unmarshal(payload["messages"], &messages); err != nil || len(messages) == 0 || len(messages) > maxMessages {
		return nil, false, fmt.Errorf("messages must contain between 1 and %d entries", maxMessages)
	}
	for _, message := range messages {
		var m map[string]json.RawMessage
		if err := json.Unmarshal(message, &m); err != nil || m == nil {
			return nil, false, errors.New("Each message must be an object")
		}
		var role string
		if err := json.Unmarshal(m["role"], &role); err != nil || strings.TrimSpace(role) == "" {
			return nil, false, errors.New("Each message requires a role")
		}
		if _, ok := m["content"]; !ok {
			return nil, false, errors.New("Each message requires content")
		}
	}
	var streaming bool
	if raw, ok := payload["stream"]; ok {
		if err := json.Unmarshal(raw, &streaming); err != nil {
			return nil, false, errors.New("stream must be a boolean")
		}
	}
	if raw, ok := payload["max_tokens"]; ok {
		var tokens int
		if err := json.Unmarshal(raw, &tokens); err != nil || tokens < 1 || tokens > maxOutputTokens {
			return nil, false, fmt.Errorf("max_tokens must be between 1 and %d", maxOutputTokens)
		}
	} else {
		payload["max_tokens"] = json.RawMessage(strconv.Itoa(defaultMaxTokens))
	}
	serialized, err := json.Marshal(payload)
	return serialized, streaming, err
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"error": map[string]string{"code": code, "message": message},
	})
}
