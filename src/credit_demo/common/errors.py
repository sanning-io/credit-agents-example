"""How an error reads once it leaves the code that raised it.

An exception's text can carry an absolute path on this machine, and every error
travels: to the other agent, the case record, the credit desk, a closing record
and the operator's log. So render an exception with `describe`, never with
`str(exc)`; `tests/test_errors_carry_no_local_path.py` fails on any other place
in `src/` that does. A local path is cut to its last part, so the file name
still shows which document failed.

A path is judged on its shape, never on whether the directory exists here: a
final segment with an extension, or four or more segments, counts. A shallow
directory (`/tmp`) and a URL route (`/health`) are left alone, since a route is
not a place on disk.
"""
from __future__ import annotations

import logging
import re
import sys
import traceback
from collections.abc import Mapping
from numbers import Number

# The characters that end a path in prose or a repr. Colon is not one: a path
# can follow a colon (`error:/home/x`).
_STOP = r"""\s'"`,;()\[\]{}<>|"""

# One pattern per shape a path arrives in. Each is its own pass, and a span one
# pass rewrites is gone before the next, so the order does not matter.
_WINDOWS = re.compile(r"(?<![\w])[A-Za-z]:\\[^" + _STOP + r"]*")
_WINDOWS_FORWARD = re.compile(r"(?<![\w])[A-Za-z]:/[^" + _STOP + r"]*")
_UNC = re.compile(r"(?<![\w\\])\\\\[^" + _STOP + r"]+")
_FILE_URI = re.compile(r"(?i)file://+[^" + _STOP + r"]+")
_TILDE = re.compile(r"~[/\\][^" + _STOP + r"]*")
_ENCODED = re.compile(r"(?i)%2f[^" + _STOP + r"]*")
# A `/` right after a bare colon, not the `://` of a URL scheme.
_COLON_PREFIXED = re.compile(r"(?<=:)/(?!/)[^" + _STOP + r"]*")
# A POSIX path starts at a `/` that follows no word character, dot, colon or
# slash, so `http://host/x` and `./runs/x` are not read as one.
_POSIX = re.compile(r"(?<![\w.:/\\])/[^" + _STOP + r"]+")
# A quoted path with a space in it, matched first so a later pass does not split it.
_QUOTED_WITH_SPACE = re.compile(
    r"'(?P<single>(?:/|~[/\\]|[A-Za-z]:[\\/]|\\\\|file://)[^'\n]*\s[^'\n]*)'"
    r"|\"(?P<double>(?:/|~[/\\]|[A-Za-z]:[\\/]|\\\\|file://)[^\"\n]*\s[^\"\n]*)\""
)

_SEGMENT_SPLIT = re.compile(r"[/\\]|%2f", re.IGNORECASE)
# A path is cut when its last segment has an extension (`case.json`) or it has
# four or more segments. A case document's route is four segments deep, so a
# request's own route never goes through here: the access log and
# `tell_the_operator_a_request_raised` keep it as sent.
_HAS_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,10}$")
_DEEP_ENOUGH_TO_BE_LOCAL = 4


def _worth_scrubbing(path: str) -> bool:
    trimmed = path.rstrip("/\\.:")
    segments = [s for s in _SEGMENT_SPLIT.split(trimmed) if s]
    if not segments:
        return False
    if _HAS_EXTENSION.search(segments[-1]):
        return True
    return len(segments) >= _DEEP_ENOUGH_TO_BE_LOCAL


def _last_part(path: str) -> str:
    trimmed = path.rstrip("/\\.:")
    tail = _SEGMENT_SPLIT.split(trimmed)[-1]
    # Keep a trailing full stop or colon so the sentence still reads.
    return (tail or "<path>") + path[len(trimmed):]


def _cut(match: "re.Match[str]") -> str:
    path = match.group(0)
    return _last_part(path) if _worth_scrubbing(path) else path


def _cut_quoted(match: "re.Match[str]") -> str:
    inner = match.group("single")
    quote = "'"
    if inner is None:
        inner = match.group("double")
        quote = '"'
    if not _worth_scrubbing(inner):
        return match.group(0)
    return f"{quote}{_last_part(inner)}{quote}"


