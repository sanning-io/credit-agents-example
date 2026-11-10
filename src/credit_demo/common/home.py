"""Where the example's case documents and run directories live. `CREDIT_DEMO_HOME`
names the directory; unset, it is the checkout. A path worked out from this file
would point into site-packages in the container.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

HOME_VARIABLE = "CREDIT_DEMO_HOME"

# src/credit_demo/common/home.py -> the checkout's root.
SOURCE_ROOT = Path(__file__).resolve().parents[3]

# The sample application a credit officer opens a case on. Its folder holds
# the application file and the applicant's documents, and stands in for the
# lender's document store. Relative to the home.
SAMPLE_APPLICATION = Path("applications") / "APP-2026-0412" / "application.json"
CREDIT_POLICY = Path("policy") / "credit-policy.pdf"

# The sample application's documents by name, for the desk's start-up check
# and its document links. Neither agent reads them from here: both find every
# case document through the case they work (`credit_demo.common.context`).
BANK_STATEMENTS = SAMPLE_APPLICATION.parent / "bank-statements.csv"
ANNUAL_REPORT = SAMPLE_APPLICATION.parent / "annual-report.pdf"

CASE_DOCUMENTS = (SAMPLE_APPLICATION, BANK_STATEMENTS, ANNUAL_REPORT, CREDIT_POLICY)


def resolve_home(env: Mapping[str, str] | None = None) -> Path:
    """`CREDIT_DEMO_HOME` when it is set, the checkout's root when it is not."""
    value = (os.environ if env is None else env).get(HOME_VARIABLE, "").strip()
    return Path(value).resolve() if value else SOURCE_ROOT


REPO_ROOT = resolve_home()


def missing_documents(root: Path = REPO_ROOT) -> list[str]:
    """The case documents that are not where this home says they are."""
    return [str(name) for name in CASE_DOCUMENTS if not (root / name).is_file()]
