"""The lender's cover calculator: arithmetic only, never a verdict.

Both agents use the same calculator, the way an underwriter and a checker at
a lender use the same credit spreadsheet. The agent enters the figures it
reads off the documents (each year's EBITDA, the existing debt service), any
adjustments it judges right (a year and an amount each), and a deposit. The
calculator returns the cover those figures give, on the financing the
application requests. It never says whether the cover is enough, which deposit
the policy asks for, or what to decide: those are the agent's judgements, made
by reading the policy. It takes numbers only: why an adjustment is right, and
where the documents show it, are written once, in the agent's decision.

Every input travels with the result, so the record of a calculation shows
exactly which figures and adjustments an agent used.

Annual earnings are the average of the years entered, and cover is annual
earnings over total annual debt service: the existing debt service plus the
facility's own, on the term and rate the application requests. That is the
credit policy's definition of debt service cover.
"""

from __future__ import annotations

from typing import Any, Sequence

from .application import Terms


class CalculatorError(ValueError):
    """Input the calculator cannot use. The agent reads the message and tries
    again."""


def annuity_factor(terms: Terms) -> float:
    rate = terms.monthly_rate
    return (1 - (1 + rate) ** -terms.term_months) / rate


def facility_annual_debt_service(terms: Terms, financed_amount_sek: float | None = None) -> int:
    """The facility's own annual debt service, on the requested term and rate,
    on `financed_amount_sek` (the requested amount when left out)."""
    financed = terms.amount_sek if financed_amount_sek is None else financed_amount_sek
    return round(financed / annuity_factor(terms) * 12)


def calculate_cover(
    *,
    terms: Terms,
    earnings_by_year: Sequence[dict[str, Any]],
    existing_annual_debt_service_sek: int,
    deposit_percent: float = 0.0,
    adjustments: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Cover on the financing the application requests (`terms`).

    `earnings_by_year` is one entry per twelve months of trading, each
    `{"year": <label>, "ebitda_sek": <amount>}`. Annual earnings are their
    average. `adjustments` are `{"year": <label>, "amount_sek": <signed amount>}`,
    each added to the year it names: numbers only, since the reason for an
    adjustment belongs to the decision that relies on it. Cover is always given
    on unadjusted earnings, and also on adjusted earnings when there are
    adjustments. `deposit_percent` is a percentage of the amount requested.
    """
    if not earnings_by_year:
        raise CalculatorError("calculate_cover: give at least one year of earnings")
    years: list[dict[str, Any]] = []
    for entry in earnings_by_year:
        label = str(entry.get("year") or "").strip()
        ebitda = entry.get("ebitda_sek")
        if not label or isinstance(ebitda, bool) or not isinstance(ebitda, (int, float)):
            raise CalculatorError("calculate_cover: each year needs a `year` label and an `ebitda_sek` amount")
        years.append({"year": label, "ebitda_sek": int(round(ebitda)), "adjustments_sek": 0})
    labels = [y["year"] for y in years]
    if len(set(labels)) != len(labels):
        raise CalculatorError("calculate_cover: each year label must appear once")

    used_adjustments: list[dict[str, Any]] = []
    for adjustment in adjustments or ():
        label = str(adjustment.get("year") or "").strip()
        amount = adjustment.get("amount_sek")
        if label not in labels:
            raise CalculatorError(
                f"calculate_cover: an adjustment names the year {label!r}, which is not "
                f"one of the years given: {', '.join(labels)}"
            )
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise CalculatorError("calculate_cover: each adjustment needs an `amount_sek`")
        years[labels.index(label)]["adjustments_sek"] += int(round(amount))
        used_adjustments.append({"year": label, "amount_sek": int(round(amount))})

    if isinstance(existing_annual_debt_service_sek, bool) or not isinstance(
        existing_annual_debt_service_sek, (int, float)
    ):
        raise CalculatorError("calculate_cover: give the existing annual debt service in SEK")
    if existing_annual_debt_service_sek < 0:
        raise CalculatorError(
            "calculate_cover: the existing annual debt service cannot be negative: give what the applicant "
            "pays on its existing loans each year, or 0"
        )
    if not 0 <= deposit_percent < 100:
        raise CalculatorError("calculate_cover: the deposit is a percentage from 0 up to 100")

    deposit_sek = round(terms.amount_sek * deposit_percent / 100)
    financed = terms.amount_sek - deposit_sek
    facility_service = facility_annual_debt_service(terms, financed)
    existing = int(round(existing_annual_debt_service_sek))
    total_service = facility_service + existing
    if total_service <= 0:
        raise CalculatorError(
            "calculate_cover: the total annual debt service is zero, so there is no cover to calculate"
        )

    unadjusted = round(sum(y["ebitda_sek"] for y in years) / len(years))
    result: dict[str, Any] = {
        "earnings_by_year": [
            {"year": y["year"], "ebitda_sek": y["ebitda_sek"], "adjusted_ebitda_sek": y["ebitda_sek"] + y["adjustments_sek"]}
            for y in years
        ],
        "annual_earnings_sek": unadjusted,
        "existing_annual_debt_service_sek": existing,
        "amount_requested_sek": terms.amount_sek,
        "deposit_percent": deposit_percent,
        "deposit_sek": deposit_sek,
        "financed_amount_sek": financed,
        "facility_annual_debt_service_sek": facility_service,
        "total_annual_debt_service_sek": total_service,
        "cover": round(unadjusted / total_service, 4),
    }
    if used_adjustments:
        adjusted = round(sum(y["ebitda_sek"] + y["adjustments_sek"] for y in years) / len(years))
        result["adjustments"] = used_adjustments
        result["adjusted_annual_earnings_sek"] = adjusted
        result["adjusted_cover"] = round(adjusted / total_service, 4)
    return result
