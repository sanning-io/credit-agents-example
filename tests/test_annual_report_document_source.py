"""The applicant's annual report PDF is generated from a committed source.

A PDF's text is compressed, so a plain `grep` over the repo does not see what it
says. These tests read the PDF as a reader does, the same way as
`test_policy_document_source.py`: extracted text, and metadata.
"""

from __future__ import annotations

import re
from pathlib import Path

import pypdf

REPO_ROOT = Path(__file__).resolve().parent.parent
ANNUAL_REPORT_HTML = REPO_ROOT / "applications" / "APP-2026-0412" / "annual-report.html"
ANNUAL_REPORT_PDF = REPO_ROOT / "applications" / "APP-2026-0412" / "annual-report.pdf"

APPLICANT = "Nordvik Haulage AB"


def _pdf_text(path: Path) -> str:
    reader = pypdf.PdfReader(str(path))
    return re.sub(r"\s+", " ", "\n".join(page.extract_text() or "" for page in reader.pages))


def test_the_annual_report_pdf_has_a_committed_source():
    assert ANNUAL_REPORT_HTML.is_file()
    assert APPLICANT in ANNUAL_REPORT_HTML.read_text(encoding="utf-8")


def test_the_annual_report_pdf_names_the_fictional_applicant():
    text = _pdf_text(ANNUAL_REPORT_PDF)
    assert APPLICANT in text
    assert "fictional company" in text


def test_the_annual_report_pdf_title_metadata_names_the_fictional_applicant():
    title = pypdf.PdfReader(str(ANNUAL_REPORT_PDF)).metadata.get("/Title", "")
    assert title.startswith(APPLICANT)


def test_the_source_and_the_rendered_pdf_agree_on_the_title():
    source_title = re.search(r"<title>(.*?)</title>", ANNUAL_REPORT_HTML.read_text(encoding="utf-8"))
    assert source_title
    rendered_title = pypdf.PdfReader(str(ANNUAL_REPORT_PDF)).metadata.get("/Title", "")
    assert rendered_title == source_title.group(1)


def test_the_annual_report_pdf_has_three_pages():
    assert len(pypdf.PdfReader(str(ANNUAL_REPORT_PDF)).pages) == 3
