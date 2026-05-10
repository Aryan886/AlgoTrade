import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../context/AuthContext";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/strategies", label: "Strategies" },
  { to: "/backtests", label: "Backtests" },
  { to: "/reports", label: "Reports" },
];

export default function AppShell() {
  const { session, logout } = useAuth();
  const location = useLocation();

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">AT</div>
          <div className="brand-copy">
            <h1>AlgoTrade Demo</h1>
            <p>NIFTY dashboard MVP with live-ready backtests and presentation-safe controls.</p>
          </div>
        </div>

        <nav className="sidebar-nav">
          <div className="nav-section-label">Workspace</div>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="nav-section-label">Utility</div>
          <NavLink
            to="/legacy-engine"
            className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
          >
            Legacy Engine View
          </NavLink>
        </div>
      </aside>

      <div className="main-shell">
        <header className="topbar">
          <div>
            <div className="topbar-title">
              {location.pathname === "/legacy-engine" ? "Legacy Engine Screen" : "College Demo Dashboard"}
            </div>
            <div className="meta-copy">Primary focus: NIFTY strategy controls, backtests, and showcase reporting.</div>
          </div>
          <div className="topbar-meta">
            <div className="user-pill">{session?.name ?? "Demo User"}</div>
            <button className="logout-button" onClick={logout} type="button">
              Sign out
            </button>
          </div>
        </header>

        <main className="page-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
