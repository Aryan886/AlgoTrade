"""
Command-line interface for backtesting.

Usage:
    python -m backtesting.cli --start 2025-11-03 --end 2026-03-15 --output results.html
    # Run backtest with debug logging enabled
    python -m backtesting.cli --start 2025-11-03 --end 2025-11-05 --debug-log logs/name of file.log -v
    python -m backtesting.cli --start YYYY-MM-DD --end YYYY-MM-DD --output report.html
    
"""

from __future__ import annotations 

import argparse
import sys
from datetime import datetime, time as dtime
from pathlib import Path

import pandas as pd

from backtesting.runner import BacktestRunner, BacktestConfig


def parse_date(date_str: str) -> datetime:
    """Parse date string to datetime."""
    try:
        # Try full datetime format first
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            # Try date-only format
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            # Default to market open time
            return datetime.combine(dt.date(), dtime(9, 15))
        except ValueError:
            raise ValueError(f"Invalid date format: {date_str}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")


def build_summary_dataframe(result) -> pd.DataFrame:
    """Build a summary table suitable for Excel/HTML export."""
    return pd.DataFrame(
        [
            {"metric": "Total P&L", "value": result.total_pnl},
            {"metric": "Number of Trades", "value": result.num_trades},
            {"metric": "Win Rate", "value": result.win_rate},
            {"metric": "Avg Win", "value": result.avg_win},
            {"metric": "Avg Loss", "value": result.avg_loss},
            {"metric": "Profit Factor", "value": result.profit_factor},
            {"metric": "Max Drawdown", "value": result.max_drawdown},
            {"metric": "Max Drawdown %", "value": result.max_drawdown_pct},
            {"metric": "Daily Sharpe", "value": getattr(result, "daily_sharpe_ratio", result.sharpe_ratio)},
            {"metric": "Expectancy", "value": result.expectancy},
            {"metric": "Skipped Entries", "value": getattr(result, "skipped_entries", 0)},
        ]
    )


def build_summary_metrics(result) -> list[dict[str, object]]:
    """Return summary rows in a frontend-friendly shape."""
    summary_df = build_summary_dataframe(result)
    rows: list[dict[str, object]] = []
    for row in summary_df.itertuples(index=False):
        rows.append(
            {
                "metric": row.metric,
                "value": row.value,
                "formattedValue": _format_metric(row.metric, row.value),
            }
        )
    return rows


def _next_available_output_path(output: Path) -> Path:
    """Return a non-destructive output path by appending a numeric suffix when needed."""
    candidate = output
    counter = 1

    while candidate.exists():
        candidate = output.with_name(f"{output.stem}_{counter}{output.suffix}")
        counter += 1

    return candidate


def export_results(output_path: str, runner: BacktestRunner, result) -> Path:
    """Export backtest results in a readable format based on file extension."""
    output = Path(output_path)
    trades_df = runner.trade_log.to_dataframe()
    summary_df = build_summary_dataframe(result)
    suffix = output.suffix.lower()

    if suffix == ".csv":
        output = _next_available_output_path(output)
        trades_df.to_csv(output, index=False)
        return output

    if suffix == ".xlsx":
        output = _next_available_output_path(output)
        try:
            with pd.ExcelWriter(output) as writer:
                summary_df.to_excel(writer, sheet_name="Summary", index=False)
                trades_df.to_excel(writer, sheet_name="Trades", index=False)
            return output
        except ImportError:
            fallback = _next_available_output_path(output.with_suffix(".html"))
            write_html_report(fallback, summary_df, trades_df, result)
            return fallback

    if suffix in {"", ".html", ".htm"}:
        if suffix == "":
            output = output.with_suffix(".html")
        output = _next_available_output_path(output)
        write_html_report(output, summary_df, trades_df, result)
        return output

    raise ValueError("Unsupported output format. Use .html, .xlsx, or .csv")


def _format_metric(metric: str, value) -> str:
    if pd.isna(value):
        return "-"
    if metric in {"Win Rate", "Max Drawdown %"}:
        return f"{float(value):.2%}"
    if metric in {"Number of Trades", "Skipped Entries"}:
        return f"{int(value)}"
    if metric in {"Profit Factor"}:
        return "inf" if value == float("inf") else f"{float(value):.2f}"
    return f"{float(value):,.2f}"


