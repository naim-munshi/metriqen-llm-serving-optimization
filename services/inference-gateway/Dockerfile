FROM golang:1.27.2-alpine AS builder
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/gateway ./cmd/server

FROM scratch
COPY --from=builder /out/gateway /gateway
USER 65532:65532
EXPOSE 8081
ENTRYPOINT ["/gateway"]
