import { createContext, useContext, useEffect, useState, ReactNode } from "react";

export interface User {
  id: number;
  email: string;
}

interface AuthContextType {
  user: User | null;
  loading: boolean;
  isAdmin: boolean;
  bypassAuth: boolean; // true = entró sin login (invitado), false = cerró sesión manualmente
  profile: { full_name: string; plan: string; created_at: string } | null;
  signIn: (email: string, password: string) => Promise<{ error: string | null }>;
  signUp: (email: string, password: string, fullName: string) => Promise<{ error: string | null }>;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const GUEST_USER: User = { id: 0, email: "invitado@predibet.com" };
const GUEST_PROFILE = {
  full_name: "INVITADO",
  plan: "Pro",
  created_at: new Date().toISOString(),
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [bypassAuth, setBypassAuth] = useState(false);
  const [profile, setProfile] = useState<AuthContextType["profile"]>(null);

  useEffect(() => {
    const storedUser = localStorage.getItem("user");
    const didSignOut = localStorage.getItem("signed_out") === "true";

    if (didSignOut) {
      // El usuario cerró sesión manualmente → mostrar login
      setBypassAuth(false);
      setUser(null);
    } else if (storedUser) {
      // Hay sesión guardada → usarla
      try {
        const parsedUser = JSON.parse(storedUser);
        setUser(parsedUser);
        setProfile({
          full_name: parsedUser.email.split("@")[0].toUpperCase(),
          plan: "Pro",
          created_at: new Date().toISOString(),
        });
        setIsAdmin(false);
        setBypassAuth(false);
      } catch (e) {
        setUser(GUEST_USER);
        setProfile(GUEST_PROFILE);
        setBypassAuth(true);
      }
    } else {
      // Sin sesión y sin cierre manual → entra como invitado
      setUser(GUEST_USER);
      setProfile(GUEST_PROFILE);
      setBypassAuth(true);
    }

    setLoading(false);
  }, []);

  const signIn = async () => ({ error: null });
  const signUp = async () => ({ error: null });

  const signOut = () => {
    localStorage.removeItem("user");
    localStorage.setItem("signed_out", "true"); // marca que cerró sesión a propósito
    setUser(null);
    setProfile(null);
    setBypassAuth(false);
    window.location.reload();
  };

  return (
    <AuthContext.Provider value={{ user, loading, isAdmin, bypassAuth, profile, signIn, signUp, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}