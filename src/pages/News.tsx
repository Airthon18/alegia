import { useCallback, useEffect, useMemo, useState } from "react";
import { ExternalLink, Newspaper, RefreshCw, Search } from "lucide-react";

// Misma URL del backend que usa Predicción (VITE_API_URL en el .env).
const API: string =
  (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_API_URL ?? "http://127.0.0.1:8001";

interface Noticia {
  titulo: string;
  resumen: string;
  enlace: string;
  imagen: string | null;
  fecha: string | null;
  fuente: string;
  categorias: string[];
}

interface RespuestaNoticias {
  noticias: Noticia[];
  fuentes: string[];
  actualizado: string | null;
  desactualizado?: boolean;
  error?: string;
}

// ─── Helpers ─────────────────────────────────────────────
const normalizar = (s: string) =>
  s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

const rtf = new Intl.RelativeTimeFormat("es", { numeric: "auto" });

function fechaRelativa(iso: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const min = Math.round((t - Date.now()) / 60000); // negativo = pasado
  if (min > -1) return "ahora";
  if (min > -60) return rtf.format(min, "minute");
  if (min > -60 * 24) return rtf.format(Math.round(min / 60), "hour");
  if (min > -60 * 24 * 7) return rtf.format(Math.round(min / (60 * 24)), "day");
  return new Date(t).toLocaleDateString("es", { day: "numeric", month: "short", year: "numeric" });
}

// ─── Tarjeta ─────────────────────────────────────────────
function Imagen({ src, alt, className }: { src: string | null; alt: string; className: string }) {
  const [fallo, setFallo] = useState(false);
  if (!src || fallo) {
    return (
      <div className={`${className} flex items-center justify-center bg-gradient-to-br from-primary/15 to-muted`}>
        <Newspaper className="h-8 w-8 text-muted-foreground/60" aria-hidden />
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFallo(true)}
      className={`${className} object-cover`}
    />
  );
}

function Tarjeta({ n, destacada }: { n: Noticia; destacada?: boolean }) {
  return (
    <a
      href={n.enlace}
      target="_blank"
      rel="noopener noreferrer"
      className={`group bg-card border border-border rounded-xl overflow-hidden hover:border-primary/40 transition-colors flex ${
        destacada ? "flex-col lg:flex-row lg:col-span-full" : "flex-col"
      }`}
    >
      <div className={`overflow-hidden shrink-0 ${destacada ? "lg:w-1/2 h-52 sm:h-64 lg:h-auto lg:min-h-[18rem]" : "h-44"}`}>
        <Imagen
          src={n.imagen}
          alt=""
          className="w-full h-full group-hover:scale-105 transition-transform duration-300"
        />
      </div>
      <div className={`p-4 flex flex-col min-w-0 flex-1 ${destacada ? "sm:p-6 lg:p-8 lg:justify-center" : ""}`}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mb-2 text-xs">
          <span className="text-primary font-medium">{n.fuente}</span>
          {n.fecha && <span className="text-muted-foreground">• {fechaRelativa(n.fecha)}</span>}
          {n.categorias[1] && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-muted-foreground truncate max-w-[10rem]">
              {n.categorias[1]}
            </span>
          )}
        </div>
        <h3
          className={`font-semibold text-foreground leading-snug mb-2 ${
            destacada ? "text-xl sm:text-2xl lg:text-3xl line-clamp-4" : "line-clamp-3"
          }`}
        >
          {n.titulo}
        </h3>
        {n.resumen && (
          <p className={`text-sm text-muted-foreground mb-4 ${destacada ? "line-clamp-4" : "line-clamp-3"}`}>
            {n.resumen}
          </p>
        )}
        <span className="mt-auto inline-flex items-center text-sm font-medium text-primary">
          Leer en {n.fuente} <ExternalLink className="ml-1 h-3 w-3" aria-hidden />
        </span>
      </div>
    </a>
  );
}

function Esqueleto({ destacada }: { destacada?: boolean }) {
  return (
    <div
      aria-hidden
      className={`bg-card border border-border rounded-xl overflow-hidden animate-pulse ${destacada ? "lg:col-span-full" : ""}`}
    >
      <div className={`bg-muted ${destacada ? "h-52 sm:h-64" : "h-44"}`} />
      <div className="p-4 space-y-3">
        <div className="h-3 w-24 rounded bg-muted" />
        <div className="h-4 w-full rounded bg-muted" />
        <div className="h-4 w-2/3 rounded bg-muted" />
      </div>
    </div>
  );
}

