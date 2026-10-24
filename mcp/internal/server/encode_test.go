package server

import (
	"context"
	"encoding/json"
	"math"
	"math/big"
	"reflect"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// A result with a number that JSON cannot hold reaches the client, with null in its place: the SDK sends no reply for a
// result that does not encode, and the client would wait until it gives up.
func TestNonFiniteNumbersReachTheClient(t *testing.T) {
	e := newTestEnv(t)
	srv := mcp.NewServer(&mcp.Implementation{Name: "test", Version: "0"}, nil)
	srv.AddTool(&mcp.Tool{Name: "nonfinite", InputSchema: json.RawMessage(`{"type":"object"}`)}, e.server.handle(func(context.Context, *call) *mcp.CallToolResult {
		out := &actOut{Shot: &shotOut{ID: "s1", Width: 10, Height: 10, Frame: rectOut{X: math.Inf(1), Y: math.NaN(), Width: 5, Height: 5}}, NewWindows: []newWindowOut{}}
		out.Summary = "shot s1"
		return newResult(out, out.resultText, nil, "")
	}))
	serverTransport, clientTransport := mcp.NewInMemoryTransports()
	if _, err := srv.Connect(context.Background(), serverTransport, nil); err != nil {
		t.Fatal(err)
	}
	session, err := mcp.NewClient(&mcp.Implementation{Name: "test", Version: "0"}, nil).Connect(context.Background(), clientTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer session.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	res, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "nonfinite", Arguments: map[string]any{}})
	if err != nil {
		t.Fatalf("no reply: %v", err)
	}
	if got := get(t, res, "shot.frame"); !reflect.DeepEqual(got, map[string]any{"x": nil, "y": nil, "width": 5.0, "height": 5.0}) {
		t.Errorf("frame %v", got)
	}
	if text(res) != "shot s1" {
		t.Errorf("text %q", text(res))
	}
}

// Content with a number that JSON cannot hold encodes as encoding/json encodes it with that number as null: field names,
// omitempty and embedded structs as encoding/json reads them.
func TestStructuredJSONKeepsTheEncoding(t *testing.T) {
	out := func(x float64) *actOut {
		o := &actOut{Actions: &actionsOut{Done: 1, Total: 2, Delivery: "partial", FailedIndex: ptr(1), HeldBack: "why"},
			Wait:         &waitOut{Ended: "quiet", RedrawObserved: true, Commits: 3, QuietMs: 200, ElapsedMs: 412, Animated: []boxOut{{X: 1, Y: 2, Width: 3, Height: 4}}},
			Shot:         &shotOut{ID: "s2", Width: 10, Height: 10, Frame: rectOut{X: x, Width: 5, Height: 5}, Changes: &changesOut{Base: "s1", Regions: []regionOut{}}},
			BeforeScroll: &beforeScrollOut{Wait: &waitOut{Ended: "quiet"}, Shot: &shotOut{ID: "s1"}},
			Lease:        &leaseOut{Held: true, RemainingS: 60}, NewWindows: []newWindowOut{}, captureNote: "not encoded"}
		o.Summary, o.Hint = "summary", ""
		return o
	}
	decode := func(data []byte) map[string]any {
		var v map[string]any
		if err := json.Unmarshal(data, &v); err != nil {
			t.Fatal(err)
		}
		return v
	}
	got, err := structuredJSON(out(math.Inf(-1)))
	if err != nil {
		t.Fatal(err)
	}
	finiteData, err := json.Marshal(out(7))
	if err != nil {
		t.Fatal(err)
	}
	want := decode(finiteData)
	want["shot"].(map[string]any)["frame"].(map[string]any)["x"] = nil
	if g := decode(got); !reflect.DeepEqual(g, want) {
		t.Errorf("got  %v\nwant %v", g, want)
	}
}

// A value whose pointer has the json.Marshaler, as *big.Int does, encodes through it beside a number that JSON cannot
// hold, as encoding/json encodes it.
func TestStructuredJSONUsesPointerMarshalers(t *testing.T) {
	if got, err := structuredJSON(map[string]any{"bad": math.NaN(), "n": big.NewInt(123)}); err != nil || string(got) != `{"bad":null,"n":123}` {
		t.Errorf("%s %v", got, err)
	}
}
