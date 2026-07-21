"use client";

import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from "react";

interface User {
  userId: string;
  username: string;
  tenantId: string;
  email?: string;
}

interface AuthContextType {
  user: User | null;
  accessToken: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, email?: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | null>(null);

const AUTH_API = "/api/auth";

function storeTokens(accessToken: string, refreshToken: string) {
  localStorage.setItem("access_token", accessToken);
  localStorage.setItem("refresh_token", refreshToken);
}

function clearTokens() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Fetch current user with a valid token
  const fetchCurrentUser = useCallback(async (token: string): Promise<User | null> => {
    try {
      const res = await fetch(`${AUTH_API}/me`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }, []);

  // Try refresh token to get a new access token
  const tryRefresh = useCallback(async (): Promise<string | null> => {
    const refreshToken = localStorage.getItem("refresh_token");
    if (!refreshToken) return null;

    try {
      const res = await fetch(`${AUTH_API}/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refreshToken }),
      });
      if (!res.ok) return null;
      const data = await res.json();
      storeTokens(data.accessToken, data.refreshToken);
      return data.accessToken;
    } catch {
      return null;
    }
  }, []);

  // On mount: try existing token, then refresh
  useEffect(() => {
    const initAuth = async () => {
      const stored = localStorage.getItem("access_token");
      if (stored) {
        const u = await fetchCurrentUser(stored);
        if (u) {
          setAccessToken(stored);
          setUser(u);
          setIsLoading(false);
          return;
        }
        // Token expired, try refresh
        const newToken = await tryRefresh();
        if (newToken) {
          const u2 = await fetchCurrentUser(newToken);
          if (u2) {
            setAccessToken(newToken);
            setUser(u2);
          }
        }
      }
      setIsLoading(false);
    };
    initAuth();
  }, [fetchCurrentUser, tryRefresh]);

  const login = useCallback(async (username: string, password: string) => {
    const res = await fetch(`${AUTH_API}/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || "登录失败");
    }

    const data = await res.json();
    storeTokens(data.accessToken, data.refreshToken);
    setAccessToken(data.accessToken);

    const u = await fetchCurrentUser(data.accessToken);
    if (u) setUser(u);
  }, [fetchCurrentUser]);

  const register = useCallback(async (username: string, password: string, email?: string) => {
    const res = await fetch(`${AUTH_API}/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, email }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || "注册失败");
    }

    const data = await res.json();
    storeTokens(data.accessToken, data.refreshToken);
    setAccessToken(data.accessToken);

    const u = await fetchCurrentUser(data.accessToken);
    if (u) setUser(u);
  }, [fetchCurrentUser]);

  const logout = useCallback(() => {
    clearTokens();
    setAccessToken(null);
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        accessToken,
        isAuthenticated: !!user,
        isLoading,
        login,
        register,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
