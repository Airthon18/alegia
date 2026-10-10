import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { ArrowLeft, Eye, EyeOff, Loader2, LogIn, MailCheck, Sparkles } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";

type Vista = "login" | "registro" | "codigo";

const MIN_PASSWORD = 8;
const ESPERA_REENVIO = 60; // segundos entre un reenvío de código y el siguiente
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function AuthModal() {
  const { signInWithGoogle, signIn, signUp, verifyCode, resendCode, continueAsGuest } = useAuth();
  const [vista, setVista] = useState<Vista>("login");
  const [loading, setLoading] = useState(false);
  const [verPassword, setVerPassword] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ fullName: "", email: "", password: "", confirmPassword: "" });
  const [codigo, setCodigo] = useState("");
  const [espera, setEspera] = useState(0);

  // Si el usuario vuelve atrás desde la página de Google, el navegador puede restaurar esta pantalla
  // tal como estaba (con los botones bloqueados): se desbloquea.
  useEffect(() => {
    const alVolver = (e: PageTransitionEvent) => {
      if (e.persisted) setLoading(false);
    };
    window.addEventListener("pageshow", alVolver);
    return () => window.removeEventListener("pageshow", alVolver);
  }, []);

  // Cuenta atrás para poder pedir otro código
  useEffect(() => {
    if (espera <= 0) return;
    const t = setTimeout(() => setEspera((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [espera]);

  const irA = (v: Vista) => {
    setVista(v);
    setError("");
    setCodigo("");
    setVerPassword(false);
  };

  const pedirCodigo = () => {
    setEspera(ESPERA_REENVIO);
    irA("codigo");
  };

  const entrarConGoogle = async () => {
    setError("");
    setLoading(true);
    const { error: e } = await signInWithGoogle();
    if (e) {
      // Sin error, el navegador ya se está yendo a Google y no hay nada más que hacer aquí
      setError("No se pudo iniciar sesión con Google. Inténtalo de nuevo.");
      setLoading(false);
    }
  };

  const enviarFormulario = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setError("");

    if (!EMAIL_RE.test(form.email.trim())) return setError("Escribe un correo válido.");
    if (vista === "registro") {
      if (form.fullName.trim().length < 2) return setError("Escribe tu nombre.");
      if (form.password.length < MIN_PASSWORD) return setError(`La contraseña debe tener al menos ${MIN_PASSWORD} caracteres.`);
      if (form.password !== form.confirmPassword) return setError("Las contraseñas no coinciden.");
    }

    setLoading(true);
    try {
      if (vista === "registro") {
        const r = await signUp(form.email, form.password, form.fullName);
        if (r.alreadyRegistered) {
          setError(r.error ?? "Este correo ya está registrado. Inicia sesión.");
          setVista("login");
        } else if (r.error) setError(r.error);
        else if (r.needsVerification) pedirCodigo();
        // sin error ni verificación: la sesión ya está iniciada y la app cambia sola
      } else {
        const r = await signIn(form.email, form.password);
        if (r.needsVerification) {
          // Se había registrado pero nunca confirmó el correo: se le manda un código nuevo
          const re = await resendCode(form.email);
          if (re.error) setError(re.error);
          else {
            toast.info("Tu correo aún no está verificado. Te enviamos un código nuevo.");
            pedirCodigo();
          }
        } else if (r.error) setError(r.error);
      }
    } finally {
      setLoading(false);
    }
  };

  const verificar = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setError("");
    if (codigo.length < 6) return setError("Escribe el código que llegó a tu correo.");
    setLoading(true);
    const r = await verifyCode(form.email, codigo);
    if (r.error) {
      setError(r.error);
      setLoading(false);
    } else {
      toast.success("¡Correo verificado! Bienvenido a alegIA.");
      // la sesión se inicia sola; este modal desaparece
    }
  };

  const reenviar = async () => {
    if (espera > 0 || loading) return;
    setError("");
    const r = await resendCode(form.email);
    if (r.error) setError(r.error);
    else {
      toast.success("Código reenviado. Revisa tu bandeja y la carpeta de spam.");
      setEspera(ESPERA_REENVIO);
    }
  };

  const campo = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  const esRegistro = vista === "registro";

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@500;700;800&family=Inter:wght@400;500;600&display=swap');

        .auth-overlay {
          position: fixed;
          inset: 0;
          z-index: 100;
          overflow-y: auto;
          background-color: rgba(5, 5, 10, 0.92);
          background-image:
            radial-gradient(at 0% 0%, rgba(139, 92, 246, 0.18) 0px, transparent 50%),
            radial-gradient(at 100% 0%, rgba(217, 70, 239, 0.15) 0px, transparent 50%);
          backdrop-filter: blur(12px);
          -webkit-backdrop-filter: blur(12px);
          display: flex;
          justify-content: center;
          padding: 20px;
          font-family: 'Inter', sans-serif;
        }
        .auth-glass-panel {
          width: 100%;
          max-width: 420px;
          margin: auto;
          background: rgba(255, 255, 255, 0.03);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 24px;
          padding: 36px 24px;
          box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5), 0 0 40px rgba(139, 92, 246, 0.1);
          position: relative;
          overflow: hidden;
        }
        @media (min-width: 480px) { .auth-glass-panel { padding: 40px 32px; } }
        .auth-glass-panel::before {
          content: '';
          position: absolute;
          top: -50%; left: -50%;
          width: 200%; height: 200%;
          background: radial-gradient(circle at 50% 0%, rgba(217, 70, 239, 0.1), transparent 50%);
          pointer-events: none;
          z-index: 0;
        }
        .auth-content { position: relative; z-index: 1; }

        .auth-brand-text {
          font-family: 'Outfit', sans-serif;
          font-size: 2.2rem;
          font-weight: 800;
          background: linear-gradient(135deg, #A78BFA 0%, #E879F9 100%);
          -webkit-background-clip: text;
          background-clip: text;
          -webkit-text-fill-color: transparent;
          letter-spacing: -0.02em;
        }
        .auth-title {
          font-family: 'Outfit', sans-serif;
          font-size: 1.5rem;
          font-weight: 700;
          color: white;
          margin-bottom: 20px;
          text-align: center;
        }

        .btn-google {
          background: #ffffff !important;
          color: #1f1f1f !important;
          font-size: 1rem !important;
          font-weight: 600 !important;
          height: 50px !important;
          width: 100%;
          border-radius: 14px !important;
          border: none !important;
          box-shadow: 0 8px 20px rgba(0, 0, 0, 0.3) !important;
          transition: transform .2s ease, box-shadow .2s ease !important;
        }
        .btn-google:active { transform: scale(0.98); }
        @media (hover: hover) { .btn-google:hover { box-shadow: 0 10px 28px rgba(217, 70, 239, 0.35) !important; transform: translateY(-1px); } }

        .auth-divider { display: flex; align-items: center; gap: 12px; margin: 20px 0; color: #64748b; font-size: 0.8rem; }
        .auth-divider::before, .auth-divider::after { content: ''; flex: 1; height: 1px; background: rgba(255,255,255,0.1); }

        .input-glass {
          background: rgba(0, 0, 0, 0.2) !important;
          border: 1px solid rgba(255, 255, 255, 0.1) !important;
          color: white !important;
          height: 48px !important;
          border-radius: 12px !important;
          font-size: 16px !important; /* evita el zoom automático en iPhone */
          transition: all 0.2s ease !important;
        }
        .input-glass:focus {
          border-color: #D946EF !important;
          box-shadow: 0 0 0 2px rgba(217, 70, 239, 0.2) !important;
          background: rgba(0, 0, 0, 0.4) !important;
        }
        .input-glass::placeholder { color: rgba(255, 255, 255, 0.3) !important; }
        .input-codigo { text-align: center; letter-spacing: 0.5em; font-size: 1.6rem !important; font-weight: 700; height: 60px !important; padding-left: 0.5em; }

        .label-glass { color: #E2E8F0 !important; font-weight: 500 !important; margin-bottom: 8px !important; display: block; }

        .btn-auth {
          background: linear-gradient(135deg, #8B5CF6 0%, #D946EF 100%) !important;
          color: white !important;
          font-family: 'Outfit', sans-serif !important;
          font-size: 1.1rem !important;
          font-weight: 700 !important;
          height: 52px !important;
          border-radius: 14px !important;
          border: none !important;
          box-shadow: 0 8px 20px rgba(217, 70, 239, 0.3) !important;
          transition: all 0.3s ease !important;
          margin-top: 16px !important;
        }
        @media (hover: hover) { .btn-auth:hover { box-shadow: 0 10px 25px rgba(217, 70, 239, 0.4) !important; transform: translateY(-2px); } }
        .btn-auth:active { transform: translateY(0); }

        .auth-link {
          background: transparent;
          border: none;
          color: #A78BFA;
          font-size: 0.9rem;
          font-weight: 500;
          cursor: pointer;
          padding: 6px;
          transition: color 0.2s;
        }
        .auth-link:hover { color: #E879F9; text-decoration: underline; }
        .auth-link:disabled { opacity: .5; cursor: not-allowed; text-decoration: none; }

        .auth-error { background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.35); color: #fca5a5; border-radius: 12px; padding: 10px 14px; font-size: 0.875rem; margin-bottom: 14px; }
      `}</style>

      <div className="auth-overlay">
        <div className="auth-glass-panel" role="dialog" aria-labelledby="auth-titulo">
          <div className="auth-content">
            <div className="text-center mb-6">
              <div className="flex items-center justify-center gap-3 mb-2">
                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-violet-500 to-fuchsia-500 flex items-center justify-center shadow-[0_0_20px_rgba(217,70,239,0.4)]">
                  <Sparkles className="h-6 w-6 text-white" />
                </div>
                <h1 className="auth-brand-text">alegIA</h1>
              </div>
              <p className="text-slate-400 text-sm font-light">Tu motor de análisis deportivo inteligente</p>
            </div>

            {/* ───────── Vista: código de verificación ───────── */}
            {vista === "codigo" ? (
              <form onSubmit={verificar} className="space-y-4">
                <div className="flex justify-center">
                  <div className="w-14 h-14 rounded-full bg-violet-500/15 flex items-center justify-center">
                    <MailCheck className="h-7 w-7 text-violet-300" />
                  </div>
                </div>
                <h2 id="auth-titulo" className="auth-title" style={{ marginBottom: 4 }}>Revisa tu correo</h2>
                <p className="text-slate-400 text-sm text-center">
                  Enviamos un código a <strong className="text-white break-all">{form.email.trim().toLowerCase()}</strong>. Escríbelo para verificar tu cuenta.
                </p>

                <Input
                  value={codigo}
                  onChange={(e) => setCodigo(e.target.value.replace(/\D/g, "").slice(0, 8))}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  placeholder="······"
                  aria-label="Código de verificación"
                  autoFocus
                  className="input-glass input-codigo"
                />

                {error && <div role="alert" className="auth-error">{error}</div>}

                <Button type="submit" className="w-full btn-auth" disabled={loading || codigo.length < 6}>
                  {loading && <Loader2 className="mr-2 h-5 w-5 animate-spin" />}
                  Verificar y entrar
                </Button>

                <div className="text-center space-y-1">
                  <div>
                    <button type="button" className="auth-link" onClick={reenviar} disabled={espera > 0 || loading}>
                      {espera > 0 ? `Reenviar código en ${espera}s` : "Reenviar código"}
                    </button>
                  </div>
                  <button type="button" className="auth-link flex items-center gap-1 mx-auto" onClick={() => irA("registro")}>
                    <ArrowLeft className="h-4 w-4" /> Cambiar de correo
                  </button>
                  <p className="text-slate-500 text-xs">¿No llega? Mira en spam o correo no deseado.</p>
                </div>
              </form>
            ) : (
              <>
                {/* ───────── Vistas: iniciar sesión / crear cuenta ───────── */}
                <h2 id="auth-titulo" className="auth-title">{esRegistro ? "Crear cuenta" : "Iniciar sesión"}</h2>

                <Button type="button" onClick={entrarConGoogle} disabled={loading} className="btn-google flex items-center justify-center gap-3">
                  {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : <LogIn className="h-5 w-5" />}
                  Continuar con Google
                </Button>

                <div className="auth-divider">o con tu correo</div>

                {error && <div role="alert" className="auth-error">{error}</div>}

                <form onSubmit={enviarFormulario} className="space-y-4" noValidate>
                  {esRegistro && (
                    <div>
                      <Label htmlFor="fullName" className="label-glass">Nombre completo</Label>
                      <Input id="fullName" value={form.fullName} onChange={campo("fullName")} placeholder="Ej. Ana García" autoComplete="name" className="input-glass" />
                    </div>
                  )}
                  <div>
                    <Label htmlFor="email" className="label-glass">Correo electrónico</Label>
                    <Input id="email" type="email" value={form.email} onChange={campo("email")} placeholder="tu@correo.com" autoComplete="email" className="input-glass" />
                  </div>
                  <div>
                    <Label htmlFor="password" className="label-glass">Contraseña</Label>
                    <div className="relative">
                      <Input
                        id="password"
                        type={verPassword ? "text" : "password"}
                        value={form.password}
                        onChange={campo("password")}
                        placeholder="••••••••"
                        autoComplete={esRegistro ? "new-password" : "current-password"}
                        className="input-glass pr-12"
                      />
                      <button
                        type="button"
                        onClick={() => setVerPassword((v) => !v)}
                        aria-label={verPassword ? "Ocultar contraseña" : "Mostrar contraseña"}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white"
                      >
                        {verPassword ? <EyeOff className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
                      </button>
                    </div>
                    {esRegistro && <p className="text-slate-500 text-xs mt-1">Mínimo {MIN_PASSWORD} caracteres.</p>}
                  </div>
                  {esRegistro && (
                    <div>
                      <Label htmlFor="confirmPassword" className="label-glass">Confirmar contraseña</Label>
                      <Input
                        id="confirmPassword"
                        type={verPassword ? "text" : "password"}
                        value={form.confirmPassword}
                        onChange={campo("confirmPassword")}
                        placeholder="••••••••"
                        autoComplete="new-password"
                        className="input-glass"
                      />
                    </div>
                  )}

                  <Button type="submit" className="w-full btn-auth" disabled={loading}>
                    {loading && <Loader2 className="mr-2 h-5 w-5 animate-spin" />}
                    {esRegistro ? "Registrarme" : "Entrar"}
                  </Button>
                </form>

                <div className="mt-5 text-center space-y-1">
                  <button type="button" className="auth-link" onClick={() => irA(esRegistro ? "login" : "registro")}>
                    {esRegistro ? "¿Ya tienes cuenta? Inicia sesión" : "¿No tienes cuenta? Regístrate gratis"}
                  </button>
                  <div>
                    <button type="button" className="auth-link" onClick={continueAsGuest} disabled={loading}>
                      Entrar como invitado
                    </button>
                    <p className="text-slate-500 text-xs">Como invitado no se guarda tu historial.</p>
                  </div>
                </div>

                <p className="text-slate-500 text-[11px] leading-relaxed text-center mt-5">
                  Al continuar, guardamos tus consultas junto con tu correo para mostrarte tu historial.
                </p>
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
}