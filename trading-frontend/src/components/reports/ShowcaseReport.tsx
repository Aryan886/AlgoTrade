import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { ShowcaseReportData } from "../../types/dashboard";
import { formatCurrency, formatPercent } from "../../utils/reporting";
import EmptyState from "../common/EmptyState";
import StatCard from "../common/StatCard";
import StatusBadge from "../common/StatusBadge";

interface ShowcaseReportProps {
  report: ShowcaseReportData | null;
  title: string;
  subtitle: string;
}

export default function ShowcaseReport({ report, title, subtitle }: ShowcaseReportProps) {
  if (!report) {
    return <EmptyState title="No report yet" message="Run a backtest or adjust report filters to populate the showcase." />;
  }

  const slTone =
    report.slAnalysis.tradesHitSl > report.slAnalysis.tradesTimeExit ? "loss" : "profit";

  return (
    <section className="report-layout">
      <div className="hero-card">
        <div>
          <h3 className="card-title">{title}</h3>
          <p className="card-subtitle">{subtitle}</p>
        </div>
        <div className="report-kpi-grid">
          <StatCard label="Total P&L" value={formatCurrency(report.kpis.totalPnl)} tone={report.kpis.totalPnl >= 0 ? "positive" : "negative"} />
          <StatCard label="Trades" value={report.kpis.trades} />
          <StatCard label="Win Rate" value={formatPercent(report.kpis.winRate)} />
          <StatCard label="Max Drawdown" value={formatCurrency(report.kpis.maxDrawdown)} tone={report.kpis.maxDrawdown > 0 ? "negative" : undefined} />
          <StatCard label="Daily Sharpe" value={report.kpis.dailySharpe.toFixed(2)} tone={report.kpis.dailySharpe >= 0 ? "positive" : "negative"} />
          <StatCard label="Expectancy" value={formatCurrency(report.kpis.expectancy)} tone={report.kpis.expectancy >= 0 ? "positive" : "negative"} />
        </div>
      </div>

      <div className="report-section-grid">
        <div className="surface-card">
          <div className="card-title-row">
            <h3 className="card-title">Summary</h3>
            <StatusBadge
              label={report.dataQuality.healthy ? "Clean data window" : "Warnings present"}
              tone={report.dataQuality.healthy ? "running" : "warning"}
            />
          </div>
          <div className="metric-list">
            {report.summary.map((item) => (
              <div key={item.metric} className="metric-row">
                <span>{item.metric}</span>
                <strong>{item.formattedValue}</strong>
              </div>
            ))}
          </div>
        </div>

        <div className="surface-card">
          <div className="card-title-row">
            <h3 className="card-title">Equity Curve</h3>
            <span className="card-subtitle">{report.equityCurve.length} points</span>
          </div>
          <div className="chart-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={report.equityCurve}>
                <XAxis dataKey="timestamp" stroke="#8b90a7" minTickGap={32} />
                <YAxis stroke="#8b90a7" width={84} tickFormatter={(value) => `${Math.round(value / 1000)}k`} />
                <Tooltip
                  contentStyle={{ backgroundColor: "#1a1d27", border: "1px solid #2e3248", borderRadius: 12 }}
                  labelStyle={{ color: "#e8eaf0" }}
                />
                <Line type="monotone" dataKey="equity" stroke="#5b7fff" strokeWidth={3} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="surface-card">
        <div className="card-title-row">
          <h3 className="card-title">Stop Loss Analysis</h3>
          <StatusBadge
            label={`${report.slAnalysis.tradesHitSl} SL hits / ${report.slAnalysis.tradesTimeExit} time exits`}
            tone={slTone}
          />
        </div>
        <div className="sl-analysis-grid">
          <StatCard label="Total Adjustments" value={report.slAnalysis.totalAdjustments} />
          <StatCard label="Avg Per Trade" value={report.slAnalysis.avgAdjustmentsPerTrade.toFixed(1)} />
          <StatCard label="Avg SL Move" value={`${report.slAnalysis.avgSlMovementPct.toFixed(2)}%`} />
          <StatCard label="Closed Trades" value={report.slAnalysis.totalTrades} />
        </div>
        <div className="summary-chip-row">
          {report.slAnalysis.slByLot.length > 0 ? (
            report.slAnalysis.slByLot.map((row) => (
              <div className="summary-chip" key={row.lotId}>
                {row.lotId}: {row.count}
              </div>
            ))
          ) : (
            <div className="summary-chip">No lot-level SL exits recorded.</div>
          )}
        </div>
      </div>

      <div className="surface-card">
        <div className="card-title-row">
          <h3 className="card-title">Data Quality</h3>
          <StatusBadge
            label={`${report.dataQuality.skippedEntries} skipped entries`}
            tone={report.dataQuality.skippedEntries > 0 ? "warning" : "running"}
          />
        </div>
        {report.dataQuality.warnings.length > 0 ? (
          <ul className="warning-list">
            {report.dataQuality.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No issues recorded" message="This run completed without data-quality warnings." />
        )}
      </div>

      <div className="surface-card">
        <div className="card-title-row">
          <h3 className="card-title">Trade Log</h3>
          <span className="card-subtitle">{report.trades.length} rows</span>
        </div>
        <div className="report-table-wrap">
          <table className="report-trade-table">
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Contract</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>Position</th>
                <th>Section</th>
                <th>Exit Reason</th>
                <th>P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {report.trades.map((trade) => (
                <tr key={`${trade.id}-${trade.symbol}`}>
                  <td>{trade.strategyId}</td>
                  <td>{trade.symbol}</td>
                  <td>{trade.entryTs ?? "-"}</td>
                  <td>{trade.exitTs ?? "-"}</td>
                  <td>{trade.positionType ?? "-"}</td>
                  <td>{trade.section ?? "-"}</td>
                  <td>{trade.exitReason ?? "-"}</td>
                  <td className={trade.realizedPnl && trade.realizedPnl < 0 ? "trade-pnl-negative" : "trade-pnl-positive"}>
                    {trade.realizedPnl == null ? "-" : formatCurrency(trade.realizedPnl)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
