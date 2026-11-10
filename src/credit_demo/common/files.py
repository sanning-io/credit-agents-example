"""What a file was for, said by the tool that hands it back. `role` is `input` or
`output`; `source` and `destination` are free text a person reads. Paths are
relative to the recorder's root, the example's home, so a record never carries
this machine's folders. Nothing here is sent to Sanning.
"""

from __future__ import annotations

from pathlib import Path

from . import home


def _relative(path: Path | str) -> str:
    """The path under the example's home, as the recorder resolves it. A
    path outside the home is handed on unchanged, and the recorder refuses
    it as outside its root."""
    try:
        return Path(path).resolve().relative_to(Path(home.REPO_ROOT).resolve()).as_posix()
    except ValueError:
        return str(path)


CREDIT_POLICY_SOURCE = "the lender's own credit policy"


def input_file(path: Path | str, *, source: str) -> dict:
    """A file this turn READ. `source` says where it came from."""
    return {"path": _relative(path), "role": "input", "source": source}


def output_file(path: Path | str, *, destination: str) -> dict:
    """A file this turn WROTE. `destination` says who it goes to."""
    return {"path": _relative(path), "role": "output", "destination": destination}
