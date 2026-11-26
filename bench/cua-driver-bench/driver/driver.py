"""hyprmasq as an AgentEnv computer driver on Omarchy (Hyprland Wayland).

Forwards hyprmasq-mcp's 6 tools (windows, screenshot, act, launch, release, close) to agents over stdio,
keeping one persistent hyprmasq-mcp subprocess per caller session so shot IDs persist across turns.
Implements computer/v1 display, capture (via grim) and pointer/keyboard operations (via hyprmasq-input
and hyprctl hyprmasq) for desktop recording and Gate 3 conformance probing.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import struct
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from mcp import ClientSession, McpError, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CONNECTION_CLOSED, CallToolResult, InitializeResult, TextContent

from agentenv_protocol import ForwardedTool, ToolContext, server_instructions
from agentenv_protocol.computer import (
    PRINTABLE_KEYS,
    ComputerDriverEnvironment,
    DeliveryFailed,
    DeviceUnavailable,
    Display,
    Image,
    ReadyCheck,
    Unsupported,
    capture,
    click,
    computer_driver,
    display,
    drag,
    press,
    scroll,
    type_text,
)

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "mcp.json"
HYPRMASQ_MCP = Path("/usr/local/bin/hyprmasq-mcp")
HYPRMASQ_INPUT = Path("/usr/local/bin/hyprmasq-input")

IDLE_S, MAX_SESSIONS, SWEEP_S = 1800, 8, 60
CLOSE_TIMEOUT_S = 10
CMD_TIMEOUT_S = 30

MODIFIERS = {"Control": "ctrl", "Shift": "shift", "Alt": "alt", "Meta": "super", "Super": "super"}
NAMED = {
    "Enter": "enter", "Tab": "tab", "Escape": "esc", " ": "space", "Backspace": "backspace", "Delete": "delete",
    "Insert": "insert", "Home": "home", "End": "end", "PageUp": "pageup", "PageDown": "pagedown",
    "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
    **{f"F{n}": f"f{n}" for n in range(1, 13)},
}


@computer_driver(
    name="hyprmasq",
    supports=[{"os": {"type": "linux", "distribution": ["omarchy"], "display": ["wayland"]}, "arch": ["amd64"]}],
)
class HyprmasqDriver(ComputerDriverEnvironment):
    buttons = ("left", "right", "middle")
    max_click_count = 3
    max_drag_points = 200
    max_scroll_amount = 50
    keys = (*MODIFIERS, *NAMED, *PRINTABLE_KEYS)

    def __init__(self) -> None:
        super().__init__()
        self._pinned: list[ReadyCheck] | None = None
        self._sessions: dict[str | None, _McpSession] = {}
        self._sweeper: asyncio.Task | None = None
        self._closing: set[asyncio.Task] = set()
        self._input_lock = asyncio.Lock()
        self._last_window: str | None = None

    def forwarded_tools(self) -> list[ForwardedTool]:
        return [
            ForwardedTool.listed(t, functools.partial(self._forward, t["name"]))
            for t in _manifest()["tools"]
        ]

    @server_instructions
    def hyprmasq_instructions(self) -> str:
        return _manifest()["instructions"]

    async def ready_checks(self) -> list[ReadyCheck]:
        plugin_check = await _check_plugin()
        if not plugin_check.ok:
            return [plugin_check]
        if self._pinned is None or not all(c.ok for c in self._pinned):
            self._pinned = await self._pinned_checks()
        return [plugin_check, *self._pinned]

    async def _pinned_checks(self) -> list[ReadyCheck]:
        hooks_data = await _hyprctl_json("hyprmasq", "hooks", "check")
        if not isinstance(hooks_data, dict) or not hooks_data.get("ok"):
            return [ReadyCheck("hyprmasq hooks", False, f"hooks check failed: {hooks_data}")]
        refused = [g for g in (hooks_data.get("groups") or []) if g.get("state") == "refused"]
        if refused:
            return [ReadyCheck("hyprmasq hooks", False, f"hooks refused: {refused}")]
        try:
            async with self._open_mcp() as (session, initialized):
                listed = (await session.list_tools()).tools
        except Exception as e:
            return [ReadyCheck("hyprmasq-mcp", False, f"hyprmasq-mcp failed: {type(e).__name__}: {e}")]
        running = {t.name for t in listed}
        pinned = {t["name"] for t in _manifest()["tools"]}
        if running != pinned:
            return [ReadyCheck("hyprmasq-mcp", False, f"tools {sorted(running)} != pinned {sorted(pinned)}")]
        return [
            ReadyCheck("hyprmasq hooks", True, "all hook groups install cleanly"),
            ReadyCheck("hyprmasq-mcp", True, f"{len(running)} tools, as pinned"),
        ]

    @contextlib.asynccontextmanager
    async def _open_mcp(self) -> AsyncIterator[tuple[ClientSession, InitializeResult]]:
        env = dict(os.environ)
        env["PATH"] = f"/usr/local/bin:/usr/bin:/bin:{env.get('PATH', '')}"
        server = StdioServerParameters(command=str(HYPRMASQ_MCP), args=[], env=env)
        async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
            yield session, await session.initialize()

    async def _forward(self, tool: str, /, **arguments: Any) -> CallToolResult:
        session = self._sessions.pop(caller := ToolContext.current().caller.session, None) or _McpSession(self._open_mcp)
        self._evict(keep=MAX_SESSIONS - 1)
        self._sessions[caller] = session
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep())
        try:
            return await session.call(tool, arguments)
        except Exception as e:
            return CallToolResult(
                isError=True,
                content=[TextContent(type="text", text=f"hyprmasq-mcp failed on {tool}: {type(e).__name__}: {e}")],
            )

    def _evict(self, keep: int = MAX_SESSIONS) -> None:
        now = time.monotonic()
        free = [caller for caller, sess in self._sessions.items() if not sess.busy]
        idle = [caller for caller in free if now - self._sessions[caller].used > IDLE_S]
        over = max(0, len(self._sessions) - len(idle) - keep)
        spare = [caller for caller in free if caller not in idle][:over]
        for caller in [*idle, *spare]:
            closing = asyncio.create_task(self._sessions.pop(caller).close())
            self._closing.add(closing)
            closing.add_done_callback(self._closing.discard)

    async def _sweep(self) -> None:
        while self._sessions:
            await asyncio.sleep(SWEEP_S)
            self._evict()

    # computer/v1 operations (used by recording and Gate 3 conformance probe)

    @display
    async def screen(self) -> Display:
        monitors = await _hyprctl_json("monitors")
        if not isinstance(monitors, list) or not monitors:
            raise DeviceUnavailable("hyprctl monitors returned no monitors")
        mon = next((m for m in monitors if m.get("focused")), monitors[0])
        scale = float(mon.get("scale") or 1.0)
        width = round(float(mon["width"]) / scale)
        height = round(float(mon["height"]) / scale)
        return Display(width, height, scale)

    @capture
    async def grab(self) -> Image:
        proc = await asyncio.create_subprocess_exec(
            "grim", "-t", "png", "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), CMD_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise DeviceUnavailable("grim timed out") from None
        if proc.returncode != 0 or len(stdout) < 24 or stdout[:8] != b"\x89PNG\r\n\x1a\n":
            raise DeviceUnavailable(f"grim failed ({proc.returncode}): {stderr.decode(errors='replace').strip()}")
        width, height = struct.unpack(">II", stdout[16:24])
        return Image(width, height, stdout, "png")

    @click
    async def click_at(self, x, y, button, count, modifiers) -> None:
        addr, lx, ly = await self._target_point(x, y)
        mods = _mods(modifiers)
        args = ["move", str(lx), str(ly), "click", button, str(count)]
        if mods:
            args.append(mods)
        await self._send_input(addr, args)

    @drag
    async def drag_along(self, path, button, duration_ms, modifiers) -> None:
        addr, ox, oy = await self._target_window(path[0][0], path[0][1])
        mods = _mods(modifiers)
        args = ["path", str(len(path))]
        for px, py in path:
            args += [str(px - ox), str(py - oy)]
        args.append(button)
        if mods:
            args.append(mods)
        await self._send_input(addr, args)

    @scroll
    async def scroll_at(self, x, y, direction, amount, modifiers) -> None:
        if modifiers:
            raise Unsupported("hyprmasq scrolls with no keys held")
        addr, lx, ly = await self._target_point(x, y)
        deltas = {
            "up": (0, -amount),
            "down": (0, amount),
            "left": (-amount, 0),
            "right": (amount, 0),
        }
        if direction not in deltas:
            raise Unsupported(f"unsupported scroll direction: {direction}")
        dx, dy = deltas[direction]
        await self._send_input(addr, ["move", str(lx), str(ly), "wheel", str(dx), str(dy)])

    @type_text
    async def type_in(self, text) -> None:
        addr = await self._keyboard_target()
        segments = _split_text(text)
        async with self._input_lock:
            sock = await _open_socket(addr)
            for kind, value in segments:
                if kind == "actions":
                    await _run_hyprmasq_input(sock, value)
                elif kind == "paste":
                    await _paste_text(sock, value)

    @press
    async def press_keys(self, keys) -> None:
        addr = await self._keyboard_target()
        chord = _chord(keys)
        await self._send_input(addr, ["keys", chord])

    async def _target_window(self, x: float, y: float) -> tuple[str, float, float]:
        clients = await _hyprctl_json("clients")
        active_ws = await _hyprctl_json("activeworkspace")
        ws_id = active_ws.get("id") if isinstance(active_ws, dict) else None
        if not isinstance(clients, list):
            raise DeviceUnavailable("hyprctl clients failed")
        matches = []
        for c in clients:
            if not c.get("mapped") or c.get("hidden"):
                continue
            if ws_id is not None and (c.get("workspace") or {}).get("id") != ws_id:
                continue
            at = c.get("at") or [0, 0]
            size = c.get("size") or [0, 0]
            if at[0] <= x < at[0] + size[0] and at[1] <= y < at[1] + size[1]:
                matches.append(c)
        if not matches:
            active = await _hyprctl_json("activewindow")
            if isinstance(active, dict) and active.get("address"):
                at = active.get("at") or [0, 0]
                self._last_window = active["address"]
                return active["address"], float(at[0]), float(at[1])
            raise DeliveryFailed(f"no window at ({x}, {y})")
        # Prefer fullscreen, then floating, then most recently focused (lowest focusHistoryID).
        matches.sort(key=lambda c: (
            0 if c.get("fullscreen") else 1,
            0 if c.get("floating") else 1,
            int(c.get("focusHistoryID", 9999)),
        ))
        win = matches[0]
        at = win.get("at") or [0, 0]
        self._last_window = win["address"]
        return win["address"], float(at[0]), float(at[1])

    async def _target_point(self, x: float, y: float) -> tuple[str, float, float]:
        addr, ox, oy = await self._target_window(x, y)
        return addr, x - ox, y - oy

    async def _keyboard_target(self) -> str:
        active = await _hyprctl_json("activewindow")
        if isinstance(active, dict) and active.get("address"):
            self._last_window = active["address"]
            return active["address"]
        if self._last_window:
            return self._last_window
        raise DeliveryFailed("no focused window for keyboard input")

    async def _send_input(self, addr: str, args: list[str]) -> None:
        async with self._input_lock:
            sock = await _open_socket(addr)
            await _run_hyprmasq_input(sock, args)


class _McpSession:
    """One persistent hyprmasq-mcp stdio process per caller session."""

    def __init__(
        self,
        open_server: Callable[[], contextlib.AbstractAsyncContextManager[tuple[ClientSession, InitializeResult]]],
    ) -> None:
        self._open = open_server
        self._calls: asyncio.Queue | None = None
        self._serving: set[asyncio.Task] = set()
        self.used, self.busy = time.monotonic(), 0

    async def call(self, tool: str, arguments: dict) -> CallToolResult:
        if self._calls is None:
            self._calls = asyncio.Queue()
            serving = asyncio.create_task(self._serve(self._calls))
            self._serving.add(serving)
            serving.add_done_callback(self._serving.discard)
        answer = asyncio.get_running_loop().create_future()
        self.busy += 1
        try:
            self._calls.put_nowait((tool, arguments, answer))
            return await answer
        finally:
            self.busy -= 1
            self.used = time.monotonic()

    async def close(self) -> None:
        if self._calls is not None:
            self._calls.put_nowait(None)
            self._calls = None
        if serving := set(self._serving):
            _, late = await asyncio.wait(serving, timeout=CLOSE_TIMEOUT_S)
            for task in late:
                task.cancel()

    async def _serve(self, calls: asyncio.Queue) -> None:
        stopped: BaseException = ConnectionError("hyprmasq-mcp stopped")
        try:
            async with self._open() as (session, _):
                try:
                    while (call := await calls.get()) is not None:
                        tool, arguments, answer = call
                        if answer.done():
                            continue
                        try:
                            result = await session.call_tool(tool, arguments)
                        except McpError as e:
                            _settle(answer, error=e)
                            if e.error.code == CONNECTION_CLOSED:
                                raise
                            continue
                        except Exception as e:
                            _settle(answer, error=e)
                            raise
                        _settle(answer, result=result)
                finally:
                    self._let_go(calls)
        except Exception as e:
            stopped = e
        finally:
            self._let_go(calls)
            while not calls.empty():
                if (call := calls.get_nowait()) is not None:
                    _settle(call[-1], error=stopped)

    def _let_go(self, calls: asyncio.Queue) -> None:
        if self._calls is calls:
            self._calls = None


def _settle(answer: asyncio.Future, *, result: Any = None, error: BaseException | None = None) -> None:
    if answer.done():
        return
    if error is not None:
        answer.set_exception(error)
    else:
        answer.set_result(result)


@functools.cache
def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


async def _hyprctl_json(*args: str) -> dict | list | None:
    proc = await asyncio.create_subprocess_exec(
        "hyprctl", "-j", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), CMD_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    try:
        data = json.loads(stdout.decode(errors="replace"))
    except ValueError:
        return None
    return data if isinstance(data, (dict, list)) else None


async def _check_plugin() -> ReadyCheck:
    ver = await _hyprctl_json("hyprmasq", "version")
    if isinstance(ver, dict) and ver.get("ok"):
        return ReadyCheck("hyprmasq plugin", True, f"hyprmasq {ver.get('version')} interface {ver.get('interface')}")
    # Collect diagnostics on why hyprmasq didn't answer.
    proc = await asyncio.create_subprocess_exec(
        "hyprctl", "plugin", "list",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), CMD_TIMEOUT_S)
    plugins = (out.decode(errors="replace") + err.decode(errors="replace")).strip()[:200]
    return ReadyCheck("hyprmasq plugin", False, f"hyprmasq version={ver!r}; plugins={plugins!r}")


async def _open_socket(addr: str) -> str:
    res = await _hyprctl_json("hyprmasq", "eis", "open", addr)
    if not isinstance(res, dict) or not res.get("ok") or not res.get("path"):
        raise DeliveryFailed(f"hyprmasq eis open {addr} failed: {res}")
    return str(res["path"])


async def _run_hyprmasq_input(sock: str, args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        str(HYPRMASQ_INPUT), "--socket", sock, "--json", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), CMD_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise DeliveryFailed("hyprmasq-input timed out") from None
    if proc.returncode != 0:
        msg = stdout.decode(errors="replace").strip() or stderr.decode(errors="replace").strip()
        raise DeliveryFailed(f"hyprmasq-input exited {proc.returncode}: {msg}")


async def _paste_text(_sock: str, text: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "wtype", "--", text,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), CMD_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise DeliveryFailed("wtype timed out") from None
    if proc.returncode != 0:
        raise DeliveryFailed(f"wtype exited {proc.returncode}: {stderr.decode(errors='replace').strip()}")


def _mods(modifiers) -> str:
    if not modifiers:
        return ""
    bad = [k for k in modifiers if k not in MODIFIERS]
    if bad:
        raise Unsupported(f"hyprmasq holds only modifier keys, not {bad}")
    return "+".join(MODIFIERS[k] for k in modifiers)


def _chord(keys) -> str:
    mapped = []
    for k in keys:
        if k in MODIFIERS:
            mapped.append(MODIFIERS[k])
        elif k in NAMED:
            mapped.append(NAMED[k])
        elif k == "+":
            mapped.append("plus")
        else:
            mapped.append(str(k))
    return "+".join(mapped)


def _split_text(text: str) -> list[tuple[str, Any]]:
    """Split text into ASCII key-event actions and non-ASCII private-clipboard paste chunks."""
    out: list[tuple[str, Any]] = []
    actions: list[str] = []
    ascii_buf: list[str] = []
    paste_buf: list[str] = []

    def flush_ascii() -> None:
        if ascii_buf:
            actions.extend(["text", "".join(ascii_buf)])
            ascii_buf.clear()

    def flush_actions() -> None:
        flush_ascii()
        if actions:
            out.append(("actions", list(actions)))
            actions.clear()

    def flush_paste() -> None:
        if paste_buf:
            out.append(("paste", "".join(paste_buf)))
            paste_buf.clear()

    for ch in text:
        code = ord(ch)
        if 0x20 <= code <= 0x7E:
            flush_paste()
            ascii_buf.append(ch)
        elif ch == "\t":
            flush_paste()
            flush_ascii()
            actions.extend(["keys", "tab"])
        elif ch in ("\n", "\r"):
            flush_paste()
            flush_ascii()
            actions.extend(["keys", "enter"])
        else:
            flush_actions()
            paste_buf.append(ch)

    flush_actions()
    flush_paste()
    return out
