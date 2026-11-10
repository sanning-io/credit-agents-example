"""Reconciliation tests for the Nordvik Haulage / Bruksvik scenario data. They read only the four
case documents (the application JSON, the bank statements CSV, the annual report PDF and the
credit policy PDF), run no agent and import none of the example's code. The case's figures, to
the krona, come from the documents themselves. Run directly: `python3
tests/test_scenario_reconciliation.py`, or with pytest. Requires `pypdf`."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pypdf

REPO_ROOT = Path(__file__).resolve().parent.parent
APPLICATION_FOLDER = REPO_ROOT / "applications" / "APP-2026-0412"
APPLICATION = APPLICATION_FOLDER / "application.json"
BANK_STATEMENTS = APPLICATION_FOLDER / "bank-statements.csv"
ANNUAL_REPORT = APPLICATION_FOLDER / "annual-report.pdf"
CREDIT_POLICY = REPO_ROOT / "policy" / "credit-policy.pdf"

# The terms the application requests, read from its file so every derived figure
# below is computed, not hardcoded.
_REQUESTED = json.loads(APPLICATION.read_text(encoding="utf-8"))["financing_requested"]
FINANCED_AMOUNT = _REQUESTED["amount_sek"]
TERM_MONTHS = _REQUESTED["term_months"]
MONTHLY_RATE = _REQUESTED["nominal_annual_rate_percent"] / 100 / 12
DEPOSIT_RATE = 0.15


def test_the_application_requests_the_terms_the_figures_rest_on():
    """The terms the figures below rest on, as the application states them."""
    assert (FINANCED_AMOUNT, TERM_MONTHS) == (4_200_000, 60)
    assert MONTHLY_RATE == 0.005  # 6.0% nominal annual


def _annuity_factor(monthly_rate: float, term_months: int) -> float:
    return (1 - (1 + monthly_rate) ** -term_months) / monthly_rate


def _read_pdf_text(path: Path) -> str:
    reader = pypdf.PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return re.sub(r"\s+", " ", text)


def _parse_money(s: str) -> int:
    return int(s.replace(",", ""))


def _load_bank_statements() -> list[dict]:
    with BANK_STATEMENTS.open(newline="") as f:
        return list(csv.DictReader(f))


def test_bank_statements_cover_twelve_months():
    rows = _load_bank_statements()
    assert len(rows) == 12


def test_bank_statements_revenue_and_ebitda_reconcile():
    rows = _load_bank_statements()

    total_revenue = sum(int(r["customer_receipts_sek"]) for r in rows)
    total_opex = sum(
        int(r["fuel_sek"])
        + int(r["driver_wages_sek"])
        + int(r["maintenance_repairs_sek"])
        + int(r["insurance_sek"])
        + int(r["tolls_and_permits_sek"])
        + int(r["admin_overhead_sek"])
        for r in rows
    )
    total_ebitda = total_revenue - total_opex
    total_existing_debt_service = sum(int(r["existing_loan_repayment_sek"]) for r in rows)

    assert total_revenue == 27_500_000
    assert total_ebitda == 2_750_000
    assert total_existing_debt_service == 1_100_000


def test_bank_statements_show_seasonality():
    rows = _load_bank_statements()
    revenues = [int(r["customer_receipts_sek"]) for r in rows]
    # A haulier's weakest month sits well below its strongest: this is not a
    # flat, evenly-divided number, and one month clearly reads as a summer trough.
    assert max(revenues) / min(revenues) > 1.5


def test_annual_report_states_prior_year_revenue_and_ebitda():
    text = _read_pdf_text(ANNUAL_REPORT)

    turnover_match = re.search(r"Net turnover ([\d,]+)", text)
    ebitda_match = re.search(r"EBITDA ([\d,]+)", text)
    assert turnover_match and ebitda_match

    prior_year_revenue = _parse_money(turnover_match.group(1))
    prior_year_ebitda = _parse_money(ebitda_match.group(1))

    assert prior_year_revenue == 24_800_000
    assert prior_year_ebitda == 1_600_000


def test_annual_report_covers_a_different_period_than_the_statements():
    text = _read_pdf_text(ANNUAL_REPORT)
    # The weaker year must be discoverable as the 12 months before the statements,
    # not restated inside them.
    assert "1 October 2024 to 30 September 2025" in text
    rows = _load_bank_statements()
    statement_months = {r["month"] for r in rows}
    assert "2024-10" not in statement_months
    assert "2025-09" not in statement_months


def test_credit_policy_states_minimum_dscr_and_window():
    text = _read_pdf_text(CREDIT_POLICY)

    min_dscr_match = re.search(r"minimum DSCR for approval is ([\d.]+)x", text)
    window_match = re.search(r"trailing (\d+) months of trading history", text)
    assert min_dscr_match and window_match

    assert float(min_dscr_match.group(1)) == 1.25
    assert int(window_match.group(1)) == 24


def test_credit_policy_states_deposit_tier_and_conditions():
    text = _read_pdf_text(CREDIT_POLICY)
    assert "15% of the financed asset value" in text
    assert "Quarterly DSCR covenant" in text
    assert "Phased release" in text


def test_financing_arithmetic_gives_the_annual_debt_service_to_the_krona():
    factor = _annuity_factor(MONTHLY_RATE, TERM_MONTHS)
    assert round(factor, 7) == 51.7255608

    monthly_payment = FINANCED_AMOUNT / factor
    annual_service = round(monthly_payment * 12)
    assert round(monthly_payment, 2) == 81_197.77
    assert annual_service == 974_373

    existing_debt_service = sum(
        int(r["existing_loan_repayment_sek"]) for r in _load_bank_statements()
    )
    total_service = annual_service + existing_debt_service
    assert total_service == 2_074_373


def test_cover_ratios_reconcile_to_the_krona():
    rows = _load_bank_statements()
    twelve_month_ebitda = sum(int(r["customer_receipts_sek"]) for r in rows) - sum(
        int(r["fuel_sek"])
        + int(r["driver_wages_sek"])
        + int(r["maintenance_repairs_sek"])
        + int(r["insurance_sek"])
        + int(r["tolls_and_permits_sek"])
        + int(r["admin_overhead_sek"])
        for r in rows
    )
    existing_debt_service = sum(int(r["existing_loan_repayment_sek"]) for r in rows)

    annual_report_text = _read_pdf_text(ANNUAL_REPORT)
    prior_year_ebitda = _parse_money(
        re.search(r"EBITDA ([\d,]+)", annual_report_text).group(1)
    )

    factor = _annuity_factor(MONTHLY_RATE, TERM_MONTHS)
    annual_service = round(FINANCED_AMOUNT / factor * 12)
    total_service = annual_service + existing_debt_service

    twenty_four_month_ebitda = round((twelve_month_ebitda + prior_year_ebitda) / 2)
    assert twenty_four_month_ebitda == 2_175_000

    cover_12 = twelve_month_ebitda / total_service
    cover_24 = twenty_four_month_ebitda / total_service
    assert round(cover_12, 2) == 1.33
    assert round(cover_24, 2) == 1.05

    # With the policy's 15% deposit tier applied.
    deposit = round(FINANCED_AMOUNT * DEPOSIT_RATE)
    financed_after_deposit = FINANCED_AMOUNT - deposit
    annual_service_after_deposit = round(financed_after_deposit / factor * 12)
    total_service_after_deposit = annual_service_after_deposit + existing_debt_service

    cover_12_deposit = twelve_month_ebitda / total_service_after_deposit
    cover_24_deposit = twenty_four_month_ebitda / total_service_after_deposit

    assert deposit == 630_000
    assert annual_service_after_deposit == 828_217
    assert total_service_after_deposit == 1_928_217
    assert round(cover_12_deposit, 2) == 1.43
    assert round(cover_24_deposit, 2) == 1.13


def test_unadjusted_cover_before_any_deposit_is_below_the_deposit_tables_floor():
    """Over the policy's 24 months, unadjusted and before any deposit, cover
    sits below the 1.10x floor of the deposit table. What a deposit and the
    policy's other sections then allow is for the agents to read. Derived
    from the documents alone, no model."""
    rows = _load_bank_statements()
    twelve_month_ebitda = sum(int(r["customer_receipts_sek"]) for r in rows) - sum(
        int(r["fuel_sek"])
        + int(r["driver_wages_sek"])
        + int(r["maintenance_repairs_sek"])
        + int(r["insurance_sek"])
        + int(r["tolls_and_permits_sek"])
        + int(r["admin_overhead_sek"])
        for r in rows
    )
    existing_debt_service = sum(int(r["existing_loan_repayment_sek"]) for r in rows)
    prior_year_ebitda = _parse_money(re.search(r"EBITDA ([\d,]+)", _read_pdf_text(ANNUAL_REPORT)).group(1))

    policy_text = _read_pdf_text(CREDIT_POLICY)
    floor = float(re.search(r"Below ([\d.]+)x", policy_text).group(1))
    minimum = float(re.search(r"minimum DSCR for approval is ([\d.]+)x", policy_text).group(1))

    factor = _annuity_factor(MONTHLY_RATE, TERM_MONTHS)
    total_service = FINANCED_AMOUNT / factor * 12 + existing_debt_service

    assert (twelve_month_ebitda + prior_year_ebitda) / 2 / total_service < floor
    # The question the agents can disagree on is whether the weak year was a
    # one-off: counted like the recent year, the case clears the minimum.
    assert twelve_month_ebitda / total_service >= minimum


def test_the_weak_year_carries_one_specific_item_the_agents_can_argue_over():
    """The annual report names one quantified item in the weak year: a bad
    debt written off when a customer went bankrupt. Whether it will recur is
    a judgement. As reported, cover is below 1.10x before a deposit and
    between 1.10x and 1.25x after the 15% deposit; added back, it is between
    1.10x and 1.25x either way."""
    text = _read_pdf_text(ANNUAL_REPORT)
    assert "went bankrupt owing SEK 400,000" in text

    factor = _annuity_factor(MONTHLY_RATE, TERM_MONTHS)
    existing = 1_100_000
    service = FINANCED_AMOUNT / factor * 12 + existing
    service_after_deposit = FINANCED_AMOUNT * (1 - DEPOSIT_RATE) / factor * 12 + existing
    as_reported = (2_750_000 + 1_600_000) / 2
    added_back = (2_750_000 + 1_600_000 + 400_000) / 2

    assert as_reported / service < 1.10
    assert 1.10 <= as_reported / service_after_deposit < 1.25
    assert 1.10 <= added_back / service < 1.25
    assert 1.10 <= added_back / service_after_deposit < 1.25


def test_the_policy_carries_general_rules_and_no_stage_direction():
    text = _read_pdf_text(CREDIT_POLICY)
    assert "Adjusted earnings" in text
    assert "only where the checker agrees with every adjustment" in text
    assert "The checker's review" in text
    assert "senior credit officer with both views attached" in text
    assert "must be challenged by the checker" not in text


if __name__ == "__main__":
    import sys

    failures = 0
    tests = [(name, fn) for name, fn in list(globals().items()) if name.startswith("test_")]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
