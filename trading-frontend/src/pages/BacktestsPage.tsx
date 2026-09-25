import { startTransition, useMemo, useState } from "react";

import { exportBacktestHtml, runBacktest } from "../api/dashboard";
import PageHeader from "../components/common/PageHeader";
import ShowcaseReport from "../components/reports/ShowcaseReport";
import type { BacktestRunResponse, StrategyId } from "../types/dashboard";

const STORAGE_KEY = "algotrade-latest-backtest";
const DEFAULT_START_DATE = "2026-03-26";
const DEFAULT_END_DATE = "2026-03-27";

function readStoredRun(): BacktestRunResponse | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return null;
    }
    return JSON.parse(raw) as BacktestRunResponse;
  } catch {
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      // Ignore storage cleanup failures and fall back to an uncached view.
    }
    return null;
  }
}

function storeRun(run: BacktestRunResponse) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(run));
    return true;
  } catch {
    return false;
  }
}

function toFileHref(path: string) {
  return `file:///${path.replace(/\\/g, "/")}`;
}

export default function BacktestsPage() {
  const initial = useMemo(() => readStoredRun(), []);
  const [startDate, setStartDate] = useState(initial?.meta.startDate ?? DEFAULT_START_DATE);
  const [endDate, setEndDate] = useState(initial?.meta.endDate ?? DEFAULT_END_DATE);
  const [strategyId, setStrategyId] = useState<StrategyId>(initial?.meta.strategyId ?? "nifty-options");
  const [report, setReport] = useState<BacktestRunResponse | null>(initial);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [exportState, setExportState] = useState<{ message: string; path: string } | null>(null);
  const [storageMessage, setStorageMessage] = useState<string | null>(null);

  async function handleRun() {
    try {
      setLoading(true);
      setError(null);
      const payload = await runBacktest({
        strategyId,
        startDate,
        endDate,
      });
      startTransition(() => {
        setReport(payload);
        setStorageMessage(
          storeRun(payload)
            ? null
            : "This run is available now, but it was too large to keep in session storage for refresh."
        );
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
        strategyId,
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
        subtitle="Run synchronous historical backtests for the supported live options strategies without leaving the dashboard."
      />

      <section className="surface-card">
        <div className="inline-form-grid backtest-grid">
          <div className="field">
            <label htmlFor="strategy">Strategy</label>
            <select id="strategy" value={strategyId} onChange={(event) => setStrategyId(event.target.value as StrategyId)}>
              <option value="nifty-options">Nifty Options Strategy</option>
              <option value="oi-expiry">OI Expiry Strategy</option>
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

        <p className="helper-text">Larger ranges may take longer to run. The latest successful run is cached in session storage when the payload fits.</p>

        {error ? <div className="error-banner">{error}</div> : null}
        {exportState ? <div className="success-banner">{exportState.message}: <span className="mono">{exportState.path}</span></div> : null}
        {storageMessage ? <div className="info-banner">{storageMessage}</div> : null}
      </section>

      <ShowcaseReport
        report={report}
        title="Backtest Showcase"
        subtitle={
          report
            ? `${report.meta.strategyId} • ${report.meta.startDate} to ${report.meta.endDate}`
            : "Run a backtest to populate the native React showcase."
        }
      />
    </>
  );
}
