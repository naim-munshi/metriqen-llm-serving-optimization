.PHONY: test test-python test-go format-check

test: test-python test-go

test-python:
	cd services/control-api && python -m pytest -q

test-go:
	cd services/inference-gateway && go test -race ./...

format-check:
	cd services/inference-gateway && test -z "$$(gofmt -l cmd/server/main.go internal/httpapi/router.go internal/httpapi/router_test.go)"
