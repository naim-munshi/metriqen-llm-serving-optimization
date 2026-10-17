package httpapi

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

const projectTestSecret = "test-only-service-token-0123456789abcdef9876543210"
const projectA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
const projectB = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

func projectRequest(token, project string) *http.Request {
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(validChat))
	r.Header.Set("Content-Type", "application/json")
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	if project != "" {
		r.Header.Set("X-Metriqen-Project-ID", project)
	}
	return r
}

func TestProjectGuardAuthorizesBeforeInferenceAndRevocationIsImmediate(t *testing.T) {
	var upstreamCalls atomic.Int32
	var controlCalls atomic.Int32
	var permitted atomic.Bool
	permitted.Store(true)
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		controlCalls.Add(1)
		if r.Method != http.MethodPost || r.URL.Path != gatewayAuthPath {
			t.Error("invalid control API method or path")
		}
		if r.Header.Get("Authorization") != "Bearer user-jwt" ||
			r.Header.Get("X-Metriqen-Gateway-Secret") != projectTestSecret ||
			r.Header.Get("Cookie") != "" {
			t.Error("missing or mismatched trusted control headers")
		}
		body, err := io.ReadAll(r.Body)
		if err != nil || string(body) != `{"project_id":"`+projectA+`"}` {
			t.Error("project auth payload malformed or prompt leaked")
		}
		w.Header().Set("Content-Type", "application/json")
		if permitted.Load() {
			_, _ = io.WriteString(w, `{"authorized":true,"project_id":"`+projectA+`"}`)
		} else {
			w.WriteHeader(http.StatusNotFound)
			_, _ = io.WriteString(w, `{"detail":"project not found"}`)
		}
	}))
	defer control.Close()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls.Add(1)
		if r.Header.Get("Authorization") != "" ||
			r.Header.Get("X-Metriqen-Gateway-Secret") != "" ||
			r.Header.Get("X-Metriqen-Project-ID") != "" {
			t.Error("identity, project or shared secret leaked to inference runtime")
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"ok"}}]}`)
	}))
	defer upstream.Close()
	h, err := NewRouterWithInferenceAndSchedulerAndAuth(
		upstream.URL, "", "", "", control.URL+gatewayAuthPath, projectTestSecret)
	if err != nil {
		t.Fatal(err)
	}

	for _, tc := range []struct {
		name, token, project string
		status               int
	}{
		{"missing JWT", "", projectA, http.StatusUnauthorized},
		{"missing project", "user-jwt", "", http.StatusBadRequest},
		{"uppercase project", "user-jwt", strings.ToUpper(projectA), http.StatusBadRequest},
		{"wrong project", "user-jwt", projectB, http.StatusServiceUnavailable},
		{"malformed UUID", "user-jwt", "not-a-uuid", http.StatusBadRequest},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := projectRequest(tc.token, tc.project)
			w := httptest.NewRecorder()
			h.ServeHTTP(w, r)
			if w.Code != tc.status {
				t.Fatalf("HTTP %d want %d: %s", w.Code, tc.status, w.Body)
			}
		})
	}
	for _, header := range []string{"Authorization", "X-Metriqen-Project-ID"} {
		r := projectRequest("user-jwt", projectA)
		r.Header.Add(header, r.Header.Get(header))
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code == http.StatusOK {
			t.Fatalf("duplicate %s bypassed gateway", header)
		}
	}
	if upstreamCalls.Load() != 0 {
		t.Fatal("denied request reached inference runtime")
	}

	valid := projectRequest("user-jwt", projectA)
	valid.Header.Set("Cookie", "DO_NOT_FORWARD_TO_CONTROL")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, valid)
	if w.Code != http.StatusOK || upstreamCalls.Load() != 1 {
		t.Fatalf("valid scoped authorization rejected: %d %s", w.Code, w.Body.String())
	}
	permitted.Store(false) // represents revoked DB membership after first call.
	w = httptest.NewRecorder()
	h.ServeHTTP(w, projectRequest("user-jwt", projectA))
	if w.Code != http.StatusForbidden || upstreamCalls.Load() != 1 {
		t.Fatalf("revoked permission reached runtime: %d", w.Code)
	}
	if controlCalls.Load() < 3 {
		t.Fatal("current authorization not checked for each valid attempt")
	}
	admission, _ := readAdmissionMetrics(t, h)
	if admission.AdmittedImmediateTotal != 1 {
		t.Fatalf("unauthorized requests consumed scheduler admission: %+v", admission)
	}
}

func TestProjectGuardFailsClosedForUntrustedDecisionAndOutage(t *testing.T) {
	decision := `{"authorized":true,"project_id":"` + projectA + `"}`
	status := http.StatusOK
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = io.WriteString(w, decision)
	}))
	defer control.Close()
	var upstreamCalls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"ok"}}]}`)
	}))
	defer upstream.Close()
	h, err := NewRouterWithInferenceAndSchedulerAndAuth(
		upstream.URL, "", "", "", control.URL+gatewayAuthPath, projectTestSecret)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name, payload string
		code int
		want int
	}{
		{"malicious true for another project", `{"authorized":true,"project_id":"`+projectB+`"}`, 200, 503},
		{"false verdict", `{"authorized":false,"project_id":"`+projectA+`"}`, 200, 503},
		{"unknown field", `{"authorized":true,"project_id":"`+projectA+`","fake":true}`, 200, 503},
		{"trailing data", `{"authorized":true,"project_id":"`+projectA+`"}{}`, 200, 503},
		{"malformed", `{`, 200, 503},
		{"denied", `{"detail":"unknown"}`, 404, 403},
		{"invalid access", `{"detail":"invalid"}`, 401, 401},
		{"control error", `{}`, 500, 503},
		{"redirect", `{}`, 302, 503},
	} {
		t.Run(tc.name, func(t *testing.T) {
			decision, status = tc.payload, tc.code
			w := httptest.NewRecorder()
			h.ServeHTTP(w, projectRequest("user-jwt", projectA))
			if w.Code != tc.want {
				t.Fatalf("HTTP %d expected %d", w.Code, tc.want)
			}
		})
	}
	if upstreamCalls.Load() != 0 {
		t.Fatal("invalid control-plane decision forwarded prompt")
	}
	control.Close()
	w := httptest.NewRecorder()
	h.ServeHTTP(w, projectRequest("user-jwt", projectA))
	if w.Code != http.StatusServiceUnavailable || upstreamCalls.Load() != 0 {
		t.Fatalf("authorization service outage must fail closed: %d", w.Code)
	}
}

