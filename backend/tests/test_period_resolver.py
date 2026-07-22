from datetime import UTC, datetime

from app.models import FiscalCalendar
from app.period_resolver import canonical_fiscal_label, canonical_period, resolve_period


AS_OF = datetime(2026, 7, 20, tzinfo=UTC)


def test_explicit_period_variants_are_canonicalized() -> None:
    assert canonical_period("FY2026 Q1 revenue") == "2026Q1"
    assert canonical_period("Q2 2025 earnings") == "2025Q2"
    assert canonical_period("微軟 2025 Q3 法說會內容") == "2025Q3"
    assert canonical_period("2024 年第三季") == "2024Q3"
    assert canonical_fiscal_label("Microsoft FY2026 Q3 call") == "FY2026 Q3"
    assert canonical_fiscal_label("Microsoft 2026 Q1 call") is None


def test_relative_periods_use_verified_availability_not_calendar_guess() -> None:
    available = ["2025Q4", "2026Q1", "2026Q2"]
    calendar = FiscalCalendar(co_code="MSFT", fiscal_year_end_month=6)

    latest = resolve_period("Microsoft 最近一季營收", available, calendar, AS_OF)
    previous = resolve_period("Microsoft 上一季營收", available, calendar, AS_OF)
    year_ago = resolve_period("Microsoft 去年同期營收", ["2025Q2", *available], calendar, AS_OF)

    assert latest.resolved_period == "2026Q2"
    assert latest.method == "latest_verified_available"
    assert latest.fiscal_calendar == calendar
    assert previous.resolved_period == "2026Q1"
    assert year_ago.resolved_period == "2025Q2"


def test_unavailable_relative_period_does_not_fall_back_to_unscoped_search() -> None:
    result = resolve_period("上一季營收", ["2026Q2"], as_of=AS_OF)
    assert result.resolved_period is None
    assert result.confidence == 0.0
    assert result.method == "previous_period_unavailable"


def test_latest_earnings_call_phrase_uses_latest_available_period() -> None:
    result = resolve_period(
        "微軟最近的法說會對話內容",
        ["2025Q4", "2026Q1"],
        as_of=AS_OF,
    )

    assert result.resolved_period == "2026Q1"
    assert result.method == "latest_verified_available"
