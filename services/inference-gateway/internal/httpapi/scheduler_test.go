package httpapi

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"
)

func waitForQueue(t *testing.T, s *admissionScheduler, count int) {
	t.Helper()
	limit := time.After(3 * time.Second)
	tick := time.NewTicker(time.Millisecond)
	defer tick.Stop()
	for {
		s.mu.Lock()
		actual := len(s.waiting)
		s.mu.Unlock()
		if actual == count {
			return
		}
		select {
		case <-limit:
			t.Fatalf("waiter count: got %d, want %d", actual, count)
		case <-tick.C:
		}
	}
}

func TestFIFOAdmissionOrderAndBoundedQueue(t *testing.T) {
	s := newAdmissionScheduler(1, 2)
	if err := s.acquire(context.Background()); err != nil {
		t.Fatal(err)
	}
	order := make(chan int, 2)
	releaseFirst := make(chan struct{})
	releaseSecond := make(chan struct{})
	done := make(chan error, 2)
	spawn := func(id int, finish <-chan struct{}) {
		go func() {
			if err := s.acquire(context.Background()); err != nil {
				done <- err
				return
			}
			order <- id
			<-finish
			s.release()
			done <- nil
		}()
	}
	spawn(1, releaseFirst)
	waitForQueue(t, s, 1)
	spawn(2, releaseSecond)
	waitForQueue(t, s, 2)
	if err := s.acquire(context.Background()); !errors.Is(err, errAdmissionFull) {
		t.Fatalf("unexpected full-queue result: %v", err)
	}
	s.release()
	select {
	case got := <-order:
		if got != 1 {
			t.Fatalf("FIFO violated: first grant %d", got)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("first waiter never received active slot")
	}
	select {
	case <-order:
		t.Fatal("second waiter bypassed active first waiter")
	default:
	}
	close(releaseFirst)
	select {
	case got := <-order:
		if got != 2 {
			t.Fatalf("FIFO violated: second grant %d", got)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("second waiter never received active slot")
	}
	close(releaseSecond)
	for i := 0; i < 2; i++ {
		select {
		case err := <-done:
			if err != nil {
				t.Fatal(err)
			}
		case <-time.After(3 * time.Second):
			t.Fatal("worker never released capacity")
		}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.active != 0 || len(s.waiting) != 0 {
		t.Fatalf("scheduler leaked slots: active=%d queued=%d", s.active, len(s.waiting))
	}
}

func TestCanceledQueuedWaiterFreesCapacityAndDoesNotStealSlot(t *testing.T) {
	s := newAdmissionScheduler(1, 1)
	if err := s.acquire(context.Background()); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	finished := make(chan error, 1)
	go func() { finished <- s.acquire(ctx) }()
	waitForQueue(t, s, 1)
	cancel()
	select {
	case err := <-finished:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("canceled waiter: %v", err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("canceled waiter remained blocked")
	}
	waitForQueue(t, s, 0)
	s.mu.Lock()
	active, queued := s.active, len(s.waiting)
	s.mu.Unlock()
	if active != 1 || queued != 0 {
		t.Fatalf("cancellation changed active capacity: active=%d queued=%d", active, queued)
	}
	s.release()
	if err := s.acquire(context.Background()); err != nil {
		t.Fatalf("active slot remained lost: %v", err)
	}
	s.release()
}

func TestQueueDeadlineDoesNotHoardActiveCapacity(t *testing.T) {
	s := newAdmissionScheduler(1, 1)
	if err := s.acquire(context.Background()); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Millisecond)
	defer cancel()
	got := s.acquire(ctx)
	if !errors.Is(got, context.DeadlineExceeded) {
		t.Fatalf("unexpected queue timeout: %v", got)
	}
	waitForQueue(t, s, 0)
	s.release()
	if err := s.acquire(context.Background()); err != nil {
		t.Fatalf("deadline leaked capacity: %v", err)
	}
	s.release()
}

func TestPreCanceledContextNeverEnqueues(t *testing.T) {
	s := newAdmissionScheduler(1, 1)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := s.acquire(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("pre-canceled request admitted: %v", err)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.active != 0 || len(s.waiting) != 0 {
		t.Fatal("pre-canceled request changed capacity")
	}
}

func TestQueueConfigurationFailsClosedAndPreservesDefault(t *testing.T) {
	for _, tc := range []struct {
		raw  string
		want int
	}{
		{"", 0}, {"0", 0}, {"1", 1}, {"32", 32},
	} {
		got, err := parseQueueDepth(tc.raw)
		if err != nil || got != tc.want {
			t.Fatalf("queue depth %q got %d, %v; want %d", tc.raw, got, err, tc.want)
		}
	}
	for _, invalid := range []string{
		"-1", "+1", "33", "01", " 2", "2 ", "1.5", "bad", "1,2",
		strings.Repeat("9", 128), fmt.Sprintf("%d", maxQueuedRequests+1),
	} {
		if _, err := parseQueueDepth(invalid); err == nil {
			t.Fatalf("invalid queue depth accepted: %q", invalid)
		}
	}
}
