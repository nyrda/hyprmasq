package server

import (
	"context"
	"errors"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// setLeased records whether this session holds the window's lease, which Shutdown releases.
func (s *Server) setLeased(window string, held bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if held {
		s.leased[window] = true
	} else {
		delete(s.leased, window)
	}
}

// renewLease starts or renews this session's lease on a window that supports it, and returns the lease, or nil.
func (s *Server) renewLease(ctx context.Context, window string, caps hypr.Capabilities) *hypr.Lease {
	if s.stopping() {
		// Shutdown releases the leases it knows of once the running calls are done.
		return nil
	}
	if !caps.Lease {
		return nil
	}
	lease, err := s.hypr.Lease(ctx, window, s.cfg.LeaseMs, s.owner)
	s.leaseAnswered(window, lease.Held, err)
	if err != nil {
		return nil
	}
	return &lease
}

// restoreLease undoes the renewal before a batch that sent nothing: without input, a new lease would hold no focus,
// and a lease of another session goes back to it for the time it had left.
func (s *Server) restoreLease(ctx context.Context, window string, before *hypr.Lease, read time.Time) {
	if s.stopping() || (before != nil && before.Held && before.Owner == s.owner) {
		return
	}
	remaining := 0
	if before != nil && before.Held {
		remaining = before.RemainingMs - int(time.Since(read).Milliseconds())
	}
	if remaining < 1 {
		s.releaseLease(ctx, window)
		return
	}
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Second)
	defer cancel()
	_, err := s.hypr.Lease(ctx, window, remaining, before.Owner)
	s.leaseAnswered(window, false, err)
}

// releaseLease ends this session's lease on the window.
func (s *Server) releaseLease(ctx context.Context, window string) {
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Second)
	defer cancel()
	_, err := s.hypr.LeaseRelease(ctx, window, s.owner)
	s.leaseAnswered(window, false, err)
}

// leaseAnswered records whether this session holds the window's lease after a lease request. Without an answer of the
// plugin the request may still have taken effect, so the window stays on the list that Shutdown releases.
func (s *Server) leaseAnswered(window string, held bool, err error) {
	var pe *hypr.Error
	switch {
	case err == nil:
		s.setLeased(window, held)
	case errors.As(err, &pe):
		// The plugin refused it, so it changed nothing.
		if pe.Code == "window_gone" || pe.Code == "no_socket" {
			s.setLeased(window, false)
		}
	default:
		s.setLeased(window, true)
	}
}

// releaseTool runs the release tool.
func (s *Server) releaseTool(ctx context.Context, c *call) *mcp.CallToolResult {
	window, err := c.windowArg()
	if err != nil {
		return simpleError(err)
	}
	ctx, cancel := context.WithTimeout(ctx, requestTimeout)
	defer cancel()
	if err := s.checkPlugin(ctx); err != nil {
		return simpleError(err)
	}
	st, err := s.hypr.State(ctx, window)
	if err != nil {
		return simpleError(err)
	}
	released := false
	if st.Socket != nil {
		// release ends the window's lease, whichever session renewed it last.
		if released, err = s.hypr.LeaseRelease(ctx, window, ""); err != nil {
			return simpleError(err)
		}
	}
	s.setLeased(window, false)
	out := &releaseOut{Released: released}
	out.Summary = "no lease was held on " + window
	if released {
		out.Summary = "lease released; " + window + " stays open"
		if st.Lease != nil && st.Lease.Owner != s.owner {
			out.Summary = "lease of another session released; " + window + " stays open"
		}
	}
	return newResult(out, out.resultText, nil, "")
}

type releaseOut struct {
	resultText
	Released bool `json:"released"`
}
