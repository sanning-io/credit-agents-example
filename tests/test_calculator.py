"""The cover calculator: arithmetic on the figures an agent enters, on the
financing the application requests, and never a verdict."""

from __future__ import annotations

import pytest

from credit_demo.common.application import Terms
from credit_demo.common.calculator import CalculatorError, calculate_cover

TERMS = Terms(amount_sek=4_200_000, term_months=60, nominal_annual_rate_percent=6.0)
RECENT = {"year": "Oct 2025 to Sep 2026", "ebitda_sek": 2_750_000}
PRIOR = {"year": "Oct 2024 to Sep 2025", "ebitda_sek": 1_600_000}


def _cover(**more):
    return calculate_cover(
        terms=TERMS,
        earnings_by_year=more.pop("earnings_by_year", [RECENT, PRIOR]),
        existing_annual_debt_service_sek=1_100_000,
        **more,
    )


def test_cover_over_two_years_matches_the_scenario_to_the_krona():
    result = _cover()
    assert result["annual_earnings_sek"] == 2_175_000
    assert result["facility_annual_debt_service_sek"] == 974_373
    assert result["total_annual_debt_service_sek"] == 2_074_373
    assert result["cover"] == 1.0485


def test_a_deposit_reduces_the_amount_financed():
    result = _cover(deposit_percent=15)
    assert result["deposit_sek"] == 630_000
    assert result["financed_amount_sek"] == 3_570_000
    assert result["facility_annual_debt_service_sek"] == 828_217
    assert result["cover"] == 1.128


def test_adjustments_give_cover_on_adjusted_earnings_beside_the_unadjusted_figure():
    result = _cover(
        deposit_percent=15,
        adjustments=[{"year": PRIOR["year"], "amount_sek": 600_000, "reason": "A downturn that ended."}],
    )
    assert result["cover"] == 1.128
    assert result["adjusted_annual_earnings_sek"] == 2_475_000
    assert result["adjusted_cover"] == 1.2836


def test_the_calculator_uses_the_terms_it_is_given_not_the_sample_applications():
    smaller = Terms(amount_sek=3_000_000, term_months=60, nominal_annual_rate_percent=6.0)
    result = calculate_cover(
        terms=smaller, earnings_by_year=[RECENT, PRIOR], existing_annual_debt_service_sek=1_100_000
    )
    assert result["amount_requested_sek"] == 3_000_000
    assert result["facility_annual_debt_service_sek"] < 974_373


def test_the_calculator_never_gives_a_verdict():
    result = _cover(earnings_by_year=[RECENT])
    assert not {"verdict", "meets_minimum", "minimum_dscr", "recommendation", "finding"} & set(result)


def test_an_adjustment_to_a_year_that_was_not_given_is_refused():
    with pytest.raises(CalculatorError, match="not one of the years given"):
        _cover(earnings_by_year=[RECENT], adjustments=[{"year": "2019", "amount_sek": 1, "reason": "x"}])


def test_no_earnings_is_refused():
    with pytest.raises(CalculatorError, match="at least one year"):
        _cover(earnings_by_year=[])


def test_a_negative_existing_debt_service_is_refused():
    """Debt service is a payment, so a negative one is a misreading of the
    documents. Calculated anyway, it would give a negative cover (-0.4968)
    instead of a refusal."""
    with pytest.raises(CalculatorError, match="cannot be negative"):
        calculate_cover(terms=TERMS, earnings_by_year=[RECENT, PRIOR], existing_annual_debt_service_sek=-3_000_000)


def test_no_debt_service_at_all_is_refused_rather_than_divided_by():
    """Cover is earnings over total debt service, and a total of zero would
    raise `ZeroDivisionError`, which would stop the case instead of going
    back to the model."""
    nothing = Terms(amount_sek=0, term_months=60, nominal_annual_rate_percent=6.0)
    with pytest.raises(CalculatorError, match="total annual debt service is zero"):
        calculate_cover(terms=nothing, earnings_by_year=[RECENT], existing_annual_debt_service_sek=0)
