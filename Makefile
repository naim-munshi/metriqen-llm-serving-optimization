.PHONY: test test-python test-go test-e2e test-benchmark test-nlp test-ui format-check portfolio-ci

# Source/CI gate only: this cannot replace real local Ollama, Mac, identity
# provider, screenshot or hardware-performance acceptance.
test: test-python test-go test-e2e test-benchmark test-nlp test-ui

test-python:
	cd services/control-api && python -m pytest -q

test-go:
	cd services/inference-gateway && go test -race ./... && go vet ./...

test-e2e:
	python -m unittest discover -s tests/e2e -v

test-benchmark:
	python -m unittest discover -s tools/benchmark/tests -v

test-nlp:
	python -m unittest discover -s tools/nlp_eval/tests -v

test-ui:
	node --test tests/ui/*.test.cjs

format-check:
	cd services/inference-gateway && test -z "$$(find . -name '*.go' -exec gofmt -l {} +)"

portfolio-ci: format-check test
	@echo "Source tests complete; actual Mac/Ollama inference, benchmarks and browser visual checks remain operator-only."
