with subtest("typing tags and characters with the active keymap"):
    hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "de" } })'))
    wait_for("German keymap", lambda: keymap("German"))
    # Started after the switch, so the shell reads the German keymap from the start.
    focus_workspace(18)
    target = foot("hm-typed", "env LC_ALL=C.UTF-8 bash --noprofile --norc -i")
    wait_drawn(target, 1000)
    focus_workspace(4)
    before_focus = snapshot()
    path = open_socket(target)
    # Y and Z swap places, AltGr and the ISO key between Shift and Y give @ € | { >, ^ is a dead key followed by
    # Space. Ctrl+U cuts the line and Ctrl+Y pastes it back, which only works if the Y key of the layout is used.
    send(path, "type", 'printf "%s\\n" "zy@€äß|{^" > /tmp/typed-layout<shortcut:ctrl+u><shortcut:ctrl+y><enter>')
    machine.wait_until_succeeds("test -s /tmp/typed-layout")
    assert machine.succeed("cat /tmp/typed-layout") == "zy@€äß|{^\n"
    # Escaped and unknown tags are typed as text. Named keys, keysyms and repeat counts edit the line.
    send(path, "type", 'xecho "\\<tab> <div></b>" ok<left:2>X<End> > /tmp/typed-keys<home><delete><settle><Return>')
    machine.wait_until_succeeds("test -s /tmp/typed-keys")
    assert machine.succeed("cat /tmp/typed-keys") == "<tab> <div></b> Xok\n"
    # Invalid text is rejected before anything is sent.
    for text, error in [
        ("echo partial > /tmp/typed-partial☃<enter>", "cannot type U+2603"),
        ("echo partial > /tmp/typed-partial<wait:soon><enter>", "<wait:MS>"),
        ("echo partial > /tmp/typed-partial<tab:0>", "repeat count"),
        ("<shortcut:hyper+x>", "unknown shortcut modifier"),
    ]:
        status, output = machine.execute(
            "su - alice -c " + shlex.quote(f"hyprmasq-input --socket {shlex.quote(path)} type {shlex.quote(text)} 2>&1")
        )
        assert status == 2 and error in output, (text, status, output)
    # ^ is a dead key and Space here, so 40 of them are 80 keys, 1190 ms at their pace, which a budget that 40 keys
    # would fit refuses before anything is sent.
    status, output = machine.execute(
        "su - alice -c " + shlex.quote(f"hyprmasq-input --json --budget 900 --socket {shlex.quote(path)} text {shlex.quote('^' * 40)}")
    )
    budgeted = json.loads(output)
    assert status == 2 and budgeted["error"]["code"] == "too_long" and budgeted["paceMs"] == 1190, (status, output)
    assert (budgeted["done"], budgeted["failedIndex"], budgeted["delivery"], budgeted["progress"]) == (0, None, "none", None), budgeted
    send(path, "type", "echo clean > /tmp/typed-clean<enter>")
    machine.wait_until_succeeds("test -s /tmp/typed-clean")
    assert machine.succeed("cat /tmp/typed-clean") == "clean\n"
    machine.succeed("! ls /tmp/typed-partial*")
    assert snapshot() == before_focus
    assert hypr(f"hyprmasq eis close {target}") == "ok"
    hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "us" } })'))
    wait_for("US keymap", lambda: keymap("English"))

with subtest("typing through the Compose table of the target's locale"):
    # On a German layout, ć is only the dead acute and c, which the Compose table of pt_BR.UTF-8 makes a ç. ç is also
    # the dead cedilla and c. foot applies the table of its own locale, and the shells run in UTF-8 either way.
    hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "de" } })'))
    wait_for("German keymap", lambda: keymap("German"))
    focus_workspace(22)
    compose_shell = "env LC_ALL=en_US.UTF-8 bash --noprofile --norc -i"
    pt_target = foot("hm-compose-pt", compose_shell, executable="env LC_ALL=pt_BR.UTF-8 foot")
    en_target = foot("hm-compose-en", compose_shell, executable="env LC_ALL=en_US.UTF-8 foot")
    wait_drawn(pt_target, 1000)
    wait_drawn(en_target, 1000)
    focus_workspace(4)
    before_focus = snapshot()

    def compose_send(path, text, sender_locale, *options):
        return machine.execute(
            "su - alice -c "
            + shlex.quote(f"env LC_ALL={sender_locale} hyprmasq-input --json --socket {shlex.quote(path)} {' '.join(options)} type {shlex.quote(text)}")
            + " 2>/tmp/hm-compose-stderr"
        )

    def compose_typed(path, name, text, sender_locale, *options):
        status, output = compose_send(path, f'printf "%s\\n" "{text}" > /tmp/{name}<enter>', sender_locale, *options)
        assert status == 0, (status, output)
        machine.wait_until_succeeds(f"test -s /tmp/{name}")
        return machine.succeed(f"cat /tmp/{name}").removesuffix("\n"), json.loads(output)

    pt_path, en_path = open_socket(pt_target), open_socket(en_target)
    # The sender's own table, en_US.UTF-8, differs from the target's: ć arrives as ç. This is why --locale exists.
    assert compose_typed(pt_path, "hm-compose-default", "ć", "en_US.UTF-8")[0] == "ç"
    # With the target's locale, ç arrives as it is, and ć cannot be typed, which is rejected before anything is sent.
    text, reply = compose_typed(pt_path, "hm-compose-pt", "çé", "en_US.UTF-8", "--locale", "pt_BR.UTF-8")
    assert text == "çé" and reply["compose"] == {"locale": "pt_BR.UTF-8", "requested": "pt_BR.UTF-8"}, (text, reply)
    status, output = compose_send(pt_path, "ć", "en_US.UTF-8", "--locale", "pt_BR.UTF-8")
    rejected = json.loads(output)
    assert status == 2 and rejected["delivery"] == "none" and rejected["compose"] is None, output
    assert rejected["error"] == {"code": "cannot_type", "message": "the keymap cannot type U+0107, also not through the Compose table of pt_BR.UTF-8"}, output
    # The other way round: a sender in pt_BR.UTF-8 cannot type ć with its own table, but can with the target's.
    status, output = compose_send(en_path, "ć", "pt_BR.UTF-8")
    assert status == 2 and json.loads(output)["error"]["code"] == "cannot_type", output
    text, reply = compose_typed(en_path, "hm-compose-en", "ć", "pt_BR.UTF-8", "--locale", "en_US.UTF-8")
    assert text == "ć" and reply["compose"] == {"locale": "en_US.UTF-8", "requested": "en_US.UTF-8"}, (text, reply)
    # Without a table for the locale, the sender uses that of en_US.UTF-8, and says so.
    text, reply = compose_typed(en_path, "hm-compose-fallback", "ć", "pt_BR.UTF-8", "--locale", "xx_XX.UTF-8")
    assert text == "ć" and reply["compose"] == {"locale": "en_US.UTF-8", "requested": "xx_XX.UTF-8"}, (text, reply)
    assert "no Compose table for the locale 'xx_XX.UTF-8', so the table of en_US.UTF-8 is used" in machine.succeed("cat /tmp/hm-compose-stderr")
    # Text without a character typed through Compose reports none.
    text, reply = compose_typed(en_path, "hm-compose-none", "ok", "pt_BR.UTF-8", "--locale", "en_US.UTF-8")
    assert text == "ok" and reply["compose"] is None, (text, reply)
    assert snapshot() == before_focus
    for target in (pt_target, en_target):
        assert hypr(f"hyprmasq eis close {target}") == "ok"
    hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "us" } })'))
    wait_for("US keymap", lambda: keymap("English"))
