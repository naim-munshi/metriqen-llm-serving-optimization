package httpapi

import (
	"crypto/subtle"
	"errors"
	"net/http"
	"strings"
)

const (
	minLocalBearerTokenLength = 32
	maxLocalBearerTokenLength = 256
)

// validateLocalBearerToken validates a single optional local-development
// shared secret. There is no tenant identity, rotation, or external authority.
func validateLocalBearerToken(token string) (string, error) {
	if token == "" {
		return "", nil
	}
	if len(token) < minLocalBearerTokenLength || len(token) > maxLocalBearerTokenLength {
		return "", errors.New("local bearer token must contain 32 to 256 ASCII characters")
	}
	for i := 0; i < len(token); i++ {
		c := token[i]
		// Avoid whitespace, Unicode, control characters, commas and quotes
		// in the single HTTP Authorization header credential.
		if c < '!' || c > '~' || c == ',' || c == '"' {
			return "", errors.New("invalid characters in local bearer token")
		}
	}
	return token, nil
}

// protectLocalInference blocks unauthenticated inference before decoding
// request bodies, consuming scheduler capacity, or forwarding a prompt.
// The token never becomes an upstream header, metric, response or log label.
func protectLocalInference(token string, next http.HandlerFunc) http.HandlerFunc {
	if token == "" {
		return next // Preserve documented default loopback-only behavior.
	}
	expected := []byte(token)
	return func(w http.ResponseWriter, r *http.Request) {
		authorization := r.Header.Values("Authorization")
		if len(authorization) != 1 || !strings.HasPrefix(authorization[0], "Bearer ") {
			rejectLocalBearer(w)
			return
		}
		candidate := []byte(strings.TrimPrefix(authorization[0], "Bearer "))
		if subtle.ConstantTimeCompare(candidate, expected) != 1 {
			rejectLocalBearer(w)
			return
		}
		next(w, r)
	}
}

func rejectLocalBearer(w http.ResponseWriter) {
	w.Header().Set("WWW-Authenticate", `Bearer realm="metriqen-local"`)
	writeError(w, http.StatusUnauthorized, "authentication_required", "A valid local bearer token is required")
}
