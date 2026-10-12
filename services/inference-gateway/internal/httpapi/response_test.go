package httpapi

import (
	"errors"
	"strings"
	"testing"
)

func TestReadValidatedCompletion(t *testing.T) {
	valid := `{"id":"chatcmpl-1","choices":[{"message":{"role":"assistant","content":"hello"}}],"usage":{"completion_tokens":1}}`
	got, err := readValidatedCompletion(strings.NewReader(valid))
	if err != nil || string(got) != valid {
		t.Fatalf("want original JSON, got %q: %v", got, err)
	}
	for _, input := range []string{
		``, `not json`, `[]`, `{}`, `{"choices":[]}`, `{"error":"secret"}`,
		`{"choices":[{}]}`, `{"choices":[{"message":null}]}`,
		`{"choices":[{"message":{"content":null}}]}`,
		`{"choices":[{"message":{"content":12}}]}`,
		`{"choices":[{"message":{"content":"valid"}}]} {"secret":"second"}`,
	} {
		if _, err := readValidatedCompletion(strings.NewReader(input)); !errors.Is(err, errInvalidChatCompletion) {
			t.Errorf("expected rejection for %q, got %v", input, err)
		}
	}
}

func TestReadValidatedCompletionTooLarge(t *testing.T) {
	input := `{"choices":[{"message":{"content":"` + strings.Repeat("x", maxNonStreamingResponseBytes) + `"}}]}`
	if _, err := readValidatedCompletion(strings.NewReader(input)); !errors.Is(err, errChatCompletionTooLarge) {
		t.Fatal(err)
	}
}

func TestReadValidatedCompletionReadError(t *testing.T) {
	if _, err := readValidatedCompletion(errorReader{}); !errors.Is(err, errInvalidChatCompletion) {
		t.Fatal(err)
	}
}

type errorReader struct{}

func (errorReader) Read(p []byte) (int, error) { return 0, errors.New("secret upstream read error") }
