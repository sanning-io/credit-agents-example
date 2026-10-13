"""How an error reads once it leaves the code that raised it.

Every error in this example travels. A turn's error crosses to the other
agent in the reply to a hand-off, lands in the case's record, is shown on the
credit desk, is signed into a closing record, and is printed to the operator's
log. And an exception's own text carries whatever its raiser put in it: a
`FileNotFoundError` for the credit policy carries the absolute path of the
policy on this machine, and rendered as it is, that path would reach the
checker's reply, the case and the operator's standard output.

So an error is rendered here, once, and never with `str(exc)` or
`traceback.format_exception_only` at the place it is raised. `describe`
renders an exception, `without_local_paths` cleans text that already carries
one, and `tell_the_operator` writes a line to the log through the same
cleaning. A local path becomes its last component: the file's NAME still
says which document failed, which is what a reader acts on, and the machine's
directory layout goes nowhere. `tests/test_errors_carry_no_local_path.py`
fails on any other place in `src/` that renders an exception by hand.

What counts as a local path is judged on its LAST PART, never on whether the
machine running this code happens to have that directory: a path whose final
segment names a file (a dot followed by an extension, `credit-policy.pdf`,
`case.json`) is cut to that name, whatever directory it sits under and whether
or not that directory exists here. A bare directory mount with nothing under
it (`/tmp`, `/run/abc`) has no such name and is left alone, which is also
what leaves a URL's path (`/cases/{id}/handoffs`, `/health`) alone: a route is
not a place on this machine's disk, and the reason a request failed is worth
keeping. Every SHAPE a local path travels in is covered: POSIX and Windows,
Windows with forward slashes, a UNC share, a `file://` URI, a `~/` home
reference, a `%2F`-encoded path, and one immediately after a bare `:` (not
part of a `scheme://`). A quoted path with a space in it is cut whole, never
split at the space.
"""

from __future__ import annotations

import logging
import re
import sys
import traceback
from collections.abc import Mapping
from numbers import Number

# The characters that end a path in prose or a repr: whitespace and the
# punctuation that closes a sentence, a tuple or a bracketed expression.
# Colon is deliberately NOT here: `error:/home/x` and `PATH=a:/home/x` are
# two of the shapes this module is told to cut, and both put the path right
# after a colon.
_STOP = r"""\s'"`,;()\[\]{}<>|"""

# One path, in every shape this example has been handed one in. Order does
# not matter between these: each is a `re.sub` pass over the whole text, and
# a span one pass rewrites is gone by the time a later pass runs, so there is
# nothing left for another pattern to double-cut.
_WINDOWS = re.compile(r"(?<![\w])[A-Za-z]:\\[^" + _STOP + r"]*")
_WINDOWS_FORWARD = re.compile(r"(?<![\w])[A-Za-z]:/[^" + _STOP + r"]*")
_UNC = re.compile(r"(?<![\w\\])\\\\[^" + _STOP + r"]+")
_FILE_URI = re.compile(r"(?i)file://+[^" + _STOP + r"]+")
_TILDE = re.compile(r"~[/\\][^" + _STOP + r"]*")
_ENCODED = re.compile(r"(?i)%2f[^" + _STOP + r"]*")
# A `/` right after a bare colon, and not the `://` of a URL scheme: the
# negative lookahead is what tells `error:/home/x` (cut) from `http://host/x`
# (a URL's scheme, left to the plain POSIX pattern below to judge on its own
# lookbehind, which excludes a `/` preceded by a colon at all).
_COLON_PREFIXED = re.compile(r"(?<=:)/(?!/)[^" + _STOP + r"]*")
# A POSIX path starts at a `/` that does not follow a word character, a dot,
# a colon or another slash, so the path part of `http://host/x` and a
# relative `./runs/x` are never read as one. It runs until whitespace or a
# character that ends a path in prose or a repr.
_POSIX = re.compile(r"(?<![\w.:/\\])/[^" + _STOP + r"]+")
# A path with a space in it, quoted so the quote marks its real end: matched
# first, and whole, so a later pass never splits it at the space.
_QUOTED_WITH_SPACE = re.compile(
    r"'(?P<single>(?:/|~[/\\]|[A-Za-z]:[\\/]|\\\\|file://)[^'\n]*\s[^'\n]*)'"
    r"|\"(?P<double>(?:/|~[/\\]|[A-Za-z]:[\\/]|\\\\|file://)[^\"\n]*\s[^\"\n]*)\""
)

