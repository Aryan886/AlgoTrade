import { useEffect, useState } from "react";
import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { fetchDashboardOverview } from "../api/dashboard";
import EmptyState from "../components/common/EmptyState";
import PageHeader from "../components/common/PageHeader";
import StatCard from "../components/common/StatCard";
import StatusBadge from "../components/common/StatusBadge";
import type { DashboardOverview } from "../types/dashboard";
import { formatCurrency, formatPercent } from "../utils/reporting";

export default function DashboardPage() {
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;

    fetchDashboardOverview()
      .then((payload) => {
        if (!active) return;
        setOverview(payload);
      })
      .catch((reason: Error) => {
        if (!active) return;
        setError(reason.message);
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  if (loading) {
    return <div className="loading-shell">Loading dashboard overview...</div>;
  }

  if (error || !overview) {
    return <div className="error-banner">{error ?? "Dashboard data is unavailable."}</div>;
  }

  const { engineStatus, marketOverview, pnlSummary, openPosition, recentTrades, strategyStatuses } = overview;

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle="Read-only engine visibility, NIFTY market pulse, and seeded strategy reporting for the demo flow."
      />

      <div className="dashboard-grid">
        <div className="span-3">
          <StatCard label="Engine status" value={engineStatus.state} detail={engineStatus.source} />
        </div>
        <div className="span-3">
          <StatCard label="Spot" value={marketOverview.spotPrice.toFixed(2)} tone={marketOverview.change >= 0 ? "positive" : "negative"} detail={`As of ${marketOverview.asOf}`} />
        </div>
        <div className="span-3">
          <StatCard label="VIX" value={marketOverview.vixValue?.toFixed(2) ?? "-"} detail={marketOverview.vixAsOf ?? "Demo fallback"} />
        </div>
        <div className="span-3">
          <StatCard label="Monthly P&L" value={formatCurrency(pnlSummary.month)} tone={pnlSummary.month >= 0 ? "positive" : "negative"} detail={`${pnlSummary.closedTrades} closed trades`} />
        </div>

        <div className="span-8 surface-card">
          <div className="card-title-row">
            <div>
              <h3 className="card-title">Intraday Market Chart</h3>
              <div className="card-subtitle">
                {marketOverview.symbol} {marketOverview.fallback ? "seeded fallback" : "database-backed snapshot"}
              </div>
            </div>
            <StatusBadge
              label={`${marketOverview.change >= 0 ? "+" : ""}${marketOverview.change.toFixed(2)} / ${formatPercent(marketOverview.changePercent / 100)}`}
              tone={marketOverview.change >= 0 ? "profit" : "loss"}
            />
          </div>
          <div className="chart-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={marketOverview.intradayChart}>
                <XAxis dataKey="timestamp" stroke="#8b90a7" minTickGap={24} />
                <YAxis stroke="#8b90a7" width={80} domain={["dataMin - 10", "dataMax + 10"]} />
                <Tooltip
                  contentStyle={{ backgroundColor: "#1a1d27", border: "1px solid #2e3248", borderRadius: 12 }}
                  labelStyle={{ color: "#e8eaf0" }}
                />
                <Line type="monotone" dataKey="close" stroke="#5b7fff" strokeWidth={3} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="span-4 surface-card">
          <div className="card-title-row">
            <h3 className="card-title">P&amp;L Snapshot</h3>
            <StatusBadge label={pnlSummary.today >= 0 ? "Green day" : "Red day"} tone={pnlSummary.today >= 0 ? "profit" : "loss"} />
          </div>
          <div className="metric-list">
            <div className="metric-row">
              <span>Today</span>
              <strong className={pnlSummary.today >= 0 ? "metric-positive" : "metric-negative"}>{formatCurrency(pnlSummary.today)}</strong>
            </div>
            <div className="metric-row">
              <span>Month</span>
              <strong className={pnlSummary.month >= 0 ? "metric-positive" : "metric-negative"}>{formatCurrency(pnlSummary.month)}</strong>
            </div>
            <div className="metric-row">
              <span>Open exposure</span>
              <strong className={pnlSummary.open >= 0 ? "metric-positive" : "metric-negative"}>{formatCurrency(pnlSummary.open)}</strong>
            </div>
            <div className="metric-row">
              <span>Total</span>
              <strong className={pnlSummary.total >= 0 ? "metric-positive" : "metric-negative"}>{formatCurrency(pnlSummary.total)}</strong>
            </div>
          </div>
        </div>

        <div className="span-6 surface-card">
          <div className="card-title-row">
            <h3 className="card-title">Open Position Summary</h3>
            <StatusBadge label={openPosition ? openPosition.status : "No open position"} tone={openPosition ? "open" : "neutral"} />
          </div>
          {openPosition ? (
            <div className="detail-list">
              <div className="detail-item"><span>Strategy</span><strong>{openPosition.strategyId}</strong></div>
              <div className="detail-item"><span>Contract</span><strong>{openPosition.symbol}</strong></div>
              <div className="detail-item"><span>Entry</span><strong>{openPosition.entryTs}</strong></div>
              <div className="detail-item"><span>Qty / Side</span><strong>{openPosition.qty} / {openPosition.side}</strong></div>
              <div className="detail-item"><span>Current P&amp;L</span><strong className={openPosition.currentPnl >= 0 ? "metric-positive" : "metric-negative"}>{formatCurrency(openPosition.currentPnl)}</strong></div>
            </div>
          ) : (
            <EmptyState title="No live demo position" message="The dashboard will show the latest open seeded trade here when one is active." />
          )}
        </div>

        <div className="span-6 surface-card">
          <div className="card-title-row">
            <h3 className="card-title">Strategy Status Summary</h3>
            <span className="card-subtitle">{strategyStatuses.length} tracked cards</span>
          </div>
          <div className="detail-list">
            {strategyStatuses.map((strategy) => (
              <div className="detail-item" key={strategy.strategyId}>
                <span>{strategy.displayName}</span>
                <StatusBadge label={strategy.status} tone={strategy.status === "RUNNING" ? "running" : "stopped"} />
              </div>
            ))}
          </div>
        </div>

        <div className="span-12 data-table">
          <div className="data-table-inner">
            <div className="card-title-row">
              <h3 className="card-title">Recent Trades</h3>
              <span className="card-subtitle">Latest rows from dashboard trade history</span>
            </div>
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th>Symbol</th>
                    <th>Entry</th>
                    <th>Status</th>
                    <th>Section</th>
                    <th>P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {recentTrades.map((trade) => (
                    <tr key={trade.id}>
                      <td>{trade.strategyId}</td>
                      <td>{trade.symbol}</td>
                      <td>{trade.entryTs ?? "-"}</td>
                      <td>
                        <StatusBadge label={trade.status} tone={trade.status === "OPEN" ? "open" : trade.realizedPnl && trade.realizedPnl >= 0 ? "profit" : "loss"} />
                      </td>
                      <td>{trade.section ?? "-"}</td>
                      <td className={trade.realizedPnl != null && trade.realizedPnl < 0 ? "trade-pnl-negative" : "trade-pnl-positive"}>
                        {trade.realizedPnl == null ? "-" : formatCurrency(trade.realizedPnl)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
