"""
BacktestTradeLogger - Rich text debug logger for backtesting.

Captures every decision point during backtesting with full calculations
for human-readable debugging and bug analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, TextIO

try:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")
except ImportError:
    IST = None


# ---------------------------------------------------------------------------
# Data Classes for Structured Event Capture
# ---------------------------------------------------------------------------

@dataclass
class GateCheckResult:
    """Result of a single gate check during entry evaluation."""
    gate_name: str
    passed: bool
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EntryContext:
    """Context for a trade entry event."""
    timestamp: datetime
    section: str
    subcategory: Optional[str]
    position_type: str
    gate_checks: List[GateCheckResult]
    spot_price: float
    legs: List[Dict[str, Any]]
    lot1_sl: float
    lot2_sl: float


@dataclass
class ExitCheckContext:
    """Context for an exit check event."""
    timestamp: datetime
    lot_id: str
    timeframe: str
    candle_close: float
    sl_level: float
    will_exit: bool
    min_hold_ok: bool


@dataclass
class ExitContext:
    """Context for a trade exit event."""
    timestamp: datetime
    lot_id: str
    exit_reason: str
    sl_level: float
    candle_close: float
    pnl: float
    entry_time: Optional[datetime] = None
    legs_pnl: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SLTrailContext:
    """Context for a stop loss trail event."""
    timestamp: datetime
    lot_id: str
    timeframe: str
    old_sl: float
    new_sl: float
    sh_value: Optional[float]
    close_price: float


# ---------------------------------------------------------------------------
# Subcategory Descriptions
# ---------------------------------------------------------------------------

SUBCATEGORY_DESC = {
    "a": "1m SMA20 Touch",
    "b": "1m SMA50 Touch",
    "c": "1m SMA200 Touch",
    "d": "5m SMA20 Touch",
    "e": "5m Donchian Mid Touch",
    "f": "5m SMA50 Touch",
    "g": "5m SMA200 Touch",
    "h": "15m SMA20 Touch",
    "i": "15m Donchian Mid Touch",
    "j": "15m SMA50 Touch",
    "k": "15m SMA200 Touch",
    "l": "1h SMA20 Touch",
    "m": "1h Donchian Mid Touch",
    "n": "1h SMA50 Touch",
    "o": "1h SMA200 Touch",
}

SECTION_DESC = {
    "section1": "SMA Touch",
    "section2": "RSI Filter",
    "section3": "Break & Retest",
}


# ---------------------------------------------------------------------------
# BacktestTradeLogger Class
# ---------------------------------------------------------------------------

class BacktestTradeLogger:
    """
    Rich text logger for backtest debugging.

    Outputs human-readable log files with detailed information about
    every trade decision point, including gate checks, SL trails, and exits.
    """

    HEADER_LINE = "=" * 80
    SUB_HEADER_LINE = "-" * 40

    def __init__(self, output_path: str, enabled: bool = True) -> None:
        """
        Initialize the logger.

        Args:
            output_path: Path to the output log file
            enabled: Whether logging is enabled
        """
        self.output_path = output_path
        self.enabled = enabled
        self._file: Optional[TextIO] = None

        if enabled:
            self._open_file()

    def _open_file(self) -> None:
        """Open the log file for writing."""
        try:
            self._file = open(self.output_path, "w", encoding="utf-8")
            self._write_header()
        except Exception as e:
            print(f"Warning: Could not open debug log file '{self.output_path}': {e}")
            self.enabled = False

    def _write_header(self) -> None:
        """Write the log file header."""
        if not self._file:
            return
        lines = [
            self.HEADER_LINE,
            "BACKTEST TRADE DEBUG LOG",
            self.HEADER_LINE,
            f"Generated: {self._format_timestamp_ist(datetime.now())}",
            "",
            "This log captures every decision point during backtesting:",
            "  - Trade entries with all gate checks and calculations",
            "  - Stop loss trail events",
            "  - Exit checks and triggers",
            "  - P&L breakdowns",
            "",
            self.HEADER_LINE,
            "",
        ]
        self._write_lines(lines)

    def _write_lines(self, lines: List[str]) -> None:
        """Write lines to the log file."""
        if not self._file:
            return
        try:
            self._file.write("\n".join(lines) + "\n")
            self._file.flush()
        except Exception:
            pass

    def _format_timestamp_ist(self, dt: datetime) -> str:
        """Format datetime to IST timezone string."""
        if dt is None:
            return "N/A"

        # Convert to IST if pytz is available
        if IST is not None:
            try:
                if dt.tzinfo is None:
                    # Assume naive datetime is already in IST
                    dt = IST.localize(dt)
                else:
                    dt = dt.astimezone(IST)
            except Exception:
                pass

        return dt.strftime("%Y-%m-%d %H:%M:%S IST")

    def _check_mark(self, passed: bool) -> str:
        """Return pass/fail marker."""
        return "[PASS]" if passed else "[FAIL]"

    def _format_float(self, value: Optional[float], decimals: int = 2) -> str:
        """Format float value or return N/A."""
        if value is None:
            return "N/A"
        return f"{float(value):.{decimals}f}"

    # -----------------------------------------------------------------------
    # Public Logging Methods
    # -----------------------------------------------------------------------

    def log_entry_evaluation_blocked(
        self,
        timestamp: datetime,
        gate_checks: List[GateCheckResult],
        reason: str,
    ) -> None:
        """Log when entry evaluation is blocked by a gate check."""
        if not self.enabled:
            return

        lines = [
            self.SUB_HEADER_LINE,
            f"[{self._format_timestamp_ist(timestamp)}] ENTRY EVALUATION - BLOCKED",
            self.SUB_HEADER_LINE,
            f"  Reason: {reason}",
            "",
            "  Gate Checks:",
        ]

        for gate in gate_checks:
            mark = self._check_mark(gate.passed)
            details_str = self._format_gate_details(gate)
            lines.append(f"    {mark} {gate.gate_name}: {details_str}")

        lines.extend([self.SUB_HEADER_LINE, ""])
        self._write_lines(lines)

    def log_entry_triggered(self, ctx: EntryContext) -> None:
        """Log a trade entry event with full context."""
        if not self.enabled:
            return

        section_desc = SECTION_DESC.get(ctx.section, ctx.section)

        lines = [
            self.HEADER_LINE,
            f"[{self._format_timestamp_ist(ctx.timestamp)}] TRADE ENTRY TRIGGERED",
            self.HEADER_LINE,
            "",
            "ENTRY CONTEXT:",
            f"  Section: {ctx.section} ({section_desc})",
        ]

        if ctx.subcategory:
            subcat_desc = SUBCATEGORY_DESC.get(ctx.subcategory, ctx.subcategory)
            lines.append(f"  Sub-category: {ctx.subcategory} ({subcat_desc})")

        lines.extend(["", "GATE CHECKS:"])

        for gate in ctx.gate_checks:
            mark = self._check_mark(gate.passed)
            details_str = self._format_gate_details(gate)
            lines.append(f"  {mark} {gate.gate_name}: {details_str}")

        position_desc = "High VIX" if ctx.position_type == "A" else "Low VIX"
        lines.extend([
            "",
            "POSITION DETAILS:",
            f"  Type: {ctx.position_type} ({position_desc})",
            f"  Spot: {self._format_float(ctx.spot_price)}",
            "  Legs:",
        ])

        for leg in ctx.legs:
            action = leg.get("action") or leg.get("side") or "?"
            opt_type = leg.get("option_type", "?")
            strike = leg.get("strike_price", "?")
            price = leg.get("last_price") or leg.get("entry_price")
            price_str = self._format_float(price) if price else "N/A"
            lines.append(f"    - {action} {opt_type} {strike} @ {price_str}")

        lines.extend([
            f"  Lot1 SL: {self._format_float(ctx.lot1_sl)} (1m timeframe)",
            f"  Lot2 SL: {self._format_float(ctx.lot2_sl)} (5m timeframe)",
            self.HEADER_LINE,
            "",
        ])

        self._write_lines(lines)

    def log_sl_trail(self, ctx: SLTrailContext) -> None:
        """Log a stop loss trail event."""
        if not self.enabled:
            return

        action = "RATCHETED DOWN" if ctx.new_sl < ctx.old_sl else "UNCHANGED"
        if ctx.new_sl > ctx.old_sl:
            action = "ANOMALY (SL increased)"

        lines = [
            self.SUB_HEADER_LINE,
            f"[{self._format_timestamp_ist(ctx.timestamp)}] SL TRAIL - {ctx.lot_id}",
            self.SUB_HEADER_LINE,
            f"  Timeframe: {ctx.timeframe}",
            f"  Candle Close: {self._format_float(ctx.close_price)}",
            f"  SH Value: {self._format_float(ctx.sh_value)}",
            f"  Old SL: {self._format_float(ctx.old_sl)} -> New SL: {self._format_float(ctx.new_sl)}",
            f"  Action: {action}",
            self.SUB_HEADER_LINE,
            "",
        ]

        self._write_lines(lines)

    def log_exit_check(self, ctx: ExitCheckContext) -> None:
        """Log an exit check event."""
        if not self.enabled:
            return

        comparison = f"close ({self._format_float(ctx.candle_close)}) {'>' if ctx.will_exit else '<='} SL ({self._format_float(ctx.sl_level)})"
        result = "EXIT TRIGGERED" if ctx.will_exit else "HOLDING"
        min_hold_status = "OK" if ctx.min_hold_ok else "NOT MET (< 5 min)"

        lines = [
            self.SUB_HEADER_LINE,
            f"[{self._format_timestamp_ist(ctx.timestamp)}] EXIT CHECK - {ctx.lot_id}",
            self.SUB_HEADER_LINE,
            f"  Timeframe: {ctx.timeframe}",
            f"  Current SL: {self._format_float(ctx.sl_level)}",
            f"  Candle Close: {self._format_float(ctx.candle_close)}",
            f"  Min Hold Period (5m): {min_hold_status}",
            f"  Comparison: {comparison}",
            f"  Result: {result}",
            self.SUB_HEADER_LINE,
            "",
        ]

        self._write_lines(lines)

    def log_exit_triggered(self, ctx: ExitContext) -> None:
        """Log a trade exit event with P&L breakdown."""
        if not self.enabled:
            return

        duration_str = "N/A"
        if ctx.entry_time:
            duration = ctx.timestamp - ctx.entry_time
            minutes = int(duration.total_seconds() / 60)
            duration_str = f"{minutes} minutes"

        lines = [
            self.HEADER_LINE,
            f"[{self._format_timestamp_ist(ctx.timestamp)}] TRADE CLOSED - {ctx.lot_id}",
            self.HEADER_LINE,
            f"  Exit Reason: {ctx.exit_reason}",
            f"  Duration: {duration_str}",
            "",
        ]

        if ctx.legs_pnl:
            lines.append("  P&L Breakdown:")
            for leg in ctx.legs_pnl:
                action = leg.get("action", "?")
                opt_type = leg.get("option_type", "?")
                strike = leg.get("strike_price", "?")
                entry = self._format_float(leg.get("entry_price"))
                exit_price = self._format_float(leg.get("exit_price"))
                leg_pnl = leg.get("pnl", 0)
                sign = "+" if leg_pnl >= 0 else ""
                lines.append(f"    {action} {opt_type} {strike}: {entry} -> {exit_price} = {sign}{self._format_float(leg_pnl)}")
            lines.append("")

        pnl_sign = "+" if ctx.pnl >= 0 else ""
        lines.extend([
            f"  Total P&L: {pnl_sign}{self._format_float(ctx.pnl)}",
            self.HEADER_LINE,
            "",
        ])

        self._write_lines(lines)

    def log_daily_summary(self, date: datetime, trades_count: int, daily_pnl: float) -> None:
        """Log a daily summary at end of trading day."""
        if not self.enabled:
            return

        pnl_sign = "+" if daily_pnl >= 0 else ""
        lines = [
            "",
            f"--- END OF DAY: {date.strftime('%Y-%m-%d')} ---",
            f"    Trades: {trades_count}",
            f"    Day P&L: {pnl_sign}{self._format_float(daily_pnl)}",
            "",
        ]
        self._write_lines(lines)

    def _format_gate_details(self, gate: GateCheckResult) -> str:
        """Format gate-specific details into readable string."""
        d = gate.details

        if gate.gate_name == "Entry Cutoff":
            current = d.get("current_time", "?")
            cutoff = d.get("cutoff_time", "?")
            return f"current_time={current} {'<' if gate.passed else '>='} cutoff={cutoff}"

        if gate.gate_name == "Main Category A":
            close = self._format_float(d.get("1m_close"))
            sma20 = self._format_float(d.get("1h_sma_20"))
            sma50 = self._format_float(d.get("1h_sma_50"))
            sma200 = self._format_float(d.get("1h_sma_200"))
            op = "<" if gate.passed else ">="
            return f"1m close ({close}) {op} 1h SMA20 ({sma20}), SMA50 ({sma50}), SMA200 ({sma200})"

        if gate.gate_name == "VIX Regime":
            vix = self._format_float(d.get("vix_value"))
            vix_sma = self._format_float(d.get("vix_sma20"))
            ptype = d.get("position_type", "?")
            threshold = d.get("sl_threshold", "?")
            op = ">" if ptype == "A" else "<="
            return f"VIX ({vix}) {op} VIX_SMA20 ({vix_sma}) -> Type {ptype} (SL threshold: {threshold}pts)"

        if "1st Flag" in gate.gate_name:
            # Handle detailed format with touches array
            touches = d.get("touches")
            if touches and len(touches) > 0:
                # Format each touch: "d (1m close 22950.00 >= 5m sma_20 22920.50)"
                touch_strs = []
                for t in touches[:3]:  # Show first 3 touches to avoid too long output
                    subcat = t.get("subcat", "?")
                    interval = t.get("interval", "?")
                    indicator = t.get("indicator", "?")
                    close_1m = self._format_float(t.get("1m_close"))
                    ind_val = self._format_float(t.get("indicator_value"))
                    touch_strs.append(f"{subcat} (1m close {close_1m} >= {interval} {indicator} {ind_val})")
                
                result = " | ".join(touch_strs)
                if len(touches) > 3:
                    result += f" | +{len(touches)-3} more"
                return result
            
            # Simpler format with just subcategory list
            active_subcats = d.get("active_subcategories")
            if active_subcats:
                subcats_str = ", ".join(sorted(active_subcats)) if active_subcats else "none"
                return f"Active subcategories: [{subcats_str}]"
            
            # Legacy format
            close_1m = self._format_float(d.get("1m_close"))
            indicator = d.get("indicator", "?")
            ind_val = self._format_float(d.get("indicator_value"))
            touched = d.get("touched", False)
            return f"1m close ({close_1m}) {'touched' if touched else 'did not touch'} {indicator} ({ind_val})"

        if gate.gate_name == "2nd Flag":
            close = self._format_float(d.get("1m_close"))
            sma20 = self._format_float(d.get("sma_20"))
            sma5_low = self._format_float(d.get("sma_5_low"))
            return f"1m close ({close}) < SMA20 ({sma20}) AND < SMA5_Low ({sma5_low})"

        if gate.gate_name == "SL Filter":
            hh = self._format_float(d.get("highest_high"))
            close = self._format_float(d.get("close"))
            sl_pts = self._format_float(d.get("sl_pts"))
            threshold = self._format_float(d.get("threshold"))
            lookback = d.get("lookback", "?")
            op = "<=" if gate.passed else ">"
            return f"SL_pts ({sl_pts}) = HH({hh}) - close({close}) [{lookback} candles] {op} threshold({threshold})"

        if gate.gate_name == "RSI Filter":
            rsi = self._format_float(d.get("rsi_value"))
            threshold = self._format_float(d.get("threshold"))
            return f"5m RSI ({rsi}) {'>' if gate.passed else '<='} {threshold}"

        if gate.gate_name == "Section 3 Break Latch":
            latched = d.get("break_latched", False)
            reason = d.get("break_reason", "N/A")
            close_1m = self._format_float(d.get("1m_close"))
            sma20 = self._format_float(d.get("5m_sma_20"))
            donch = self._format_float(d.get("5m_donchian_mid"))
            return f"break_latched={latched}, reason={reason}, 1m close ({close_1m}) vs 5m SMA20 ({sma20}) / Donchian ({donch})"

        if gate.gate_name == "Section 3 Retest":
            close_1m = self._format_float(d.get("1m_close"))
            sma20 = self._format_float(d.get("5m_sma_20"))
            donch = self._format_float(d.get("5m_donchian_mid"))
            return f"1m close ({close_1m}) reached 5m SMA20 ({sma20}) or Donchian ({donch})"

        # Default: just stringify the details
        return str(d) if d else ""

    def close(self) -> None:
        """Close the log file."""
        if self._file:
            try:
                self._write_lines([
                    "",
                    self.HEADER_LINE,
                    "END OF BACKTEST DEBUG LOG",
                    self.HEADER_LINE,
                ])
                self._file.close()
            except Exception:
                pass
            self._file = None


# ---------------------------------------------------------------------------
# Null Logger (no-op implementation)
# ---------------------------------------------------------------------------

class NullTradeLogger:
    """No-op logger for when logging is disabled."""

    def log_entry_evaluation_blocked(self, *args, **kwargs) -> None:
        pass

    def log_entry_triggered(self, *args, **kwargs) -> None:
        pass

    def log_sl_trail(self, *args, **kwargs) -> None:
        pass

    def log_exit_check(self, *args, **kwargs) -> None:
        pass

    def log_exit_triggered(self, *args, **kwargs) -> None:
        pass

    def log_daily_summary(self, *args, **kwargs) -> None:
        pass

    def close(self) -> None:
        pass