_SEGMENT_SPLIT = re.compile(r"[/\\]|%2f", re.IGNORECASE)
# The last part of a path is worth cutting to when it names a FILE: a dot
# followed by a short run of letters or digits (`credit-policy.pdf`,
# `case.json`). And, whether or not it does, when it runs four segments deep
# or more: a bare directory mount with nothing under it (`/tmp`, `/run/abc`)
# is shallow and has no name either, and is left alone rather than mangled
# into a token that no longer reads as the route it was. Whether the
# directory exists on THIS machine never decides it: that would cut
# `POST /tmp` to `POST tmp` on any machine that happens to have one, and leave
# a real path alone on a machine that does not, missing directory or
# extensionless file included (`--logs /app/runs/checker-logstore/does-not-
# exist`, four segments deep, names no extension at all).
#
# Four is not deeper than every route this example serves: a case document's
# route, `/cases/{id}/documents/{document}`, is four segments deep too, and
# text that carries it is cut to its last part. So a request's own route
# never goes through here: the access log (`_ScrubLocalPaths`) and the
# operator's line for a request that raised
# (`tell_the_operator_a_request_raised`) keep it as the client sent it.
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
    # Keep what followed the path in the same token, a trailing full stop or
    # colon included, so the sentence around it still reads.
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
    """An exception as one line: its type and its message, with no local path.

    The only way this repo renders an exception it caught."""
    rendered = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    return without_local_paths(rendered)


def tell_the_operator(message: str) -> None:
    """Write a line to the process's log now, not when a buffer fills.

    Standard error, flushed. Standard output is block-buffered whenever it
    is not a terminal, which is every container and every launcher that
    inherits it, and a buffered line is lost outright if the process is
    killed before the buffer is written. Cleaned of local paths like
    every other error this repo renders, because the log is a destination
    too: an operator's log is copied into tickets and chat.
    """
    print(without_local_paths(message), file=sys.stderr, flush=True)


def _route(target: str) -> str:
    """A request's path kept as it came, and its query cleaned."""
    path, mark, query = target.partition("?")
    return path + mark + without_local_paths(query)


def tell_the_operator_a_request_raised(service: str, method: str, route: str, exc: BaseException) -> None:
    """The operator's line for a request that raised: the service, the
    request and the exception.

    The request's route is kept as the client sent it, as the access log
    keeps it, and only the exception is cleaned. A route is not a place on
    this machine's disk, and a case document's route
    (`/cases/{id}/documents/{document}`) is four segments deep, deep enough
    that `without_local_paths` would cut it to its last part.
    """
    print(f"{service}: {method} {_route(route)} raised: {describe(exc)}", file=sys.stderr, flush=True)


# What a log record says when this module cannot render it without its path.
_UNRENDERABLE = "a log record that could not be rendered without its local paths"


def _cleaned_arg(arg: object) -> object:
    """A log record's arg, cleaned: a number, a boolean or `None` as it is,
    anything else rendered to text with no local path."""
    if arg is None or isinstance(arg, Number):
        return arg
    return without_local_paths(arg)


class _ScrubLocalPaths(logging.Filter):
    """Every record this filter reaches loses its path the same way this
    module's other renderers do, whoever wrote it.

    A record's own text is cleaned through `without_local_paths` before any
    handler formats it, and a traceback attached to the record (`exc_info`)
    is dropped rather than cleaned: a frame's own text is a source line and a
    local file path together, and this repo has no way to clean the second
    without also mangling the first. Dropping it is the same trade the module
    docstring already makes for `describe`: the type and the message survive,
    the machine's own layout does not.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            self._scrub(record)
        except Exception:
            # A filter that raises raises at the logging call, in whatever
            # code logged. A record this filter cannot clean is replaced
            # rather than passed on with its path.
            record.msg, record.args = _UNRENDERABLE, ()
            record.__dict__.pop("color_message", None)
        if record.exc_info or record.exc_text:
            record.exc_info = None
            record.exc_text = None
        return True

    @staticmethod
    def _scrub(record: logging.LogRecord) -> None:
        # The text and every arg are cleaned one by one, and the args keep
        # their number, order and keys: a formatter that reads the record's
        # own args (uvicorn's access log reads five, the status code a
        # number) still gets them. A number, a boolean or `None` stays as it
        # is, so `%d` still formats it. Anything else is rendered to text
        # and cleaned: a `Path`, an exception, a list or a mapping carries a
        # path in its text as surely as a string does. A message that is not
        # a string, such as an exception logged on its own, is rendered too.
        # A request's route is not a place on this machine's disk, so the
        # access log's route (its third arg) keeps its path as the client
        # sent it, and `/favicon.ico` stays `/favicon.ico`. Its query is
        # cleaned.
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
        # A path can still be split across the args, or between the text
        # and an arg (`"%s/%s" % (folder, name)`), so the message is judged
        # whole as well. Where that finds a path, the record becomes the
        # cleaned message with no args: no formatter but the access log's
        # reads them.
        message = record.getMessage()
        cleaned = without_local_paths(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
            record.__dict__.pop("color_message", None)


def configure_logging(*names: str) -> None:
    """Install the scrub filter where a record can reach a handler.

    A filter on a `Logger` only runs for a record that logger itself emits,
    never for one a child logger emits and merely propagates through it,
    which is why this is not one filter on the root logger. It is a filter on
    a handler this function attaches to the root (so anything that DOES
    propagate to root is covered once), and the SAME filter attached directly
    to every logger named here, because uvicorn's own loggers
    (`uvicorn`, `uvicorn.error`, `uvicorn.access`) configure their own
    handlers with `propagate=False`, and a record they log never reaches
    root at all. Idempotent, so a service that calls this more than once
    still ends up with one filter, not a chain of them.
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
