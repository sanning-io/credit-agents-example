"""The credit policy PDF is generated from a committed source, and both name the
fictional lender.

A PDF's text is compressed, so a plain `grep` over the repo does not see a name in
its rendered text or in its `/Title` metadata. These tests read the PDF as a reader
does: extracted text, and metadata.
"""

from __future__ import annotations

import re
from pathlib import Path

import pypdf

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_HTML = REPO_ROOT / "policy" / "credit-policy.html"
POLICY_PDF = REPO_ROOT / "policy" / "credit-policy.pdf"

LENDER = "Bruksvik Finans AB"


def _pdf_text(path: Path) -> str:
    reader = pypdf.PdfReader(str(path))
    return re.sub(r"\s+", " ", "\n".join(page.extract_text() or "" for page in reader.pages))


def test_the_policy_pdf_has_a_committed_source():
    assert POLICY_HTML.is_file()
    assert LENDER in POLICY_HTML.read_text(encoding="utf-8")


def test_the_policy_pdf_body_names_the_fictional_lender():
    text = _pdf_text(POLICY_PDF)
    assert LENDER in text
    assert "Bruksvik Financial Services" in text


def test_the_policy_pdf_title_metadata_names_the_fictional_lender():
    title = pypdf.PdfReader(str(POLICY_PDF)).metadata.get("/Title", "")
    assert title.startswith(LENDER)


def test_the_source_and_the_rendered_pdf_agree_on_the_title():
    source_title = re.search(r"<title>(.*?)</title>", POLICY_HTML.read_text(encoding="utf-8"))
    assert source_title
    rendered_title = pypdf.PdfReader(str(POLICY_PDF)).metadata.get("/Title", "")
    assert rendered_title == source_title.group(1)
