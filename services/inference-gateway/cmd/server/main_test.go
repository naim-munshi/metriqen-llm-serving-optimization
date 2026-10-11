package main

import "testing"

func TestUnauthenticatedInferenceRequiresLoopbackListener(t *testing.T) {
	for _, tc := range []struct {
		addr, upstream string
		allowed        bool
	}{
		{"127.0.0.1:8081", "http://127.0.0.1:11434", true},
		{"localhost:8081", "http://localhost:11434", true},
		{"[::1]:8081", "http://[::1]:11434", true},
		{"0.0.0.0:8081", "http://127.0.0.1:11434", false},
		{":8081", "http://127.0.0.1:11434", false},
		{"192.168.1.4:8081", "http://127.0.0.1:11434", false},
		{"0.0.0.0:8081", "", true}, // health-only local Compose
	} {
		if got := listenerAllowed(tc.addr, tc.upstream); got != tc.allowed {
			t.Errorf("listenerAllowed(%q, %q)=%v, want %v", tc.addr, tc.upstream, got, tc.allowed)
		}
	}
}
