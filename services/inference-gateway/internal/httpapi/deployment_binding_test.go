package httpapi

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

const deployedModel = "llama3.2:1b"
const deployedName = "stable"

var deployedDigest = strings.Repeat("a", 64)

func strictRequest(body, deployment string) *http.Request {
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Authorization", "Bearer user-jwt")
	r.Header.Set("X-Metriqen-Project-ID", projectA)
	if deployment != "" {
		r.Header.Set("X-Metriqen-Deployment", deployment)
	}
	return r
}

func strictChat(model string) string {
	return `{"model":"` + model + `","messages":[{"role":"user","content":"private prompt"}]}`
}

func positiveRouteJSON() string {
	return `{"authorized":true,"project_id":"` + projectA +
		`","deployment":"stable","model_tag":"` + deployedModel +
		`","model_digest":"` + deployedDigest + `","generation":1}`
}

func strictGateway(t *testing.T, control, upstream string) http.Handler {
	t.Helper()
	h, err := NewRouterWithInferenceAndSchedulerAndDeploymentAuth(
		upstream, "", "", "", control+gatewayAuthPath,
		projectTestSecret, "1")
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func TestStrictRoutingBindsBodyAndLoadedDigestBeforeForwarding(t *testing.T) {
	var controlCalls atomic.Int32
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		controlCalls.Add(1)
		if r.URL.Path != gatewayAuthPath || r.Method != http.MethodPost ||
			r.Header.Get("Authorization") != "Bearer user-jwt" ||
			r.Header.Get("X-Metriqen-Gateway-Secret") != projectTestSecret ||
			r.Header.Get("Cookie") != "" {
			t.Error("strict control-plane headers or path invalid")
		}
		data, err := io.ReadAll(r.Body)
		if err != nil || string(data) != `{"project_id":"`+projectA+`","deployment":"stable"}` {
			t.Errorf("control request must contain only project and deployment: %s", data)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, positiveRouteJSON())
	}))
	defer control.Close()

	var probes atomic.Int32
	var chats atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/ps":
			probes.Add(1)
			if r.Method != http.MethodGet || r.Header.Get("Authorization") != "" {
				t.Error("model status request leaked a credential")
			}
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"models":[{"name":"`+deployedModel+`","digest":"`+deployedDigest+`"}]}`)
		case "/v1/chat/completions":
			chats.Add(1)
			if r.Header.Get("Authorization") != "" || r.Header.Get("X-Metriqen-Deployment") != "" ||
				r.Header.Get("X-Metriqen-Project-ID") != "" ||
				r.Header.Get("X-Metriqen-Gateway-Secret") != "" {
				t.Error("client identity or project header leaked to runtime")
			}
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"ok"}}]}`)
		default:
			t.Errorf("unexpected upstream route %s", r.URL.Path)
		}
	}))
	defer upstream.Close()

	h := strictGateway(t, control.URL, upstream.URL)
	for _, tc := range []struct {
		name, body, deployment string
		status                 int
	}{
		{"no deployment", strictChat(deployedModel), "", 400},
		{"unsafe deployment", strictChat(deployedModel), "../owner", 400},
		{"uppercase deployment", strictChat(deployedModel), "Stable", 400},
		{"forged model body", strictChat("deepseek-r1:7b"), deployedName, 403},
		{"valid strict route", strictChat(deployedModel), deployedName, 200},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			h.ServeHTTP(w, strictRequest(tc.body, tc.deployment))
			if w.Code != tc.status {
				t.Fatalf("unexpected HTTP %d want %d: %s", w.Code, tc.status, w.Body)
			}
		})
	}
	duplicate := strictRequest(strictChat(deployedModel), deployedName)
	duplicate.Header.Add("X-Metriqen-Deployment", deployedName)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, duplicate)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("duplicate deployment header accepted: %d", w.Code)
	}
	if chats.Load() != 1 || probes.Load() != 1 || controlCalls.Load() != 3 {
		t.Fatalf("unexpected forwarded chats/probes/authorizations: %d %d %d",
			chats.Load(), probes.Load(), controlCalls.Load())
	}
	metrics, _ := readAdmissionMetrics(t, h)
	if metrics.AdmittedImmediateTotal != 1 {
		t.Fatalf("denied models consumed the inference scheduler: %+v", metrics)
	}
}

