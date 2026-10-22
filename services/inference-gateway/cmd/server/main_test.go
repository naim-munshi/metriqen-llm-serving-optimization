package main

import (
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
)

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

func TestLocalInferenceHostGuardDeniesDNSRebindingBeforeHandler(t *testing.T) {
	var calls atomic.Int32
	inner := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusOK)
	})
	guard := secureGatewayHandler("127.0.0.1:8081", "http://127.0.0.1:11434", inner)
	for _, tc := range []struct {
		host   string
		accept bool
	}{
		{"127.0.0.1:8081", true},
		{"localhost:8081", true},
		{"[::1]:8081", true},
		{"evil.example:8081", false},
		{"localhost.evil.example:8081", false},
		{"127.0.0.1.evil.example:8081", false},
		{"127.0.0.1:8082", false},
		{"localhost", false},
		{"[::1%lo0]:8081", false},
		{"0.0.0.0:8081", false},
		{"[::ffff:127.0.0.1]:8081", false},
		{"", false},
	} {
		t.Run(tc.host, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8081/v1/chat/completions", nil)
			request.Host = tc.host
			before := calls.Load()
			response := httptest.NewRecorder()
			guard.ServeHTTP(response, request)
			if tc.accept {
				if response.Code != http.StatusOK || calls.Load() != before+1 {
					t.Fatalf("trusted host rejected: HTTP %d, handler calls %d", response.Code, calls.Load()-before)
				}
				return
			}
			if response.Code != http.StatusMisdirectedRequest || calls.Load() != before {
				t.Fatalf("untrusted host reached inference: HTTP %d, calls %d", response.Code, calls.Load()-before)
			}
			if response.Header().Get("Cache-Control") != "no-store" ||
				response.Header().Get("X-Content-Type-Options") != "nosniff" {
				t.Fatal("untrusted Host response must not be cached or sniffed")
			}
		})
	}
}

func TestHealthOnlyContainerKeepsExistingHostBehavior(t *testing.T) {
	var calls atomic.Int32
	inner := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusOK)
	})
	healthOnly := secureGatewayHandler("0.0.0.0:8081", "", inner)
	request := httptest.NewRequest(http.MethodGet, "http://example.test/health/live", nil)
	request.Host = "example.test"
	response := httptest.NewRecorder()
	healthOnly.ServeHTTP(response, request)
	if response.Code != http.StatusOK || calls.Load() != 1 {
		t.Fatal("health-only container behavior must remain unchanged")
	}
}
