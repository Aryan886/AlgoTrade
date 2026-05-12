from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from utils.db_func import DB_PATH, fetch_market_data, fetch_vix_data

from .dashboard_models import (
    EngineStatusCard,
    MarketOverview,
    MarketPoint,
    OpenPositionSummary,
    PnlRow,
    PnlSummary,
    StrategyCard,
    StrategyId,
    TradeRow,
)


STRATEGY_CATALOG: dict[str, dict[str, str]] = {
    "nifty-options": {
        "display_name": "Nifty Options Strategy",
        "description": "Live market NIFTY options setup with real synchronous backtests.",
        "strategy_type": "Live-ready",
        "mode": "backtest-enabled",
    },
    "donchian-options": {
        "display_name": "Donchian Options Strategy",
        "description": "Demo-controlled Donchian breakout options showcase for the MVP.",
        "strategy_type": "Demo",
        "mode": "demo-controlled",
    },
    "sma-spread": {
        "display_name": "SMA Spread Strategy",
        "description": "Demo-controlled SMA spread presentation strategy for the dashboard.",
        "strategy_type": "Demo",
        "mode": "demo-controlled",
    },
}


@dataclass
class ReportFilters:
    strategy_id: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class DashboardStore:
    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path

    def initialize(self, reference_time: Optional[datetime] = None) -> None:
        now = reference_time or datetime.now()
        with self._connect() as conn:
            self._create_tables(conn)
            self._seed_strategy_state(conn, now)
            self._seed_trade_history(conn, now)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _create_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dashboard_strategy_state (
                strategy_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                last_started_at TEXT,
                last_stopped_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dashboard_trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                entry_ts TEXT NOT NULL,
                exit_ts TEXT,
                side TEXT NOT NULL,
                qty INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                realized_pnl REAL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                position_type TEXT,
                section TEXT,
                subcat TEXT,
                exit_reason TEXT,
                sl_initial REAL,
                sl_final REAL,
                sl_adjustments INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()

    def _seed_strategy_state(self, conn: sqlite3.Connection, now: datetime) -> None:
        count = conn.execute("SELECT COUNT(*) FROM dashboard_strategy_state").fetchone()[0]
        if count:
            return

        started_at = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        stopped_at = (now - timedelta(days=1, hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        updated_at = now.strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            (
                "nifty-options",
                STRATEGY_CATALOG["nifty-options"]["display_name"],
                "RUNNING",
                STRATEGY_CATALOG["nifty-options"]["mode"],
                started_at,
                None,
                updated_at,
            ),
            (
                "donchian-options",
                STRATEGY_CATALOG["donchian-options"]["display_name"],
                "STOPPED",
                STRATEGY_CATALOG["donchian-options"]["mode"],
                None,
                stopped_at,
                updated_at,
            ),
            (
                "sma-spread",
                STRATEGY_CATALOG["sma-spread"]["display_name"],
                "STOPPED",
                STRATEGY_CATALOG["sma-spread"]["mode"],
                None,
                stopped_at,
                updated_at,
            ),
        ]
        conn.executemany(
            """
            INSERT INTO dashboard_strategy_state (
                strategy_id, display_name, status, mode, last_started_at, last_stopped_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()

    def _seed_trade_history(self, conn: sqlite3.Connection, now: datetime) -> None:
        count = conn.execute("SELECT COUNT(*) FROM dashboard_trade_history").fetchone()[0]
        if count:
            return

        base_day = datetime.combine(now.date() - timedelta(days=4), datetime.min.time()).replace(hour=9, minute=25)
        seeds: list[tuple] = []
        sample_rows = [
            ("nifty-options", "NIFTY26MAY22400PE", 0, 15, "SELL", 50, 184.5, 132.3, 2610.0, "CLOSED", "backtest", "B", "Morning Break", "VIX Support", "TIME_EXIT", 212.0, 168.0, 2),
            ("nifty-options", "NIFTY26MAY22500CE", 0, 65, "SELL", 50, 126.2, 153.8, -1380.0, "CLOSED", "backtest", "A", "Early Fade", "SL Guard", "SL_HIT", 149.0, 149.0, 0),
            ("donchian-options", "NIFTY26MAY22600CE", 1, 20, "BUY", 25, 92.5, 118.2, 642.5, "CLOSED", "demo", "A", "Channel Retest", "Momentum", "TARGET", 78.0, 96.0, 1),
            ("sma-spread", "NIFTY26MAY22350PE", 1, 90, "SELL", 25, 164.8, 140.1, 617.5, "CLOSED", "demo", "B", "Trend Follow", "Pullback", "TIME_EXIT", 188.0, 171.0, 1),
            ("nifty-options", "NIFTY26MAY22450CE", 2, 25, "SELL", 50, 143.0, 101.4, 2080.0, "CLOSED", "live-sync", "A", "Opening Range", "Confirmed", "TARGET", 168.0, 129.0, 2),
            ("donchian-options", "NIFTY26MAY22700PE", 2, 70, "BUY", 25, 88.7, 73.1, -390.0, "CLOSED", "demo", "B", "Breakdown", "Reversal", "SL_HIT", 76.5, 76.5, 0),
            ("sma-spread", "NIFTY26MAY22300PE", 3, 35, "SELL", 25, 156.2, 121.9, 857.5, "CLOSED", "demo", "B", "Midday Drift", "Follow-through", "TARGET", 181.0, 149.0, 2),
            ("nifty-options", "NIFTY26MAY22550CE", 4, 0, "SELL", 50, 111.6, None, 420.0, "OPEN", "live-sync", "A", "Afternoon Compression", "Pending Exit", None, 132.0, 118.0, 1),
        ]

        for idx, row in enumerate(sample_rows):
            (
                strategy_id,
                symbol,
                day_offset,
                minute_offset,
                side,
                qty,
                entry_price,
                exit_price,
                pnl,
                status,
                source,
                position_type,
                section,
                subcat,
                exit_reason,
                sl_initial,
                sl_final,
                sl_adjustments,
            ) = row
            entry_ts = base_day + timedelta(days=day_offset, minutes=minute_offset)
            exit_ts = None
            if status == "CLOSED":
                exit_ts = entry_ts + timedelta(minutes=38 + idx * 4)
            seeds.append(
                (
                    strategy_id,
                    symbol,
                    entry_ts.strftime("%Y-%m-%d %H:%M:%S"),
                    exit_ts.strftime("%Y-%m-%d %H:%M:%S") if exit_ts else None,
                    side,
                    qty,
                    entry_price,
                    exit_price,
                    pnl,
                    status,
                    source,
                    position_type,
                    section,
                    subcat,
                    exit_reason,
                    sl_initial,
                    sl_final,
                    sl_adjustments,
                )
            )

        conn.executemany(
            """
            INSERT INTO dashboard_trade_history (
                strategy_id, symbol, entry_ts, exit_ts, side, qty, entry_price, exit_price, realized_pnl,
                status, source, position_type, section, subcat, exit_reason, sl_initial, sl_final, sl_adjustments
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            seeds,
        )
        conn.commit()

    def list_strategies(self) -> list[StrategyCard]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT strategy_id, display_name, status, mode, last_started_at, last_stopped_at, updated_at
                FROM dashboard_strategy_state
                ORDER BY CASE strategy_id
                    WHEN 'nifty-options' THEN 1
                    WHEN 'donchian-options' THEN 2
                    ELSE 3
                END
                """
            ).fetchall()
        return [self._map_strategy_row(row) for row in rows]

    def set_strategy_status(self, strategy_id: StrategyId, running: bool, now: Optional[datetime] = None) -> StrategyCard:
        timestamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT strategy_id FROM dashboard_strategy_state WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(strategy_id)

            if running:
                conn.execute(
                    """
                    UPDATE dashboard_strategy_state
                    SET status = 'RUNNING', last_started_at = ?, updated_at = ?
                    WHERE strategy_id = ?
                    """,
                    (timestamp, timestamp, strategy_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE dashboard_strategy_state
                    SET status = 'STOPPED', last_stopped_at = ?, updated_at = ?
                    WHERE strategy_id = ?
                    """,
                    (timestamp, timestamp, strategy_id),
                )
            conn.commit()
            row = conn.execute(
                """
                SELECT strategy_id, display_name, status, mode, last_started_at, last_stopped_at, updated_at
                FROM dashboard_strategy_state
                WHERE strategy_id = ?
                """,
                (strategy_id,),
            ).fetchone()
        return self._map_strategy_row(row)

    def get_pnl_summary(self, reference_time: Optional[datetime] = None) -> PnlSummary:
        now = reference_time or datetime.now()
        today = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN exit_ts LIKE ? || '%' THEN realized_pnl END), 0),
                    COALESCE(SUM(CASE WHEN exit_ts LIKE ? || '%' THEN realized_pnl END), 0),
                    COALESCE(SUM(CASE WHEN status = 'OPEN' THEN realized_pnl END), 0),
                    COALESCE(SUM(CASE WHEN status = 'CLOSED' THEN realized_pnl END), 0),
                    COUNT(CASE WHEN status = 'CLOSED' THEN 1 END)
                FROM dashboard_trade_history
                """,
                (today, month),
            ).fetchone()
        return PnlSummary(
            today=float(row[0] or 0.0),
            month=float(row[1] or 0.0),
            open=float(row[2] or 0.0),
            total=float((row[2] or 0.0) + (row[3] or 0.0)),
            closedTrades=int(row[4] or 0),
        )

    def get_recent_trades(self, limit: int = 6) -> list[TradeRow]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM dashboard_trade_history
                ORDER BY entry_ts DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._map_trade_row(row) for row in rows]

    def get_open_position(self) -> Optional[OpenPositionSummary]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM dashboard_trade_history
                WHERE status = 'OPEN'
                ORDER BY entry_ts DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return OpenPositionSummary(
            strategyId=row["strategy_id"],
            symbol=row["symbol"],
            side=row["side"],
            qty=int(row["qty"]),
            entryTs=row["entry_ts"],
            entryPrice=float(row["entry_price"]),
            currentPnl=float(row["realized_pnl"] or 0.0),
            status=row["status"],
            positionType=row["position_type"],
            section=row["section"],
            subcat=row["subcat"],
        )

    def get_market_overview(self) -> MarketOverview:
        try:
            market_df = fetch_market_data(symbol="NIFTY50", interval="1m", limit=120, db_path=self.db_path)
            vix_df = fetch_vix_data(symbol="NIFTY50", db_path=self.db_path)
            if market_df is None or market_df.empty:
                raise ValueError("market data unavailable")

            latest = market_df.iloc[-1]
            previous = market_df.iloc[-2] if len(market_df) > 1 else latest
            intraday_df = market_df.tail(60)
            vix_row = vix_df.iloc[-1] if vix_df is not None and not vix_df.empty else None
            return MarketOverview(
                symbol="NIFTY50",
                spotPrice=float(latest.get("close") or 0.0),
                previousClose=float(previous.get("close") or 0.0),
                change=float((latest.get("close") or 0.0) - (previous.get("close") or 0.0)),
                changePercent=self._pct_change(previous.get("close"), latest.get("close")),
                asOf=intraday_df.index[-1].strftime("%Y-%m-%d %H:%M:%S"),
                vixValue=float(vix_row.get("vix_value")) if vix_row is not None and vix_row.get("vix_value") is not None else None,
                vixAsOf=vix_df.index[-1].strftime("%Y-%m-%d %H:%M:%S") if vix_row is not None else None,
                intradayChart=[
                    MarketPoint(timestamp=ts.strftime("%H:%M"), close=float(row.get("close") or 0.0))
                    for ts, row in intraday_df.iterrows()
                ],
                fallback=False,
            )
        except Exception:
            return self._fallback_market_overview()

    def get_report_trades(self, filters: ReportFilters) -> list[TradeRow]:
        query = "SELECT * FROM dashboard_trade_history WHERE 1=1"
        params: list[object] = []
        query, params = self._apply_report_filters(query, params, filters)
        query += " ORDER BY entry_ts ASC, id ASC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._map_trade_row(row) for row in rows]

    def get_pnl_rows(self, group_by: str, filters: ReportFilters) -> list[PnlRow]:
        period_expr = "substr(exit_ts, 1, 10)" if group_by == "daily" else "substr(exit_ts, 1, 7)"
        query = f"""
            SELECT
                {period_expr} AS period,
                COALESCE(SUM(realized_pnl), 0) AS pnl,
                COUNT(*) AS trade_count,
                COALESCE(AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END), 0) AS win_rate
            FROM dashboard_trade_history
            WHERE status = 'CLOSED' AND exit_ts IS NOT NULL
        """
        params: list[object] = []
        query, params = self._apply_report_filters(query, params, filters, on_exit_ts=True)
        query += f" GROUP BY {period_expr} ORDER BY {period_expr} ASC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            PnlRow(
                period=row["period"],
                pnl=float(row["pnl"] or 0.0),
                tradeCount=int(row["trade_count"] or 0),
                winRate=float(row["win_rate"] or 0.0),
            )
            for row in rows
            if row["period"]
        ]

    def build_engine_status(self, engine_state: Optional[str], engine_mode: str, last_update_ts: Optional[str], source: str) -> EngineStatusCard:
        return EngineStatusCard(
            state=engine_state or "IDLE",
            mode=engine_mode,
            lastUpdateTs=last_update_ts,
            source=source,
        )

    def _apply_report_filters(
        self,
        query: str,
        params: list[object],
        filters: ReportFilters,
        *,
        on_exit_ts: bool = False,
    ) -> tuple[str, list[object]]:
        timestamp_col = "exit_ts" if on_exit_ts else "entry_ts"
        if filters.strategy_id:
            query += " AND strategy_id = ?"
            params.append(filters.strategy_id)
        if filters.start_date:
            query += f" AND substr({timestamp_col}, 1, 10) >= ?"
            params.append(filters.start_date.isoformat())
        if filters.end_date:
            query += f" AND substr({timestamp_col}, 1, 10) <= ?"
            params.append(filters.end_date.isoformat())
        return query, params

    def _map_strategy_row(self, row: sqlite3.Row) -> StrategyCard:
        meta = STRATEGY_CATALOG[row["strategy_id"]]
        return StrategyCard(
            strategyId=row["strategy_id"],
            displayName=row["display_name"],
            description=meta["description"],
            strategyType=meta["strategy_type"],
            status=row["status"],
            mode=row["mode"],
            lastStartedAt=row["last_started_at"],
            lastStoppedAt=row["last_stopped_at"],
            updatedAt=row["updated_at"],
        )

    def _map_trade_row(self, row: sqlite3.Row) -> TradeRow:
        return TradeRow(
            id=int(row["id"]),
            strategyId=row["strategy_id"],
            symbol=row["symbol"],
            entryTs=row["entry_ts"],
            exitTs=row["exit_ts"],
            side=row["side"],
            qty=int(row["qty"]),
            entryPrice=float(row["entry_price"]),
            exitPrice=float(row["exit_price"]) if row["exit_price"] is not None else None,
            realizedPnl=float(row["realized_pnl"]) if row["realized_pnl"] is not None else None,
            status=row["status"],
            source=row["source"],
            positionType=row["position_type"],
            section=row["section"],
            subcat=row["subcat"],
            exitReason=row["exit_reason"],
            slInitial=float(row["sl_initial"]) if row["sl_initial"] is not None else None,
            slFinal=float(row["sl_final"]) if row["sl_final"] is not None else None,
            slAdjustments=int(row["sl_adjustments"] or 0),
        )

    def _fallback_market_overview(self) -> MarketOverview:
        points = [
            MarketPoint(timestamp=stamp, close=value)
            for stamp, value in [
                ("09:15", 22382.4),
                ("10:00", 22411.8),
                ("11:00", 22396.1),
                ("12:00", 22428.9),
                ("13:00", 22406.7),
                ("14:00", 22444.3),
                ("15:00", 22461.5),
            ]
        ]
        return MarketOverview(
            symbol="NIFTY50",
            spotPrice=22461.5,
            previousClose=22412.8,
            change=48.7,
            changePercent=self._pct_change(22412.8, 22461.5),
            asOf="demo-seeded",
            vixValue=14.82,
            vixAsOf="demo-seeded",
            intradayChart=points,
            fallback=True,
        )

    @staticmethod
    def _pct_change(previous: Optional[float], current: Optional[float]) -> float:
        if previous in (None, 0):
            return 0.0
        return float(((float(current or 0.0) - float(previous)) / float(previous)) * 100.0)
