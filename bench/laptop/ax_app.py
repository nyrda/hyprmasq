"""Driving an app by its accessibility tree with macOS's own API, for run_mac.py's setup of the Claude app, without a
computer-use tool: Cua Driver's daemons keep a window of their own over the screen, which Claude's full control of
the screen cannot hide. The runner reads the tree, presses what it expects and sends keys to the app's process. It
needs the Accessibility grant of the app it runs from, such as the terminal. Each element is written as Cua Driver
writes it, so that the runner's patterns read the same: role, "title", = "value" and (description)."""

import re
import time

import AppKit
import Quartz
from ApplicationServices import (AXUIElementCopyAttributeValue, AXUIElementCreateApplication,
                                 AXUIElementPerformAction, AXUIElementSetAttributeValue)

# Virtual key codes of the keys the runner sends.
KEYS = {"return": 36, "v": 9, "escape": 53}


def attribute(element, name):
    error, value = AXUIElementCopyAttributeValue(element, name, None)
    return value if error == 0 else None


class AXApp:
    def __init__(self, bundle):
        apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle)
        if not apps:
            raise RuntimeError(f"{bundle} does not run")
        self.pid = apps[0].processIdentifier()
        self.app = AXUIElementCreateApplication(self.pid)
        # The window the runner works in, once it set one: reads, presses and keys stay within it.
        self.scope = None
        # Chromium, and so Electron, builds its tree for an assistive client only when asked.
        AXUIElementSetAttributeValue(self.app, "AXManualAccessibility", True)

    def windows(self):
        return list(attribute(self.app, "AXWindows") or [])

    def elements(self, limit=4000):
        """The elements of the scope's window, or of the app's windows, depth first, each with its row as Cua Driver
        writes it."""
        found, stack = [], list(reversed([self.scope] if self.scope is not None else self.windows()))
        while stack and len(found) < limit:
            element = stack.pop()
            role = attribute(element, "AXRole") or ""
            title = attribute(element, "AXTitle")
            value = attribute(element, "AXValue")
            description = attribute(element, "AXDescription")
            row = role
            if title:
                row += f' "{title}"'
            if isinstance(value, str) and value:
                row += f' = "{" ".join(value.split())}"'
            if description:
                row += f" ({description})"
            found.append((row, element))
            # An app whose accessibility is in a bad state lists itself as its window and its own child: the walk
            # never enters the application element, which would loop.
            if role != "AXApplication":
                stack.extend(reversed(attribute(element, "AXChildren") or []))
        return found

    def tree(self):
        return "\n".join(row for row, _ in self.elements())

    def find(self, pattern):
        return next((element for row, element in self.elements() if re.search(pattern, row)), None)

    def press(self, pattern, timeout=10):
        """Presses the first element whose row matches pattern, waiting for it to appear. Whether it did."""
        deadline = time.time() + timeout
        while True:
            element = self.find(pattern)
            if element is not None:
                return AXUIElementPerformAction(element, "AXPress") == 0
            if time.time() > deadline:
                return False
            time.sleep(0.5)

    def focus(self, pattern):
        element = self.find(pattern)
        if element is None:
            return False
        AXUIElementSetAttributeValue(element, "AXFocused", True)
        return True

    def raise_scope(self):
        """The scope's window raised and made the app's main window, so that keys the app's process receives go to
        it."""
        if self.scope is None:
            return False
        AXUIElementPerformAction(self.scope, "AXRaise")
        AXUIElementSetAttributeValue(self.scope, "AXMain", True)
        time.sleep(0.3)
        return attribute(self.app, "AXMainWindow") == self.scope

    def key(self, name, command=False):
        """A key press sent to the app's process, with Command held when asked."""
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, KEYS[name], down)
            Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand if command else 0)
            Quartz.CGEventPostToPid(self.pid, event)
            time.sleep(0.05)
