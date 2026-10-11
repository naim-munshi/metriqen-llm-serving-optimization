.PHONY: test test-python test-go test-e2e test-benchmark test-nlp format-check

test: test-python test-go test-e2e test-benchmark test-nlp

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

format-check:
	cd services/inference-gateway && test -z "$$(gofmt -l $(find . -name '*.go'))"