func TestProjectAuthorizationStrictConfiguration(t *testing.T) {
	bad := []string{
		"http://localhost:8000" + gatewayAuthPath,
		"http://127.0.0.1:8000/health/live",
		"http://127.0.0.1:8000" + gatewayAuthPath + "?x=1",
		"http://127.0.0.1:8000" + gatewayAuthPath + "#x",
		"http://127.0.0.1:8000" + gatewayAuthPath + "/",
		"https://127.0.0.1:8000" + gatewayAuthPath,
		"http://127.0.0.2:8000" + gatewayAuthPath,
		"http://192.0.2.1:8000" + gatewayAuthPath,
		"http://user:pass@127.0.0.1:8000" + gatewayAuthPath,
	}
	for _, value := range bad {
		if _, err := newProjectAuthorizer(value, projectTestSecret); err == nil {
			t.Fatalf("accepted unsafe authority: %s", value)
		}
	}
	if _, err := newProjectAuthorizer("http://127.0.0.1:8000"+gatewayAuthPath, "short"); err == nil {
		t.Fatal("accepted weak service credential")
	}
	for _, tc := range []struct{ localToken, url, secret string }{
		{localTestBearerToken, "http://127.0.0.1:8000" + gatewayAuthPath, projectTestSecret},
		{"", "http://127.0.0.1:8000" + gatewayAuthPath, ""},
		{"", "", projectTestSecret},
		{"", "http://127.0.0.1:8000" + gatewayAuthPath, projectTestSecret},
	} {
		upstreamURL := "http://127.0.0.1:11434"
		if tc.localToken == "" && tc.url != "" && tc.secret != "" {
			upstreamURL = "" // guarded mode must have a configured upstream.
		}
		if _, err := NewRouterWithInferenceAndSchedulerAndAuth(upstreamURL, "", "",
			tc.localToken, tc.url, tc.secret); err == nil {
			t.Fatal("invalid or conflicting auth configuration accepted")
		}
	}
}
