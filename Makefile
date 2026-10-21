# Python is invoked as python3 on macOS; override with PYTHON=/absolute/path/to/python if needed.
PYTHON ?= python3

.PHONY: test test-python test-go test-e2e test-benchmark test-nlp test-ui format-check portfolio-ci

# Source/CI gate only: this cannot replace real local Ollama, Mac, identity
# provider, screenshot or hardware-performance acceptance.
test: test-python test-go test-e2e test-benchmark test-nlp test-ui

test-python:
	cd services/control-api && $(PYTHON) -m pytest -q

test-go:
	cd services/inference-gateway && go test -race ./... && go vet ./...

test-e2e:
	$(PYTHON) -m unittest discover -s tests/e2e -v

test-benchmark:
	$(PYTHON) -m unittest discover -s tools/benchmark/tests -v

test-nlp:
	$(PYTHON) -m unittest discover -s tools/nlp_eval/tests -v

test-ui:
	node --test tests/ui/*.test.cjs

format-check:
	cd services/inference-gateway && test -z "$$(find . -name '*.go' -exec gofmt -l {} +)"

portfolio-ci: format-check test
	@echo "Source tests complete; actual Mac/Ollama inference, benchmarks and browser visual checks remain operator-only."
