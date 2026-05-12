import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";

import { AuthProvider, useAuth } from "./context/AuthContext";
import AppShell from "./layout/AppShell";
import BacktestsPage from "./pages/BacktestsPage";
import DashboardPage from "./pages/DashboardPage";
import LoginPage from "./pages/LoginPage";
import ReportsPage from "./pages/ReportsPage";
import StrategiesPage from "./pages/StrategiesPage";
import LegacyEngineApp from "./legacy/LegacyEngineApp";

function ProtectedRoute() {
  const { session } = useAuth();
  const location = useLocation();

  if (!session) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}

function PublicOnlyRoute() {
  const { session } = useAuth();
  return session ? <Navigate to="/dashboard" replace /> : <Outlet />;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route element={<PublicOnlyRoute />}>
          <Route path="/login" element={<LoginPage />} />
        </Route>

        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/strategies" element={<StrategiesPage />} />
            <Route path="/backtests" element={<BacktestsPage />} />
            <Route path="/reports" element={<ReportsPage />} />
            <Route path="/legacy-engine" element={<LegacyEngineApp />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AuthProvider>
  );
}
