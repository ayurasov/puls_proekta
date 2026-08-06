import { useState, useEffect, useCallback, createContext, useContext } from "react";
import { getMe, logout as apiLogout, login as apiLogin } from "../lib/api";
import { queryClient } from "../lib/queryClient";

interface AuthUser {
  id: number;
  username: string;
  name: string;
  email: string;
  role: string;
}

interface AuthCtx {
  user: AuthUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<{ ok: boolean; error?: string }>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthCtx>({
  user: null,
  loading: true,
  login: async () => ({ ok: false }),
  logout: async () => {},
  refresh: async () => {},
});

export function useAuthProvider() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await getMe();
      setUser(data.user || null);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = useCallback(async (username: string, password: string) => {
    try {
      const data = await apiLogin(username, password);
      if (data.user) {
        queryClient.clear();
        setUser(data.user);
        return { ok: true };
      }
      return { ok: false, error: data.error || "Ошибка входа" };
    } catch (err: any) {
      return { ok: false, error: "Неверный логин или пароль" };
    }
  }, []);

  const logout = useCallback(async () => {
    await apiLogout();
    queryClient.clear();
    setUser(null);
  }, []);

  return { user, loading, login, logout, refresh };
}

export { AuthContext };

export function useAuth() {
  return useContext(AuthContext);
}
