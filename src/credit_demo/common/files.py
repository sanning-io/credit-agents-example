"""What a file was for, said by the tool that handed it back.

A tool that names a file returns it as the LangChain `artifact`, and the
SDK's callback handler anchors the file's name, size and hash. The path on
its own would anchor every file in a case the same way: a reader of the
trail could see that four files were touched and not which of them went
into the decision and which came out of it.

The SDK reads `role`, `source` and `destination` off the same object the
path comes from and promotes them onto the file's own record. `role` is
`input` or `output` and nothing else; a value the SDK does not recognize is
dropped rather than guessed at, so these two functions are the only place
this repo names one.

`source` and `destination` are free text, and they are read by a person: say
where the file came from, or who it goes to, in the words the case uses.
Neither is checked by anything. Nothing here is sent to Sanning: the file's
bytes stay in this repo's own store, and the record carries the role beside
the hash.

The path is given relative to the recorder's root folder (the example's
home), which is how the SDK resolves and records it: the record then says
`applications/APP-2026-0412/bank-statements.csv`, the same on every machine, and never this
machine's folders or its user's name.
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


# How a file record says where the credit policy came from.
CREDIT_POLICY_SOURCE = "the lender's own credit policy"


def input_file(path: Path | str, *, source: str) -> dict:
    """A file this turn READ. `source` says where it came from."""
    return {"path": _relative(path), "role": "input", "source": source}


def output_file(path: Path | str, *, destination: str) -> dict:
    """A file this turn WROTE. `destination` says who it goes to."""
    return {"path": _relative(path), "role": "output", "destination": destination}