def _build_kpi_cards(result) -> str:
    cards = [
        ("Total P&L", f"{result.total_pnl:,.2f}", "positive" if result.total_pnl >= 0 else "negative"),
        ("Trades", f"{result.num_trades}", "neutral"),
        ("Win Rate", f"{result.win_rate:.2%}", "neutral"),
        ("Max Drawdown", f"{result.max_drawdown:,.2f}", "negative" if result.max_drawdown > 0 else "neutral"),
        ("Daily Sharpe", f"{getattr(result, 'daily_sharpe_ratio', result.sharpe_ratio):.2f}", "positive" if getattr(result, 'daily_sharpe_ratio', result.sharpe_ratio) > 0 else "neutral"),
        ("Expectancy", f"{result.expectancy:,.2f}", "positive" if result.expectancy >= 0 else "negative"),
    ]
    return "\n".join(
        f'<div class="kpi-card {tone}"><div class="kpi-label">{label}</div><div class="kpi-value">{value}</div></div>'
        for label, value, tone in cards
    )


def _build_equity_curve_svg(result) -> str:
    curve = list(getattr(result, "equity_curve", []) or [])
    if len(curve) < 2:
        return '<div class="empty-state">Not enough equity points to draw a curve yet.</div>'

    values = [float(eq) for _, eq in curve]
    width = 960
    height = 260
    padding = 24
    min_v = min(values)
    max_v = max(values)
    span = max(max_v - min_v, 1.0)

    points = []
    for idx, value in enumerate(values):
        x = padding + (idx / (len(values) - 1)) * (width - 2 * padding)
        y = height - padding - ((value - min_v) / span) * (height - 2 * padding)
        points.append(f"{x:.2f},{y:.2f}")

    start_value = values[0]
    end_value = values[-1]
    trend_class = "positive" if end_value >= start_value else "negative"

    return f"""
    <div class="equity-wrap">
      <div class="equity-meta">
        <span>Start Equity: {start_value:,.2f}</span>
        <span>End Equity: {end_value:,.2f}</span>
        <span>Points: {len(values)}</span>
      </div>
      <svg viewBox="0 0 {width} {height}" class="equity-chart" role="img" aria-label="Equity curve chart">
        <polyline class="equity-line {trend_class}" fill="none" points="{' '.join(points)}"></polyline>
      </svg>
    </div>
    """


def _build_trades_table(trades_df: pd.DataFrame) -> str:
    if trades_df.empty:
        return '<div class="empty-state">No trades were recorded for this run.</div>'

    display_df = trades_df.copy()

    # Define which columns to show and their order
    display_columns = [
        "trade_id", "entry_signal_time", "entry_fill_time", "exit_signal_time", "exit_fill_time",
        "lot_id", "position_type",
        "entry_price", "exit_price", "pnl", "section", "subcat",
        "sl_initial", "sl_final", "sl_adjustments", "exit_reason"
    ]
    # Filter to only columns that exist
    display_columns = [c for c in display_columns if c in display_df.columns]

    if "pnl" in display_df.columns:
        display_df["row_class"] = display_df["pnl"].apply(
            lambda value: "trade-win" if pd.notna(value) and float(value) > 0 else (
                "trade-loss" if pd.notna(value) and float(value) < 0 else "trade-flat"
            )
        )
        display_df["pnl"] = display_df["pnl"].apply(lambda v: "-" if pd.isna(v) else f"{float(v):,.2f}")
    else:
        display_df["row_class"] = "trade-flat"

    for col in ("entry_price", "exit_price", "sl_initial", "sl_final"):
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(lambda v: "-" if pd.isna(v) or v == 0 else f"{float(v):,.2f}")

    if "sl_adjustments" in display_df.columns:
        display_df["sl_adjustments"] = display_df["sl_adjustments"].apply(lambda v: str(int(v)) if pd.notna(v) else "-")

    if "exit_reason" in display_df.columns:
        display_df["exit_reason"] = display_df["exit_reason"].apply(lambda v: v if v else "-")

    headers = "".join(f"<th>{col.replace('_', ' ').title()}</th>" for col in display_columns)
    body_rows = []
    for _, row in display_df.iterrows():
        cells = "".join(f"<td>{row[col]}</td>" for col in display_columns)
        body_rows.append(f'<tr class="{row["row_class"]}">{cells}</tr>')

    return f"""
    <table class="trade-table">
      <thead><tr>{headers}</tr></thead>
      <tbody>
        {''.join(body_rows)}
      </tbody>
    </table>
    """


