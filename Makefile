.PHONY: test test-python test-go test-e2e format-check

test: test-python test-go test-e2e

test-python:
	cd services/control-api && python -m pytest -q

test-go:
	cd services/inference-gateway && go test -race ./... && go vet ./...

test-e2e:
	python -m unittest discover -s tests/e2e -v

format-check:
	cd services/inference-gateway && test -z "$$(gofmt -l $(find . -name '*.go'))"
