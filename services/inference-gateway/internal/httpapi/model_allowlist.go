package httpapi

import (
    "errors"
    "strings"
)

// parseAllowedModels accepts an optional, exact-match model-tag allowlist.
// Model tags are mutable aliases, NOT immutable revision/digest pins.
func parseAllowedModels(raw string) (map[string]struct{}, error) {
    if raw == "" {
        return nil, nil // Preserve existing local development behavior.
    }
    if len(raw) > 8192 {
        return nil, errors.New("model allowlist exceeds size bound")
    }
    names := strings.Split(raw, ",")
    if len(names) > 32 {
        return nil, errors.New("model allowlist supports at most 32 names")
    }
    allowed := make(map[string]struct{}, len(names))
    for _, name := range names {
        if name == "" || len(name) > maxModelNameSize || strings.TrimSpace(name) != name {
            return nil, errors.New("model allowlist contains an invalid name")
        }
        for index, c := range []byte(name) {
            valid := (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                (c >= '0' && c <= '9') || (index > 0 && (c == '_' || c == '-' || c == '.' || c == '/' || c == ':'))
            if !valid {
                return nil, errors.New("model allowlist contains unsupported characters")
            }
        }
        if _, exists := allowed[name]; exists {
            return nil, errors.New("model allowlist contains duplicate names")
        }
        allowed[name] = struct{}{}
    }
    return allowed, nil
}
