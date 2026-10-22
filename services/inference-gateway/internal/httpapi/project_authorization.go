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
const gatewayReleasePath = "/internal/gateway/release"
const maxProjectJWTLength = 16 << 10
const projectLeaseDeadline = 150 * time.Second

var canonicalDeployment = regexp.MustCompile("^[a-z][a-z0-9_-]{0,31}$")
var canonicalDigest = regexp.MustCompile("^[0-9a-f]{64}$")
var canonicalPermit = regexp.MustCompile("^[0-9a-f]{32}$")

type projectLeaseGrant struct {
	Scope  string `json:"scope"`
	Permit string `json:"permit"`
}

func (g *projectLeaseGrant) valid() bool {
	return g != nil && canonicalDigest.MatchString(g.Scope) && canonicalPermit.MatchString(g.Permit)
}

var canonicalProjectID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

type projectAuthorizer struct {
	endpoint           string
	secret             string
	client             *http.Client
	requireDeployment  bool
	leaseEnabled       bool
	tokenBudgetEnabled bool
}

func newBudgetedProjectAuthorizer(rawURL, secret, deploymentMode, leaseMode, budgetMode string) (*projectAuthorizer, error) {
	if budgetMode != "" && budgetMode != "0" && budgetMode != "1" {
		return nil, errors.New("invalid project output reservation mode")
	}
	auth, err := newLeasedProjectAuthorizer(rawURL, secret, deploymentMode, leaseMode)
	if err != nil {
		return nil, err
	}
	if budgetMode == "1" {
		if auth == nil || !auth.leaseEnabled {
			return nil, errors.New("project output reservations require verified distributed project leases")
		}
		auth.tokenBudgetEnabled = true
	}
	return auth, nil
}

func newLeasedProjectAuthorizer(rawURL, secret, deploymentMode, leaseMode string) (*projectAuthorizer, error) {
	if leaseMode != "" && leaseMode != "0" && leaseMode != "1" {
		return nil, errors.New("invalid distributed project concurrency mode")
	}
	auth, err := newStrictProjectAuthorizer(rawURL, secret, deploymentMode)
	if err != nil {
		return nil, err
	}
	if leaseMode == "1" {
		if auth == nil {
			return nil, errors.New("distributed project concurrency requires project authorization")
		}
		auth.leaseEnabled = true
	}
	return auth, nil
}

