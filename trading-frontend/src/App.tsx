import { useContext } from "react";
import { StatusContext } from "./context/StatusProvider";

import IdleTradeEntry from "./components/screens/IdleTradeEntry";
import TradePreview from "./components/screens/TradePreview";
import OpenPosition from "./components/screens/OpenPosition";
import ClosingOverlay from "./components/screens/ClosingOverlay";
import ErrorView from "./components/screens/ErrorView";
import Loader from "./components/common/Loader";
import NetworkBanner from "./components/common/NetworkBanner";

export default function App() {
  const { status, loading, networkError } = useContext(StatusContext);

  // Shell loading state (NOT an engine state)
  if (loading || !status) {
    return <Loader />;
  }

  console.log("Engine State:", status.engine_state);
  return (
    <>
      <div style={{ color:"red"}}>APP IS RENDERING</div>
      {networkError && <NetworkBanner />}

      {status.engine_state === "IDLE" && <IdleTradeEntry status={status}/>}
      {status.engine_state === "PREVIEWING" && <TradePreview status={status} />}
      {status.engine_state === "OPEN" && <OpenPosition />}
      {status.engine_state === "CLOSING" && <ClosingOverlay />}
      {status.engine_state === "ERROR" && <ErrorView />}
    </>
  );
}
