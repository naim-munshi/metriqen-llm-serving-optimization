package httpapi

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
)

// All fixtures are synthetic: no private prompt text, user token, Ollama,
// network service, GPU, or real model performance is involved.
var selectionFixture = []byte(`{"model":"llama3.2:1b","messages":[{"role":"system","content":"` +
	strings.Repeat("S", 1024) + `"},{"role":"user","content":"` +
	strings.Repeat("test only ", 160) + `"}],"temperature":0,"stream":true}`)

var modelSelectionSink string
var selectionAllowedSink bool
var selectionBytesSink []byte

// Mirrors the previous production path: validation plus a full re-decode
// for the strict deployment and another full re-decode for the allowlist.
// Measured only to compare equivalent CPU-side model-selection work.
func baselineModelSelection(body []byte, expected string, allowlist map[string]struct{}) ([]byte, bool, bool, error) {
	validated, streaming, err := validateChatRequest(body)
	if err != nil {
		return nil, false, false, err
	}
	var fields map[string]json.RawMessage
	var boundModel string
	if err := json.Unmarshal(validated, &fields); err != nil {
		return nil, false, false, err
	}
	if err := json.Unmarshal(fields["model"], &boundModel); err != nil {
		return nil, false, false, err
	}
	if boundModel != expected {
		return validated, streaming, false, nil
	}
	var allowlistFields map[string]json.RawMessage
	var allowlistModel string
	if err := json.Unmarshal(validated, &allowlistFields); err != nil {
		return nil, false, false, err
	}
	if err := json.Unmarshal(allowlistFields["model"], &allowlistModel); err != nil {
		return nil, false, false, err
	}
	_, allowed := allowlist[allowlistModel]
	return validated, streaming, allowed, nil
}

func singlePassModelSelection(body []byte, expected string, allowlist map[string]struct{}) ([]byte, bool, bool, error) {
	validated, streaming, selected, err := validateChatRequestWithModel(body)
	if err != nil {
		return nil, false, false, err
	}
	_, allowed := allowlist[selected]
	return validated, streaming, allowed && selected == expected, nil
}

func TestSinglePassModelSelectionPreservesCanonicalModelAndForwardedJSON(t *testing.T) {
	fixtures := []struct {
		name, body string
	}{
		{"explicit model", string(selectionFixture)},
		{"duplicate model last wins", `{"model":"wrong:model","model":"llama3.2:1b","messages":[{"role":"user","content":"test"}]}`},
		{"duplicate model invalid last", `{"model":"llama3.2:1b","model":null,"messages":[{"role":"user","content":"test"}]}`},
		{"escaped key and model", `{"\u006dodel":"llama3.2:1b","messages":[{"role":"user","content":"test"}]}`},
		{"unicode in content", `{"model":"llama3.2:1b","messages":[{"role":"user","content":"日本語 বাংলা"}],"max_tokens":128}`},
		{"boolean stream", `{"model":"llama3.2:1b","messages":[{"role":"user","content":"hi"}],"stream":false}`},
		{"model header injection", `{"model":"llama3.2:1b\\nX-Forwarded","messages":[{"role":"user","content":"test"}]}`},
		{"malformed JSON", `{"model":"llama3.2:1b",`},
	}
	allow := map[string]struct{}{"llama3.2:1b": {}}
	for _, fixture := range fixtures {
		t.Run(fixture.name, func(t *testing.T) {
			body := []byte(fixture.body)
			expectedBody, expectedStream, expectedAllowed, oldErr := baselineModelSelection(
				body, "llama3.2:1b", allow)
			newBody, newStream, newAllowed, newErr := singlePassModelSelection(
				body, "llama3.2:1b", allow)
			if (oldErr == nil) != (newErr == nil) {
				t.Fatalf("validation success differs: baseline=%v new=%v", oldErr, newErr)
			}
			if oldErr != nil {
				if newErr.Error() != oldErr.Error() {
					t.Fatalf("validation error changed: %v vs %v", oldErr, newErr)
				}
				return
			}
			if !bytes.Equal(expectedBody, newBody) ||
				expectedStream != newStream || expectedAllowed != newAllowed {
				t.Fatalf("selected model or forwarded JSON changed: %v / %v, %t / %t",
					expectedBody, newBody, expectedAllowed, newAllowed)
			}
			_, _, model, err := validateChatRequestWithModel(body)
			if err != nil {
				t.Fatal(err)
			}
			var canonical map[string]json.RawMessage
			if err := json.Unmarshal(newBody, &canonical); err != nil {
				t.Fatal(err)
			}
			var forwardedModel string
			if err := json.Unmarshal(canonical["model"], &forwardedModel); err != nil {
				t.Fatal(err)
			}
			if model != forwardedModel {
				t.Fatalf("validated model %q != model sent upstream %q", model, forwardedModel)
			}
		})
	}
}

func TestSinglePassRejectsForgedModelAndNeverReusesModelAcrossRequests(t *testing.T) {
	allow := map[string]struct{}{"llama3.2:1b": {}}
	first := []byte(`{"model":"llama3.2:1b","messages":[{"role":"user","content":"hi"}]}`)
	second := []byte(`{"model":"deepseek-r1:7b","messages":[{"role":"user","content":"hi"}]}`)
	for _, body := range [][]byte{first, second, first} {
		validated, _, selected, err := validateChatRequestWithModel(body)
		if err != nil {
			t.Fatal(err)
		}
		_, exists := allow[selected]
		if exists != (bytes.Equal(body, first)) {
			t.Fatalf("cross-request selection leak: selected=%q", selected)
		}
		if len(validated) == 0 {
			t.Fatal("validated forwarding body unexpectedly empty")
		}
	}
	if _, _, _, err := validateChatRequestWithModel([]byte("null")); err == nil {
		t.Fatal("null request bypassed validation")
	}
	if _, _, _, err := validateChatRequestWithModel([]byte("{}")); err == nil {
		t.Fatal("empty JSON bypassed validation")
	}
}

// Model-selection CPU microbenchmark only. This does not measure Ollama
// inference throughput, TTFT, GPU memory or p95 request latency. Both paths
// validate identical bytes on the same Go runtime with the same allowlist.
func BenchmarkModelSelection(b *testing.B) {
	allow := map[string]struct{}{"llama3.2:1b": {}}
	for _, tc := range []struct {
		name string
		fn   func([]byte, string, map[string]struct{}) ([]byte, bool, bool, error)
	}{
		{"baseline_double_decode", baselineModelSelection},
		{"optimized_single_pass", singlePassModelSelection},
	} {
		b.Run(tc.name, func(b *testing.B) {
			b.ReportAllocs()
			b.SetBytes(int64(len(selectionFixture)))
			for i := 0; i < b.N; i++ {
				output, streaming, allowed, err := tc.fn(selectionFixture, "llama3.2:1b", allow)
				if err != nil || !allowed || !streaming {
					b.Fatal(fmt.Errorf("invalid synthetic benchmark result: %w", errors.Join(err)))
				}
				modelSelectionSink = "llama3.2:1b"
				selectionAllowedSink = allowed
				selectionBytesSink = output
			}
		})
	}
}
