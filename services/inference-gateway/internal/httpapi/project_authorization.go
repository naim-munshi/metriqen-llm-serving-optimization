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
	"regexp"
	"strconv"
	"strings"
	"time"
)

// These credentials protect only an opt-in, loopback-to-loopback development
// authorization call. No caller token is forwarded to the inference runtime.
const gatewayAuthPath = "/internal/gateway/authorize"
const maxProjectJWTLength = 16 << 10

var canonicalProjectID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

type projectAuthorizer struct {
	endpoint string
	secret   string
	client   *http.Client
}

func newProjectAuthorizer(rawURL, secret string) (*projectAuthorizer, error) {
	if rawURL == "" && secret == "" {
		return nil, nil
	}
	if rawURL == "" || secret == "" {
		return nil, errors.New("project authorization requires both local URL and service credential")
	}
	if _, err := validateLocalBearerToken(secret); err != nil {
		return nil, errors.New("invalid project authorization service credential")
	}
	u, err := url.Parse(rawURL)
	if err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" ||
		u.User != nil || u.RawQuery != "" || u.Fragment != "" ||
		u.Path != gatewayAuthPath || u.RawPath != "" {
		return nil, errors.New("project authorization must use fixed loopback HTTP path")
	}
	port, err := strconv.Atoi(u.Port())
	if err != nil || port < 1 || port > 65535 {
		return nil, errors.New("project authorization requires explicit loopback port")
	}
	if u.String() != fmt.Sprintf("http://127.0.0.1:%d%s", port, gatewayAuthPath) {
		return nil, errors.New("project authorization endpoint is not canonical")
	}
	return &projectAuthorizer{
		endpoint: u.String(),
		secret:   secret,
		client: &http.Client{
			Timeout: 2500 * time.Millisecond,
			CheckRedirect: func(*http.Request, []*http.Request) error {
				return http.ErrUseLastResponse
			},
			Transport: &http.Transport{
				Proxy:                 nil,
				DialContext:           (&net.Dialer{Timeout: 1 * time.Second}).DialContext,
				ResponseHeaderTimeout: 2 * time.Second,
				DisableKeepAlives:     true,
			},
		},
	}, nil
}

func (a *projectAuthorizer) protect(next http.HandlerFunc) http.HandlerFunc {
	if a == nil {
		return next // Existing explicitly loopback-only legacy mode.
	}
	return func(w http.ResponseWriter, r *http.Request) {
		authorization := r.Header.Values("Authorization")
		if len(authorization) != 1 || !strings.HasPrefix(authorization[0], "Bearer ") {
			denyProjectAuth(w, http.StatusUnauthorized)
			return
		}
		bearer := strings.TrimPrefix(authorization[0], "Bearer ")
		if len(bearer) == 0 || len(bearer) > maxProjectJWTLength ||
			strings.ContainsAny(bearer, " \t\r\n,") {
			denyProjectAuth(w, http.StatusUnauthorized)
			return
		}
		projects := r.Header.Values("X-Metriqen-Project-ID")
		if len(projects) != 1 || !canonicalProjectID.MatchString(projects[0]) {
			writeError(w, http.StatusBadRequest, "invalid_project", "Canonical project ID required")
			return
		}
		// No body read, admission queue, upstream request, or prompt access
		// occurs before a successful current-membership authorization.
		body := []byte(`{"project_id":"` + projects[0] + `"}`)
		ctx, cancel := context.WithTimeout(r.Context(), 2500*time.Millisecond)
		defer cancel()
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, a.endpoint, bytes.NewReader(body))
		if err != nil {
			denyProjectAuth(w, http.StatusServiceUnavailable)
			return
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json")
		req.Header.Set("Authorization", authorization[0])
		req.Header.Set("X-Metriqen-Gateway-Secret", a.secret)
		resp, err := a.client.Do(req)
		if err != nil {
			denyProjectAuth(w, http.StatusServiceUnavailable)
			return
		}
		defer resp.Body.Close()
		switch resp.StatusCode {
		case http.StatusUnauthorized:
			denyProjectAuth(w, http.StatusUnauthorized)
			return
		case http.StatusForbidden, http.StatusNotFound:
			denyProjectAuth(w, http.StatusForbidden)
			return
		case http.StatusOK:
			// A 200 is only accepted with an exact, matching positive decision.
		default:
			denyProjectAuth(w, http.StatusServiceUnavailable)
			return
		}
		mediaType, _, err := mime.ParseMediaType(resp.Header.Get("Content-Type"))
		if err != nil || mediaType != "application/json" {
			denyProjectAuth(w, http.StatusServiceUnavailable)
			return
		}
		var decision struct {
			Authorized bool   `json:"authorized"`
			ProjectID  string `json:"project_id"`
		}
		decoder := json.NewDecoder(io.LimitReader(resp.Body, 513))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&decision); err != nil || !decision.Authorized ||
			decision.ProjectID != projects[0] {
			denyProjectAuth(w, http.StatusServiceUnavailable)
			return
		}
		var remaining any
		if err := decoder.Decode(&remaining); !errors.Is(err, io.EOF) {
			denyProjectAuth(w, http.StatusServiceUnavailable)
			return
		}
		next(w, r)
	}
}

func denyProjectAuth(w http.ResponseWriter, status int) {
	if status == http.StatusUnauthorized {
		w.Header().Set("WWW-Authenticate", `Bearer realm="metriqen-project"`)
	}
	switch status {
	case http.StatusUnauthorized:
		writeError(w, status, "authentication_required", "Verified access token required")
	case http.StatusForbidden:
		writeError(w, status, "project_forbidden", "Project inference access denied")
	default:
		writeError(w, http.StatusServiceUnavailable, "authorization_unavailable", "Project authorization unavailable")
	}
}
