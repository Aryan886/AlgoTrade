import { useEffect, useState } from "react";

import { fetchStrategies, startStrategy, stopStrategy } from "../api/dashboard";
import PageHeader from "../components/common/PageHeader";
import StatusBadge from "../components/common/StatusBadge";
import type { StrategyCard, StrategyId } from "../types/dashboard";

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<StrategyCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<StrategyId | null>(null);

  useEffect(() => {
    loadStrategies();
  }, []);

  async function loadStrategies() {
    try {
      setLoading(true);
      setError(null);
      const payload = await fetchStrategies();
      setStrategies(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load strategies.");
    } finally {
      setLoading(false);
    }
  }

  async function handleAction(strategyId: StrategyId, action: "start" | "stop") {
    try {
      setBusyId(strategyId);
      const updated = action === "start" ? await startStrategy(strategyId) : await stopStrategy(strategyId);
      setStrategies((current) => current.map((item) => (item.strategyId === updated.strategyId ? updated : item)));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update strategy state.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <PageHeader
        title="Strategies"
        subtitle="Exactly three curated cards for the MVP, with persisted demo start and stop controls."
        actions={<button className="secondary-button" onClick={loadStrategies} type="button">Refresh</button>}
      />

      {error ? <div className="error-banner">{error}</div> : null}
      {loading ? <div className="loading-shell">Loading strategy cards...</div> : null}

      {!loading ? (
        <div className="strategies-grid">
          {strategies.map((strategy) => (
            <article className="strategy-card" key={strategy.strategyId}>
              <div className="strategy-card-header">
                <div className="strategy-meta">
                  <div className="badge-chip">{strategy.strategyType}</div>
                  <div>
                    <h3 className="card-title">{strategy.displayName}</h3>
                    <p className="card-subtitle">{strategy.description}</p>
                  </div>
                </div>
                <StatusBadge label={strategy.status} tone={strategy.status === "RUNNING" ? "running" : "stopped"} />
              </div>

              <div className="detail-list">
                <div className="detail-item"><span>Strategy ID</span><strong>{strategy.strategyId}</strong></div>
                <div className="detail-item"><span>Mode</span><strong>{strategy.mode}</strong></div>
                <div className="detail-item"><span>Last started</span><strong>{strategy.lastStartedAt ?? "-"}</strong></div>
                <div className="detail-item"><span>Last stopped</span><strong>{strategy.lastStoppedAt ?? "-"}</strong></div>
                <div className="detail-item"><span>Updated</span><strong>{strategy.updatedAt}</strong></div>
              </div>

              <div className="button-row">
                <button
                  className="primary-button"
                  disabled={busyId === strategy.strategyId || strategy.status === "RUNNING"}
                  onClick={() => handleAction(strategy.strategyId, "start")}
                  type="button"
                >
                  {busyId === strategy.strategyId ? "Working..." : "Start"}
                </button>
                <button
                  className="danger-button"
                  disabled={busyId === strategy.strategyId || strategy.status === "STOPPED"}
                  onClick={() => handleAction(strategy.strategyId, "stop")}
                  type="button"
                >
                  Stop
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </>
  );
}