def _build_data_quality_html(result) -> str:
    warnings = list(getattr(result, "data_quality_warnings", []) or [])
    skipped_entries = int(getattr(result, "skipped_entries", 0) or 0)

    if not warnings and skipped_entries == 0:
        return '<div class="empty-state">No strict backtest data-quality issues were recorded.</div>'

    items = "".join(f"<li>{warning}</li>" for warning in warnings[:20])
    extra = ""
    if len(warnings) > 20:
        extra = f"<li>...and {len(warnings) - 20} more warning(s).</li>"

    return f"""
    <div class="sl-lot-breakdown">Skipped Entries: {skipped_entries}</div>
    <ul class="warning-list">
      {items}
      {extra}
    </ul>
    """


def _compute_sl_metrics(trades) -> dict:
    """Compute stop loss analytics from trades."""
    closed = [t for t in trades if t.pnl is not None]
    if not closed:
        return {}

    sl_hit_trades = [t for t in closed if t.exit_reason == "SL_HIT"]
    time_exit_trades = [t for t in closed if t.exit_reason == "TIME_EXIT"]

    # Compute averages
    trades_with_adjustments = [t for t in closed if t.sl_adjustment_count > 0]
    total_adjustments = sum(t.sl_adjustment_count for t in closed)
    avg_adjustments = total_adjustments / len(closed) if closed else 0

    movements = [t.sl_movement_pct for t in closed if t.sl_initial > 0]
    avg_movement = sum(movements) / len(movements) if movements else 0

    # SL hits by lot
    sl_by_lot = {}
    for t in sl_hit_trades:
        sl_by_lot[t.lot_id] = sl_by_lot.get(t.lot_id, 0) + 1

    return {
        "total_adjustments": total_adjustments,
        "avg_adjustments_per_trade": avg_adjustments,
        "avg_sl_movement_pct": avg_movement,
        "trades_hit_sl": len(sl_hit_trades),
        "trades_time_exit": len(time_exit_trades),
        "total_trades": len(closed),
        "sl_by_lot": sl_by_lot,
    }


def build_report_payload(result, trades_df: pd.DataFrame | None = None) -> dict[str, object]:
    """Return the structured sections behind the HTML showcase."""
    trades_df = trades_df if trades_df is not None else pd.DataFrame()
    sl_metrics = _compute_sl_metrics(getattr(result, "trades", []) or [])
    return {
        "summary": build_summary_metrics(result),
        "kpis": {
            "totalPnl": float(getattr(result, "total_pnl", 0.0) or 0.0),
            "trades": int(getattr(result, "num_trades", 0) or 0),
            "winRate": float(getattr(result, "win_rate", 0.0) or 0.0),
            "maxDrawdown": float(getattr(result, "max_drawdown", 0.0) or 0.0),
            "dailySharpe": float(getattr(result, "daily_sharpe_ratio", getattr(result, "sharpe_ratio", 0.0)) or 0.0),
            "expectancy": float(getattr(result, "expectancy", 0.0) or 0.0),
        },
        "equityCurve": [
            {
                "timestamp": ts.isoformat(sep=" ") if hasattr(ts, "isoformat") else str(ts),
                "equity": float(value),
            }
            for ts, value in list(getattr(result, "equity_curve", []) or [])
        ],
        "slAnalysis": {
            "totalAdjustments": int(sl_metrics.get("total_adjustments", 0) or 0),
            "avgAdjustmentsPerTrade": float(sl_metrics.get("avg_adjustments_per_trade", 0.0) or 0.0),
            "avgSlMovementPct": float(sl_metrics.get("avg_sl_movement_pct", 0.0) or 0.0),
            "tradesHitSl": int(sl_metrics.get("trades_hit_sl", 0) or 0),
            "tradesTimeExit": int(sl_metrics.get("trades_time_exit", 0) or 0),
            "totalTrades": int(sl_metrics.get("total_trades", 0) or 0),
            "slByLot": [
                {"lotId": str(lot_id), "count": int(count)}
                for lot_id, count in sorted((sl_metrics.get("sl_by_lot", {}) or {}).items())
            ],
        },
        "dataQuality": {
            "skippedEntries": int(getattr(result, "skipped_entries", 0) or 0),
            "warnings": list(getattr(result, "data_quality_warnings", []) or []),
            "healthy": not bool(getattr(result, "data_quality_warnings", []) or []) and int(getattr(result, "skipped_entries", 0) or 0) == 0,
        },
        "tradeCount": int(len(trades_df.index)) if trades_df is not None else 0,
    }


