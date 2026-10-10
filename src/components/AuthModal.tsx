import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { Loader2, Sparkles } from "lucide-react";

export function AuthModal() {
  const [isLogin, setIsLogin] = useState(true);
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState({ fullName: "", email: "", password: "", confirmPassword: "" });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    if (!isLogin && form.password !== form.confirmPassword) {
      toast.error("Las contraseñas no coinciden");
      setLoading(false);
      return;
    }

    // MODO PRUEBA: acepta cualquier email y contraseña sin base de datos
    const nombreMostrar = form.fullName || form.email.split("@")[0];
    const usuarioPrueba = { id: 1, email: form.email, full_name: nombreMostrar };

    toast.success(isLogin ? "¡Bienvenido de vuelta a alegIA!" : "Cuenta creada exitosamente. ¡Bienvenido a alegIA!");
    localStorage.setItem("user", JSON.stringify(usuarioPrueba));
    localStorage.removeItem("signed_out");

    setTimeout(() => {
      setLoading(false);
      window.location.reload();
    }, 800);
  };

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@500;700;800&family=Inter:wght@400;500;600&display=swap');

        .auth-overlay {
          position: fixed;
          inset: 0;
          z-index: 100;
          background-color: rgba(5, 5, 10, 0.85);
          backdrop-filter: blur(12px);
          -webkit-backdrop-filter: blur(12px);
          display: flex;
          align-items: center;
          justify-content: center;
          font-family: 'Inter', sans-serif;
        }

        .auth-glass-panel {
          width: 100%;
          max-width: 420px;
          margin: 0 20px;
          background: rgba(255, 255, 255, 0.03);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 24px;
          padding: 40px 32px;
          box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5), 
                      0 0 40px rgba(139, 92, 246, 0.1);
          position: relative;
          overflow: hidden;
        }

        /* Destello decorativo de fondo en el panel */
        .auth-glass-panel::before {
          content: '';
          position: absolute;
          top: -50%; left: -50%;
          width: 200%; height: 200%;
          background: radial-gradient(circle at 50% 0%, rgba(217, 70, 239, 0.1), transparent 50%);
          pointer-events: none;
          z-index: 0;
        }

        .auth-content {
          position: relative;
          z-index: 1;
        }

        .auth-brand-text {
          font-family: 'Outfit', sans-serif;
          font-size: 2.2rem;
          font-weight: 800;
          background: linear-gradient(135deg, #A78BFA 0%, #E879F9 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          letter-spacing: -0.02em;
        }

        .auth-title {
          font-family: 'Outfit', sans-serif;
          font-size: 1.5rem;
          font-weight: 700;
          color: white;
          margin-bottom: 24px;
        }

        /* Inputs estilizados para sobreescribir shadcn/ui por defecto en este modal */
        .input-glass {
          background: rgba(0, 0, 0, 0.2) !important;
          border: 1px solid rgba(255, 255, 255, 0.1) !important;
          color: white !important;
          height: 48px !important;
          border-radius: 12px !important;
          transition: all 0.2s ease !important;
        }
        .input-glass:focus {
          border-color: #D946EF !important;
          box-shadow: 0 0 0 2px rgba(217, 70, 239, 0.2) !important;
          background: rgba(0, 0, 0, 0.4) !important;
        }
        .input-glass::placeholder {
          color: rgba(255, 255, 255, 0.3) !important;
        }

        .label-glass {
          color: #E2E8F0 !important;
          font-weight: 500 !important;
          margin-bottom: 8px !important;
          display: block;
        }

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
          margin-top: 10px !important;
        }
        .btn-auth:hover {
          box-shadow: 0 10px 25px rgba(217, 70, 239, 0.4) !important;
          transform: translateY(-2px);
        }
        .btn-auth:active {
          transform: translateY(0);
        }

        .auth-switch {
          background: transparent;
          border: none;
          color: #A78BFA;
          font-size: 0.9rem;
          font-weight: 500;
          cursor: pointer;
          transition: color 0.2s;
        }
        .auth-switch:hover {
          color: #E879F9;
          text-decoration: underline;
        }
      `}</style>

      <div className="auth-overlay">
        <div className="auth-glass-panel">
          <div className="auth-content">
            
            {/* Header del Modal */}
            <div className="text-center mb-8">
              <div className="flex items-center justify-center gap-3 mb-2">
                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-violet-500 to-fuchsia-500 flex items-center justify-center shadow-[0_0_20px_rgba(217,70,239,0.4)]">
                  <Sparkles className="h-6 w-6 text-white" />
                </div>
                <h1 className="auth-brand-text">alegIA</h1>
              </div>
              <p className="text-slate-400 text-sm font-light">
                Tu motor de análisis deportivo inteligente
              </p>
            </div>

            <h2 className="auth-title">
              {isLogin ? "Iniciar sesión" : "Crear cuenta"}
            </h2>

            {/* Formulario */}
            <form onSubmit={handleSubmit} className="space-y-4">
              {!isLogin && (
                <div>
                  <Label htmlFor="fullName" className="label-glass">Nombre completo</Label>
                  <Input
                    id="fullName"
                    value={form.fullName}
                    onChange={(e) => setForm({ ...form, fullName: e.target.value })}
                    placeholder="Ej. Ana García"
                    required={!isLogin}
                    className="input-glass"
                  />
                </div>
              )}
              <div>
                <Label htmlFor="email" className="label-glass">Correo electrónico</Label>
                <Input
                  id="email"
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  placeholder="tu@correo.com"
                  required
                  className="input-glass"
                />
              </div>
              <div>
                <Label htmlFor="password" className="label-glass">Contraseña</Label>
                <Input
                  id="password"
                  type="password"
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  placeholder="••••••••"
                  required
                  minLength={6}
                  className="input-glass"
                />
              </div>
              {!isLogin && (
                <div>
                  <Label htmlFor="confirmPassword" className="label-glass">Confirmar contraseña</Label>
                  <Input
                    id="confirmPassword"
                    type="password"
                    value={form.confirmPassword}
                    onChange={(e) => setForm({ ...form, confirmPassword: e.target.value })}
                    placeholder="••••••••"
                    required={!isLogin}
                    className="input-glass"
                  />
                </div>
              )}
              
              <Button type="submit" className="w-full btn-auth" disabled={loading}>
                {loading && <Loader2 className="mr-2 h-5 w-5 animate-spin" />}
                {isLogin ? "Entrar ahora" : "Registrarme"}
              </Button>
            </form>

            {/* Toggle Login/Registro */}
            <div className="mt-6 text-center">
              <button
                type="button"
                onClick={() => {
                  setIsLogin(!isLogin);
                  setForm({ fullName: "", email: "", password: "", confirmPassword: "" });
                }}
                className="auth-switch"
              >
                {isLogin ? "¿No tienes cuenta? Regístrate gratis" : "¿Ya tienes cuenta? Inicia sesión"}
              </button>
            </div>

          </div>
        </div>
      </div>
    </>
  );
}