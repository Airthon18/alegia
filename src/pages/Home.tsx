import { Button } from "@/components/ui/button";
import {
  Activity, ArrowRight, BarChart3, Bot, Layers, MessageSquare,
  Shield, Sparkles, Target, Trophy, Zap,
} from "lucide-react";

interface HomeProps {
  // Lleva a la página Predicción (la misma de siempre).
  onNavigateChat: () => void;
}

// Pestaña con la que debe abrirse Predicción ("chat" o "equipos"). Predicción la lee al montarse.
const TAB_KEY = "alegia_prediccion_tab";

const stats = [
  { icon: Trophy,    label: "Equipos de LaLiga",      value: "20",  accent: "text-violet-400",  bg: "bg-violet-400/10" },
  { icon: Layers,    label: "Grupos de mercados",     value: "13",  accent: "text-fuchsia-400", bg: "bg-fuchsia-400/10" },
  { icon: BarChart3, label: "Partidos de validación", value: "699", accent: "text-violet-400",  bg: "bg-violet-400/10" },
  { icon: Bot,       label: "Agentes de IA",          value: "3",   accent: "text-fuchsia-400", bg: "bg-fuchsia-400/10" },
];

const features = [
  {
    icon: Activity,
    title: "Modelo estadístico",
    desc: "Elo, goles esperados y estadísticas de córners, tarjetas, faltas y tiros, validados partido a partido.",
  },
  {
    icon: Target,
    title: "Cuota justa y fiabilidad",
    desc: "Cada mercado muestra su probabilidad, su cuota justa y cuánto mejora el modelo a la frecuencia histórica.",
  },
  {
    icon: Shield,
    title: "Debate de agentes",
    desc: "Un analista de datos, un técnico y un tipster debaten, y un moderador te resume el veredicto final.",
  },
];

const steps = [
  { n: "1", title: "Elige el partido", desc: "Escríbelo en el chat (Barcelona vs Sevilla) o selecciona los dos equipos." },
  { n: "2", title: "El modelo calcula", desc: "Probabilidades, goles esperados y todos los mercados de apuesta." },
  { n: "3", title: "Recibe el veredicto", desc: "Una apuesta sugerida con su confianza, lista para contrastar con la cuota real." },
];