// ─── Página ──────────────────────────────────────────────
export default function News() {
  const [datos, setDatos] = useState<RespuestaNoticias | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState(false);
  const [busqueda, setBusqueda] = useState("");
  const [categoria, setCategoria] = useState("");

  const cargar = useCallback(() => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 60_000); // el servidor gratuito puede tardar en despertar
    setCargando(true);
    setError(false);
    fetch(`${API}/noticias`, { signal: ctrl.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<RespuestaNoticias>;
      })
      .then((j) => {
        if (j.error || !Array.isArray(j.noticias)) throw new Error(j.error ?? "respuesta inválida");
        setDatos(j);
      })
      .catch(() => setError(true))
      .finally(() => {
        clearTimeout(timer);
        setCargando(false);
      });
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, []);

  useEffect(() => cargar(), [cargar]);

  const noticias = datos?.noticias ?? [];

  // Etiquetas más frecuentes (equipos, jugadores...), sin la genérica "Primera División"
  const etiquetas = useMemo(() => {
    const cuenta = new Map<string, number>();
    for (const n of noticias) {
      for (const c of n.categorias) {
        if (normalizar(c) === "primera division") continue;
        cuenta.set(c, (cuenta.get(c) ?? 0) + 1);
      }
    }
    return [...cuenta.entries()]
      .filter(([, k]) => k >= 2)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
      .map(([c]) => c);
  }, [noticias]);

  const visibles = useMemo(() => {
    const q = normalizar(busqueda.trim());
    return noticias.filter(
      (n) =>
        (!categoria || n.categorias.includes(categoria)) &&
        (!q || normalizar(`${n.titulo} ${n.resumen}`).includes(q)),
    );
  }, [noticias, busqueda, categoria]);

  const [primera, ...resto] = visibles;
  const hayFiltro = !!busqueda.trim() || !!categoria;

  return (
    <div className="p-4 sm:p-6 lg:p-10 fade-in">
      <div className="max-w-6xl">
        <div className="flex flex-wrap items-end justify-between gap-3 mb-6">
          <div>
            <h1 className="text-2xl font-bold text-foreground mb-1">Noticias de LaLiga</h1>
            <p className="text-muted-foreground text-sm sm:text-base">
              Lo último del fútbol español, de medios deportivos.
            </p>
          </div>
          <button
            type="button"
            onClick={cargar}
            disabled={cargando}
            className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground hover:border-primary/40 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${cargando ? "animate-spin" : ""}`} aria-hidden />
            Actualizar
          </button>
        </div>

        {/* Buscador y etiquetas */}
        {noticias.length > 0 && (
          <div className="mb-6 space-y-3">
            <label className="relative block max-w-md">
              <span className="sr-only">Buscar noticias</span>
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" aria-hidden />
              <input
                type="search"
                value={busqueda}
                onChange={(e) => setBusqueda(e.target.value)}
                placeholder="Buscar un equipo o jugador…"
                className="w-full rounded-lg border border-border bg-card py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-muted-foreground outline-none focus:border-primary/60"
              />
            </label>
            {etiquetas.length > 0 && (
              <div className="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1" role="group" aria-label="Filtrar por etiqueta">
                {["", ...etiquetas].map((e) => {
                  const sel = categoria === e;
                  return (
                    <button
                      key={e || "todas"}
                      type="button"
                      aria-pressed={sel}
                      onClick={() => setCategoria(e)}
                      className={`shrink-0 rounded-full border px-3 py-1 text-sm transition-colors ${
                        sel
                          ? "border-primary bg-primary/15 text-foreground font-medium"
                          : "border-border bg-card text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {e || "Todas"}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {datos?.desactualizado && (
          <p className="mb-4 text-sm text-amber-400">No se pudo actualizar ahora: se muestran las últimas noticias guardadas.</p>
        )}

        {/* Carga */}
        {cargando && !datos && (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-6" aria-busy="true" aria-label="Cargando noticias">
            <Esqueleto destacada />
            {[0, 1, 2].map((i) => (
              <Esqueleto key={i} />
            ))}
          </div>
        )}

        {/* Error */}
        {error && !datos && (
          <div role="alert" className="rounded-xl border border-destructive/40 bg-destructive/10 p-6 text-center">
            <p className="text-foreground mb-3">No se pudieron cargar las noticias. Si el servidor estaba dormido, puede tardar un minuto.</p>
            <button
              type="button"
              onClick={cargar}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              Reintentar
            </button>
          </div>
        )}

        {/* Sin resultados */}
        {datos && noticias.length === 0 && !error && (
          <p className="text-muted-foreground py-10 text-center">Ahora mismo no hay noticias disponibles. Inténtalo en unos minutos.</p>
        )}
        {datos && noticias.length > 0 && visibles.length === 0 && (
          <p className="text-muted-foreground py-10 text-center">
            Ninguna noticia coincide con tu búsqueda.{" "}
            <button
              type="button"
              className="text-primary underline"
              onClick={() => {
                setBusqueda("");
                setCategoria("");
              }}
            >
              Quitar filtros
            </button>
          </p>
        )}

        {/* Noticias */}
        {primera && (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-6">
            <Tarjeta n={primera} destacada={!hayFiltro || visibles.length > 2} />
            {resto.map((n) => (
              <Tarjeta key={n.enlace} n={n} />
            ))}
          </div>
        )}

        {datos && datos.fuentes.length > 0 && (
          <p className="mt-8 text-xs text-muted-foreground">
            Fuente: {datos.fuentes.join(", ")}. Los titulares, resúmenes e imágenes pertenecen a cada medio; cada tarjeta enlaza a la noticia original.
          </p>
        )}
      </div>
    </div>
  );
}