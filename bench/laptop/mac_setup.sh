#!/bin/zsh
# mac_setup.sh: prepares ~/hyprmasq-bench on a Mac for run_mac.py. It copies the questionnaire and the runner
# from this checkout, makes a venv on Homebrew's framework Python 3.11 with PySide6 6.8 and Quartz, and makes
# Questionnaire.app, a copy of that Python's Python.app with its own name and bundle identifier, so that macOS and
# the computer-use tools see an app named Questionnaire rather than one of several Pythons. A Python without the
# framework's Python.app, such as uv's, is not an app that the tools can find. It also makes the folder of the
# Claude app's Code sessions, whose settings deny Claude Code's own tools, and Cua's Claude Code home.
set -e
B=~/hyprmasq-bench
HERE=${0:A:h}
PY=$(brew --prefix python@3.11)/Frameworks/Python.framework/Versions/3.11
mkdir -p $B/form $B/results
cp $HERE/../tasks/form/{form_qt.py,items.py,check.py} \
  $HERE/{run_mac.py,ax_app.py,mac_series.sh,mac_windows.py,agent_process.py,files_task.py} $B/form/
mkdir -p $B/tasks/inkscape
cp $HERE/../tasks/inkscape/{drawing.svg,check.py} $B/tasks/inkscape/
$PY/bin/python3.11 -m venv $B/qt-macos
uv pip install -q --python $B/qt-macos/bin/python "PySide6==6.8.*" pyobjc-framework-Quartz \
  pyobjc-framework-ApplicationServices
rm -rf $B/Questionnaire.app
cp -R $PY/Resources/Python.app $B/Questionnaire.app
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier org.example.Questionnaire" \
  -c "Set :CFBundleName Questionnaire" $B/Questionnaire.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string Questionnaire" $B/Questionnaire.app/Contents/Info.plist
codesign --force --deep --sign - $B/Questionnaire.app
cat > $B/questionnaire.sh <<EOF
#!/bin/zsh
# questionnaire.sh STATE-DIR COUNT: the Qt questionnaire as the app Questionnaire, org.example.Questionnaire.
export PYTHONPATH=$B/qt-macos/lib/python3.11/site-packages
exec $B/Questionnaire.app/Contents/MacOS/Python $B/form/form_qt.py "\$@"
EOF
chmod +x $B/questionnaire.sh

# The Claude app's Code sessions for Claude's computer use start in this folder: its settings leave the agent its
# computer-use tools alone, as the other tools' agents have no shell.
mkdir -p $B/claude-cu/.claude
cat > $B/claude-cu/.claude/settings.json <<EOF
{
  "permissions": {
    "deny": ["Bash", "Read", "Edit", "Write", "Glob", "Grep", "NotebookEdit", "WebFetch", "WebSearch", "Agent",
             "Task", "Skill", "mcp__Claude_Browser", "mcp__claude-in-chrome"]
  }
}
EOF

# OpenAI's computer use's own Codex home, as Cua has one: the login, the link to the Computer Use app, copies of the
# plugins and their marketplace, and of the user's config.toml only what runs computer use. Codex loads the
# bundled plugins only from its default home, $HOME/.codex, with their files inside it, so the copies name this
# home where the user's name ~/.codex, and run_mac.py starts Codex with HOME set to it and no CODEX_HOME. The user's
# AGENTS.md, memories, projects and other settings stay out of the runs.
O=$B/homes/openai-cu/.codex
rm -rf $B/homes/openai-cu
mkdir -p $O
cp ~/.codex/auth.json $O/
ln -s ~/.codex/computer-use $O/computer-use
cp -R ~/.codex/plugins $O/plugins
# Computer Use's helper reaches the Computer Use app through a socket in $HOME's group container of the app.
G="Library/Group Containers/2DC432GLL2.com.openai.sky.CUAService"
mkdir -p "$B/homes/openai-cu/Library/Group Containers"
ln -s ~/"$G" "$B/homes/openai-cu/$G"
cp -R ~/.codex/.tmp $O/.tmp
/usr/bin/python3 - ~/.codex $O <<'EOF'
import os, re, sys
user, home = sys.argv[1:3]
KEEP = ("marketplaces.openai-bundled", 'plugins."computer-use@openai-bundled"',
        'plugins."unified-computer-use@openai-bundled"', "mcp_servers.node_repl", "mcp_servers.node_repl.env",
        "mcp_servers.computer-use", "features", "shell_environment_policy.set")
out, table = [], ""
for line in open(os.path.join(user, "config.toml")):
    header = re.match(r"\[(.+)\]\s*$", line)
    if header:
        table = header.group(1)
    # Of the top-level keys, only the hook that tells the Computer Use app a turn ended.
    if (table in KEEP) or (not table and line.startswith("notify")):
        out.append(line.replace(user, home))
open(os.path.join(home, "config.toml"), "w").write("".join(out))
for top in ("plugins", ".tmp"):
    for path, _, files in os.walk(os.path.join(home, top)):
        for name in files:
            if name.endswith((".json", ".mjs", ".toml")):
                full = os.path.join(path, name)
                text = open(full, errors="surrogateescape").read()
                if user in text:
                    open(full, "w", errors="surrogateescape").write(text.replace(user, home))
EOF

# Cua's Claude Code home: cua-driver's MCP server and skill, as cua-driver documents them for Claude Code, and a
# link to the user's keychains, where Claude Code finds the login of the user's Claude subscription.
if [[ ! -d $B/homes/claude-cua/.claude ]]; then
  mkdir -p $B/homes/claude-cua
  HOME=$B/homes/claude-cua claude mcp add --scope user cua-driver -- ~/.local/bin/cua-driver mcp
  HOME=$B/homes/claude-cua cua-driver skills install
fi
mkdir -p $B/homes/claude-cua/Library
ln -sfn ~/Library/Keychains $B/homes/claude-cua/Library/Keychains
# cua-driver finds its daemon by a socket in $HOME's Library/Caches. Both of Cua's homes reach the user's daemon,
# which holds CuaDriver's Screen Recording and direct-capture consent: from a home of its own, cua-driver would start
# a daemon there that macOS has not asked, and whose screenshots fail.
for h in cua claude-cua; do
  mkdir -p $B/homes/$h/Library/Caches
  [[ -L $B/homes/$h/Library/Caches/cua-driver ]] || { HOME=$B/homes/$h cua-driver stop >/dev/null 2>&1; rm -rf $B/homes/$h/Library/Caches/cua-driver; }
  ln -sfn ~/Library/Caches/cua-driver $B/homes/$h/Library/Caches/cua-driver
done
[[ -d /Applications/Inkscape.app ]] || echo "mac_setup.sh: install Inkscape for its task: brew install --cask inkscape"
