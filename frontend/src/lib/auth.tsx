import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getToken, setToken } from "./api";

export type Me = {
  user: { id: string; email: string; full_name: string; role: string; email_verified: boolean };
  merchant: { id: string; name: string; is_demo: boolean; onboarding_complete: boolean; website?: string; industry?: string; default_currency: string; business_type?: string; timezone?: string };
};

type Ctx = { me: Me | null; loading: boolean; signIn: (token: string, me: Me) => void; signOut: () => void; refresh: () => Promise<void> };
const AuthCtx = createContext<Ctx>(null as unknown as Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(!!getToken());
  const refresh = useCallback(async () => {
    if (!getToken()) { setMe(null); setLoading(false); return; }
    try { setMe(await api<Me>("/api/auth/me")); } catch { setMe(null); } finally { setLoading(false); }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    const h = () => setMe(null);
    window.addEventListener("recoverai:logout", h);
    return () => window.removeEventListener("recoverai:logout", h);
  }, []);
  const signIn = (token: string, m: Me) => { setToken(token); setMe(m); };
  const signOut = () => { setToken(null); setMe(null); };
  return <AuthCtx.Provider value={{ me, loading, signIn, signOut, refresh }}>{children}</AuthCtx.Provider>;
}
export const useAuth = () => useContext(AuthCtx);
