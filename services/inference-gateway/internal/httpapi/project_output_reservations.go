package httpapi

import (
	"bytes"
	"context"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// A reservation is only created by the trusted project authorization reply,
// NEVER client-controlled request fields or cached project identity.
// It does not represent actual model-generated or billable token consumption.
type projectOutputReservationKey struct{}

type projectOutputReservation struct {
	authorizer *projectAuthorizer
	lease      projectLeaseGrant
}

// reserveOutput sends only the (opaque) live permit and the max_tokens
// extracted from the already validated, forwarded JSON. Do not send prompts,
// JWTs, tenant IDs or model text to this endpoint.
func (r projectOutputReservation) reserveOutput(ctx context.Context, cap int) int {
	a := r.authorizer
	if a == nil || !a.tokenBudgetEnabled || !a.leaseEnabled ||
		!r.lease.valid() || cap < 1 || cap > maxOutputTokens {
		return http.StatusServiceUnavailable
	}
	requestCtx, cancel := context.WithTimeout(ctx, 2500*time.Millisecond)
	defer cancel()
	body := []byte(`{"scope":"` + r.lease.Scope + `","permit":"` +
		r.lease.Permit + `","max_tokens":` + strconv.Itoa(cap) + `}`)
	url := strings.TrimSuffix(a.endpoint, gatewayAuthPath) + "/internal/gateway/reserve-output"
	req, err := http.NewRequestWithContext(requestCtx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return http.StatusServiceUnavailable
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Metriqen-Gateway-Secret", a.secret)
	resp, err := a.client.Do(req)
	if err != nil {
		return http.StatusServiceUnavailable
	}
	defer resp.Body.Close()
	switch resp.StatusCode {
	case http.StatusNoContent:
		return 0
	case http.StatusTooManyRequests:
		return http.StatusTooManyRequests
	case http.StatusConflict:
		return http.StatusConflict
	default:
		return http.StatusServiceUnavailable
	}
}
