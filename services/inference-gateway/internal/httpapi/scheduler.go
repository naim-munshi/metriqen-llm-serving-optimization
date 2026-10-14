package httpapi

import (
	"context"
	"errors"
	"strconv"
	"sync"
	"time"
)

const (
	maxQueuedRequests = 32
	queueWaitTimeout  = 5 * time.Second
)

var errAdmissionFull = errors.New("local inference admission is full")

// admissionScheduler bounds active requests and, optionally, pending requests.
// It grants each released slot to the oldest still-queued waiter. Nothing is
// stored in the scheduler except a completion channel: no prompts, model
// outputs or tenant information. It is process-local and not distributed.
type admissionScheduler struct {
	mu        sync.Mutex
	active    int
	maxActive int
	maxQueued int
	waiting   []*admissionWaiter
}

type admissionWaiter struct {
	ready chan struct{}
}

func newAdmissionScheduler(maxActive, maxQueued int) *admissionScheduler {
	return &admissionScheduler{maxActive: maxActive, maxQueued: maxQueued}
}

// parseQueueDepth keeps the existing zero-wait overload behavior by default.
// An explicit decimal integer between 0 and 32 enables bounded FIFO waiting.
func parseQueueDepth(raw string) (int, error) {
	if raw == "" {
		return 0, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil || value < 0 || value > maxQueuedRequests || strconv.Itoa(value) != raw {
		return 0, errors.New("queue depth must be a canonical integer between 0 and 32")
	}
	return value, nil
}

// acquire either reserves an active slot, waits for a FIFO handoff, or fails
// closed when the waiting queue is full. Context cancellation removes waiters
// without leaving phantom capacity or forwarding any pending prompt upstream.
func (s *admissionScheduler) acquire(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	s.mu.Lock()
	if err := ctx.Err(); err != nil {
		s.mu.Unlock()
		return err
	}
	if s.active < s.maxActive && len(s.waiting) == 0 {
		s.active++
		s.mu.Unlock()
		return nil
	}
	if len(s.waiting) >= s.maxQueued {
		s.mu.Unlock()
		return errAdmissionFull
	}
	waiter := &admissionWaiter{ready: make(chan struct{})}
	s.waiting = append(s.waiting, waiter)
	s.mu.Unlock()

	select {
	case <-waiter.ready:
		if err := ctx.Err(); err != nil {
			s.release() // A handoff occurred during cancellation.
			return err
		}
		return nil
	case <-ctx.Done():
		s.mu.Lock()
		for index, pending := range s.waiting {
			if pending == waiter {
				copy(s.waiting[index:], s.waiting[index+1:])
				s.waiting[len(s.waiting)-1] = nil
				s.waiting = s.waiting[:len(s.waiting)-1]
				s.mu.Unlock()
				return ctx.Err()
			}
		}
		s.mu.Unlock()
		// A release already handed off the active slot and removed our
		// queue entry; pass that slot to the next waiter or free it.
		s.release()
		return ctx.Err()
	}
}

// release transfers an active slot directly to a queued waiter, preserving
// admission capacity and FIFO order without waking every waiter.
func (s *admissionScheduler) release() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.active == 0 {
		panic("inference admission released without an active slot")
	}
	if len(s.waiting) > 0 {
		waiter := s.waiting[0]
		copy(s.waiting, s.waiting[1:])
		s.waiting[len(s.waiting)-1] = nil
		s.waiting = s.waiting[:len(s.waiting)-1]
		close(waiter.ready)
		return
	}
	s.active--
}
