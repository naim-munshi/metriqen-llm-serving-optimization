package httpapi

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestQueueWaitHistogramBucketBoundaries(t *testing.T) {
	tests := []struct {
		duration time.Duration
		bucket   int
	}{
		{0, 0},
		{10 * time.Millisecond, 0},
		{10*time.Millisecond + time.Nanosecond, 1},
		{50 * time.Millisecond, 1},
		{50*time.Millisecond + time.Nanosecond, 2},
		{100 * time.Millisecond, 2},
		{250 * time.Millisecond, 3},
		{500 * time.Millisecond, 4},
		{time.Second, 5},
		{2 * time.Second, 6},
		{5 * time.Second, 7},
		{5*time.Second + time.Nanosecond, 8},
	}
	for _, tc := range tests {
		if got := queueWaitBucket(tc.duration); got != tc.bucket {
			t.Fatalf("wait=%s bucket=%d, want %d", tc.duration, got, tc.bucket)
		}
	}
}

func TestQueueWaitHistogramCountsAllQueuedOutcomes(t *testing.T) {
	s := newAdmissionScheduler(1, 2)
	if err := s.acquire(context.Background()); err != nil {
		t.Fatal(err)
	}
	// No queue wait sample is created for immediate admissions.
	snap := s.snapshot()
	if snap.QueueWaitObservationsTotal != 0 || snap.QueueWaitDurationTotalUs != 0 {
		t.Fatalf("immediate admission recorded queue latency: %+v", snap)
	}
	// Force realistic elapsed times without a timing-sensitive sleep or model.
	s.recordQueuedOutcome(nil, time.Now().Add(-75*time.Millisecond))
	s.recordQueuedOutcome(context.DeadlineExceeded, time.Now().Add(-300*time.Millisecond))
	s.recordQueuedOutcome(context.Canceled, time.Now().Add(-3*time.Second))
	snap = s.snapshot()
	if snap.QueueWaitObservationsTotal != 3 ||
		snap.AdmittedQueuedTotal != 1 || snap.QueueTimeoutTotal != 1 ||
		snap.QueueCanceledTotal != 1 ||
		snap.QueueWaitDurationTotalUs < 3375000 {
		t.Fatalf("missing wait outcomes/durations: %+v", snap)
	}
	if snap.QueueWaitUpperBoundsMs != queueWaitUpperBoundsMs {
		t.Fatalf("unexpected bucket boundaries: %v", snap.QueueWaitUpperBoundsMs)
	}
	if snap.QueueWaitBucketCounts[2] != 1 ||
		snap.QueueWaitBucketCounts[4] != 1 ||
		snap.QueueWaitBucketCounts[7] != 1 {
		t.Fatalf("unexpected wait bucket counts: %+v", snap.QueueWaitBucketCounts)
	}
	var sum uint64
	for _, value := range snap.QueueWaitBucketCounts {
		sum += value
	}
	if sum != snap.QueueWaitObservationsTotal {
		t.Fatalf("histogram samples differ from observations: %d versus %d", sum, snap.QueueWaitObservationsTotal)
	}
	s.release()
}

func TestQueueWaitHistogramMeasuresActualQueuedDeadline(t *testing.T) {
	s := newAdmissionScheduler(1, 1)
	if err := s.acquire(context.Background()); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Millisecond)
	defer cancel()
	if err := s.acquire(ctx); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected deadline while queued, got %v", err)
	}
	snap := s.snapshot()
	if snap.QueueWaitObservationsTotal != 1 ||
		snap.QueueTimeoutTotal != 1 || snap.AdmittedQueuedTotal != 0 ||
		snap.QueueWaitDurationTotalUs == 0 {
		t.Fatalf("queued deadline not observed: %+v", snap)
	}
	var sum uint64
	for _, count := range snap.QueueWaitBucketCounts {
		sum += count
	}
	if sum != 1 {
		t.Fatalf("queued timeout must increment exactly one bucket, got %d", sum)
	}
	s.release()
}
