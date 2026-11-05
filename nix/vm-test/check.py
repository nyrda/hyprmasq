"""Checks the VM test's areas, and with --docs the subtest tables of docs/vm-test.md against them.

    python3 nix/vm-test/check.py [--docs docs/vm-test.md]

The test runs each area's code inside the try block of the test script, so the Nix build indents it. That is exact as
long as no string in an area spans lines, apart from docstrings. An area may use what common.py defines and what the
areas it requires define, which this checks for the names it reads, and no variable at an area's top level may hide a
helper of common.py from a later area. It cannot see state that lives in the VM, such as a window or a log that an
earlier area left; areas.json declares such requirements too.
"""

import ast
import builtins
import io
import json
import re
import symtable
import sys
import tokenize
from pathlib import Path

directory = Path(__file__).resolve().parent
# Names that the NixOS test driver gives the test script.
driver_names = {"machine", "subtest", "driver", "log", "os", "retry", "t", "start_all", "join_all", "polling_condition"}
# Names that nix/hyprmasq-vm-test.nix defines before common.py.
nix_names = {"expected_version", "nested"}
errors = []


def names(source, filename):
    """The names that a module binds at its top level, and the global names that its code and its functions read. The
    top level comes from the syntax tree, where a comprehension keeps its targets and := in it binds outside, and the
    functions from Python's symbol table."""
    bound, reads = set(), set()

    def visit(node, local):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            if isinstance(node, ast.ClassDef):
                outside = node.bases + [keyword.value for keyword in node.keywords]
            else:
                outside = node.args.defaults + [default for default in node.args.kw_defaults if default]
            for child in node.decorator_list + outside:
                visit(child, local)
        elif isinstance(node, ast.Lambda):
            for child in node.args.defaults + [default for default in node.args.kw_defaults if default]:
                visit(child, local)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            inner = set(local)
            for generator in node.generators:
                visit(generator.iter, inner)
                inner |= {name.id for name in ast.walk(generator.target) if isinstance(name, ast.Name)}
                for condition in generator.ifs:
                    visit(condition, inner)
            for child in [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]:
                visit(child, inner)
        elif isinstance(node, ast.NamedExpr):
            bound.add(node.target.id)
            visit(node.value, local)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                if node.id not in local:
                    reads.add(node.id)
            else:
                bound.add(node.id)
        else:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                bound.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                bound.add(node.name)
            elif isinstance(node, ast.MatchMapping) and node.rest:
                bound.add(node.rest)
            for child in ast.iter_child_nodes(node):
                visit(child, local)

    visit(ast.parse(source, filename), set())

    def functions(scope):
        for child in scope.get_children():
            reads.update(symbol.get_name() for symbol in child.get_symbols() if symbol.is_global() and symbol.is_referenced())
            functions(child)

    functions(symtable.symtable(source, filename, "exec"))
    # Python's own names, such as __conditional_annotations__.
    return bound, {name for name in reads if not (name.startswith("__") and name.endswith("__"))}


def docstring_lines(tree):
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                lines.add(first.lineno)
    return lines


def multiline_strings(source):
    """The first lines of the strings that span lines."""
    starts, fstrings = [], []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.STRING and token.start[0] != token.end[0]:
            starts.append(token.start[0])
        elif token.type == getattr(tokenize, "FSTRING_START", None):
            fstrings.append(token.start[0])
        elif token.type == getattr(tokenize, "FSTRING_END", None):
            start = fstrings.pop()
            if start != token.end[0]:
                starts.append(start)
    return starts


def subtests(tree):
    """The names of the subtests in an area, in order."""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                call = item.context_expr
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "subtest":
                    if not (len(call.args) == 1 and isinstance(call.args[0], ast.Constant)):
                        errors.append(f"line {call.lineno}: a subtest's name must be a plain string")
                        continue
                    names.append((call.lineno, call.args[0].value))
    return [name for _, name in sorted(names)]


