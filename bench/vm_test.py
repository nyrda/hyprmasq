"""Checks the shared VM setup helpers without starting a VM."""

import ast
import shlex
import unittest
from pathlib import Path
from unittest import mock


def helpers():
    # vm.py is a NixOS driver script with top-level VM operations. Load only its helpers.
    source = ast.parse(Path(__file__).with_name("vm.py").read_text())
    functions = [node for node in source.body if isinstance(node, ast.FunctionDef)]
    namespace = {"time": mock.Mock(), "clients": mock.Mock(), "hypr": mock.Mock(), "shlex": shlex}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "vm.py", "exec"), namespace)  # noqa: S102
    namespace["time"].monotonic.return_value = 0
    namespace["hypr"] = mock.Mock()
    return namespace


def client(address="0x1", title="page", workspace="web", size=(800, 600), app="firefox"):
    return {"address": address, "title": title, "workspace": {"name": workspace}, "size": size, "class": app}


class SetupHelpers(unittest.TestCase):
    def test_window_returns_the_first_match_or_none(self):
        vm = helpers()
        vm["clients"] = lambda: [client(app="foot"), client(address="0x2")]
        self.assertEqual(vm["window"](lambda c: c["class"] == "firefox")["address"], "0x2")
        self.assertIsNone(vm["window"](lambda c: c["class"] == "chromium"))

    def test_move_waits_for_the_requested_address_and_workspace(self):
        vm = helpers()
        vm["clients"] = mock.Mock(side_effect=[
            [client(workspace="1"), client(address="0x2")], [client()]])
        vm["move"]("0x1", "web")
        self.assertIn('window = "address:0x1", workspace = "name:web", follow = false',
                      shlex.split(vm["hypr"].call_args.args[0])[1])
        self.assertEqual(vm["clients"].call_count, 2)
        vm["time"].sleep.assert_called_once_with(0.5)

    def test_settle_preserves_each_tasks_delays_and_snapshot(self):
        for task, initial, interval, fields in (
            ("board", 5, 5, ("address", "title", "workspace", "size")),
            ("calc", 5, 5, ("address", "title", "workspace", "size")),
            ("forgejo", 5, 3, ("address", "title", "workspace")),
            ("thunderbird", 10, 5, ("address", "title")),
        ):
            with self.subTest(task=task):
                vm = helpers()
                changing = client(size=(900, 600)) if "size" in fields else client(title="loading")
                vm["clients"] = mock.Mock(side_effect=[[client()], [changing], [changing], [changing]])
                vm["settle"]("task", initial_delay=initial, interval=interval, fields=fields)
                self.assertEqual(vm["time"].sleep.call_args_list,
                                 [mock.call(initial), mock.call(interval), mock.call(0.5), mock.call(interval)])

    def test_thunderbird_ignores_the_terminal_and_workspace(self):
        vm = helpers()
        vm["clients"] = mock.Mock(side_effect=[
            [client(), client(app="foot")], [client(workspace="mail"), client(title="top", app="foot")]])
        vm["settle"]("Thunderbird", initial_delay=10, interval=5, fields=("address", "title"),
                     predicate=lambda c: c["class"] != "foot")
        self.assertEqual(vm["time"].sleep.call_args_list, [mock.call(10), mock.call(5)])


if __name__ == "__main__":
    unittest.main()
