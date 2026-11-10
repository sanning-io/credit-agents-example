"""The README's "Using it with your own agents" section names files that are
still in the code, and the README keeps to the repo's writing rules: no em
dashes, no time words.

The section names files, not functions, so a reader sees where each part
lives without a code tour. This test fails when a file it names is missing,
so the section cannot drift from the code.
"""

from __future__ import annotations

import re

from credit_demo.common.documents import REPO_ROOT

README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
HEADING = "## Using it with your own agents"
TIME_WORDS = ("now", "currently", "yet", "still", "new", "soon", "today", "recently", "no longer", "at the moment")
BANNED_WORDS = ("should", "simply", "easy", "just")
SUPPORTED_PROVIDERS = "Sanning supports AWS S3 today. Other providers are on the roadmap."


def section() -> str:
    start = README.index(HEADING)
    end = README.find("\n## ", start + 1)
    return README[start:] if end == -1 else README[start:end]


def test_the_section_names_the_langchain_the_sanning_and_the_examples_own_parts():
    text = " ".join(section().split())
    for part in ("ordinary LangChain agents", "the example's own", "Sanning adds three pieces"):
        assert part in text
    for path in (
        "src/credit_demo/common/turn.py",
        "src/credit_demo/common/sanning.py",
        "src/credit_demo/evidence_builder/case_file.py",
        "src/credit_demo/common/calculator.py",
    ):
        assert path in text, f"the section no longer names `{path}`"


def test_every_path_in_the_section_exists():
    paths = set(re.findall(r"`(src/[\w/.-]+)`", " ".join(section().split())))
    assert paths
    assert [p for p in paths if not (REPO_ROOT / p).is_file()] == []


def test_the_readme_links_no_hosted_demo():
    assert "railway.app" not in README


def test_the_readme_has_no_em_dashes_or_time_words():
    assert "—" not in README
    words = "|".join(re.escape(word) for word in (*TIME_WORDS, *BANNED_WORDS))
    # The one sentence that says which storage providers work is a statement
    # about the provider list, so its "today" is allowed.
    text = README.replace(SUPPORTED_PROVIDERS, "")
    found = re.findall(rf"\b(?:{words})\b", text, flags=re.IGNORECASE)
    assert found == []
    assert SUPPORTED_PROVIDERS in README


def test_the_readme_says_checker_agent():
    assert "checking agent" not in README.lower()