def check_areas():
    areas = json.loads((directory / "areas.json").read_text())
    order = [area["name"] for area in areas]
    requires = {area["name"]: area["requires"] for area in areas}
    if len(set(order)) != len(order):
        errors.append("areas.json names an area twice")
    for position, name in enumerate(order):
        for needed in requires[name]:
            if needed not in order[:position]:
                errors.append(f"area {name} requires {needed}, which is no area before it")
    files = {path.stem for path in directory.glob("*.py")} - {"common", "check"}
    if files != set(order):
        errors.append(f"the area files {sorted(files)} are not the areas of areas.json {sorted(order)}")

    common_names, common_reads = names((directory / "common.py").read_text(), "common.py")
    known = common_names | driver_names | nix_names | set(dir(builtins))
    for used in sorted(common_reads - known):
        errors.append(f"common.py uses {used}, which only an area can define")
    trees, defined, reads = {}, {}, {}
    for name in order:
        path = directory / f"{name}.py"
        if not path.exists():
            continue
        source = path.read_text()
        trees[name] = tree = ast.parse(source, str(path))
        defined[name], reads[name] = names(source, path.name)
        # A class body reads names in ways that this check does not follow.
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                errors.append(f"{path.name}:{node.lineno}: define classes in common.py")
        docstrings = docstring_lines(tree)
        for line in multiline_strings(source):
            if line not in docstrings:
                errors.append(f"{path.name}:{line}: a string spans lines, which the test's indentation would change")

    def closure(name):
        needed = set(requires[name])
        for other in requires[name]:
            needed |= closure(other)
        return needed

    for name, tree in trees.items():
        available = set().union(*(defined.get(other, set()) for other in closure(name)))
        for used in sorted(reads[name] - defined[name] - known - available):
            owners = [other for other in order if used in defined.get(other, set())]
            where = f"defined in {', '.join(owners)}" if owners else "defined nowhere"
            errors.append(f"area {name} uses {used}, {where}: require an area that defines it, or move it to common.py")
    # The areas share their globals, so a variable at an area's top level replaces a helper of common.py of that name
    # for the areas after it.
    for position, name in enumerate(order):
        for rebound in sorted(defined.get(name, set()) & common_names):
            hidden = [later for later in order[position + 1 :] if later in trees and rebound in reads[later] - defined[later]]
            if hidden:
                errors.append(f"area {name} binds {rebound}, which hides common.py's {rebound} from {', '.join(hidden)}: rename it")
    return order, {name: subtests(tree) for name, tree in trees.items()}, requires


def table_rows(lines):
    """The cells of the rows of the table that starts at lines[0], without its header."""
    rows = []
    for line in lines[2:]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in re.split(r"(?<!\\)\|", line)[1:-1]])
    return rows


def check_docs(path, order, names, requires):
    lines = Path(path).read_text().splitlines()
    overview = next((i for i, line in enumerate(lines) if line.startswith("| Area | Needs |")), None)
    if overview is None:
        errors.append(f"{path}: no table of areas, with the header | Area | Needs |")
    else:
        rows = table_rows(lines[overview:])
        if [row[0] for row in rows] != [f"`{name}`" for name in order]:
            errors.append(f"{path}: the table of areas lists {[row[0] for row in rows]}, not the areas in order {order}")
        for row in rows:
            name = row[0].strip("`")
            needed = [n.strip().strip("`") for n in row[1].split(",") if n.strip()]
            if name in requires and needed != requires[name]:
                errors.append(f"{path}: area {name} needs {requires[name]} in areas.json, not {needed}")
    headings = [(i, line.removeprefix("### ")) for i, line in enumerate(lines) if line.startswith("### ")]
    if [heading for _, heading in headings] != [f"`{name}`" for name in order]:
        errors.append(f"{path}: the subtest tables are headed {[h for _, h in headings]}, not by the areas in order {order}")
    for i, heading in headings:
        name = heading.strip("`")
        start = next((j for j in range(i + 1, len(lines)) if lines[j].startswith("| Subtest |")), None)
        documented = [row[0] for row in table_rows(lines[start:])] if start is not None else []
        if name in names and documented != names[name]:
            missing = [n for n in names[name] if n not in documented]
            extra = [n for n in documented if n not in names[name]]
            detail = f"missing {missing}, unknown {extra}" if missing or extra else "in another order"
            errors.append(f"{path}: the subtests of {name} differ from {name}.py: {detail}")


def main():
    order, names, requires = check_areas()
    if sys.argv[1:2] == ["--docs"]:
        check_docs(sys.argv[2], order, names, requires)
    for error in errors:
        print(error, file=sys.stderr)
    sys.exit(1 if errors else 0)


main()
