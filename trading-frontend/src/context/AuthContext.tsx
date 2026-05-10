/* eslint-disable react-refresh/only-export-components */

import {
  createContext,
  startTransition,
  useContext,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react";

import { loginDemo } from "../api/dashboard";
import type { DemoUserSession } from "../types/dashboard";

const STORAGE_KEY = "algotrade-demo-session";

interface AuthContextValue {
  session: DemoUserSession | null;
  isLoading: boolean;
  loginError: string | null;
  login: (email: string, password: string) => Promise<boolean>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: PropsWithChildren) {
  const [session, setSession] = useState<DemoUserSession | null>(() => {
    const saved = sessionStorage.getItem(STORAGE_KEY);
    if (!saved) {
      return null;
    }

    try {
      return JSON.parse(saved) as DemoUserSession;
    } catch {
      sessionStorage.removeItem(STORAGE_KEY);
      return null;
    }
  });
  const [isLoading, setIsLoading] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);

  const value = useMemo<AuthContextValue>(
    () => ({
      session,
      isLoading,
      loginError,
      login: async (email, password) => {
        setIsLoading(true);
        setLoginError(null);

        try {
          const response = await loginDemo(email, password);
          if (!response.ok || !response.user) {
            setLoginError(response.message ?? "Unable to sign in.");
            return false;
          }

          startTransition(() => {
            setSession(response.user);
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(response.user));
          });
          return true;
        } catch (error) {
          const message = error instanceof Error ? error.message : "Unable to sign in.";
          setLoginError(message);
          return false;
        } finally {
          setIsLoading(false);
        }
      },
      logout: () => {
        startTransition(() => {
          setSession(null);
          sessionStorage.removeItem(STORAGE_KEY);
          sessionStorage.removeItem("algotrade-latest-backtest");
        });
      },
    }),
    [session, isLoading, loginError],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return context;
}
