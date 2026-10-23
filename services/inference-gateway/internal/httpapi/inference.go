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
	"os"
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

// Keep the existing 1 KiB runtime behavior unless the *operator* opts in.
// 4 KiB might reduce buffered burst read/write/flush cycles; no real
// inference performance or TTFT improvement has been established.
const (
	defaultSSERelayReadBufferBytes   = 1024
	candidateSSERelayReadBufferBytes = 4096
)

func parseSSERelayReadBufferBytes(raw string) (int, error) {
	switch raw {
	case "":
		return defaultSSERelayReadBufferBytes, nil
	case "4096":
		return candidateSSERelayReadBufferBytes, nil
	default:
		return 0, errors.New("SSE relay buffer must be unset or exactly 4096 bytes")
	}
}

// inferenceBridge deliberately has no API authentication. It MUST remain on a
// loopback-only development listener until authentication and quotas are wired.
type inferenceBridge struct {
	endpoint                string
	client                  *http.Client
	admission               *admissionScheduler
	allowedModels           map[string]struct{}
	upstreamFailures        *upstreamFailureCounters
	outcomes                *inferenceOutcomeCounters
	usage                   *providerUsageCounters
	sseRelayReadBufferBytes int
}

func newInferenceBridge(rawURL string) (*inferenceBridge, error) {
	readBufferBytes, err := parseSSERelayReadBufferBytes(os.Getenv("METRIQEN_SSE_RELAY_BUFFER_BYTES"))
	if err != nil {
		return nil, err
	}
	bridge := &inferenceBridge{
		admission: newAdmissionScheduler(maxConcurrent, 0),
		upstreamFailures: &upstreamFailureCounters{},
		outcomes: &inferenceOutcomeCounters{},
		usage: &providerUsageCounters{},
		sseRelayReadBufferBytes: readBufferBytes,
	}
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
	validated, streaming, requestedModel, requestedOutputCap, err := validateChatRequestWithBudget(body)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	// For strict project/deployment routing, compare the *validated JSON
	// model* to the SQL-authorized model bound to this request context. A
	// forged header cannot authorize a different model in the prompt body.
	if binding, present := r.Context().Value(deploymentBindingKey{}).(deploymentBinding); present {
		// This value comes directly from the JSON map parsed and validated
		// by validateChatRequestWithModel. It is the same canonical value
		// sent upstream, even for duplicate input JSON keys.
		if requestedModel != binding.modelTag {
			writeError(w, http.StatusForbidden, "deployment_model_mismatch",
				"Requested model does not match authorized deployment")
			return
		}
		matched, err := bridge.deploymentModelLoaded(r.Context(), binding)
		if err != nil {
			writeError(w, http.StatusServiceUnavailable, "model_attestation_unavailable",
				"Local model identity verification unavailable")
			return
		}
		if !matched {
			writeError(w, http.StatusConflict, "deployment_model_unavailable",
				"Authorized model digest is not currently loaded")
			return
		}
	}
	if len(bridge.allowedModels) > 0 {
		// Reuse the verified model across both allowlist and deployment
		// guards: no second/third JSON parse of the complete request body.
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

	// Reserve the full validated output max_tokens cap, not an untrusted
	// model's self-reported usage. No refunds: an upstream error, rejected
	// prompt or disconnected client conservatively spends the entire cap.
	// This happens after local admission, before any prompt reaches Ollama.
	if budget, ok := r.Context().Value(projectOutputReservationKey{}).(projectOutputReservation); ok {
		switch budget.reserveOutput(r.Context(), requestedOutputCap) {
		case 0:
			// Atomically budgeted by one live authenticated inference lease.
		case http.StatusTooManyRequests:
			w.Header().Set("Retry-After", "60")
			writeError(w, http.StatusTooManyRequests, "project_output_budget_exceeded",
				"Project requested output token budget exceeded")
			return
		case http.StatusConflict:
			writeError(w, http.StatusConflict, "project_reservation_invalid",
				"Inference permit is expired or already reserved")
			return
		default:
			writeError(w, http.StatusServiceUnavailable, "output_reservation_unavailable",
				"Project output token reservation unavailable")
			return
		}
	}

	// Exactly one terminal outcome per successfully admitted inference attempt.
	// Invalid inputs, model allowlist failures and admission 429s are excluded.
	attemptStarted := time.Now() // After admission: FIFO queue wait is excluded.
	bridge.outcomes.start()
	outcome := inferencePreResponseFailure
	streamDone := false
	usage := providerUsageObservation{kind: providerUsageUnavailable}
	defer func() {
		bridge.outcomes.finish(outcome, streamDone, time.Since(attemptStarted))
		// A classified usage observation is always recorded for each
		// *completed admitted* attempt, including transport failures.
		bridge.usage.recordWithStreamResult(streaming, outcome, streamDone, usage)
	}()

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
			outcome = inferenceClientCanceled
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
		completion, candidateUsage, err := readValidatedCompletionWithUsage(response.Body, requestedOutputCap)
		if err != nil {
			if errors.Is(ctx.Err(), context.DeadlineExceeded) {
				bridge.upstreamFailures.record(upstreamTimeout)
				writeError(w, http.StatusGatewayTimeout, "inference_timeout", "Inference request timed out")
			} else if errors.Is(ctx.Err(), context.Canceled) {
				outcome = inferenceClientCanceled
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
		written, writeErr := w.Write(completion)
		if writeErr != nil || written != len(completion) {
			outcome = inferencePostResponseInterrupted
			if errors.Is(r.Context().Err(), context.Canceled) {
				outcome = inferenceClientCanceled
			}
			return
		}
		outcome = inferenceNonstreamComplete
		usage = candidateUsage // Only count provider-reported figures on a complete write.
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	outcome = inferencePostResponseInterrupted // Until a clean upstream EOF is observed.

	// Do not buffer SSE until completion; flush after each read and honor
	// cancellation through the upstream request context.
	flusher, ok := w.(http.Flusher)
	if !ok {
		return
	}
	buffer := make([]byte, bridge.sseRelayReadBufferBytes)
	forwarded := 0
	var terminal sseTerminalTracker
	usageTracker := newSSEProviderUsageTracker(requestedOutputCap)
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
			written, writeErr := w.Write(buffer[:n])
			if writeErr != nil || written != n {
				if errors.Is(r.Context().Err(), context.Canceled) {
					outcome = inferenceClientCanceled
				}
				return
			}
			flusher.Flush()
			// Observe only bytes successfully forwarded to the caller.
			// The tracker stores a constant number of bytes, not model text.
			terminal.observe(buffer[:n])
			// Bounded usage metadata observer reads only bytes already
			// successfully forwarded. It cannot mark untrusted data as
			// billable usage, nor reinterpret missing/invalid [DONE].
			usageTracker.observe(buffer[:n])
		}
		if readErr != nil {
			if errors.Is(r.Context().Err(), context.Canceled) {
				outcome = inferenceClientCanceled
			} else if errors.Is(ctx.Err(), context.DeadlineExceeded) {
				bridge.upstreamFailures.record(upstreamTimeout)
			} else if errors.Is(readErr, io.EOF) {
				// Preserve transport-EOF accounting; v2 metrics explicitly
				// distinguish an SSE terminal marker from a truncated stream.
				outcome = inferenceStreamCleanEOF
				streamDone = terminal.completed()
				usage = usageTracker.result(streamDone)
			} else {
				bridge.upstreamFailures.record(upstreamStreamReadError)
			}
			return
		}
	}
}

// validateChatRequest keeps the previous internal API for contract tests and
// integrations. Serving uses the detail variant to avoid repeated JSON model
// decoding with both strict deployment authorization and model allowlists.
func validateChatRequest(body []byte) ([]byte, bool, error) {
	validated, streaming, _, err := validateChatRequestWithModel(body)
	return validated, streaming, err
}

// validateChatRequestWithModel returns the same model selected by the first
// JSON parse that produces the sanitized forwarded payload. Model selection
// is never trusted from a client header, authorization claim, or cached value.
func validateChatRequestWithModel(body []byte) ([]byte, bool, string, error) {
	validated, streaming, model, _, err := validateChatRequestWithBudget(body)
	return validated, streaming, model, err
}

// One JSON parse provides the canonical forwarded model and max_tokens cap,
// including the server-inserted default of 256 for missing max_tokens.
// Both strings and numbers always refer to exactly the sanitized JSON.
func validateChatRequestWithBudget(body []byte) ([]byte, bool, string, int, error) {
	var payload map[string]json.RawMessage
	if err := json.Unmarshal(body, &payload); err != nil || payload == nil {
		return nil, false, "", 0, errors.New("Request must be a JSON object")
	}
	var model string
	if err := json.Unmarshal(payload["model"], &model); err != nil || strings.TrimSpace(model) == "" || len(model) > maxModelNameSize {
		return nil, false, "", 0, errors.New("A non-empty model name is required")
	}
	var messages []json.RawMessage
	if err := json.Unmarshal(payload["messages"], &messages); err != nil || len(messages) == 0 || len(messages) > maxMessages {
		return nil, false, "", 0, fmt.Errorf("messages must contain between 1 and %d entries", maxMessages)
	}
	for _, message := range messages {
		var m map[string]json.RawMessage
		if err := json.Unmarshal(message, &m); err != nil || m == nil {
			return nil, false, "", 0, errors.New("Each message must be an object")
		}
		var role string
		if err := json.Unmarshal(m["role"], &role); err != nil || strings.TrimSpace(role) == "" {
			return nil, false, "", 0, errors.New("Each message requires a role")
		}
		if _, ok := m["content"]; !ok {
			return nil, false, "", 0, errors.New("Each message requires content")
		}
	}
	var streaming bool
	if raw, ok := payload["stream"]; ok {
		if err := json.Unmarshal(raw, &streaming); err != nil {
			return nil, false, "", 0, errors.New("stream must be a boolean")
		}
	}
	requestedOutputCap := defaultMaxTokens
	if raw, ok := payload["max_tokens"]; ok {
		var tokens int
		if err := json.Unmarshal(raw, &tokens); err != nil || tokens < 1 || tokens > maxOutputTokens {
			return nil, false, "", 0, fmt.Errorf("max_tokens must be between 1 and %d", maxOutputTokens)
		}
		requestedOutputCap = tokens
	} else {
		payload["max_tokens"] = json.RawMessage(strconv.Itoa(defaultMaxTokens))
	}
	serialized, err := json.Marshal(payload)
	return serialized, streaming, model, requestedOutputCap, err
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"error": map[string]string{"code": code, "message": message},
	})
}
