import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "@/integrations/supabase/client";

export interface User {
  id: string; // uuid de Supabase ("invitado" si entró sin cuenta)
  email: string;
}

interface Profile {
  full_name: string;
  plan: string;
  created_at: string;
  avatar_url?: string;
}

interface AuthContextType {
  user: User | null;
  loading: boolean;
  isAdmin: boolean;
  bypassAuth: boolean; // true = entró como invitado (sin cuenta, no se guarda historial)
  profile: Profile | null;
  signInWithGoogle: () => Promise<{ error: string | null }>;
  continueAsGuest: () => void;
  signIn: (email: string, password: string) => Promise<ResultadoAuth>;
  signUp: (email: string, password: string, fullName: string) => Promise<ResultadoAuth>;
  verifyCode: (email: string, code: string) => Promise<ResultadoAuth>; // código de 6 dígitos que llega al correo
  resendCode: (email: string) => Promise<ResultadoAuth>;
  signOut: () => Promise<void>;
}

export interface ResultadoAuth {
  error: string | null;
  needsVerification?: boolean; // hay que escribir el código que llegó al correo
  alreadyRegistered?: boolean; // ese correo ya tiene cuenta
}

// Mensajes de Supabase -> español
function traducir(e: { message?: string; code?: string } | null): string | null {
  if (!e) return null;
  const c = e.code ?? "";
  const m = (e.message ?? "").toLowerCase();
  if (c === "invalid_credentials" || m.includes("invalid login credentials")) return "Correo o contraseña incorrectos.";
  if (c === "user_already_exists" || m.includes("already registered")) return "Este correo ya está registrado. Inicia sesión.";
  if (c === "weak_password" || m.includes("password should")) return "La contraseña es muy débil. Usa al menos 8 caracteres.";
  if (c === "over_email_send_rate_limit" || m.includes("rate limit") || m.includes("security purposes"))
    return "Se enviaron demasiados correos. Espera unos minutos e inténtalo de nuevo.";
  if (c === "otp_expired" || m.includes("expired") || m.includes("invalid")) return "El código es incorrecto o ya venció.";
  if (c === "validation_failed" && m.includes("provider")) return "El acceso con Google aún no está activado.";
  if (m.includes("failed to fetch") || m.includes("network")) return "Sin conexión con el servidor.";
  return "No se pudo completar la acción. Inténtalo de nuevo.";
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const GUEST_KEY = "alegia_guest_mode";
const GUEST_USER: User = { id: "invitado", email: "invitado@predibet.com" };

const leerInvitado = () => {
  try {
    return localStorage.getItem(GUEST_KEY) === "true";
  } catch {
    return false;
  }
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [bypassAuth, setBypassAuth] = useState(false);
  const [profile, setProfile] = useState<Profile | null>(null);

  // Refleja en el estado lo que diga Supabase (o el modo invitado si no hay sesión)
  const aplicar = useCallback((session: Session | null) => {
    const u = session?.user;
    if (u && u.email) {
      const meta = (u.user_metadata ?? {}) as Record<string, string | undefined>;
      try {
        localStorage.removeItem(GUEST_KEY);
      } catch {
        /* sin storage */
      }
      setUser({ id: u.id, email: u.email });
      setProfile({
        full_name: meta.full_name || meta.name || u.email.split("@")[0],
        plan: "Pro",
        created_at: u.created_at,
        avatar_url: meta.avatar_url || meta.picture,
      });
      setBypassAuth(false);
    } else if (leerInvitado()) {
      setUser(GUEST_USER);
      setProfile({ full_name: "INVITADO", plan: "Pro", created_at: new Date().toISOString() });
      setBypassAuth(true);
    } else {
      setUser(null);
      setProfile(null);
      setBypassAuth(false);
    }
  }, []);

  useEffect(() => {
    // El login de prueba anterior dejó usuarios falsos guardados en el navegador: se limpian.
    try {
      localStorage.removeItem("user");
      localStorage.removeItem("signed_out");
    } catch {
      /* sin storage */
    }

    let vivo = true;
    supabase.auth
      .getSession()
      .then(({ data }) => {
        if (vivo) aplicar(data.session);
      })
      .catch(() => {
        if (vivo) aplicar(null);
      })
      .finally(() => {
        if (vivo) setLoading(false);
      });

    // También cubre la vuelta desde Google, cerrar sesión y la renovación del token
    const { data: sub } = supabase.auth.onAuthStateChange((_evento, session) => aplicar(session));
    return () => {
      vivo = false;
      sub.subscription.unsubscribe();
    };
  }, [aplicar]);

  const signInWithGoogle = async () => {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: window.location.origin,
        queryParams: { prompt: "select_account" }, // deja elegir cuenta de Google
      },
    });
    return { error: error ? error.message : null };
  };

  const continueAsGuest = () => {
    try {
      localStorage.setItem(GUEST_KEY, "true");
    } catch {
      /* sin storage: el invitado dura hasta recargar */
    }
    aplicar(null);
  };

  const signOut = async () => {
    try {
      localStorage.removeItem(GUEST_KEY);
    } catch {
      /* sin storage */
    }
    try {
      await supabase.auth.signOut();
    } catch {
      /* si falla la red igualmente se cierra en este navegador */
    }
    aplicar(null);
  };

  const limpiar = (email: string) => email.trim().toLowerCase();

  const signUp = async (email: string, password: string, fullName: string): Promise<ResultadoAuth> => {
    const { data, error } = await supabase.auth.signUp({
      email: limpiar(email),
      password,
      options: { data: { full_name: fullName.trim() }, emailRedirectTo: window.location.origin },
    });
    if (error) return { error: traducir(error), alreadyRegistered: error.code === "user_already_exists" };
    // Con "Confirm email" activo, un correo ya registrado no da error: devuelve un usuario sin identidades
    if (data.user && Array.isArray(data.user.identities) && data.user.identities.length === 0) {
      return { error: "Este correo ya está registrado. Inicia sesión.", alreadyRegistered: true };
    }
    if (data.session) return { error: null }; // la confirmación por correo está desactivada: ya entró
    return { error: null, needsVerification: true };
  };

  const signIn = async (email: string, password: string): Promise<ResultadoAuth> => {
    const { error } = await supabase.auth.signInWithPassword({ email: limpiar(email), password });
    if (error) {
      if (error.code === "email_not_confirmed" || /not confirmed/i.test(error.message ?? "")) {
        return { error: null, needsVerification: true }; // se registró pero nunca puso el código
      }
      return { error: traducir(error) };
    }
    return { error: null };
  };

  const verifyCode = async (email: string, code: string): Promise<ResultadoAuth> => {
    const { error } = await supabase.auth.verifyOtp({ email: limpiar(email), token: code.trim(), type: "signup" });
    return { error: traducir(error) }; // si va bien, onAuthStateChange inicia la sesión
  };

  const resendCode = async (email: string): Promise<ResultadoAuth> => {
    const { error } = await supabase.auth.resend({ type: "signup", email: limpiar(email) });
    return { error: traducir(error) };
  };

  return (
    <AuthContext.Provider
      value={{ user, loading, isAdmin: false, bypassAuth, profile, signInWithGoogle, continueAsGuest, signIn, signUp, verifyCode, resendCode, signOut }}
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