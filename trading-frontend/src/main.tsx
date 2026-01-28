import React  from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { StatusProvider } from "./context/StatusProvider";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <StatusProvider>
      <App />
    </StatusProvider>
  </React.StrictMode>
)