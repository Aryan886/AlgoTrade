export interface DemoUserSession {
  name: string;
  role: string;
}

export interface LoginResponse {
  ok: boolean;
  user: DemoUserSession | null;
  message?: string;
}

export interface MarketPoint {
  timestamp: string;
  close: number;
}

export interface MarketOverview {
  symbol: string;
  spotPrice: number;
  previousClose?: number | null;
  change: number;
  changePercent: number;
  asOf: string;
  vixValue?: number | null;
  vixAsOf?: string | null;
  intradayChart: MarketPoint[];
  fallback: boolean;
}

export interface PnlSummary {
  today: number;
  month: number;
  open: number;
  total: number;
  closedTrades: number;
}

export type StrategyId = "nifty-options" | "oi-expiry" | "donchian-options" | "sma-spread";

export interface StrategyCard {
  strategyId: StrategyId;
  displayName: string;
  description: string;
  strategyType: "Live-ready" | "Demo";
  status: "RUNNING" | "STOPPED" | "PAUSED";
  mode: string;
  lastStartedAt?: string | null;
  lastStoppedAt?: string | null;
  updatedAt: string;
}

export interface OpenPositionSummary {
  strategyId: StrategyId;
  symbol: string;
  side: string;
  qty: number;
  entryTs: string;
  entryPrice: number;
  currentPnl: number;
  status: string;
  positionType?: string | null;
  section?: string | null;
  subcat?: string | null;
}

export interface TradeRow {
  id: number;
  strategyId: StrategyId;
  symbol: string;
  entryTs?: string | null;
  exitTs?: string | null;
  side: string;
  qty: number;
  entryPrice: number;
  exitPrice?: number | null;
  realizedPnl?: number | null;
  status: string;
  source: string;
  positionType?: string | null;
  section?: string | null;
  subcat?: string | null;
  exitReason?: string | null;
  slInitial?: number | null;
  slFinal?: number | null;
  slAdjustments: number;
}

export interface EngineStatusCard {
  state: string;
  mode: string;
  lastUpdateTs?: string | null;
  source: string;
}

export interface DashboardOverview {
  engineStatus: EngineStatusCard;
  marketOverview: MarketOverview;
  pnlSummary: PnlSummary;
  openPosition?: OpenPositionSummary | null;
  recentTrades: TradeRow[];
  strategyStatuses: StrategyCard[];
}

export interface BacktestRunRequest {
  strategyId: "nifty-options" | "oi-expiry";
  startDate: string;
  endDate: string;
}

export interface BacktestKpis {
  totalPnl: number;
  trades: number;
  winRate: number;
  maxDrawdown: number;
  dailySharpe: number;
  expectancy: number;
}

export interface SummaryMetric {
  metric: string;
  value: number | string;
  formattedValue: string;
}

export interface EquityPoint {
  timestamp: string;
  equity: number;
}

export interface SlByLotRow {
  lotId: string;
  count: number;
}

export interface SlAnalysis {
  totalAdjustments: number;
  avgAdjustmentsPerTrade: number;
  avgSlMovementPct: number;
  tradesHitSl: number;
  tradesTimeExit: number;
  totalTrades: number;
  slByLot: SlByLotRow[];
}

export interface DataQualitySummary {
  skippedEntries: number;
  warnings: string[];
  healthy: boolean;
}

export interface BacktestMeta {
  strategyId: "nifty-options" | "oi-expiry";
  startDate: string;
  endDate: string;
  executedAt: string;
  htmlExportable: boolean;
  outputPath?: string | null;
}

export interface BacktestRunResponse {
  summary: SummaryMetric[];
  kpis: BacktestKpis;
  equityCurve: EquityPoint[];
  slAnalysis: SlAnalysis;
  dataQuality: DataQualitySummary;
  trades: TradeRow[];
  warnings: string[];
  meta: BacktestMeta;
}

export interface HtmlExportResponse {
  ok: boolean;
  outputPath: string;
  fileName: string;
  message?: string;
}

export interface PnlRow {
  period: string;
  pnl: number;
  tradeCount: number;
  winRate: number;
}

export interface ShowcaseReportData {
  summary: SummaryMetric[];
  kpis: BacktestKpis;
  equityCurve: EquityPoint[];
  slAnalysis: SlAnalysis;
  dataQuality: DataQualitySummary;
  trades: TradeRow[];
}
