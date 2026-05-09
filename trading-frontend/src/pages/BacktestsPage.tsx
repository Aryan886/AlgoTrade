import { startTransition, useMemo, useState } from "react";

import { exportBacktestHtml, runBacktest } from "../api/dashboard";
import PageHeader from "../components/common/PageHeader";
import ShowcaseReport from "../components/reports/ShowcaseReport";
import type { BacktestRunResponse } from "../types/dashboard";

const STORAGE_KEY = "algotrade-latest-backtest";

function readStoredRun(): BacktestRunResponse | null {
  const raw = sessionStorage.getItem(STORAGE_KEY);
  if (!raw) {
    return null;
  }

  try {
    return JSON.parse(raw) as BacktestRunResponse;
  } catch {
    sessionStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

function toFileHref(path: string) {
  return `file:///${path.replace(/\\/g, "/")}`;
}

export default function BacktestsPage() {
  const initial = useMemo(() => readStoredRun(), []);
  const [startDate, setStartDate] = useState(initial?.meta.startDate ?? "2026-03-26");
  const [endDate, setEndDate] = useState(initial?.meta.endDate ?? "2026-03-27");
  const [report, setReport] = useState<BacktestRunResponse | null>(initial);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [exportState, setExportState] = useState<{ message: string; path: string } | null>(null);

  async function handleRun() {
    try {
      setLoading(true);
      setError(null);
      const payload = await runBacktest({
        strategyId: "nifty-options",
        startDate,
        endDate,
      });
      startTransition(() => {
        setReport(payload);
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Backtest failed.");
    } finally {
      setLoading(false);
    }
  }

  async function handleExport() {
    try {
      setLoading(true);
      setError(null);
      const payload = await exportBacktestHtml({
        strategyId: "nifty-options",
        startDate,
        endDate,
        useLatestRun: Boolean(report),
      });
      setExportState({ message: payload.message ?? "HTML report exported.", path: payload.outputPath });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not export HTML.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Backtests"
        subtitle="Real synchronous backtests for the Nifty Options Strategy, capped to a 5-trading-day MVP window."
      />

      <section className="surface-card">
        <div className="inline-form-grid backtest-grid">
          <div className="field">
            <label htmlFor="strategy">Strategy</label>
            <select id="strategy" value="nifty-options" disabled>
              <option value="nifty-options">Nifty Options Strategy</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="startDate">Start date</label>
            <input id="startDate" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="endDate">End date</label>
            <input id="endDate" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
          </div>
        </div>

        <div className="button-row">
          <button className="primary-button" disabled={loading} onClick={handleRun} type="button">
            {loading ? "Running..." : "Run Backtest"}
          </button>
          <button className="secondary-button" disabled={loading} onClick={handleExport} type="button">
            Export HTML
          </button>
          {exportState ? (
            <a className="ghost-button" href={toFileHref(exportState.path)} rel="noreferrer" target="_blank">
              Open generated report
            </a>
          ) : null}
        </div>

        <p className="helper-text">The UI preserves the latest successful run in session storage so refresh keeps the showcase visible.</p>

        {error ? <div className="error-banner">{error}</div> : null}
        {exportState ? <div className="success-banner">{exportState.message}: <span className="mono">{exportState.path}</span></div> : null}
      </section>

      <ShowcaseReport
        report={report}
        title="Backtest Showcase"
        subtitle={report ? `${report.meta.startDate} to ${report.meta.endDate}` : "Run a backtest to populate the native React showcase."}
      />
    </>
  );
}
