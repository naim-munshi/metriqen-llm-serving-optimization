package httpapi

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
)

const maxNonStreamingResponseBytes = 2 << 20 // 2 MiB; never buffer unbounded JSON.

var (
	errInvalidChatCompletion  = errors.New("invalid upstream chat completion")
	errChatCompletionTooLarge = errors.New("upstream chat completion exceeds size limit")
)

// readValidatedCompletion retains the existing internal contract for callers.
// Provider usage is measured separately without changing HTTP validation.
func readValidatedCompletion(body io.Reader) ([]byte, error) {
	completion, _, err := readValidatedCompletionWithUsage(body, defaultMaxTokens)
	return completion, err
}

// readValidatedCompletionWithUsage decodes bounded upstream JSON exactly once.
// Optional usage numbers never affect HTTP success and are not persisted.
// No user text or model output content is kept for metrics.
func readValidatedCompletionWithUsage(body io.Reader, outputCap int) ([]byte, providerUsageObservation, error) {
	empty := providerUsageObservation{kind: providerUsageUnavailable}
	raw, err := io.ReadAll(io.LimitReader(body, maxNonStreamingResponseBytes+1))
	if err != nil {
		return nil, empty, errInvalidChatCompletion
	}
	if len(raw) > maxNonStreamingResponseBytes {
		return nil, empty, errChatCompletionTooLarge
	}
	var data struct {
		Choices []struct {
			Message *struct {
				Content *string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
		Usage json.RawMessage `json:"usage"`
	}
	if json.Unmarshal(raw, &data) != nil || len(data.Choices) == 0 ||
		data.Choices[0].Message == nil || data.Choices[0].Message.Content == nil {
		return nil, empty, errInvalidChatCompletion
	}
	return raw, classifyProviderUsage(data.Usage, outputCap), nil
}

// The Ollama/OpenAI-compatible 'usage' envelope is a self-report; it is
// classified as internally consistent ONLY if three exact integer fields
// exist and total == prompt + completion. Real tokenizer accounting is out
// of scope; even consistent claims can be false, inaccurate, or spoofed.
// Unknown nested 'usage' fields are ignored (never stored).
func classifyProviderUsage(raw json.RawMessage, outputCap int) providerUsageObservation {
	result := providerUsageObservation{kind: providerUsageMalformed}
	if len(raw) == 0 {
		result.kind = providerUsageMissing
		return result
	}
	if outputCap < 1 || outputCap > maxOutputTokens {
		return result
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil || fields == nil {
		return result
	}
	parse := func(key string) (int64, bool) {
		value, ok := fields[key]
		if !ok || len(value) == 0 {
			return 0, false
		}
		// encoding/json treats literal JSON null as a successful decode
		// into a zero-valued int64. Reject it explicitly: missing usage
		// cannot masquerade as an actual zero-token observation.
		if bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return 0, false
		}
		var number int64
		if err := json.Unmarshal(value, &number); err != nil || number < 0 {
			return 0, false
		}
		return number, true
	}
	prompt, promptOK := parse("prompt_tokens")
	completion, completionOK := parse("completion_tokens")
	total, totalOK := parse("total_tokens")
	if !promptOK || !completionOK || !totalOK {
		return result
	}
	if prompt > maxReportedPromptTokens || completion > maxOutputTokens ||
		total > maxReportedPromptTokens+maxOutputTokens ||
		total != prompt+completion {
		result.kind = providerUsageInconsistent
		return result
	}
	if completion > int64(outputCap) {
		result.kind = providerUsageExceedsCap
		return result
	}
	result.kind = providerUsagePlausible
	result.promptTokens = uint64(prompt)
	result.completionTokens = uint64(completion)
	result.totalTokens = uint64(total)
	return result
}
