package server

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestCompactActionsSendTheSameEvents(t *testing.T) {
	named := `[{"action":"click","x":40,"y":50,"button":"right","count":2,"modifiers":["ctrl"]},{"action":"move","x":60,"y":70},{"action":"path","points":[[20,20],[80,90]]},{"action":"scroll","x":100,"y":100,"dy":3,"unit":"notch","dx":-1},{"action":"type","text":"hello\nworld"},{"action":"key","keys":["ctrl","a"]},{"action":"key","keys":["right"],"repeat":7},{"action":"pause","ms":0}]`
	compact := `[["click",40,50,"right",2,["ctrl"]],["move",60,70],["path",[[20,20],[80,90]]],["scroll",100,100,3,"notch",-1],["type","hello\nworld"],["key",["ctrl","a"]],["key","right",7],["pause",0]]`
	var inputs [][][]string
	for _, actions := range []string{named, compact} {
		e := newTestEnv(t)
		e.window("0xa1", "1a", 320, 200)
		e.screenshot(`{"window":"0xa1"}`)
		res := e.act(`{"window":"0xa1","shot":"s1","capture":"none","actions":` + actions + `}`)
		if res.IsError || get(t, res, "actions.delivery") != "complete" {
			t.Fatal(text(res))
		}
		inputs = append(inputs, e.inputs())
	}
	if !reflect.DeepEqual(inputs[0], inputs[1]) {
		t.Fatalf("named %q, compact %q", inputs[0], inputs[1])
	}
}

func TestInvalidCompactActionsSendNothing(t *testing.T) {
	for _, action := range []string{`[]`, `["missing",10,20]`, `["click",10]`, `["click",null,20]`, `["type",10]`, `["key",[]]`, `["key","right",501]`, `["move",10,20,30]`, `[10]`, `[null,20]`, `[10,null]`, `[true,20]`, `[10,"20"]`, `[10,20,30]`, `[-1,20]`} {
		e := newTestEnv(t)
		e.window("0xa1", "1a", 320, 200)
		e.screenshot(`{"window":"0xa1"}`)
		res := e.act(`{"window":"0xa1","shot":"s1","actions":[` + action + `]}`)
		if errorCode(t, res) == "" || get(t, res, "actions.delivery") != "none" || len(e.inputs()) != 0 {
			t.Fatal(action, text(res), e.inputs())
		}
	}
}

func TestPointClicksSendNamedClickEvents(t *testing.T) {
	var want [][]string
	for _, actions := range []string{`[{"action":"click","x":40,"y":50}]`, `[["click",40,50]]`, `[[40,50]]`} {
		e := newTestEnv(t)
		e.window("0xa1", "1a", 320, 200)
		e.screenshot(`{"window":"0xa1"}`)
		res := e.act(`{"shot":"s1","capture":"none","actions":` + actions + `}`)
		if res.IsError || get(t, res, "actions.delivery") != "complete" {
			t.Fatal(text(res))
		}
		if want == nil {
			want = e.inputs()
		} else if !reflect.DeepEqual(e.inputs(), want) {
			t.Fatalf("%s sent %v, want %v", actions, e.inputs(), want)
		}
	}
}

// The supplied shot, including a zoom, binds the target even after another
// window was captured. Exercise the registered MCP tool, not only its parser.
func TestShotIdentifiesWindow(t *testing.T) {
	for _, zoom := range []bool{false, true} {
		e := newTestEnv(t)
		e.window("0xa1", "1a", 320, 200)
		e.window("0xb2", "2b", 320, 200)
		shot := shotID(t, e.screenshot(`{"window":"0xa1"}`))
		want := [][]string{{"move", "5", "10", "click", "left", "1", "-"}}
		if zoom {
			shot = shotID(t, e.screenshot(`{"window":"0xa1","region":{"shot":"s1","x":40,"y":20,"width":100,"height":50}}`))
			want[0][1], want[0][2] = "25", "20"
		}
		e.screenshot(`{"window":"0xb2"}`)
		session := connect(t, e.server, nil)
		args, _ := json.Marshal(map[string]any{"shot": shot, "capture": "none", "actions": [][]int{{10, 20}}})
		res, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "act", Arguments: json.RawMessage(args)})
		if err != nil {
			t.Fatal(err)
		}
		res = mirrored(t, res)
		if res.IsError || get(t, res, "actions.delivery") != "complete" || !reflect.DeepEqual(e.inputs(), want) {
			t.Fatal(text(res), e.inputs())
		}
		for _, run := range readInputLog(t, e.inputLog) {
			if run.Event == "start" && !strings.Contains(strings.Join(run.Argv, " "), "/fake/0xa1.eis") {
				t.Fatalf("input went to another window: %v", run.Argv)
			}
		}
	}
}

func TestShotTargetCannotFallBackToAnotherWindow(t *testing.T) {
	for _, change := range []func(*testEnv){
		func(e *testEnv) { e.server.shots.remove("s1") },
		func(e *testEnv) { e.fake.removeWindow("0xa1") },
		func(e *testEnv) { e.fake.with(func() { e.fake.find("0xa1").StableID = "new" }) },
		func(e *testEnv) { e.fake.with(func() { e.fake.find("0xa1").Width = 400 }) },
	} {
		e := newTestEnv(t)
		e.window("0xa1", "1a", 320, 200)
		e.screenshot(`{"window":"0xa1"}`)
		e.window("0xb2", "2b", 320, 200)
		e.screenshot(`{"window":"0xb2"}`)
		change(e)
		res := e.act(`{"shot":"s1","capture":"none","actions":[[10,20]]}`)
		if errorCode(t, res) == "" || get(t, res, "actions.delivery") != "none" || len(e.inputs()) != 0 {
			t.Fatal(text(res), e.inputs())
		}
	}
}
