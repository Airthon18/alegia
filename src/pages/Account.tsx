import { useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { Button } from "@/components/ui/button";
import { User, CreditCard, CheckCircle2, Crown, CalendarDays, Mail, Sparkles, Zap } from "lucide-react";
import { toast } from "sonner";

const benefits = [
  "Predicciones de IA ilimitadas",
  "Alertas en tiempo real",
  "Estadísticas avanzadas de equipos",
  "Acceso prioritario a alegIA",
  "Análisis de cuotas de valor (Value Bets)",
  "Soporte VIP 24/7",
];

export default function Account() {
  const { user, profile } = useAuth();
  const [showConfirm, setShowConfirm] = useState(false);

  const handleSubscribe = () => {
    setShowConfirm(true);
    setTimeout(() => {
      toast.success("¡Suscripción activada! Bienvenido al nivel Pro de alegIA.");
      setShowConfirm(false);
    }, 1500);
  };

  return (
    <>
      <style>{`
        .account-container {
          padding: 24px 16px 100px;
          max-width: 1000px;
          margin: 0 auto;
          width: 100%;
        }
        @media (min-width: 768px) {
          .account-container { padding: 40px 24px; }
        }

        .page-title {
          font-family: 'Outfit', sans-serif;
          font-size: 2rem;
          font-weight: 800;
          color: white;
          margin-bottom: 24px;
          display: flex;
          align-items: center;
          gap: 12px;
        }

        .glass-card {
          background: rgba(255, 255, 255, 0.02);
          border: 1px solid rgba(255, 255, 255, 0.06);
          backdrop-filter: blur(12px);
          -webkit-backdrop-filter: blur(12px);
          border-radius: 24px;
          padding: 24px;
          position: relative;
          overflow: hidden;
        }

        .glass-card-pro {
          background: linear-gradient(180deg, rgba(139, 92, 246, 0.05) 0%, rgba(217, 70, 239, 0.02) 100%);
          border: 1px solid rgba(217, 70, 239, 0.2);
          box-shadow: 0 10px 40px -10px rgba(217, 70, 239, 0.15);
        }

        /* Destello para la tarjeta Pro */
        .glass-card-pro::before {
          content: '';
          position: absolute;
          top: 0; right: 0;
          width: 150px; height: 150px;
          background: radial-gradient(circle, rgba(217, 70, 239, 0.2) 0%, transparent 70%);
          filter: blur(20px);
          pointer-events: none;
        }

        .icon-box {
          width: 48px; height: 48px;
          border-radius: 14px;
          display: flex; align-items: center; justify-content: center;
          background: rgba(255, 255, 255, 0.05);
          border: 1px solid rgba(255, 255, 255, 0.1);
          color: #A78BFA;
        }

        .icon-box-pro {
          background: linear-gradient(135deg, rgba(139, 92, 246, 0.2), rgba(217, 70, 239, 0.2));
          border: 1px solid rgba(217, 70, 239, 0.3);
          color: #E879F9;
        }

        .info-label {
          font-size: 0.75rem;
          font-weight: 600;
          color: #94A3B8;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          margin-bottom: 4px;
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .info-value {
          font-size: 1rem;
          font-weight: 500;
          color: #F8FAFC;
        }

        .badge-free {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 6px 12px; border-radius: 8px;
          background: rgba(148, 163, 184, 0.1);
          border: 1px solid rgba(148, 163, 184, 0.2);
          color: #CBD5E1; font-size: 0.85rem; font-weight: 600;
        }

        .badge-pro {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 6px 12px; border-radius: 8px;
          background: linear-gradient(90deg, rgba(139, 92, 246, 0.2), rgba(217, 70, 239, 0.2));
          border: 1px solid rgba(217, 70, 239, 0.4);
          color: #E879F9; font-size: 0.85rem; font-weight: 600;
          box-shadow: 0 0 15px rgba(217, 70, 239, 0.2);
        }

        .btn-upgrade {
          background: linear-gradient(135deg, #8B5CF6 0%, #D946EF 100%) !important;
          color: white !important;
          font-family: 'Outfit', sans-serif !important;
          font-size: 1.1rem !important;
          font-weight: 700 !important;
          height: 54px !important;
          border-radius: 14px !important;
          border: none !important;
          box-shadow: 0 8px 20px rgba(217, 70, 239, 0.25) !important;
          transition: all 0.3s ease !important;
        }
        .btn-upgrade:hover {
          box-shadow: 0 12px 25px rgba(217, 70, 239, 0.4) !important;
          transform: translateY(-2px);
        }
        
        .btn-cancel {
          background: rgba(239, 68, 68, 0.05) !important;
          color: #F87171 !important;
          border: 1px solid rgba(239, 68, 68, 0.2) !important;
          border-radius: 12px !important;
          height: 44px !important;
          font-weight: 500 !important;
        }
        .btn-cancel:hover {
          background: rgba(239, 68, 68, 0.1) !important;
        }
      `}</style>

      <div className="account-container fade-in">
        <h1 className="page-title">
          <User className="text-fuchsia-400" size={32} />
          Mi Perfil
        </h1>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          
          {/* TARJETA: DATOS DE LA CUENTA */}
          <div className="glass-card flex flex-col">
            <div className="flex items-center gap-4 mb-8">
              <div className="icon-box">
                <User size={24} />
              </div>
              <div>
                <h2 className="font-['Outfit'] text-xl font-bold text-white">Datos Personales</h2>
                <p className="text-sm text-slate-400">Gestiona tu información</p>
              </div>
            </div>

            <div className="space-y-6 flex-1">
              <div>
                <p className="info-label"><User size={14} /> Nombre Completo</p>
                <p className="info-value pl-5">{profile?.full_name || "Usuario alegIA"}</p>
              </div>
              
              <div>
                <p className="info-label"><Mail size={14} /> Correo Electrónico</p>
                <p className="info-value pl-5">{user?.email}</p>
              </div>
              
              <div>
                <p className="info-label"><CalendarDays size={14} /> Miembro desde</p>
                <p className="info-value pl-5">
                  {profile?.created_at ? new Date(profile.created_at).toLocaleDateString("es-ES", { year: 'numeric', month: 'long', day: 'numeric' }) : "Recientemente"}
                </p>
              </div>
              
              <div className="pt-2">
                <p className="info-label mb-2"><Zap size={14} /> Nivel Actual</p>
                <div className="pl-5">
                  <span className={profile?.plan === "pro" ? "badge-pro" : "badge-free"}>
                    {profile?.plan === "pro" ? <Crown size={16} /> : null}
                    {profile?.plan === "pro" ? "Usuario PRO" : "Plan Básico (Free)"}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* TARJETA: PLAN PRO */}
          <div className="glass-card glass-card-pro flex flex-col">
            {/* Etiqueta Flotante Recomentada */}
            <div className="absolute top-0 right-0 bg-gradient-to-r from-violet-500 to-fuchsia-500 text-white text-[10px] sm:text-xs font-bold px-4 py-1.5 rounded-bl-xl tracking-wider uppercase shadow-lg">
              Recomendado
            </div>

            <div className="flex items-center gap-4 mb-6">
              <div className="icon-box-pro">
                <Crown size={24} />
              </div>
              <div>
                <h2 className="font-['Outfit'] text-xl font-bold text-white">Mejora a Pro</h2>
                <p className="text-sm text-fuchsia-300">Desbloquea todo el poder de alegIA</p>
              </div>
            </div>

            <div className="mb-6 flex items-baseline gap-2">
              <span className="font-['Outfit'] text-5xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-violet-300 to-fuchsia-300">
                $9.99
              </span>
              <span className="text-slate-400 font-medium">USD / mes</span>
            </div>

            <ul className="space-y-4 mb-8 flex-1">
              {benefits.map((b, idx) => (
                <li key={idx} className="flex items-start gap-3 text-sm md:text-base text-slate-200">
                  <CheckCircle2 className="h-5 w-5 text-fuchsia-400 shrink-0 mt-0.5" />
                  <span>{b}</span>
                </li>
              ))}
            </ul>

            <div className="mt-auto space-y-3 relative z-10">
              <Button 
                className="w-full btn-upgrade" 
                onClick={handleSubscribe} 
                disabled={showConfirm}
              >
                {showConfirm ? (
                  <span className="flex items-center gap-2">
                    <Sparkles className="animate-spin" size={18} /> Procesando...
                  </span>
                ) : (
                  <span className="flex items-center gap-2">
                    <Crown size={20} /> Suscribirme Ahora
                  </span>
                )}
              </Button>

              {profile?.plan === "pro" && (
                <Button
                  variant="outline"
                  className="w-full btn-cancel"
                  onClick={() => toast.info("Tu suscripción ha sido cancelada. Mantendrás los beneficios hasta fin de mes.")}
                >
                  Cancelar mi suscripción
                </Button>
              )}
            </div>
          </div>

        </div>
      </div>
    </>
  );
}