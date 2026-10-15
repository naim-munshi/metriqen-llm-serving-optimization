package httpapi

import (
	"sync"
	"testing"
	"time"
)

func TestInferenceDurationHistogramBoundariesAndAccounting(t *testing.T) {
	tests := []struct {
		elapsed time.Duration
		bucket  int
	}{
		{-time.Millisecond, 0},
		{0, 0},
		{100 * time.Millisecond, 0},
		{100*time.Millisecond + time.Nanosecond, 1},
		{250 * time.Millisecond, 1},
		{30 * time.Second, 7},
		{30*time.Second + time.Nanosecond, 8},
	}
	m := &inferenceOutcomeCounters{}
	for _, tc := range tests {
		m.start()
		m.finish(inferenceNonstreamComplete, false, tc.elapsed)
		s := m.snapshot()
		if s.AttemptsTotal != uint64(s.DurationObservationsTotal) ||
			s.ActiveAttempts != 0 || s.NonstreamCompleteTotal != s.AttemptsTotal {
			t.Fatalf("duration accounting mismatch: %+v", s)
		}
		if inferenceDurationBucket(tc.elapsed) != tc.bucket {
			t.Fatalf("elapsed %s assigned wrong bucket: got %d want %d", tc.elapsed, inferenceDurationBucket(tc.elapsed), tc.bucket)
		}
	}
	s := m.snapshot()
	var sum uint64
	for _, n := range s.DurationBucketCounts {
		sum += n
	}
	if s.SchemaVersion != 3 || sum != s.DurationObservationsTotal ||
		s.DurationObservationsTotal != uint64(len(tests)) {
		t.Fatalf("histogram count mismatch: %+v", s)
	}
	if s.DurationBucketUpperBoundsMs != inferenceDurationUpperBoundsMs {
		t.Fatalf("incorrect fixed bucket bounds: %+v", s.DurationBucketUpperBoundsMs)
	}
	expected := [9]uint64{3, 2, 0, 0, 0, 0, 0, 1, 1}
	if s.DurationBucketCounts != expected {
		t.Fatalf("unexpected histogram: got %v want %v", s.DurationBucketCounts, expected)
	}
	wantUs := uint64((0 + 0 + 100*time.Millisecond + 100*time.Millisecond + time.Nanosecond + 250*time.Millisecond + 30*time.Second + 30*time.Second + time.Nanosecond) / time.Microsecond)
	if s.DurationTotalUs != wantUs {
		t.Fatalf("duration sum mismatch got %d want %d", s.DurationTotalUs, wantUs)
	}
}

func TestInferenceDurationSnapshotIncludesActiveWithoutCompletedObservation(t *testing.T) {
	m := &inferenceOutcomeCounters{}
	m.start()
	s := m.snapshot()
	if s.AttemptsTotal != 1 || s.ActiveAttempts != 1 ||
		s.DurationObservationsTotal != 0 || s.DurationTotalUs != 0 {
		t.Fatalf("active incomplete attempt must not contribute to histogram: %+v", s)
	}
	m.finish(inferencePreResponseFailure, false, 250*time.Millisecond)
	s = m.snapshot()
	if s.DurationObservationsTotal != 1 || s.PreResponseFailureTotal != 1 ||
		s.DurationTotalUs != 250000 || s.DurationBucketCounts[1] != 1 {
		t.Fatalf("failure duration missing from histogram: %+v", s)
	}
}

func TestInferenceDurationConcurrentRecordsAndSnapshots(t *testing.T) {
	m := &inferenceOutcomeCounters{}
	const requests = 320
	var workers sync.WaitGroup
	workers.Add(requests)
	for i := 0; i < requests; i++ {
		go func() {
			defer workers.Done()
			m.start()
			_ = m.snapshot()
			m.finish(inferencePreResponseFailure, false, 120*time.Millisecond)
			_ = m.snapshot()
		}()
	}
	workers.Wait()
	s := m.snapshot()
	if s.AttemptsTotal != requests || s.ActiveAttempts != 0 ||
		s.PreResponseFailureTotal != requests ||
		s.DurationObservationsTotal != requests ||
		s.DurationBucketCounts[1] != requests ||
		s.DurationTotalUs != requests*120000 {
		t.Fatalf("concurrent duration metrics lost updates: %+v", s)
	}
}
