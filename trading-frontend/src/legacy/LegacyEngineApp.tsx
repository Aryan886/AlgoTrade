import { useContext } from "react";

import NetworkBanner from "../components/common/NetworkBanner";
import ClosingOverlay from "../components/screens/ClosingOverlay";
import ErrorView from "../components/screens/ErrorView";
import IdleTradeEntry from "../components/screens/IdleTradeEntry";
import OpenPosition from "../components/screens/OpenPosition";
import TradePreview from "../components/screens/TradePreview";
import Loader from "../components/common/Loader";
import { StatusContext, StatusProvider } from "../context/StatusProvider";

function LegacyEngineView() {
  const { status, loading, networkError } = useContext(StatusContext);

  if (loading || !status) {
    return <Loader />;
  }

  return (
    <>
      {networkError ? <NetworkBanner /> : null}
      {status.engine_state === "IDLE" ? <IdleTradeEntry status={status} /> : null}
      {status.engine_state === "PREVIEWING" ? <TradePreview status={status} /> : null}
      {status.engine_state === "OPEN" ? <OpenPosition /> : null}
      {status.engine_state === "CLOSING" ? <ClosingOverlay /> : null}
      {status.engine_state === "ERROR" ? <ErrorView /> : null}
    </>
  );
}

export default function LegacyEngineApp() {
  return (
    <div className="surface-card">
      <div className="card-title-row">
        <div>
          <h3 className="card-title">Legacy Engine Preview Flow</h3>
          <p className="card-subtitle">Preserved for compatibility while the new dashboard becomes the default shell.</p>
        </div>
      </div>
      <StatusProvider>
        <LegacyEngineView />
      </StatusProvider>
    </div>
  );
}
