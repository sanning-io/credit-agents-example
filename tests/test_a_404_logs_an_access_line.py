"""A request the desk answers with a 404 logs one ordinary access line.

`/favicon.ico` looks like a file to the local-path scrub filter on uvicorn's
loggers, because a name with an extension does. Cut to its file name, with
the record's args dropped, it would not format: uvicorn's access formatter
reads five args, so the line would become `--- Logging error ---` and a
traceback.
"""

from __future__ import annotations

import logging

from uvicorn.logging import AccessFormatter

from credit_demo.common.errors import _ScrubLocalPaths


def _access_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname=__file__, lineno=1,
        msg='%s - "%s %s HTTP/%s" %d', args=("127.0.0.1:60994", "GET", path, "1.1", 404), exc_info=None,
    )


def _line(record: logging.LogRecord) -> str:
    fmt = '%(client_addr)s - "%(request_line)s" %(status_code)s'
    return AccessFormatter(fmt, use_colors=False).format(record)


def test_a_request_for_a_file_name_that_is_not_there_formats_as_an_access_line():
    record = _access_record("/favicon.ico")
    assert _ScrubLocalPaths().filter(record) is True
    assert _line(record) == '127.0.0.1:60994 - "GET /favicon.ico HTTP/1.1" 404 Not Found'


def test_a_local_path_in_a_record_is_still_scrubbed_and_its_args_keep_their_shape():
    record = logging.LogRecord(
        name="credit_demo.x", level=logging.WARNING, pathname=__file__, lineno=1,
        msg="could not read %s for %s", args=("/Users/someone/work/credit-policy.pdf", "case"), exc_info=None,
    )
    assert _ScrubLocalPaths().filter(record) is True
    assert record.getMessage() == "could not read credit-policy.pdf for case"
    assert len(record.args) == 2


def test_a_local_path_in_a_request_line_other_than_its_route_is_still_scrubbed():
    record = _access_record("/documents/x?from=/Users/someone/work/credit-policy.pdf")
    assert _ScrubLocalPaths().filter(record) is True
    assert "/Users/someone" not in _line(record)
