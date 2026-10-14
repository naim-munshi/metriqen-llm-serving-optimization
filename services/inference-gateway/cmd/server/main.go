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
		log.Fatal("unauthenticated inference must bind to loopback only")
	}
	allowedModels := os.Getenv("METRIQEN_ALLOWED_MODELS")
	router, err := httpapi.NewRouterWithInferenceAndModels(upstream, allowedModels)
	if err != nil {
		log.Fatal("invalid local inference upstream configuration")
	}
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
