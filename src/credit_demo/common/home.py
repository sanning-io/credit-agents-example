"""Where this example's case documents and its run directories live.

One answer, used by everything that needs one. `parents[3]` of a module's
own file is the repository root when the package is run from a checkout and
is `lib/python3.x` when it is installed into site-packages, as the container
installs it: there, a path worked out from the source would point inside the
Python installation, where no document is.

`CREDIT_DEMO_HOME` names the directory explicitly, and the Dockerfile sets
it to where it copies the documents. Unset, it is the checkout this package
sits in, which is what every laptop run and the test ladder use. Either way
a home without the documents in it is reported by `missing_documents`, and a
service that finds any missing will not start a run.
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
# The credit policy in force, which every case opened today is judged against.
CREDIT_POLICY = Path("policy") / "credit-policy.pdf"

# The sample application's documents by name, for the desk's start-up check
# and its document links. Neither agent reads them from here: both find every
# case document through the case they work (`credit_demo.common.context`).
BANK_STATEMENTS = SAMPLE_APPLICATION.parent / "bank-statements.csv"
ANNUAL_REPORT = SAMPLE_APPLICATION.parent / "annual-report.pdf"

# The four case documents of the sample case.
CASE_DOCUMENTS = (SAMPLE_APPLICATION, BANK_STATEMENTS, ANNUAL_REPORT, CREDIT_POLICY)


def resolve_home(env: Mapping[str, str] | None = None) -> Path:
    """`CREDIT_DEMO_HOME` when it is set, the checkout's root when it is not."""
    value = (os.environ if env is None else env).get(HOME_VARIABLE, "").strip()
    return Path(value).resolve() if value else SOURCE_ROOT


REPO_ROOT = resolve_home()


def missing_documents(root: Path = REPO_ROOT) -> list[str]:
    """The case documents that are not where this home says they are."""
    return [str(name) for name in CASE_DOCUMENTS if not (root / name).is_file()]
