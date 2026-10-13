"""No error this example renders carries an absolute path on this machine.

An error travels further than the code that raised it: to the other agent in
the reply to a hand-off, onto the credit desk, into a signed
closing record, and onto the operator's console. A `FileNotFoundError` for
the credit policy carries the policy's absolute path, and rendered as it is
it would carry that path to every one of them. So every error is rendered by
one function
(`credit_demo.common.errors`), and this file holds it to that twice: on what
the function does, and on there being no other place in `src/` that renders
an exception by hand.

**What the guard checks, named.** Two families of hand-rendering, both found
by walking the parsed AST rather than matching text:

- Forms that render an exception whatever it is called: `logging.exception`
  (or `<anything>.exception(...)`), `exc_info=True` on any call,
  `traceback.print_exc`/`format_exc`/`format_exception_only`,
  `sys.stderr.write`, and a bare `print(`.
- Forms that render the exception an `except ... as NAME:` block just bound,
  under WHATEVER NAME that block gave it: `str(NAME)`, `repr(NAME)`,
  `format(NAME)`, `"...".format(NAME)`, `"%s" % NAME`, `NAME.args`, and `NAME`
  interpolated into an f-string. Walking the AST is what lets this be "any
  variable name" rather than a fixed list of `exc`/`err`/`e`: the check does
  not need to guess a variable's name, because it reads the one the `except`
  block actually bound.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from credit_demo.common import home
from credit_demo.common.errors import describe, tell_the_operator, without_local_paths

SRC = Path(__file__).resolve().parents[1] / "src" / "credit_demo"
POLICY = str(home.REPO_ROOT / home.CREDIT_POLICY)


def test_a_missing_policy_is_named_and_its_path_is_not():
    error = FileNotFoundError(2, "No such file or directory", POLICY)

    rendered = describe(error)

    assert rendered == "FileNotFoundError: [Errno 2] No such file or directory: 'credit-policy.pdf'"
    assert POLICY not in rendered


@pytest.mark.parametrize(
    "text, expected",
    [
        (f"could not read {POLICY}.", "could not read credit-policy.pdf."),
        ("/tmp/x/y/run.json: read-only", "run.json: read-only"),
        (r"C:\Users\someone\credit-demo\policy\credit-policy.pdf is gone",
         "credit-policy.pdf is gone"),
        (f"two: {POLICY} and {POLICY}", "two: credit-policy.pdf and credit-policy.pdf"),
        # A `file://` URI.
        ("could not open file:///home/user/policy/credit-policy.pdf",
         "could not open credit-policy.pdf"),
        # Windows, forward slashes.
        (r"see C:/Users/someone/credit-demo/policy/credit-policy.pdf",
         "see credit-policy.pdf"),
        # A UNC share.
        (r"\\fileserver\share\policy\credit-policy.pdf is unreachable",
         "credit-policy.pdf is unreachable"),
        # A colon-prefixed path, not a URL scheme.
        ("error:/home/user/policy/credit-policy.pdf",
         "error:credit-policy.pdf"),
        ("PATH=a:/home/user/policy/credit-policy.pdf",
         "PATH=credit-policy.pdf"),
        # `~/`.
        ("~/credit-demo/policy/credit-policy.pdf is gone",
         "credit-policy.pdf is gone"),
        # `%2F`-encoded.
        ("saw %2Fhome%2Fuser%2Fpolicy%2Fcredit-policy.pdf in the log",
         "saw credit-policy.pdf in the log"),
        # An absolute path whose first directory does not exist on this
        # machine either: this is judged on the last part naming a file,
        # never on whether the directory exists here.
        ("/nowhere-on-this-box/policy/credit-policy.pdf is gone",
         "credit-policy.pdf is gone"),
    ],
)
def test_an_absolute_path_on_this_machine_becomes_its_last_part(text, expected):
    assert without_local_paths(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "POST http://checker.local:8002/cases/abc/handoffs failed",
        "the route /cases/abc/handoffs answered 409",
        "GET /runs and GET /runs/{id}",
        "a relative ./runs/underwriter path",
        "3/4 of the calls",
        # A bare directory mount with nothing under it: it is not a file, and
        # it reads exactly like a route this repo could have.
        "POST /tmp failed",
        "the route /run/abc answered 409",
    ],
)
def test_a_url_a_route_or_a_relative_path_is_left_alone(text):
    """A route is not a place on this machine's disk, and the reason a
    request failed is worth keeping. Nor is a bare directory mount with
    nothing under it: `/tmp` and `/run/abc` read exactly like a route, and
    cutting them to `tmp` and `abc` would break the route in the message."""
    assert without_local_paths(text) == text


def test_a_path_with_a_space_is_cut_whole_not_split():
    """The regex character class that ends a match on whitespace would
    otherwise cut `'/home/user/My Documents/credit policy.pdf'` at the first
    space, leaving `Documents/credit policy.pdf` sitting in the output
    unscrubbed. A quoted path is cut as one span instead."""
    text = "could not read '/home/user/My Documents/credit policy.pdf'"
    assert without_local_paths(text) == "could not read 'credit policy.pdf'"


def test_the_operator_line_carries_no_path(capfd):
    tell_the_operator(f"run r: could not write {POLICY}")

    logged = capfd.readouterr().err
    assert "credit-policy.pdf" in logged
    assert POLICY not in logged


def test_a_turn_whose_tool_raised_on_a_path_reports_the_name_and_not_the_path(repo_tmp_path):
    """The chokepoint for a path in a turn's error: a turn's error is what
    the checker sends back as the review's error and what the log line
    prints."""
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool

    from credit_demo.common.turn import Turn
    from tests.fakes import ScriptedChatModel, fake_anchorer
    from tests.turns import run

    @tool
    def read_credit_policy() -> str:
        """Read the policy."""
        raise FileNotFoundError(2, "No such file or directory", POLICY)

    anchorer, _client = fake_anchorer(repo_tmp_path)
    outcome = run(
        Turn(
            name="review",
            tools=[read_credit_policy],
            system_prompt="",
            task="review",
            output=repo_tmp_path / "review.json",
            owed="a review",
        ),
        anchorer=anchorer,
        model=ScriptedChatModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "read_credit_policy", "args": {}, "id": "c"}]),
            AIMessage(content="done"),
        ]),
    )

    assert outcome.status == "failed"
    assert "credit-policy.pdf" in outcome.error, outcome.error
    assert POLICY not in outcome.error


def test_a_turn_that_cannot_clear_its_workspace_names_the_file_and_not_the_path(repo_tmp_path):
    """The other way a path can reach a turn's error: the message this module
    writes itself, naming the file it could not clear. Every error a turn
    returns is cleaned in one place, and this one is written with the path
    in it."""
    from credit_demo.common.turn import Turn
    from tests.fakes import ScriptedChatModel, fake_anchorer
    from tests.turns import run

    owed = repo_tmp_path / "review.json"
    owed.mkdir()
    anchorer, _client = fake_anchorer(repo_tmp_path)
    outcome = run(
        Turn(name="review", tools=[], system_prompt="", task="review", output=owed, owed="a review"),
        anchorer=anchorer,
        model=ScriptedChatModel(responses=[]),
    )

    assert outcome.status == "failed"
    assert "review.json" in outcome.error, outcome.error
    assert str(repo_tmp_path) not in outcome.error, outcome.error


# ── The AST guard itself ────────────────────────────────────────────────────


def _dotted(node: ast.AST) -> str | None:
    """`sys.stderr.write` out of an `Attribute`/`Name` chain, or `None`."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _is_name(node: ast.AST | None, bound: str) -> bool:
    return isinstance(node, ast.Name) and node.id == bound


