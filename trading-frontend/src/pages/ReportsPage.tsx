import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchReportPnl, fetchReportTrades } from "../api/dashboard";
import PageHeader from "../components/common/PageHeader";
import EmptyState from "../components/common/EmptyState";
import StatCard from "../components/common/StatCard";
import ShowcaseReport from "../components/reports/ShowcaseReport";
import type { PnlRow, StrategyId, TradeRow } from "../types/dashboard";
import { buildShowcaseReportFromTrades, buildSummaryCards, formatCurrency, formatPercent } from "../utils/reporting";

export default function ReportsPage() {
  const [strategyId, setStrategyId] = useState<StrategyId | "">("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [dailyRows, setDailyRows] = useState<PnlRow[]>([]);
  const [monthlyRows, setMonthlyRows] = useState<PnlRow[]>([]);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const params = { strategyId, startDate: startDate || undefined, endDate: endDate || undefined };
      const [daily, monthly, tradeRows] = await Promise.all([
        fetchReportPnl({ ...params, groupBy: "daily" }),
        fetchReportPnl({ ...params, groupBy: "monthly" }),
        fetchReportTrades(params),
      ]);
      setDailyRows(daily);
      setMonthlyRows(monthly);
      setTrades(tradeRows);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load reports.");
    } finally {
      setLoading(false);
    }
  }, [endDate, startDate, strategyId]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadData();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadData]);

  const report = useMemo(() => (trades.length ? buildShowcaseReportFromTrades(trades) : null), [trades]);
  const dailySummary = buildSummaryCards(dailyRows);
  const monthlySummary = buildSummaryCards(monthlyRows);

  return (
    <>
      <PageHeader
        title="Reports"
        subtitle="A history page that mirrors the backtesting showcase structure while aggregating the seeded dashboard trade history."
        actions={
          <button className="primary-button" onClick={() => void loadData()} type="button">
            Apply filters
          </button>
        }
      />

      <section className="surface-card">
        <div className="inline-form-grid">
          <div className="field">
            <label htmlFor="reportStrategy">Strategy filter</label>
            <select
              id="reportStrategy"
              value={strategyId}
              onChange={(event) => setStrategyId(event.target.value as StrategyId | "")}
            >
              <option value="">All strategies</option>
              <option value="nifty-options">Nifty Options Strategy</option>
              <option value="donchian-options">Donchian Options Strategy</option>
              <option value="sma-spread">SMA Spread Strategy</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="reportStart">Start date</label>
            <input id="reportStart" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="reportEnd">End date</label>
            <input id="reportEnd" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
          </div>
        </div>
      </section>

      {error ? <div className="error-banner">{error}</div> : null}
      {loading ? <div className="loading-shell">Loading report aggregates...</div> : null}

      {!loading ? (
        <>
          <div className="reports-summary-grid">
            <StatCard label="Daily grouped P&L" value={formatCurrency(dailySummary.totalPnl)} tone={dailySummary.totalPnl >= 0 ? "positive" : "negative"} detail={`${dailySummary.totalTrades} grouped trades`} />
            <StatCard label="Daily win rate" value={formatPercent(dailySummary.averageWinRate)} />
            <StatCard label="Monthly grouped P&L" value={formatCurrency(monthlySummary.totalPnl)} tone={monthlySummary.totalPnl >= 0 ? "positive" : "negative"} detail={`${monthlySummary.totalTrades} grouped trades`} />
            <StatCard label="Monthly win rate" value={formatPercent(monthlySummary.averageWinRate)} />
          </div>

          {dailyRows.length === 0 && monthlyRows.length === 0 && trades.length === 0 ? (
            <EmptyState title="No report rows" message="Try widening the date range or clearing the strategy filter." />
          ) : null}

          <ShowcaseReport
            report={report}
            title="Reporting Showcase"
            subtitle="The same section order and mental model as the backtest HTML showcase, rebuilt from dashboard trade history."
          />
        </>
      ) : null}
    </>
  );
}