def _build_sl_analysis_html(trades) -> str:
    """Build HTML section for SL analysis."""
    metrics = _compute_sl_metrics(trades)
    if not metrics:
        return '<div class="empty-state">No SL data available - no closed trades.</div>'

    # Build lot breakdown
    lot_breakdown = ""
    if metrics["sl_by_lot"]:
        lot_items = " | ".join(f"{lot}: {count}" for lot, count in sorted(metrics["sl_by_lot"].items()))
        lot_breakdown = f'<div class="sl-lot-breakdown">SL Exits by Lot: {lot_items}</div>'

    return f"""
    <div class="sl-kpi-grid">
      <div class="kpi-card neutral">
        <div class="kpi-label">Total SL Adjustments</div>
        <div class="kpi-value">{metrics['total_adjustments']}</div>
      </div>
      <div class="kpi-card neutral">
        <div class="kpi-label">Avg Per Trade</div>
        <div class="kpi-value">{metrics['avg_adjustments_per_trade']:.1f}</div>
      </div>
      <div class="kpi-card neutral">
        <div class="kpi-label">Avg SL Movement</div>
        <div class="kpi-value">{metrics['avg_sl_movement_pct']:.2f}%</div>
      </div>
      <div class="kpi-card {'negative' if metrics['trades_hit_sl'] > metrics['trades_time_exit'] else 'positive'}">
        <div class="kpi-label">SL Hit / Time Exit</div>
        <div class="kpi-value">{metrics['trades_hit_sl']} / {metrics['trades_time_exit']}</div>
      </div>
    </div>
    {lot_breakdown}
    """


