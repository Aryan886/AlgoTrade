import type {
  BacktestRunRequest,
  BacktestRunResponse,
  DashboardOverview,
  HtmlExportResponse,
  LoginResponse,
  PnlRow,
  StrategyCard,
  StrategyId,
  TradeRow,
} from "../types/dashboard";
import { apiFetch } from "./http";

export function loginDemo(email: string, password: string) {
  return apiFetch<LoginResponse>("/api/session/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function fetchDashboardOverview() {
  return apiFetch<DashboardOverview>("/api/dashboard/overview");
}

export function fetchStrategies() {
  return apiFetch<StrategyCard[]>("/api/strategies");
}

export function startStrategy(strategyId: StrategyId) {
  return apiFetch<StrategyCard>(`/api/strategies/${strategyId}/start`, {
    method: "POST",
  });
}

export function stopStrategy(strategyId: StrategyId) {
  return apiFetch<StrategyCard>(`/api/strategies/${strategyId}/stop`, {
    method: "POST",
  });
}

export function runBacktest(payload: BacktestRunRequest) {
  return apiFetch<BacktestRunResponse>("/api/backtests/run", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function exportBacktestHtml(payload: {
  strategyId: StrategyId;
  startDate?: string;
  endDate?: string;
  useLatestRun?: boolean;
}) {
  return apiFetch<HtmlExportResponse>("/api/backtests/export-html", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchReportPnl(params: {
  groupBy: "daily" | "monthly";
  strategyId?: StrategyId | "";
  startDate?: string;
  endDate?: string;
}) {
  const query = new URLSearchParams();
  query.set("groupBy", params.groupBy);
  if (params.strategyId) query.set("strategyId", params.strategyId);
  if (params.startDate) query.set("startDate", params.startDate);
  if (params.endDate) query.set("endDate", params.endDate);
  return apiFetch<PnlRow[]>(`/api/reports/pnl?${query.toString()}`);
}

export function fetchReportTrades(params: {
  strategyId?: StrategyId | "";
  startDate?: string;
  endDate?: string;
}) {
  const query = new URLSearchParams();
  if (params.strategyId) query.set("strategyId", params.strategyId);
  if (params.startDate) query.set("startDate", params.startDate);
  if (params.endDate) query.set("endDate", params.endDate);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return apiFetch<TradeRow[]>(`/api/reports/trades${suffix}`);
}
