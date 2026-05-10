import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "../context/AuthContext";

export default function LoginPage() {
  const { login, isLoading, loginError } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("demo@algotrade.local");
  const [password, setPassword] = useState("demo123");

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const ok = await login(email, password);
    if (ok) {
      navigate("/dashboard", { replace: true });
    }
  }

  return (
    <div className="login-screen">
      <section className="login-panel">
        <div>
          <h1>AlgoTrade College Demo</h1>
          <p>Sign in to the routed MVP dashboard for strategies, live-ready backtests, and showcase reports.</p>
        </div>

        <div className="login-credentials">
          <strong>Demo credentials</strong>
          <div className="mono">demo@algotrade.local</div>
          <div className="mono">demo123</div>
        </div>

        <form className="form-grid" onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="email">Email</label>
            <input id="email" value={email} onChange={(event) => setEmail(event.target.value)} />
          </div>

          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>

          {loginError ? <div className="error-banner">{loginError}</div> : null}

          <button className="primary-button" disabled={isLoading} type="submit">
            {isLoading ? "Signing in..." : "Enter dashboard"}
          </button>
        </form>
      </section>
    </div>
  );
}
