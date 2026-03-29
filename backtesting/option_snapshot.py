from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from utils.db_func import (
    build_nifty_strategy_required_contracts,
    summarize_option_snapshot_coverage,
)


def _extract_ce_strikes(options_data: List[Dict[str, Any]]) -> List[int]:
    ce_strikes: List[int] = []
    for row in options_data:
        if (row.get("option_type") or "").upper() != "CE":
            continue
        strike_price = row.get("strike_price")
        if strike_price is None:
            continue
        try:
            ce_strikes.append(int(float(strike_price)))
        except Exception:
            continue
    return ce_strikes


def summarize_nifty_strategy_snapshot(
    position_type: Literal["A", "B"],
    strikes: Any,
    options_data: List[Dict[str, Any]],
) -> Dict[str, Any]:
    required_contracts = build_nifty_strategy_required_contracts(
        strikes.spot,
        available_ce_strikes=_extract_ce_strikes(options_data),
        position_type=position_type,
    )
    return summarize_option_snapshot_coverage(
        options_data,
        required_contracts=required_contracts,
    )


def format_snapshot_gap_message(position_type: Literal["A", "B"], coverage: Dict[str, Any]) -> str:
    missing_desc = ", ".join(
        f"{item['option_type']} {item['strike_price']}"
        for item in coverage.get("missing_required_contracts", [])
    ) or "unknown"
    return (
        "Option snapshot incomplete for strategy plan "
        f"{position_type}: missing {missing_desc} "
        f"(contracts={coverage.get('contract_count')}, "
        f"range={coverage.get('min_available_strike')}-{coverage.get('max_available_strike')})"
    )


def validate_nifty_strategy_snapshot(
    position_type: Literal["A", "B"],
    strikes: Any,
    options_data: List[Dict[str, Any]],
) -> Optional[str]:
    coverage = summarize_nifty_strategy_snapshot(position_type, strikes, options_data)
    if coverage.get("complete", False):
        return None
    return format_snapshot_gap_message(position_type, coverage)