func TestStrictRoutingDeniesRetaggedMissingAndAmbiguousModels(t *testing.T) {
	var status atomic.Int32
	status.Store(200)
	var positive atomic.Bool
	positive.Store(true)
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(int(status.Load()))
		if positive.Load() {
			_, _ = io.WriteString(w, positiveRouteJSON())
		} else {
			_, _ = io.WriteString(w, `{"detail":"not ready"}`)
		}
	}))
	defer control.Close()

	models := `{"models":[{"name":"`+deployedModel+`","digest":"`+deployedDigest+`"}]}`
	var chats atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/ps":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, models)
		case "/v1/chat/completions":
			chats.Add(1)
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"ok"}}]}`)
		}
	}))
	defer upstream.Close()

	h := strictGateway(t, control.URL, upstream.URL)
	for _, tc := range []struct {
		name, loaded string
		expected int
	}{
		{"model not loaded", `{"models":[]}`, 409},
		{"digest changed", `{"models":[{"name":"`+deployedModel+`","digest":"`+strings.Repeat("b", 64)+`"}]}`, 409},
		{"duplicate loaded model", `{"models":[{"name":"`+deployedModel+`","digest":"`+deployedDigest+`"},{"name":"`+deployedModel+`","digest":"`+deployedDigest+`"}]}`, 503},
		{"malformed Ollama", `{"models":"forged"}`, 503},
		{"missing models", `{}`, 503},
	} {
		t.Run(tc.name, func(t *testing.T) {
			models = tc.loaded
			w := httptest.NewRecorder()
			h.ServeHTTP(w, strictRequest(strictChat(deployedModel), deployedName))
			if w.Code != tc.expected {
				t.Fatalf("expected HTTP %d got %d: %s", tc.expected, w.Code, w.Body)
			}
		})
	}
	if chats.Load() != 0 {
		t.Fatal("unready or changed model reached runtime")
	}
	status.Store(http.StatusConflict)
	positive.Store(false)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, strictRequest(strictChat(deployedModel), deployedName))
	if w.Code != 409 || chats.Load() != 0 {
		t.Fatalf("stopped SQL deployment was forwarded: HTTP %d", w.Code)
	}
	status.Store(http.StatusNotFound)
	w = httptest.NewRecorder()
	h.ServeHTTP(w, strictRequest(strictChat(deployedModel), deployedName))
	if w.Code != 403 || chats.Load() != 0 {
		t.Fatalf("cross-tenant SQL deployment was forwarded: HTTP %d", w.Code)
	}
}

func TestStrictRoutingValidatesControlDecisionAndConfiguration(t *testing.T) {
	tests := []struct {
		name, response string
	}{
		{"wrong deployment", strings.Replace(positiveRouteJSON(), `"deployment":"stable"`, `"deployment":"other"`, 1)},
		{"wrong project", strings.Replace(positiveRouteJSON(), projectA, projectB, 1)},
		{"invalid digest", strings.Replace(positiveRouteJSON(), deployedDigest, "not-a-sha", 1)},
		{"invalid model name", strings.Replace(positiveRouteJSON(), deployedModel, "../unsafe", 1)},
		{"missing generation", strings.Replace(positiveRouteJSON(), `,"generation":1`, "", 1)},
		{"unknown field", strings.Replace(positiveRouteJSON(), "}", `,"actor":"owner"}`, 1)},
		{"malformed", `{"authorized":true`},
	}
	var response string
	var upstreamCalls atomic.Int32
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, response)
	}))
	defer control.Close()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		upstreamCalls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"models":[]}`)
	}))
	defer upstream.Close()
	h := strictGateway(t, control.URL, upstream.URL)
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			response = tc.response
			w := httptest.NewRecorder()
			h.ServeHTTP(w, strictRequest(strictChat(deployedModel), deployedName))
			if w.Code != 503 {
				t.Fatalf("untrusted control reply granted access: %d %s", w.Code, w.Body)
			}
		})
	}
	if upstreamCalls.Load() != 0 {
		t.Fatal("untrusted control-plane decision reached runtime")
	}
	if _, err := newStrictProjectAuthorizer("", "", "1"); err == nil {
		t.Fatal("strict mode accepted absent project auth endpoint")
	}
	if _, err := newStrictProjectAuthorizer(control.URL+gatewayAuthPath, projectTestSecret, "yes"); err == nil {
		t.Fatal("untrusted routing toggle accepted")
	}
	if _, err := NewRouterWithInferenceAndSchedulerAndDeploymentAuth(
		upstream.URL, "", "", localTestBearerToken,
		control.URL+gatewayAuthPath, projectTestSecret, "1"); err == nil {
		t.Fatal("strict mode accepted conflicting legacy bearer token")
	}
	// Strict and legacy JSON have intentionally distinct response contracts.
	var legacy map[string]any
	if err := json.Unmarshal([]byte(positiveRouteJSON()), &legacy); err != nil {
		t.Fatal(err)
	}
}
