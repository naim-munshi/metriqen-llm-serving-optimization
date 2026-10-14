package httpapi

import (
    "context"
    "errors"
    "net"
    "sync"
    "syscall"
)

// Upstream metrics classify request outcomes AFTER admission. They are
// process-local, bounded, aggregate counters with no model, prompt, user,
// response-body, URL, or raw error labels. No inference request is made by
// the metrics endpoint itself.
type upstreamFailureKind uint8

const (
    upstreamConnectionRefused upstreamFailureKind = iota
    upstreamTransportError
    upstreamTimeout
    upstreamHTTPRejected
    upstreamHTTPError
    upstreamProtocolError
    upstreamResponseTooLarge
    upstreamStreamReadError
    upstreamStreamTooLarge
)

type upstreamFailureCounters struct {
    mu sync.Mutex
    refused uint64
    transport uint64
    timeout uint64
    rejected uint64
    httpError uint64
    protocol uint64
    tooLarge uint64
    streamRead uint64
    streamTooLarge uint64
}

// Snapshot schema 1 intentionally contains only fixed keys. These are
// failure-event counters, NOT throughput, success-rate, or availability
// measurements. Streaming failures can occur after HTTP 200 was sent.
type upstreamFailureSnapshot struct {
    SchemaVersion int `json:"schema_version"`
    ConnectionRefusedTotal uint64 `json:"connection_refused_total"`
    TransportErrorsTotal uint64 `json:"transport_errors_total"`
    TimeoutsTotal uint64 `json:"timeouts_total"`
    HTTPRejectedTotal uint64 `json:"http_rejected_total"`
    HTTPOtherStatusTotal uint64 `json:"http_other_status_total"`
    ProtocolErrorsTotal uint64 `json:"protocol_errors_total"`
    ResponseTooLargeTotal uint64 `json:"response_too_large_total"`
    StreamReadErrorsTotal uint64 `json:"stream_read_errors_total"`
    StreamSizeLimitTotal uint64 `json:"stream_size_limit_total"`
}

func (m *upstreamFailureCounters) record(kind upstreamFailureKind) {
    m.mu.Lock()
    defer m.mu.Unlock()
    switch kind {
    case upstreamConnectionRefused:
        m.refused++
    case upstreamTransportError:
        m.transport++
    case upstreamTimeout:
        m.timeout++
    case upstreamHTTPRejected:
        m.rejected++
    case upstreamHTTPError:
        m.httpError++
    case upstreamProtocolError:
        m.protocol++
    case upstreamResponseTooLarge:
        m.tooLarge++
    case upstreamStreamReadError:
        m.streamRead++
    case upstreamStreamTooLarge:
        m.streamTooLarge++
    }
}

func (m *upstreamFailureCounters) snapshot() upstreamFailureSnapshot {
    m.mu.Lock()
    defer m.mu.Unlock()
    return upstreamFailureSnapshot{
        SchemaVersion: 1,
        ConnectionRefusedTotal: m.refused,
        TransportErrorsTotal: m.transport,
        TimeoutsTotal: m.timeout,
        HTTPRejectedTotal: m.rejected,
        HTTPOtherStatusTotal: m.httpError,
        ProtocolErrorsTotal: m.protocol,
        ResponseTooLargeTotal: m.tooLarge,
        StreamReadErrorsTotal: m.streamRead,
        StreamSizeLimitTotal: m.streamTooLarge,
    }
}

// Transport errors are classified from typed errors, never by matching
// private error strings or capturing endpoint addresses.
func classifyUpstreamTransport(err error) upstreamFailureKind {
    if errors.Is(err, syscall.ECONNREFUSED) {
        return upstreamConnectionRefused
    }
    var netErr net.Error
    if errors.Is(err, syscall.ETIMEDOUT) ||
        errors.Is(err, context.DeadlineExceeded) ||
        (errors.As(err, &netErr) && netErr.Timeout()) {
        return upstreamTimeout
    }
    return upstreamTransportError
}
