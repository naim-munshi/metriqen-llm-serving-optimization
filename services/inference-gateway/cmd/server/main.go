package main

import (
	"context"
	"errors"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/naim-munshi/metriqen-llm-serving-optimization/services/inference-gateway/internal/httpapi"
)

func main() {
	addr := os.Getenv("METRIQEN_GATEWAY_BIND_ADDR")
	if addr == "" {
		addr = "127.0.0.1:8081"
	}

	upstream := os.Getenv("METRIQEN_UPSTREAM_URL")
	if !listenerAllowed(addr, upstream) {
		log.Fatal("local inference must bind to loopback only")
	}
	allowedModels := os.Getenv("METRIQEN_ALLOWED_MODELS")
	queueDepth := os.Getenv("METRIQEN_QUEUE_DEPTH")
	token := os.Getenv("METRIQEN_GATEWAY_BEARER_TOKEN")
	projectAuthURL := os.Getenv("METRIQEN_GATEWAY_PROJECT_AUTH_URL")
	projectAuthSecret := os.Getenv("METRIQEN_GATEWAY_PROJECT_AUTH_TOKEN")
	deploymentMode := os.Getenv("METRIQEN_ENFORCE_DEPLOYMENT_ROUTING")
	leaseMode := os.Getenv("METRIQEN_GATEWAY_PROJECT_INFLIGHT_LEASES")
	budgetMode := os.Getenv("METRIQEN_GATEWAY_PROJECT_OUTPUT_RESERVATIONS")
	router, err := httpapi.NewRouterWithInferenceAndSchedulerAndDeploymentAndBudget(
		upstream, allowedModels, queueDepth, token, projectAuthURL,
		projectAuthSecret, deploymentMode, leaseMode, budgetMode)
	if err != nil {
		log.Fatal("invalid local inference upstream configuration")
	}
	// Binding to 127.0.0.1 alone does not prevent DNS rebinding attacks:
	// browsers can connect to loopback while presenting an attacker Host.
	// Require a canonical loopback Host at the real server entry point.
	router = secureGatewayHandler(addr, upstream, router)
	server := &http.Server{
		Addr:              addr,
		Handler:           router,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		// Streaming may exceed the previous 10-second response limit.
		WriteTimeout: 130 * time.Second,
		IdleTimeout:  30 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			log.Printf("shutdown error: %v", err)
		}
	}()

	log.Printf("Metriqen local inference gateway listening on %s", addr)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}

// listenerAllowed prevents accidental public exposure of the unauthenticated
// development-only inference endpoint. The default health-only Compose service
// can continue to listen inside its container with the inference route disabled.
func listenerAllowed(addr, upstream string) bool {
	if upstream == "" {
		return true
	}
	hostname, _, err := net.SplitHostPort(addr)
	if err != nil {
		return false
	}
	return hostname == "127.0.0.1" || hostname == "localhost" || hostname == "::1"
}

 
// secureGatewayHandler intentionally leaves the health-only container's
// externally bound endpoint unchanged. Inference mode is a localhost-only
// development service and rejects DNS rebinding and wrong-port Host headers.
func secureGatewayHandler(addr, upstream string, next http.Handler) http.Handler {
	if upstream == "" {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, bindPort, err := net.SplitHostPort(addr)
		if err != nil || !trustedLocalHost(r.Host, bindPort) {
			w.Header().Set("Cache-Control", "no-store")
			w.Header().Set("X-Content-Type-Options", "nosniff")
			http.Error(w, "Untrusted local request host", http.StatusMisdirectedRequest)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// Never resolve client-controlled hostnames. Even a hostname that currently
// resolves to 127.0.0.1 is untrusted: it may be controlled by a remote site.
func trustedLocalHost(requestHost, expectedPort string) bool {
	if expectedPort == "" {
		return false
	}
	host, port, err := net.SplitHostPort(requestHost)
	if err != nil || port != expectedPort {
		return false
	}
	return host == "127.0.0.1" || host == "::1" || host == "localhost"
}
