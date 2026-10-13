"""The example says **checker agent**, and so does everything in `src/`:
docstrings, comments and the checker service's title included."""

from __future__ import annotations

import re
from pathlib import Path

from credit_demo.checker.web import create_app
from tests.test_checker_web import _settings

SRC = Path(__file__).resolve().parents[1] / "src"
# Across a line break too: the whitespace that wraps a docstring, and the `#`
# that starts a wrapped comment's next line, are read as the space they stand
# for.
CHECKING = re.compile(r"checking(?:[\s_-]|#)+agent", re.IGNORECASE)


def test_nothing_in_src_says_checking_agent():
    found = []
    for path in sorted(SRC.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".html", ".md"}:
            text = path.read_text(encoding="utf-8")
            for match in CHECKING.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                found.append(f"{path.relative_to(SRC)}:{line}: {' '.join(match.group().split())}")
    assert found == []


def test_the_check_sees_the_words_wrapped_across_a_line():
    for wrapped in ("the checking\nagent", "the checking\n    agent", "# the checking\n    # agent"):
        assert CHECKING.search(wrapped), wrapped


def test_the_checker_service_is_titled_checker_agent():
    assert "checker agent" in create_app(_settings()).title
