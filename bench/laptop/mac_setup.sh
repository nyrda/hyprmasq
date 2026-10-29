#!/bin/zsh
# mac_setup.sh: prepares ~/hyprmasq-bench on a Mac for run_mac_form.py. It copies the questionnaire and the runner
# from this checkout, makes a venv on Homebrew's framework Python 3.11 with PySide6 6.8 and Quartz, and makes
# Questionnaire.app, a copy of that Python's Python.app with its own name and bundle identifier, so that macOS and
# the computer-use tools see an app named Questionnaire rather than one of several Pythons. A Python without the
# framework's Python.app, such as uv's, is not an app that the tools can find.
set -e
B=~/hyprmasq-bench
HERE=${0:A:h}
PY=$(brew --prefix python@3.11)/Frameworks/Python.framework/Versions/3.11
mkdir -p $B/form $B/results
cp $HERE/../tasks/form/{form_qt.py,items.py,check.py} $HERE/{run_mac_form.py,mac_windows.py,agent_process.py} $B/form/
$PY/bin/python3.11 -m venv $B/qt-macos
uv pip install -q --python $B/qt-macos/bin/python "PySide6==6.8.*" pyobjc-framework-Quartz
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
