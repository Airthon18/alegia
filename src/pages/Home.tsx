import { Button } from "@/components/ui/button";
import { 
  Users, Target, TrendingUp, BarChart3, 
  Zap, ArrowRight, Shield, Clock, 
  Sparkles, Activity, MessageSquare 
} from "lucide-react";

interface HomeProps {
  onNavigateChat: () => void;
}

const stats = [
  { icon: Users,       label: "Usuarios activos", value: "15K+", accent: "text-violet-400", bg: "bg-violet-400/10" },
  { icon: Target,      label: "Precisión media",  value: "89%",  accent: "text-fuchsia-400", bg: "bg-fuchsia-400/10" },
  { icon: TrendingUp,  label: "Predicciones",     value: "120K+",accent: "text-violet-400", bg: "bg-violet-400/10" },
  { icon: BarChart3,   label: "Ligas cubiertas",  value: "30+",  accent: "text-fuchsia-400", bg: "bg-fuchsia-400/10" },
];

const features = [
  {
    icon: Activity,
    title: "Datos en vivo",
    desc: "Procesamos estadísticas, lesiones y cuotas en tiempo real para darte la ventaja.",
  },
  {
    icon: Shield,
    title: "IA Confiable",
    desc: "Modelos predictivos entrenados con décadas de historial deportivo global.",
  },
  {
    icon: MessageSquare,
    title: "Asistente 24/7",
    desc: "Pregunta a alegIA sobre cualquier partido en cualquier momento del día.",
  },
];

export default function Home({ onNavigateChat }: HomeProps) {
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
          padding-bottom: 80px; /* Espacio para el botón flotante en móvil */
        }

        h1, h2, h3, .font-display {
          font-family: 'Outfit', sans-serif;
        }

        /* Glassmorphism Cards */
        .glass-card {
          background: rgba(255, 255, 255, 0.03);
          border: 1px solid rgba(255, 255, 255, 0.05);
          backdrop-filter: blur(10px);
          -webkit-backdrop-filter: blur(10px);
          border-radius: 20px;
        }

        .gradient-text {
          background: linear-gradient(135deg, #A78BFA 0%, #E879F9 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
        }

        .btn-primary {
          background: linear-gradient(135deg, #8B5CF6 0%, #D946EF 100%) !important;
          color: white !important;
          border: none !important;
          box-shadow: 0 8px 25px rgba(139, 92, 246, 0.25) !important;
          transition: all 0.3s ease !important;
        }
        .btn-primary:active {
          transform: scale(0.98);
        }
      `}</style>

      <div className="app-container overflow-x-hidden">
        
        {/* HERO SECTION */}
        <header className="pt-16 pb-10 px-5 md:pt-28 md:pb-20 text-center max-w-4xl mx-auto flex flex-col items-center">
          <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full glass-card text-violet-300 text-xs md:text-sm font-medium mb-6">
            <Sparkles size={16} className="text-fuchsia-400" />
            La revolución de las apuestas con IA
          </div>
          
          <h1 className="text-5xl md:text-7xl font-extrabold tracking-tight mb-4 leading-[1.1]">
            Conoce a <span className="gradient-text">alegIA</span>
          </h1>
          
          <p className="text-slate-400 text-base md:text-lg max-w-xl mx-auto mb-8 font-light leading-relaxed px-2">
            Tu asistente inteligente. Análisis estadístico profundo, noticias en tiempo real y la mejor estrategia deportiva en la palma de tu mano.
          </p>

          {/* Botón oculto en móvil (se muestra abajo flotante), visible en Desktop */}
          <Button 
            size="lg" 
            onClick={onNavigateChat} 
            className="hidden md:flex btn-primary font-display text-lg px-8 py-6 rounded-2xl items-center gap-2"
          >
            <Zap className="h-5 w-5 fill-current" />
            Hablar con alegIA
            <ArrowRight className="h-5 w-5" />
          </Button>
        </header>

        {/* STATS GRID (Adaptable: 2 columnas en móvil, 4 en desktop) */}
        <section className="px-5 mb-16 max-w-5xl mx-auto">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-6">
            {stats.map((stat, idx) => {
              const Icon = stat.icon;
              return (
                <div key={idx} className="glass-card p-5 flex flex-col items-center text-center">
                  <div className={`w-12 h-12 rounded-2xl ${stat.bg} flex items-center justify-center mb-3`}>
                    <Icon size={24} className={stat.accent} />
                  </div>
                  <div className="font-display text-2xl md:text-3xl font-bold text-white mb-1">
                    {stat.value}
                  </div>
                  <div className="text-[11px] md:text-xs text-slate-400 font-medium uppercase tracking-wider">
                    {stat.label}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* FEATURES (Lista vertical en móvil, horizontal en desktop) */}
        <section className="px-5 mb-16 max-w-5xl mx-auto">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {features.map((feat, idx) => {
              const Icon = feat.icon;
              return (
                <div key={idx} className="glass-card p-6 flex flex-col items-start">
                  <Icon size={28} className="text-fuchsia-400 mb-4" />
                  <h3 className="font-display text-lg font-semibold text-white mb-2">{feat.title}</h3>
                  <p className="text-slate-400 text-sm leading-relaxed">
                    {feat.desc}
                  </p>
                </div>
              );
            })}
          </div>
        </section>

        {/* ABOUT SECTION */}
        <section className="px-5 mb-12 max-w-3xl mx-auto">
          <div className="glass-card p-6 md:p-10 relative overflow-hidden">
            {/* Decoración de fondo */}
            <div className="absolute -right-20 -top-20 w-64 h-64 bg-violet-600/10 rounded-full blur-3xl pointer-events-none" />
            
            <div className="text-violet-400 text-xs font-bold uppercase tracking-widest mb-3">
              Sobre Nosotros
            </div>
            <h2 className="font-display text-3xl font-bold text-white mb-6">
              El motor detrás de alegIA
            </h2>
            
            <div className="space-y-4 text-slate-300 font-light text-sm md:text-base leading-relaxed">
              <p>
                En <strong className="text-white font-medium">alegIA</strong>, hemos fusionado nuestra pasión por los deportes con lo último en inteligencia artificial. Ya no dependes de la intuición; ahora cuentas con ciencia de datos pura.
              </p>
              <p>
                Nuestro asistente está diseñado para entender tu forma de jugar, ofreciéndote análisis de cuotas, probabilidades históricas y el estado físico de los jugadores en tiempo real. 
              </p>
              <p>
                Ya sea la Champions League o ligas locales, <strong className="text-fuchsia-400 font-medium">alegIA</strong> está disponible 24/7 para ayudarte a armar la estrategia ganadora perfecta.
              </p>
            </div>
          </div>
        </section>

        {/* FOOTER TEXT */}
        <div className="text-center pb-8 text-slate-500 text-[10px] tracking-[0.2em] uppercase">
          alegIA Tech © {new Date().getFullYear()}
        </div>

        {/* MOBILE STICKY CTA (Botón flotante solo para celulares) */}
        <div className="md:hidden fixed bottom-0 left-0 right-0 p-4 bg-gradient-to-t from-[#05050A] via-[#05050A]/90 to-transparent z-50">
          <Button 
            onClick={onNavigateChat} 
            className="w-full btn-primary font-display text-lg h-14 rounded-2xl flex items-center justify-center gap-2"
          >
            <Zap className="h-5 w-5 fill-current" />
            Abrir Chat IA
          </Button>
        </div>

      </div>
    </>
  );
}