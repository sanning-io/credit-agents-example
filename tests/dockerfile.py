"""The Dockerfile, read the way `docker build` reads it.

A check that searched the file's text for `ENV PYTHONUNBUFFERED=1` would pass
on a line commented out with `#`, which the build ignores. So the tests that
say what the image does read its instructions here: comments and blank lines
dropped, continuation lines joined, each instruction split into its keyword
and its arguments.
"""

from __future__ import annotations

import shlex
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def instructions(text: str | None = None) -> list[tuple[str, str]]:
    """Every instruction, in order, as `(KEYWORD, arguments)`."""
    text = DOCKERFILE.read_text() if text is None else text
    joined: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        joined.append(pending + line)
        pending = ""
    if pending:
        joined.append(pending)
    parsed = []
    for line in joined:
        keyword, _, arguments = line.partition(" ")
        parsed.append((keyword.upper(), arguments.strip()))
    return parsed


def env(text: str | None = None) -> dict[str, str]:
    """What the image's `ENV` instructions set, in the `KEY=VALUE` form."""
    values: dict[str, str] = {}
    for keyword, arguments in instructions(text):
        if keyword != "ENV":
            continue
        for pair in shlex.split(arguments):
            key, sep, value = pair.partition("=")
            assert sep, f"ENV {arguments!r} is not in the KEY=VALUE form this reads"
            values[key] = value
    return values


def copies(text: str | None = None) -> list[tuple[list[str], str]]:
    """Each `COPY`: its sources, relative to the build context, and its
    destination."""
    found = []
    for keyword, arguments in instructions(text):
        if keyword == "COPY":
            parts = shlex.split(arguments)
            found.append((parts[:-1], parts[-1]))
    return found


def workdir(text: str | None = None) -> str:
    dirs = [arguments for keyword, arguments in instructions(text) if keyword == "WORKDIR"]
    assert dirs, "the Dockerfile sets no WORKDIR"
    return dirs[-1]
