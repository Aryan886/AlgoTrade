from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Request

from backtesting.cli import build_report_payload, export_results
from backtesting.runner import BacktestConfig, BacktestRunner
from engine.engine import TradingEngine
from utils.db_func import DB_PATH

from .dashboard_models import (
    BacktestMeta,
    BacktestRunRequest,
    BacktestRunResponse,
    DashboardOverview,
    HtmlExportRequest,
    HtmlExportResponse,
    LoginRequest,
    LoginResponse,
    PnlGroupBy,
    PnlRow,
    StrategyCard,
    StrategyId,
    TradeRow,
)
from .dashboard_store import DashboardStore, ReportFilters


DEMO_EMAIL = "demo@algotrade.local"
DEMO_PASSWORD = "demo123"


@dataclass
class StoredBacktestRun:
    request: BacktestRunRequest
    runner: BacktestRunner
    result: object


def create_dashboard_router(engine: TradingEngine, db_path: str = DB_PATH) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["dashboard-mvp"])
    store = DashboardStore(db_path=db_path)

    @router.post("/session/login", response_model=LoginResponse)
    def login(payload: LoginRequest) -> LoginResponse:
        if payload.email == DEMO_EMAIL and payload.password == DEMO_PASSWORD:
            return LoginResponse(
                ok=True,
                user={"name": "College Demo User", "role": "Presenter"},
            )
        return LoginResponse(ok=False, user=None, message="Invalid demo credentials.")

    @router.get("/dashboard/overview", response_model=DashboardOverview)
    def get_dashboard_overview() -> DashboardOverview:
        status = None
        source = "demo-state"
        try:
            status = engine.get_status()
            source = "engine/status"
        except Exception:
            status = None

        engine_status = store.build_engine_status(
            engine_state=status.engine_state.value if status else None,
            engine_mode=status.mode if status else "demo-only",
            last_update_ts=status.last_update_ts.isoformat() if status and status.last_update_ts else None,
            source=source,
        )
        return DashboardOverview(
            engineStatus=engine_status,
            marketOverview=store.get_market_overview(),
            pnlSummary=store.get_pnl_summary(),
            openPosition=store.get_open_position(),
            recentTrades=store.get_recent_trades(),
            strategyStatuses=store.list_strategies(),
        )

    @router.get("/strategies", response_model=list[StrategyCard])
    def list_strategies() -> list[StrategyCard]:
        return store.list_strategies()

    @router.post("/strategies/{strategy_id}/start", response_model=StrategyCard)
    def start_strategy(strategy_id: StrategyId) -> StrategyCard:
        try:
            return store.set_strategy_status(strategy_id, running=True)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown strategy.")

    @router.post("/strategies/{strategy_id}/stop", response_model=StrategyCard)
    def stop_strategy(strategy_id: StrategyId) -> StrategyCard:
        try:
            return store.set_strategy_status(strategy_id, running=False)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown strategy.")

    @router.post("/backtests/run", response_model=BacktestRunResponse)
    def run_backtest(payload: BacktestRunRequest, request: Request) -> BacktestRunResponse:
        validated = _validate_backtest_request(payload)
        runner = _create_runner(validated, db_path=db_path)
        try:
            result = runner.run()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        response = _build_backtest_response(payload, runner, result)
        request.app.state.latest_backtest_run = StoredBacktestRun(
            request=payload,
            runner=runner,
            result=result,
        )
        return response

    @router.post("/backtests/export-html", response_model=HtmlExportResponse)
    def export_backtest_html(payload: HtmlExportRequest, request: Request) -> HtmlExportResponse:
        stored: Optional[StoredBacktestRun] = getattr(request.app.state, "latest_backtest_run", None)
        runner: Optional[BacktestRunner] = None
        result = None
        run_request: Optional[BacktestRunRequest] = None

        if payload.useLatestRun:
            if stored is None:
                raise HTTPException(status_code=400, detail="No successful backtest run is available for export.")
            runner = stored.runner
            result = stored.result
            run_request = stored.request
        else:
            if payload.startDate is None or payload.endDate is None:
                raise HTTPException(status_code=400, detail="startDate and endDate are required when useLatestRun is false.")
            run_request = BacktestRunRequest(
                strategyId=payload.strategyId,
                startDate=payload.startDate,
                endDate=payload.endDate,
            )
            validated = _validate_backtest_request(run_request)
            runner = _create_runner(validated, db_path=db_path)
            try:
                result = runner.run()
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            request.app.state.latest_backtest_run = StoredBacktestRun(
                request=run_request,
                runner=runner,
                result=result,
            )

        output_path = _default_report_output_path(run_request)
        try:
            saved_path = export_results(str(output_path), runner, result)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not export HTML report: {exc}")

        return HtmlExportResponse(
            ok=True,
            outputPath=str(Path(saved_path).resolve()),
            fileName=Path(saved_path).name,
            message="HTML report exported successfully.",
        )

    @router.get("/reports/pnl", response_model=list[PnlRow])
    def get_report_pnl(
        groupBy: PnlGroupBy = "daily",
        strategyId: Optional[StrategyId] = None,
        startDate: Optional[str] = None,
        endDate: Optional[str] = None,
    ) -> list[PnlRow]:
        filters = _parse_filters(strategyId, startDate, endDate)
        return store.get_pnl_rows(group_by=groupBy, filters=filters)

    @router.get("/reports/trades", response_model=list[TradeRow])
    def get_report_trades(
        strategyId: Optional[StrategyId] = None,
        startDate: Optional[str] = None,
        endDate: Optional[str] = None,
    ) -> list[TradeRow]:
        filters = _parse_filters(strategyId, startDate, endDate)
        return store.get_report_trades(filters)

    router.store = store
    return router