def _global_findings(tree: ast.AST) -> list[tuple[int, str]]:
    """Forms that render an exception whatever it is called, found anywhere
    in the module: they need no exception variable to correlate against."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            dotted = _dotted(node.func)
            if dotted and dotted != "exception" and dotted.endswith(".exception"):
                found.append((node.lineno, "logging.exception"))
            elif dotted in ("traceback.print_exc", "traceback.format_exc"):
                found.append((node.lineno, f"traceback.{dotted.rsplit('.', 1)[-1]}"))
            elif dotted == "traceback.format_exception_only":
                found.append((node.lineno, "traceback.format_exception_only"))
            elif dotted == "sys.stderr.write":
                found.append((node.lineno, "sys.stderr.write"))
            elif isinstance(node.func, ast.Name) and node.func.id == "print":
                found.append((node.lineno, "a bare print"))
            for kw in node.keywords:
                if kw.arg == "exc_info" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    found.append((node.lineno, "exc_info=True"))
    return found


def _uses_of(node: ast.AST, bound: str) -> list[tuple[int, str]]:
    """Forms that render `bound`, the name an `except ... as bound:` block
    just gave its exception. Correlated by AST identity, not by a fixed list
    of names, which is what makes this "any variable name"."""
    found: list[tuple[int, str]] = []
    if isinstance(node, ast.Call):
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name in ("str", "repr") and any(_is_name(a, bound) for a in node.args):
            found.append((node.lineno, f"{name}() of an exception"))
        elif name == "format" and any(_is_name(a, bound) for a in node.args):
            found.append((node.lineno, "format() of an exception"))
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
            and any(_is_name(a, bound) for a in node.args)
        ):
            found.append((node.lineno, '"...".format(exc)'))
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        right = node.right
        names = right.elts if isinstance(right, (ast.Tuple, ast.List)) else [right]
        if any(_is_name(n, bound) for n in names):
            found.append((node.lineno, '"%s" % exc'))
    elif isinstance(node, ast.Attribute) and node.attr == "args" and _is_name(node.value, bound):
        found.append((node.lineno, "exc.args"))
    elif isinstance(node, ast.JoinedStr):
        for value in node.values:
            if isinstance(value, ast.FormattedValue) and _is_name(value.value, bound):
                found.append((value.lineno, "an exception in an f-string"))
    return found


def _handler_findings(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.name:
            bound = node.name
            for inner in ast.walk(node):
                if inner is not node:
                    found.extend(_uses_of(inner, bound))
    return found


def _findings_in(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return []
    return sorted(_global_findings(tree) + _handler_findings(tree))


def test_no_module_renders_an_exception_or_prints_by_hand():
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "errors.py" and path.parent.name == "common":
            continue
        for lineno, what in _findings_in(path):
            found.append(f"{path.relative_to(SRC)}:{lineno}: {what}")

    assert found == [], (
        "render these through credit_demo.common.errors (describe, "
        "without_local_paths, tell_the_operator):\n" + "\n".join(found)
    )


# Each planted snippet is real, parseable source, so the AST guard sees
# exactly what it would see in `src/`, and each one plants the pattern under
# an exception variable named something OTHER than `exc`/`err`/`e`, since the
# whole point of walking the AST is that the name does not matter.
_PLANTED = {
    "logging.exception": "log.exception('it failed')",
    "exc_info=True": "log.warning('it failed', exc_info=True)",
    "traceback.print_exc": "traceback.print_exc()",
    "traceback.format_exc": "traceback.format_exc()",
    "traceback.format_exception_only": "traceback.format_exception_only(type(problem), problem)",
    "sys.stderr.write": "sys.stderr.write('it failed')",
    "a bare print": "print('hello')",
    "str() of an exception": "try:\n    pass\nexcept ValueError as problem:\n    x = str(problem)",
    "repr() of an exception": "try:\n    pass\nexcept ValueError as problem:\n    x = repr(problem)",
    "format() of an exception": "try:\n    pass\nexcept ValueError as problem:\n    x = format(problem)",
    '"...".format(exc)': "try:\n    pass\nexcept ValueError as problem:\n    x = '{}'.format(problem)",
    '"%s" % exc': "try:\n    pass\nexcept ValueError as problem:\n    x = '%s' % problem",
    "exc.args": "try:\n    pass\nexcept ValueError as problem:\n    x = problem.args",
    "an exception in an f-string": "try:\n    pass\nexcept ValueError as problem:\n    x = f'it failed: {problem}'",
}


@pytest.mark.parametrize("what", sorted(_PLANTED))
def test_the_source_check_sees_each_way_by_hand(what):
    """Each pattern above finds the form it names, under a variable name the
    check was never told about, so the check above is not green for want of
    looking."""
    tree = ast.parse(_PLANTED[what])
    seen = {label for _, label in _global_findings(tree) + _handler_findings(tree)}
    assert what in seen, (what, seen)


def test_the_logging_filter_scrubs_the_message_and_drops_the_traceback():
    """`configure_logging`'s filter is the belt-and-suspenders half of the
    guard: a record the SDK or a library writes never goes through
    `describe`, and this is what still cleans it before a handler formats
    it. A traceback attached to the record is dropped rather than cleaned:
    a frame's own text is a source line and a local path together, and there
    is no way to clean the second without mangling the first."""
    import logging

    from credit_demo.common.errors import _ScrubLocalPaths

    record = logging.LogRecord(
        name="credit_demo.common.evidence",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="local mirror write failed for event %s: could not write %s",
        args=("abc123", POLICY),
        exc_info=None,
    )
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        record.exc_info = sys.exc_info()

    kept = _ScrubLocalPaths().filter(record)

    assert kept is True
    assert "credit-policy.pdf" in record.getMessage()
    assert POLICY not in record.getMessage()
    assert record.exc_info is None
    assert record.exc_text is None


def test_an_access_log_line_with_no_path_still_formats():
    """uvicorn's access formatter reads the record's five args, so a filter
    that cleared every record's args would make each request log a
    formatting error instead of its access line."""
    import logging

    from uvicorn.logging import AccessFormatter

    from credit_demo.common.errors import _ScrubLocalPaths

    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:60994", "GET", "/health", "1.1", 200),
        exc_info=None,
    )

    assert _ScrubLocalPaths().filter(record) is True
    line = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(record)
    assert line == '127.0.0.1:60994 - "GET /health HTTP/1.1" 200 OK'
