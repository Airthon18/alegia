import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Loader2, Sparkles } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";

function IconoGoogle() {
  return (
    <svg viewBox="0 0 48 48" className="h-5 w-5" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
      <path fill="#FBBC05" d="M10.53 28.59A14.5 14.5 0 0 1 9.5 24c0-1.59.28-3.14.76-4.59l-7.98-6.19A23.99 23.99 0 0 0 0 24c0 3.77.9 7.35 2.56 10.78l7.97-6.19z"/>
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
    </svg>
  );
}

export function AuthModal() {
  const { signInWithGoogle, continueAsGuest } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Si el usuario vuelve atrás desde la página de Google, el navegador puede restaurar esta pantalla
  // con el botón bloqueado: se desbloquea.
  useEffect(() => {
    const alVolver = (e: PageTransitionEvent) => {
      if (e.persisted) setLoading(false);
    };
    window.addEventListener("pageshow", alVolver);
    return () => window.removeEventListener("pageshow", alVolver);
  }, []);

  const entrarConGoogle = async () => {
    setError("");
    setLoading(true);
    const { error: e } = await signInWithGoogle();
    if (e) {
      // Sin error, el navegador ya se está yendo a Google
      setError("No se pudo iniciar sesión con Google. Inténtalo de nuevo.");
      setLoading(false);
    }
  };

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

            <h2 id="auth-titulo" className="auth-title">Inicia sesión</h2>

            {error && <div role="alert" className="auth-error">{error}</div>}

            <Button type="button" onClick={entrarConGoogle} disabled={loading} className="btn-google flex items-center justify-center gap-3">
              {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : <IconoGoogle />}
              Continuar con Google
            </Button>

            <div className="mt-5 text-center">
              <button type="button" className="auth-link" onClick={continueAsGuest} disabled={loading}>
                Entrar como invitado
              </button>
              <p className="text-slate-500 text-xs">Como invitado no se guarda tu historial.</p>
            </div>

            <p className="text-slate-500 text-[11px] leading-relaxed text-center mt-5">
              Al continuar, guardamos tus consultas junto con tu correo de Google para mostrarte tu historial.
            </p>
          </div>
        </div>
      </div>
    </>
  );
}