"""The bank statements, annual report and credit policy, read the same way by
whichever agent reads them. This module reads the documents and does not interpret
the policy. No document is named here: each agent's tools pass the locations of
the case.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import TypedDict

import pypdf

from . import home

# Resolved once, by `credit_demo.common.home`: the checkout, or the directory
# `CREDIT_DEMO_HOME` names, which is how the installed package in the
# container finds the documents it was shipped with.
REPO_ROOT = home.REPO_ROOT

OPEX_COLUMNS = (
    "fuel_sek",
    "driver_wages_sek",
    "maintenance_repairs_sek",
    "insurance_sek",
    "tolls_and_permits_sek",
    "admin_overhead_sek",
)


class BankStatementTotals(TypedDict):
    months: int
    total_revenue_sek: int
    total_opex_sek: int
    ebitda_sek: int
    existing_annual_debt_service_sek: int


def read_pdf_text(path: Path) -> str:
    reader = pypdf.PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return re.sub(r"\s+", " ", text).strip()


def load_bank_statement_rows(path: Path) -> list[dict[str, str]]:
    with Path(path).open(newline="") as f:
        return list(csv.DictReader(f))


def bank_statement_months(path: Path) -> list[dict[str, int | str]]:
    """Each month of the bank statements, reduced to receipts, operating
    expenses, EBITDA and the existing loan's repayment."""
    months = []
    for row in load_bank_statement_rows(path):
        receipts = int(row["customer_receipts_sek"])
        expenses = sum(int(row[column]) for column in OPEX_COLUMNS)
        months.append(
            {
                "month": row["month_label"],
                "receipts_sek": receipts,
                "operating_expenses_sek": expenses,
                "ebitda_sek": receipts - expenses,
                "existing_loan_repayment_sek": int(row["existing_loan_repayment_sek"]),
            }
        )
    return months


def bank_statement_totals(path: Path) -> BankStatementTotals:
    """The bank statements, reduced to the figures a debt service cover
    calculation needs: EBITDA (receipts less operating expenses) and the
    existing loan's own annual debt service."""
    rows = load_bank_statement_rows(path)
    total_revenue = sum(int(r["customer_receipts_sek"]) for r in rows)
    total_opex = sum(int(r[col]) for r in rows for col in OPEX_COLUMNS)
    existing_debt_service = sum(int(r["existing_loan_repayment_sek"]) for r in rows)
    return {
        "months": len(rows),
        "total_revenue_sek": total_revenue,
        "total_opex_sek": total_opex,
        "ebitda_sek": total_revenue - total_opex,
        "existing_annual_debt_service_sek": existing_debt_service,
    }
