import { ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";

const news = [
  {
    title: "Real Madrid se refuerza con fichaje estrella para la Champions",
    summary: "El club blanco cierra la incorporación de un mediocampista top del mercado europeo para la próxima temporada de Champions League.",
    date: "23 Mar 2026",
    source: "Marca",
    image: "https://images.unsplash.com/photo-1522778119026-d647f0596c20?w=400&h=250&fit=crop",
  },
  {
    title: "Premier League: Arsenal lidera la tabla tras goleada histórica",
    summary: "Los Gunners aplastaron 5-0 al rival londinense y se colocan como favoritos al título de la Premier League 2025-26.",
    date: "22 Mar 2026",
    source: "ESPN",
    image: "https://images.unsplash.com/photo-1574629810360-7efbbe195018?w=400&h=250&fit=crop",
  },
  {
    title: "Messi y el Inter Miami, imparables en la MLS 2026",
    summary: "Con 15 goles en 10 partidos, Messi lidera la tabla de goleadores de la MLS y el Inter Miami marcha primero en la Conferencia Este.",
    date: "21 Mar 2026",
    source: "AS",
    image: "https://images.unsplash.com/photo-1553778263-73a83bab9b0c?w=400&h=250&fit=crop",
  },
  {
    title: "Barcelona anuncia nuevo estadio inteligente con IA integrada",
    summary: "El nuevo Camp Nou contará con sistemas de inteligencia artificial para mejorar la experiencia del aficionado y el análisis táctico en tiempo real.",
    date: "20 Mar 2026",
    source: "Sport",
    image: "https://images.unsplash.com/photo-1489944440615-453fc2b6a9a9?w=400&h=250&fit=crop",
  },
  {
    title: "Champions League: sorteo de cuartos deja cruces explosivos",
    summary: "Manchester City vs Bayern Múnich y Real Madrid vs PSG son los duelos más esperados de los cuartos de final.",
    date: "19 Mar 2026",
    source: "UEFA.com",
    image: "https://images.unsplash.com/photo-1431324155629-1a6deb1dec8d?w=400&h=250&fit=crop",
  },
  {
    title: "LaLiga introduce el VAR con IA para la próxima temporada",
    summary: "La liga española será la primera en implementar un sistema de videoarbitraje completamente automatizado con inteligencia artificial.",
    date: "18 Mar 2026",
    source: "Mundo Deportivo",
    image: "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?w=400&h=250&fit=crop",
  },
];

export default function News() {
  return (
    <div className="p-6 lg:p-10 fade-in">
      <h1 className="text-2xl font-bold text-foreground mb-2">Últimas Noticias de Fútbol</h1>
      <p className="text-muted-foreground mb-8">Las noticias más relevantes del mundo del fútbol</p>

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-6 max-w-6xl">
        {news.map((item) => (
          <div key={item.title} className="bg-card border border-border rounded-xl overflow-hidden group hover:border-primary/30 transition-colors">
            <div className="h-44 overflow-hidden">
              <img
                src={item.image}
                alt={item.title}
                className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                loading="lazy"
              />
            </div>
            <div className="p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-xs text-primary font-medium">{item.source}</span>
                <span className="text-xs text-muted-foreground">• {item.date}</span>
              </div>
              <h3 className="font-semibold text-foreground mb-2 line-clamp-2 leading-snug">{item.title}</h3>
              <p className="text-sm text-muted-foreground line-clamp-2 mb-4">{item.summary}</p>
              <Button variant="ghost" size="sm" className="text-primary hover:text-primary p-0 h-auto">
                Leer más <ExternalLink className="ml-1 h-3 w-3" />
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