export default function Home({ onNavigateChat }: HomeProps) {
  const irA = (tab: "chat" | "equipos") => {
    try {
      sessionStorage.setItem(TAB_KEY, tab);
    } catch {
      /* sin storage: Predicción abre en el chat */
    }
    onNavigateChat();
  };

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;700;800&family=Inter:wght@300;400;500&display=swap');

        .app-container {
          background-color: #05050A;
          background-image:
            radial-gradient(at 0% 0%, rgba(139, 92, 246, 0.15) 0px, transparent 50%),
            radial-gradient(at 100% 0%, rgba(217, 70, 239, 0.15) 0px, transparent 50%);
          font-family: 'Inter', sans-serif;
          color: #F8FAFC;
          min-height: 100vh;
          position: relative;
          padding-bottom: calc(96px + env(safe-area-inset-bottom, 0px)); /* hueco para la barra fija del móvil */
        }
        @media (min-width: 768px) { .app-container { padding-bottom: 32px; } }

        .app-container h1, .app-container h2, .app-container h3, .font-display {
          font-family: 'Outfit', sans-serif;
        }

        .glass-card {
          background: rgba(255, 255, 255, 0.03);
          border: 1px solid rgba(255, 255, 255, 0.06);
          backdrop-filter: blur(10px);
          -webkit-backdrop-filter: blur(10px);
          border-radius: 20px;
        }
        .glass-card.hoverable { transition: transform .2s ease, border-color .2s ease, background .2s ease; }
        @media (hover: hover) {
          .glass-card.hoverable:hover { transform: translateY(-3px); border-color: rgba(167, 139, 250, 0.35); background: rgba(255,255,255,0.05); }
        }

        .gradient-text {
          background: linear-gradient(135deg, #A78BFA 0%, #E879F9 100%);
          -webkit-background-clip: text;
          background-clip: text;
          -webkit-text-fill-color: transparent;
        }

        .btn-primary {
          background: linear-gradient(135deg, #8B5CF6 0%, #D946EF 100%) !important;
          color: white !important;
          border: none !important;
          box-shadow: 0 8px 25px rgba(139, 92, 246, 0.25) !important;
          transition: transform .2s ease, box-shadow .2s ease !important;
        }
        .btn-primary:active { transform: scale(0.98); }
        @media (hover: hover) { .btn-primary:hover { box-shadow: 0 10px 32px rgba(217, 70, 239, 0.35) !important; } }

        .btn-ghost {
          background: rgba(255, 255, 255, 0.04) !important;
          color: #E9D5FF !important;
          border: 1px solid rgba(167, 139, 250, 0.35) !important;
          transition: background .2s ease !important;
        }
        .btn-ghost:active { transform: scale(0.98); }
        @media (hover: hover) { .btn-ghost:hover { background: rgba(167, 139, 250, 0.12) !important; } }
      `}</style>

      <div className="app-container overflow-x-hidden">
        {/* HERO */}
        <header className="pt-12 pb-10 px-4 sm:px-6 md:pt-24 md:pb-16 text-center max-w-4xl mx-auto flex flex-col items-center">
          <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full glass-card text-violet-300 text-xs md:text-sm font-medium mb-6">
            <Sparkles size={16} className="text-fuchsia-400 shrink-0" />
            Predicciones de LaLiga con IA
          </div>

          <h1 className="text-[2.75rem] sm:text-6xl md:text-7xl font-extrabold tracking-tight mb-4 leading-[1.08]">
            Conoce a <span className="gradient-text">alegIA</span>
          </h1>

          <p className="text-slate-400 text-base md:text-lg max-w-xl mx-auto mb-8 font-light leading-relaxed">
            Elige un partido de LaLiga y obtén probabilidades, cuotas justas y el veredicto de tres agentes de IA, con la fiabilidad de cada mercado a la vista.
          </p>

          {/* En escritorio/tablet los botones van aquí; en móvil van en la barra fija de abajo */}
          <div className="hidden md:flex items-center justify-center gap-3 flex-wrap">
            <Button size="lg" onClick={() => irA("chat")} className="btn-primary font-display text-lg px-8 py-6 rounded-2xl flex items-center gap-2">
              <Zap className="h-5 w-5 fill-current" />
              Hablar con alegIA
              <ArrowRight className="h-5 w-5" />
            </Button>
            <Button size="lg" onClick={() => irA("equipos")} className="btn-ghost font-display text-lg px-8 py-6 rounded-2xl flex items-center gap-2">
              <Trophy className="h-5 w-5" />
              Elegir equipos
            </Button>
          </div>
        </header>

        {/* DATOS REALES DEL MODELO */}
        <section aria-label="El modelo en cifras" className="px-4 sm:px-6 mb-12 md:mb-16 max-w-5xl mx-auto">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 md:gap-5">
            {stats.map((stat) => {
              const Icon = stat.icon;
              return (
                <div key={stat.label} className="glass-card hoverable p-4 sm:p-5 flex flex-col items-center text-center">
                  <div className={`w-11 h-11 sm:w-12 sm:h-12 rounded-2xl ${stat.bg} flex items-center justify-center mb-3`}>
                    <Icon size={22} className={stat.accent} />
                  </div>
                  <div className="font-display text-2xl md:text-3xl font-bold text-white mb-1">{stat.value}</div>
                  <div className="text-[11px] md:text-xs text-slate-400 font-medium uppercase tracking-wider leading-snug">{stat.label}</div>
                </div>
              );
            })}
          </div>
        </section>

        {/* QUÉ OFRECE */}
        <section aria-label="Qué ofrece alegIA" className="px-4 sm:px-6 mb-12 md:mb-16 max-w-5xl mx-auto">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {features.map((feat) => {
              const Icon = feat.icon;
              return (
                <div key={feat.title} className="glass-card hoverable p-5 sm:p-6 flex flex-col items-start">
                  <Icon size={28} className="text-fuchsia-400 mb-4" />
                  <h3 className="font-display text-lg font-semibold text-white mb-2">{feat.title}</h3>
                  <p className="text-slate-400 text-sm leading-relaxed">{feat.desc}</p>
                </div>
              );
            })}
          </div>
        </section>

        {/* CÓMO FUNCIONA */}
        <section aria-label="Cómo funciona" className="px-4 sm:px-6 mb-12 md:mb-16 max-w-5xl mx-auto">
          <div className="text-center mb-6 md:mb-8">
            <div className="text-violet-400 text-xs font-bold uppercase tracking-widest mb-2">Cómo funciona</div>
            <h2 className="font-display text-2xl md:text-3xl font-bold text-white">De la pregunta al veredicto en segundos</h2>
          </div>
          <ol className="grid grid-cols-1 md:grid-cols-3 gap-4 list-none p-0 m-0">
            {steps.map((s) => (
              <li key={s.n} className="glass-card p-5 sm:p-6 flex gap-4 items-start">
                <span className="font-display shrink-0 w-9 h-9 rounded-full bg-gradient-to-br from-violet-500 to-fuchsia-500 text-white font-bold flex items-center justify-center">
                  {s.n}
                </span>
                <div className="min-w-0">
                  <h3 className="font-display text-base font-semibold text-white mb-1">{s.title}</h3>
                  <p className="text-slate-400 text-sm leading-relaxed">{s.desc}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {/* LLAMADA FINAL (escritorio; en móvil ya está la barra fija) */}
        <section className="hidden md:block px-6 mb-12 max-w-3xl mx-auto">
          <div className="glass-card p-10 text-center relative overflow-hidden">
            <div className="absolute -right-20 -top-20 w-64 h-64 bg-violet-600/10 rounded-full blur-3xl pointer-events-none" />
            <h2 className="font-display text-3xl font-bold text-white mb-3 relative">Prueba con tu próximo partido</h2>
            <p className="text-slate-400 mb-6 relative">Escríbelo en el chat o elige los equipos: las dos formas usan el mismo modelo.</p>
            <div className="flex justify-center gap-3 flex-wrap relative">
              <Button onClick={() => irA("chat")} className="btn-primary font-display rounded-xl px-6 h-12">
                <MessageSquare className="h-4 w-4 mr-2" /> Abrir el chat
              </Button>
              <Button onClick={() => irA("equipos")} className="btn-ghost font-display rounded-xl px-6 h-12">
                <Trophy className="h-4 w-4 mr-2" /> Elegir equipos
              </Button>
            </div>
          </div>
        </section>

        {/* AVISO + PIE */}
        <div className="px-4 sm:px-6 max-w-3xl mx-auto text-center">
          <p className="text-slate-500 text-xs leading-relaxed mb-4">
            Análisis orientativo basado en datos históricos de LaLiga: ninguna apuesta es segura. Solo para mayores de 18 años. Juega con responsabilidad.
          </p>
          <div className="pb-4 text-slate-600 text-[10px] tracking-[0.2em] uppercase">alegIA Tech © {new Date().getFullYear()}</div>
        </div>

        {/* BARRA FIJA SOLO MÓVIL */}
        <div className="md:hidden fixed bottom-0 left-0 right-0 px-4 pt-6 bg-gradient-to-t from-[#05050A] via-[#05050A]/90 to-transparent z-50" style={{ paddingBottom: "calc(1rem + env(safe-area-inset-bottom, 0px))" }}>
          <div className="flex gap-3">
            <Button onClick={() => irA("chat")} className="flex-[1.3] btn-primary font-display text-base h-14 rounded-2xl flex items-center justify-center gap-2 min-w-0">
              <Zap className="h-5 w-5 fill-current shrink-0" />
              <span className="truncate">Hablar con alegIA</span>
            </Button>
            <Button onClick={() => irA("equipos")} aria-label="Elegir equipos" className="flex-1 btn-ghost font-display text-base h-14 rounded-2xl flex items-center justify-center gap-2 min-w-0">
              <Trophy className="h-5 w-5 shrink-0" />
              <span className="truncate">Equipos</span>
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}