package httpapi

import (
	"encoding/json"
	"errors"
	"io"
)

const maxNonStreamingResponseBytes = 2 << 20 // 2 MiB; no buffering unbounded upstream JSON.

var (
	errInvalidChatCompletion  = errors.New("invalid upstream chat completion")
	errChatCompletionTooLarge = errors.New("upstream chat completion exceeds size limit")
)

// readValidatedCompletion buffers only bounded non-streaming output before sending
// success headers. Raw upstream error content is never included in returned errors.
// This local development endpoint supports assistant text; tool-call-only replies
// with null content are deliberately not treated as successful text completions.
func readValidatedCompletion(body io.Reader) ([]byte, error) {
	raw, err := io.ReadAll(io.LimitReader(body, maxNonStreamingResponseBytes+1))
	if err != nil {
		return nil, errInvalidChatCompletion
	}
	if len(raw) > maxNonStreamingResponseBytes {
		return nil, errChatCompletionTooLarge
	}
	var data struct {
		Choices []struct {
			Message *struct {
				Content *string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if json.Unmarshal(raw, &data) != nil || len(data.Choices) == 0 || data.Choices[0].Message == nil || data.Choices[0].Message.Content == nil {
		return nil, errInvalidChatCompletion
	}
	return raw, nil
}