// newStrictProjectAuthorizer refuses partial or inconsistent opt-in mode.
// Legacy project-only authorization remains backward-compatible.
func newStrictProjectAuthorizer(rawURL, secret, mode string) (*projectAuthorizer, error) {
	if mode != "" && mode != "0" && mode != "1" {
		return nil, errors.New("invalid project deployment routing mode")
	}
	auth, err := newProjectAuthorizer(rawURL, secret)
	if err != nil {
		return nil, err
	}
	if mode == "1" {
		if auth == nil {
			return nil, errors.New("deployment routing requires project authorization")
		}
		auth.requireDeployment = true
	}
	return auth, nil
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
		deployment := ""
		if a.requireDeployment {
			names := r.Header.Values("X-Metriqen-Deployment")
			if len(names) != 1 || !canonicalDeployment.MatchString(names[0]) {
				writeError(w, http.StatusBadRequest, "invalid_deployment",
					"Canonical deployment name required")
				return
			}
			deployment = names[0]
			body = []byte(`{"project_id":"` + projects[0] +
				`","deployment":"` + deployment + `"}`)
		}
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
		case http.StatusConflict:
			writeError(w, http.StatusConflict, "deployment_not_ready",
				"Model deployment is not ready")
			return
		case http.StatusTooManyRequests:
			// Do not consume local scheduler capacity or forward the prompt.
			// Accept only the two operator-defined bounded retry intervals.
			// Never reflect an arbitrary control-service header to clients.
			retry := resp.Header.Values("Retry-After")
			if a.leaseEnabled && len(retry) == 1 && retry[0] == "1" {
				w.Header().Set("Retry-After", "1")
				writeError(w, http.StatusTooManyRequests, "project_capacity_exceeded",
					"Project active inference capacity exceeded")
			} else {
				w.Header().Set("Retry-After", "60")
				writeError(w, http.StatusTooManyRequests, "project_quota_exceeded",
					"Project inference request quota exceeded")
			}
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
		decoder := json.NewDecoder(io.LimitReader(resp.Body, 1025))
		decoder.DisallowUnknownFields()
		var nextRequest *http.Request
		var lease *projectLeaseGrant
		// Release a syntactically valid distributed permit on every exit,
		// including a rejected authorization response. Otherwise an already
		// acquired permit may exhaust Redis capacity until its crash TTL.
		defer func() {
			if a.leaseEnabled && lease.valid() {
				a.releaseLease(*lease)
			}
		}()
		var outputReservationRequired bool
		if a.requireDeployment {
			var decision struct {
				Authorized                bool               `json:"authorized"`
				ProjectID                 string             `json:"project_id"`
				Deployment                string             `json:"deployment"`
				ModelTag                  string             `json:"model_tag"`
				ModelDigest               string             `json:"model_digest"`
				Generation                int                `json:"generation"`
				Lease                     *projectLeaseGrant `json:"lease,omitempty"`
				OutputReservationRequired bool               `json:"output_reservation_required,omitempty"`
			}
			decodeErr := decoder.Decode(&decision)
			lease = decision.Lease
			if decodeErr != nil ||
				!decision.Authorized || decision.ProjectID != projects[0] ||
				decision.Deployment != deployment || decision.Generation < 1 ||
				!validModelTag(decision.ModelTag) ||
				!canonicalDigest.MatchString(decision.ModelDigest) {
				denyProjectAuth(w, http.StatusServiceUnavailable)
				return
			}
			outputReservationRequired = decision.OutputReservationRequired
			nextRequest = r.WithContext(context.WithValue(r.Context(),
				deploymentBindingKey{}, deploymentBinding{
					modelTag: decision.ModelTag, digest: decision.ModelDigest,
				}))
		} else {
			var decision struct {
				Authorized                bool               `json:"authorized"`
				ProjectID                 string             `json:"project_id"`
				Lease                     *projectLeaseGrant `json:"lease,omitempty"`
				OutputReservationRequired bool               `json:"output_reservation_required,omitempty"`
			}
			decodeErr := decoder.Decode(&decision)
			lease = decision.Lease
			if decodeErr != nil ||
				!decision.Authorized || decision.ProjectID != projects[0] {
				denyProjectAuth(w, http.StatusServiceUnavailable)
				return
			}
			outputReservationRequired = decision.OutputReservationRequired
			nextRequest = r
		}
		if a.leaseEnabled {
			if !lease.valid() {
				denyProjectAuth(w, http.StatusServiceUnavailable)
				return
			}
		} else if lease != nil {
			// A partially configured control API must never silently
			// enable or bypass the distributed concurrency contract.
			denyProjectAuth(w, http.StatusServiceUnavailable)
			return
		}
		var remaining any
		if err := decoder.Decode(&remaining); !errors.Is(err, io.EOF) {
			denyProjectAuth(w, http.StatusServiceUnavailable)
			return
		}
		if a.tokenBudgetEnabled != outputReservationRequired {
			// A partially enabled API/gateway combination cannot drop
			// output reservations while still serving inference.
			denyProjectAuth(w, http.StatusServiceUnavailable)
			return
		}
		if a.leaseEnabled {
			// Handler is bounded by its 120-second upstream timeout and the
			// loopback HTTP server's 130-second write deadline. Hard-limit
			// this handler to 150s, well inside the 240s Redis crash lease.
			// A stalled handler beyond this budget must not be assumed
			// safe without a real serving process kill switch.
			executionCtx, end := context.WithTimeout(nextRequest.Context(), projectLeaseDeadline)
			defer end()
			requestCtx := executionCtx
			if a.tokenBudgetEnabled {
				requestCtx = context.WithValue(executionCtx,
					projectOutputReservationKey{}, projectOutputReservation{
						authorizer: a, lease: *lease,
					})
			}
			next(w, nextRequest.WithContext(requestCtx))
			return
		}
		next(w, nextRequest)
	}
}

func (a *projectAuthorizer) releaseLease(lease projectLeaseGrant) {
	if a == nil || !a.leaseEnabled || !lease.valid() {
		return
	}
	// A canceled client context must not prevent releasing its permit.
	// Release errors are conservatively recovered via Redis lease expiry.
	ctx, cancel := context.WithTimeout(context.Background(), 2500*time.Millisecond)
	defer cancel()
	body := []byte(`{"scope":"` + lease.Scope + `","permit":"` + lease.Permit + `"}`)
	endpoint := strings.TrimSuffix(a.endpoint, gatewayAuthPath) + gatewayReleasePath
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Metriqen-Gateway-Secret", a.secret)
	resp, err := a.client.Do(req)
	if err == nil {
		_ = resp.Body.Close()
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