def without_local_paths(text: object) -> str:
    """`text`, with every local path cut to the name of the file it ends in."""
    rendered = "" if text is None else str(text)
    rendered = _QUOTED_WITH_SPACE.sub(_cut_quoted, rendered)
    for pattern in (
        _WINDOWS,
        _WINDOWS_FORWARD,
        _UNC,
        _FILE_URI,
        _TILDE,
        _ENCODED,
        _COLON_PREFIXED,
        _POSIX,
    ):
        rendered = pattern.sub(_cut, rendered)
    return rendered


def describe(exc: BaseException) -> str:
    """An exception as one line: its type and its message, with no local path."""
    rendered = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    return without_local_paths(rendered)


def tell_the_operator(message: str) -> None:
    """Write a line to standard error, flushed, with local paths cut.

    Standard output is block-buffered when it is not a terminal, and a buffered
    line is lost if the process is killed.
    """
    print(without_local_paths(message), file=sys.stderr, flush=True)


def _route(target: str) -> str:
    """A request's path kept as it came, and its query cleaned."""
    path, mark, query = target.partition("?")
    return path + mark + without_local_paths(query)


def tell_the_operator_a_request_raised(service: str, method: str, route: str, exc: BaseException) -> None:
    """The operator's line for a request that raised.

    Only the exception is cleaned: `without_local_paths` would cut a four
    segment route such as `/cases/{id}/documents/{document}` to its last part.
    """
    print(f"{service}: {method} {_route(route)} raised: {describe(exc)}", file=sys.stderr, flush=True)


# What a record says when it cannot be cleaned.
_UNRENDERABLE = "a log record that could not be rendered without its local paths"


def _cleaned_arg(arg: object) -> object:
    """A number, a boolean or `None` stays as it is, so `%d` still formats it."""
    if arg is None or isinstance(arg, Number):
        return arg
    return without_local_paths(arg)


class _ScrubLocalPaths(logging.Filter):
    """Cuts local paths out of every log record before a handler formats it.

    A traceback on the record is dropped, not cleaned: its frames carry source
    lines and file paths together.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            self._scrub(record)
        except Exception:
            # A filter that raises would raise at the logging call, so a record
            # it cannot clean is replaced.
            record.msg, record.args = _UNRENDERABLE, ()
            record.__dict__.pop("color_message", None)
        if record.exc_info or record.exc_text:
            record.exc_info = None
            record.exc_text = None
        return True

    @staticmethod
    def _scrub(record: logging.LogRecord) -> None:
        # The args keep their number, order and keys, because uvicorn's access
        # log reads five of them. The access log's route (its third arg) keeps
        # its path as sent; its query is cleaned.
        access = record.name == "uvicorn.access"
        record.msg = without_local_paths(str(record.msg))
        if isinstance(record.args, Mapping):
            record.args = {key: _cleaned_arg(arg) for key, arg in record.args.items()}
        elif isinstance(record.args, tuple):
            record.args = tuple(
                _route(arg) if access and index == 2 and isinstance(arg, str) else _cleaned_arg(arg)
                for index, arg in enumerate(record.args)
            )
        color = record.__dict__.get("color_message")
        if isinstance(color, str):
            record.__dict__["color_message"] = without_local_paths(color)
        if access or not record.args:
            return
        # A path can be split across args (`"%s/%s"`), so judge the message whole too.
        message = record.getMessage()
        cleaned = without_local_paths(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
            record.__dict__.pop("color_message", None)


def configure_logging(*names: str) -> None:
    """Install the scrub filter on a root handler and on each named logger.

    A logger's filter runs only for records that logger emits, and uvicorn's
    loggers do not propagate to root. Calling it again adds no second filter.
    """
    scrub = _ScrubLocalPaths()
    root = logging.getLogger()
    if not any(
        isinstance(handler, logging.StreamHandler)
        and any(isinstance(f, _ScrubLocalPaths) for f in handler.filters)
        for handler in root.handlers
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler.addFilter(scrub)
        root.addHandler(handler)
    for name in names:
        logger = logging.getLogger(name)
        if not any(isinstance(f, _ScrubLocalPaths) for f in logger.filters):
            logger.addFilter(scrub)