def write_html_report(output: Path, summary_df: pd.DataFrame, trades_df: pd.DataFrame, result) -> None:
    """Write a polished HTML report containing summary, equity curve, and trade log."""
    formatted_summary = summary_df.copy()
    formatted_summary["formatted_value"] = [
        _format_metric(row.metric, row.value) for row in formatted_summary.itertuples(index=False)
    ]
    summary_rows = "".join(
        f"<tr><td>{row.metric}</td><td>{row.formatted_value}</td></tr>"
        for row in formatted_summary.itertuples(index=False)
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Backtest Report</title>
  <style>
    :root {{
      --bg: #f3f6ef;
      --panel: #ffffff;
      --ink: #17301f;
      --muted: #5f6f65;
      --line: #d8e0d4;
      --accent: #2f6b3b;
      --accent-soft: #dcedd7;
      --danger: #b53a2f;
      --danger-soft: #fde3df;
      --shadow: 0 12px 30px rgba(24, 45, 27, 0.08);
    }}
    body {{
      font-family: Segoe UI, Arial, sans-serif;
      margin: 0;
      padding: 28px;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, #e8f3dd 0%, rgba(232, 243, 221, 0) 32%),
        linear-gradient(180deg, #f7faf5 0%, var(--bg) 100%);
    }}
    h1, h2 {{
      margin-bottom: 12px;
    }}
    h1 {{
      font-size: 34px;
      margin-top: 0;
      margin-bottom: 8px;
    }}
    p {{
      color: var(--muted);
      margin-top: 0;
    }}
    .hero {{
      margin-bottom: 22px;
    }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 14px;
      margin-bottom: 20px;
    }}
    .kpi-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px 18px;
      box-shadow: var(--shadow);
    }}
    .kpi-card.positive {{
      background: linear-gradient(180deg, #ffffff 0%, #f2fbef 100%);
    }}
    .kpi-card.negative {{
      background: linear-gradient(180deg, #ffffff 0%, #fff2ef 100%);
    }}
    .kpi-label {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 8px;
    }}
    .kpi-value {{
      font-size: 28px;
      font-weight: 700;
    }}
    .layout {{
      display: grid;
      grid-template-columns: 1.1fr 1.9fr;
      gap: 20px;
      margin-bottom: 20px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 20px;
      box-shadow: var(--shadow);
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
      font-size: 14px;
      background: var(--panel);
    }}
    th, td {{
      border: 1px solid var(--line);
      padding: 10px 12px;
      text-align: left;
    }}
    th {{
      background: #eef3eb;
    }}
    tr:nth-child(even) td {{
      background: #f9fbf8;
    }}
    .trade-table tbody tr.trade-win td {{
      background: var(--accent-soft);
    }}
    .trade-table tbody tr.trade-loss td {{
      background: var(--danger-soft);
    }}
    .trade-table tbody tr.trade-flat td {{
      background: #fbfcfb;
    }}
    .table-wrap {{
      overflow-x: auto;
    }}
    .equity-wrap {{
      display: flex;
      flex-direction: column;
      gap: 12px;
    }}
    .equity-meta {{
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 13px;
    }}
    .equity-chart {{
      width: 100%;
      height: auto;
      background: linear-gradient(180deg, #fbfdf9 0%, #f1f7ec 100%);
      border: 1px solid var(--line);
      border-radius: 14px;
    }}
    .equity-line {{
      stroke-width: 4;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}
    .equity-line.positive {{
      stroke: var(--accent);
    }}
    .equity-line.negative {{
      stroke: var(--danger);
    }}
    .empty-state {{
      padding: 18px;
      border: 1px dashed var(--line);
      border-radius: 14px;
      color: var(--muted);
      background: #fbfcfa;
    }}
    .sl-kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    .sl-lot-breakdown {{
      font-size: 13px;
      color: var(--muted);
      padding: 10px 14px;
      background: #f8faf6;
      border-radius: 10px;
      border: 1px solid var(--line);
    }}
    .warning-list {{
      margin: 14px 0 0;
      padding-left: 20px;
      color: var(--ink);
    }}
    .warning-list li {{
      margin-bottom: 8px;
    }}
    @media (max-width: 900px) {{
      body {{
        padding: 16px;
      }}
      .layout {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <div class="hero">
    <h1>Backtest Report</h1>
    <p>A cleaner view of strategy performance, trade quality, and equity movement.</p>
  </div>
  <div class="kpi-grid">
    {_build_kpi_cards(result)}
  </div>
  <div class="layout">
    <div class="card">
      <h2>Summary</h2>
      <table>
        <thead><tr><th>Metric</th><th>Value</th></tr></thead>
        <tbody>{summary_rows}</tbody>
      </table>
    </div>
    <div class="card">
      <h2>Equity Curve</h2>
      {_build_equity_curve_svg(result)}
    </div>
  </div>
  <div class="card">
    <h2>Stop Loss Analysis</h2>
    {_build_sl_analysis_html(result.trades)}
  </div>
  <div class="card">
    <h2>Data Quality</h2>
    {_build_data_quality_html(result)}
  </div>
  <div class="card">
    <h2>Trade Log</h2>
    <div class="table-wrap">
      {_build_trades_table(trades_df)}
    </div>
  </div>
</body>
</html>"""
    output.write_text(html, encoding="utf-8")


def main() -> int:
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="Backtest NIFTY options strategy on historical data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run backtest for November 2025 to March 2026
    python -m backtesting.cli --start 2025-11-03 --end 2026-03-15

    # Run with verbose output and save an HTML report
    python -m backtesting.cli --start 2025-11-03 --end 2026-03-15 -v --output results.html

    # Save to Excel when an XLSX engine is installed
    python -m backtesting.cli --start 2025-11-03 --end 2026-03-15 --output results.xlsx

    # Use custom database path
    python -m backtesting.cli --start 2025-11-03 --end 2026-03-15 --db db/trading_bot.db
        """,
    )

    parser.add_argument(
        "--start",
        type=str,
        required=True,
        help="Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)",
    )
    parser.add_argument(
        "--end",
        type=str,
        required=True,
        help="End date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="db/trading_bot.db",
        help="Path to SQLite database (default: db/trading_bot.db)",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="NIFTY50",
        help="Trading symbol (default: NIFTY50)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output report file (.html, .xlsx, or .csv). HTML is the most readable default.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed progress",
    )
    parser.add_argument(
        "--debug-log",
        type=str,
        default=None,
        help="Path for rich text debug log file (e.g., logs/backtest_debug.log)",
    )

    args = parser.parse_args()

    # Parse dates
    try:
        start_date = parse_date(args.start)
        end_date = parse_date(args.end)
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    # Validate date range
    if end_date < start_date:
        print("Error: End date must be after or equal to start date")
        return 1

    # Create config
    config = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        db_path=args.db,
        symbol=args.symbol,
        verbose=args.verbose,
        debug_log_path=args.debug_log,
    )

    # Run backtest
    print("=" * 60)
    print("NIFTY Options Strategy Backtest")
    print("=" * 60)
    print(f"Period: {start_date.date()} to {end_date.date()}")
    print(f"Symbol: {args.symbol}")
    print(f"Database: {args.db}")
    print("=" * 60)
    print()

    try:
        runner = BacktestRunner(config)
        result = runner.run()
    except Exception as e:
        print(f"Error during backtest: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    # Print results
    print()
    print(result.summary())

    # Export report if requested
    if args.output:
        try:
            saved_path = export_results(args.output, runner, result)
            if Path(args.output).suffix.lower() == ".xlsx" and saved_path.suffix.lower() == ".html":
                print(f"Excel writer not available; saved HTML report to: {saved_path}")
            else:
                print(f"Backtest report saved to: {saved_path}")
        except Exception as e:
            print(f"Warning: Could not save report: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