def initialize_dashboard_store(router: APIRouter, reference_time: Optional[datetime] = None) -> None:
    store: DashboardStore = router.store
    store.initialize(reference_time=reference_time)


def _validate_backtest_request(payload: BacktestRunRequest) -> BacktestRunRequest:
    if payload.strategyId not in {"nifty-options", "oi-expiry"}:
        raise HTTPException(status_code=400, detail="Only nifty-options and oi-expiry support real backtests in this MVP.")

    start_day = date.fromisoformat(payload.startDate)
    end_day = date.fromisoformat(payload.endDate)
    if end_day < start_day:
        raise HTTPException(status_code=400, detail="endDate must be on or after startDate.")
    return payload


def _create_runner(payload: BacktestRunRequest, db_path: str = DB_PATH) -> BacktestRunner:
    start_dt = datetime.combine(date.fromisoformat(payload.startDate), time(9, 15))
    end_dt = datetime.combine(date.fromisoformat(payload.endDate), time(15, 30))
    config = BacktestConfig(
        start_date=start_dt,
        end_date=end_dt,
        strategy_id=payload.strategyId,
        db_path=db_path,
        symbol="NIFTY50",
        verbose=False,
    )
    return BacktestRunner(config)


def _build_backtest_response(payload: BacktestRunRequest, runner: BacktestRunner, result: object) -> BacktestRunResponse:
    trades_df = runner.trade_log.to_dataframe()
    report = build_report_payload(result=result, trades_df=trades_df)
    strategy_qty = _strategy_backtest_qty(payload.strategyId)
    return BacktestRunResponse(
        summary=report["summary"],
        kpis=report["kpis"],
        equityCurve=report["equityCurve"],
        slAnalysis=report["slAnalysis"],
        dataQuality=report["dataQuality"],
        trades=[
            TradeRow(
                id=index + 1,
                strategyId=payload.strategyId,
                symbol=str(row.get("trade_id") or f"trade-{index+1}"),
                entryTs=_stringify(row.get("entry_fill_time") or row.get("entry_time")),
                exitTs=_stringify(row.get("exit_fill_time") or row.get("exit_time")),
                side="MULTI" if payload.strategyId == "oi-expiry" else "SELL",
                qty=strategy_qty,
                entryPrice=float(row.get("entry_price") or 0.0),
                exitPrice=float(row.get("exit_price")) if pd.notna(row.get("exit_price")) else None,
                realizedPnl=float(row.get("pnl")) if pd.notna(row.get("pnl")) else None,
                status="CLOSED" if pd.notna(row.get("pnl")) else "OPEN",
                source="backtest",
                positionType=(row.get("position_type") if pd.notna(row.get("position_type")) else None),
                section=(row.get("section") if pd.notna(row.get("section")) else None),
                subcat=(row.get("subcat") if pd.notna(row.get("subcat")) else None),
                exitReason=(row.get("exit_reason") if pd.notna(row.get("exit_reason")) else None),
                slInitial=float(row.get("sl_initial")) if pd.notna(row.get("sl_initial")) else None,
                slFinal=float(row.get("sl_final")) if pd.notna(row.get("sl_final")) else None,
                slAdjustments=int(row.get("sl_adjustments") or 0),
            )
            for index, (_, row) in enumerate(trades_df.iterrows())
        ],
        warnings=report["dataQuality"]["warnings"],
        meta=BacktestMeta(
            strategyId=payload.strategyId,
            startDate=payload.startDate,
            endDate=payload.endDate,
            executedAt=datetime.now().isoformat(),
            htmlExportable=True,
        ),
    )


def _default_report_output_path(run_request: BacktestRunRequest) -> Path:
    output_dir = Path("results")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{run_request.strategyId}_{run_request.startDate}_{run_request.endDate}.html"


def _parse_filters(strategy_id: Optional[str], start_date: Optional[str], end_date: Optional[str]) -> ReportFilters:
    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
    if parsed_start and parsed_end and parsed_end < parsed_start:
        raise HTTPException(status_code=400, detail="endDate must be on or after startDate.")
    return ReportFilters(strategy_id=strategy_id, start_date=parsed_start, end_date=parsed_end)


def _stringify(value: object) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat(sep=" ")
    return str(value)


def _strategy_backtest_qty(strategy_id: StrategyId) -> int:
    return 1 if strategy_id == "oi-expiry" else 50
