from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


StrategyId = Literal["nifty-options", "oi-expiry", "donchian-options", "sma-spread"]
StrategyStatus = Literal["RUNNING", "STOPPED", "PAUSED"]
StrategyType = Literal["Live-ready", "Demo"]
PnlGroupBy = Literal["daily", "monthly"]


class DemoUserSession(BaseModel):
    name: str
    role: str


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    ok: bool
    user: Optional[DemoUserSession] = None
    message: Optional[str] = None


class MarketPoint(BaseModel):
    timestamp: str
    close: float


class MarketOverview(BaseModel):
    symbol: str
    spotPrice: float
    previousClose: Optional[float] = None
    change: float
    changePercent: float
    asOf: str
    vixValue: Optional[float] = None
    vixAsOf: Optional[str] = None
    intradayChart: list[MarketPoint]
    fallback: bool = False


class PnlSummary(BaseModel):
    today: float
    month: float
    open: float
    total: float
    closedTrades: int


class StrategyCard(BaseModel):
    strategyId: StrategyId
    displayName: str
    description: str
    strategyType: StrategyType
    status: StrategyStatus
    mode: str
    lastStartedAt: Optional[str] = None
    lastStoppedAt: Optional[str] = None
    updatedAt: str


class OpenPositionSummary(BaseModel):
    strategyId: StrategyId
    symbol: str
    side: str
    qty: int
    entryTs: str
    entryPrice: float
    currentPnl: float
    status: str
    positionType: Optional[str] = None
    section: Optional[str] = None
    subcat: Optional[str] = None


class TradeRow(BaseModel):
    id: int
    strategyId: StrategyId
    symbol: str
    entryTs: Optional[str] = None
    exitTs: Optional[str] = None
    side: str
    qty: int
    entryPrice: float
    exitPrice: Optional[float] = None
    realizedPnl: Optional[float] = None
    status: str
    source: str
    positionType: Optional[str] = None
    section: Optional[str] = None
    subcat: Optional[str] = None
    exitReason: Optional[str] = None
    slInitial: Optional[float] = None
    slFinal: Optional[float] = None
    slAdjustments: int = 0


class EngineStatusCard(BaseModel):
    state: str
    mode: str
    lastUpdateTs: Optional[str] = None
    source: str


class DashboardOverview(BaseModel):
    engineStatus: EngineStatusCard
    marketOverview: MarketOverview
    pnlSummary: PnlSummary
    openPosition: Optional[OpenPositionSummary] = None
    recentTrades: list[TradeRow]
    strategyStatuses: list[StrategyCard]


class BacktestRunRequest(BaseModel):
    strategyId: StrategyId
    startDate: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    endDate: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")


class BacktestKpis(BaseModel):
    totalPnl: float
    trades: int
    winRate: float
    maxDrawdown: float
    dailySharpe: float
    expectancy: float


class SummaryMetric(BaseModel):
    metric: str
    value: float | int | str
    formattedValue: str


class EquityPoint(BaseModel):
    timestamp: str
    equity: float


class SlByLotRow(BaseModel):
    lotId: str
    count: int


class SlAnalysis(BaseModel):
    totalAdjustments: int
    avgAdjustmentsPerTrade: float
    avgSlMovementPct: float
    tradesHitSl: int
    tradesTimeExit: int
    totalTrades: int
    slByLot: list[SlByLotRow]


class DataQualitySummary(BaseModel):
    skippedEntries: int
    warnings: list[str]
    healthy: bool


class BacktestMeta(BaseModel):
    strategyId: StrategyId
    startDate: str
    endDate: str
    executedAt: str
    htmlExportable: bool = True
    outputPath: Optional[str] = None


class BacktestRunResponse(BaseModel):
    summary: list[SummaryMetric]
    kpis: BacktestKpis
    equityCurve: list[EquityPoint]
    slAnalysis: SlAnalysis
    dataQuality: DataQualitySummary
    trades: list[TradeRow]
    warnings: list[str]
    meta: BacktestMeta


class HtmlExportRequest(BaseModel):
    strategyId: StrategyId
    startDate: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    endDate: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    useLatestRun: bool = False


class HtmlExportResponse(BaseModel):
    ok: bool
    outputPath: str
    fileName: str
    message: Optional[str] = None


class PnlRow(BaseModel):
    period: str
    pnl: float
    tradeCount: int
    winRate: float
