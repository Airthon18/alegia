// Lógica de la pestaña "En vivo": agrupar por estado y liga, filtrar y dar formato. Sin React ni red, para poder probarla.
// Los instantes llegan en UTC (segundos) y se comparan en UTC; America/Lima se usa SOLO para mostrar fechas y horas.

export type EstadoPartido =
  | "proximo"
  | "en_juego"
  | "descanso"
  | "prorroga"
  | "penales"
  | "por_confirmar"
  | "finalizado"
  | "suspendido"
  | "cancelado"
  | "aplazado";

/** confirmado: el partido terminó o el proveedor lo declara en vivo · provisional: hay goles pero sin estado confirmado · no_disponible: sin datos. */
export interface Marcador {
  estado: "confirmado" | "provisional" | "no_disponible";
  local: number | null;
  visitante: number | null;
  recibido_unix?: number; // cuándo se recibió este dato
  desactualizado?: boolean; // true: es el último confirmado y la respuesta más reciente ya no lo trae
}

export interface PartidoVivo {
  id: number;
  local: string;
  visitante: string;
  inicio_unix: number;
  dia_peru: string;
  estado: EstadoPartido;
  etiqueta: string;
  minuto: number | null; // solo si el proveedor lo envía
  nota: string | null;
  marcador: Marcador;
  competicion_id: number | null;
  liga: { pais: string; nombre: string } | null;
  jornada?: number | null;
  consultado_unix: number; // cuándo se le preguntó a la fuente
  estado_desde_unix: number; // desde cuándo se ve este estado
  pronostico_previo: boolean;
  // Datos de la fuente en vivo (API-Football) cuando el partido está emparejado
  fuente?: "API-Football" | "FootyStats";
  fase?: string | null; // 1T, descanso, 2T, prorroga, pausa_prorroga, penales, final, tras_prorroga, tras_penales
  minuto_extra?: number | null;
  af_id?: number | null;
  resultado_90?: { local: number; visitante: number } | null;
  prorroga?: { local: number; visitante: number } | null;
  penales?: { local: number; visitante: number } | null;
  ronda?: string | null;
  senal_atrasada?: boolean;
}

export interface FuenteVivo {
  estado: "pendiente_configurar" | "conectada" | "desconectada";
  tipo?: string | null;
  detalle?: string | null;
  consultado_unix?: number;
  proxima_unix?: number;
  intervalo_s?: number;
  cuota?: { dia_limite: number | null; dia_restantes: number | null } | null;
  vinculados?: number;
  ambiguos?: number;
  sin_par_en_tus_ligas?: number;
  otros_en_vivo?: number;
}

export interface RelojInfo {
  local_unix: number;
  referencia_unix: number | null;
  desfase_s: number | null;
  corregido: boolean;
}

export interface RespuestaVivo {
  consultado_unix: number;
  ahora_unix: number; // instante UTC de referencia con el que el servidor clasificó
  estado_consulta: "ok" | "parcial";
  avisos: string[];
  aviso_reloj: string | null; // el reloj del servidor está desviado respecto a UTC
  fechas_consultadas: string[];
  en_vivo_total: number;
  por_confirmar_total: number;
  proximos_cercanos: number;
  reloj: RelojInfo;
  fuente_vivo?: FuenteVivo;
  partidos: PartidoVivo[];
}

/** Forma mínima de /api/ligas que necesita este módulo. */
export interface LigaApi {
  pais: string;
  nombre: string;
  temporadas: { id: number }[];
}

export type FiltroVivo = { tipo: "todas" } | { tipo: "pais"; pais: string } | { tipo: "liga"; pais: string; nombre: string };

export type IdSeccion = "en_juego" | "por_confirmar" | "interrumpidos" | "finalizados";

// Los partidos futuros NO están aquí: viven en "Próximos partidos".
export const SECCIONES: { id: IdSeccion; titulo: string; ayuda?: string }[] = [
  { id: "en_juego", titulo: "En vivo", ayuda: "Solo partidos que FootyStats declara en juego, con el dato recibido hace pocos minutos." },
  {
    id: "por_confirmar",
    titulo: "Estado por confirmar",
    ayuda: "Ya pasó la hora de inicio y FootyStats todavía no confirma el estado: no se afirma que se esté jugando.",
  },
  { id: "interrumpidos", titulo: "Suspendidos, aplazados o cancelados" },
  { id: "finalizados", titulo: "Finalizados recientes" },
];

const SECCION_DE_ESTADO: Partial<Record<EstadoPartido, IdSeccion>> = {
  en_juego: "en_juego",
  descanso: "en_juego",
  prorroga: "en_juego",
  penales: "en_juego",
  por_confirmar: "por_confirmar",
  suspendido: "interrumpidos",
  aplazado: "interrumpidos",
  cancelado: "interrumpidos",
  finalizado: "finalizados",
};

export const ESTADOS_EN_VIVO: EstadoPartido[] = ["en_juego", "descanso", "prorroga", "penales"];

export const normalizar = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();

/** Liga de un partido: se resuelve con /api/ligas (por id de temporada) y, si no, con lo que trae el servidor. */
export function ligaDe(p: PartidoVivo, ligas: LigaApi[]): { pais: string; nombre: string } | null {
  if (p.competicion_id != null) {
    const l = ligas.find((x) => x.temporadas.some((t) => t.id === p.competicion_id));
    if (l) return { pais: l.pais, nombre: l.nombre };
  }
  return p.liga;
}

export function filtrarPartidos(partidos: PartidoVivo[], ligas: LigaApi[], filtro: FiltroVivo): PartidoVivo[] {
  if (filtro.tipo === "todas") return partidos;
  return partidos.filter((p) => {
    const l = ligaDe(p, ligas);
    if (!l) return false;
    return filtro.tipo === "pais" ? l.pais === filtro.pais : l.pais === filtro.pais && l.nombre === filtro.nombre;
  });
}

/** Cuántos partidos están realmente en vivo (evidencia explícita del proveedor). Es el número que muestra el contador. */
export const contarEnVivo = (partidos: PartidoVivo[]) => partidos.filter((p) => ESTADOS_EN_VIVO.includes(p.estado)).length;

export interface GrupoLiga {
  clave: string;
  titulo: string;
  partidos: PartidoVivo[];
}
export interface SeccionVivo {
  id: IdSeccion;
  titulo: string;
  ayuda?: string;
  total: number;
  ligas: GrupoLiga[];
}

/** Secciones por estado (solo las que tienen partidos), cada una agrupada por liga y ordenada por hora de inicio. */
export function agruparPorEstadoYLiga(partidos: PartidoVivo[], ligas: LigaApi[]): SeccionVivo[] {
  const secciones: SeccionVivo[] = [];
  for (const def of SECCIONES) {
    const delGrupo = partidos.filter((p) => SECCION_DE_ESTADO[p.estado] === def.id);
    if (delGrupo.length === 0) continue;
    const porLiga = new Map<string, GrupoLiga>();
    for (const p of delGrupo) {
      const l = ligaDe(p, ligas);
      const titulo = l ? `${l.pais} – ${l.nombre}` : "Otras competiciones";
      const g = porLiga.get(titulo) ?? { clave: titulo, titulo, partidos: [] };
      g.partidos.push(p);
      porLiga.set(titulo, g);
    }
    const grupos = [...porLiga.values()].sort((a, b) => a.titulo.localeCompare(b.titulo));
    grupos.forEach((g) => g.partidos.sort((a, b) => a.inicio_unix - b.inicio_unix || a.id - b.id));
    secciones.push({ id: def.id, titulo: def.titulo, ayuda: def.ayuda, total: delGrupo.length, ligas: grupos });
  }
  return secciones;
}

/** Una sola entrada por ID: si un partido llega dos veces (o cambia de estado entre respuestas) queda la última. */
export function unicosPorId(partidos: PartidoVivo[] | undefined | null): PartidoVivo[] {
  const m = new Map<number, PartidoVivo>();
  for (const p of partidos ?? []) m.set(p.id, p);
  return [...m.values()];
}

/** Minuto solo si el proveedor lo envió; si no, se dice que no está disponible (nunca se calcula con la hora de inicio). */
export function textoMinuto(p: PartidoVivo): string {
  if (p.estado === "descanso") return "Descanso";
  if (p.estado === "prorroga") return "Prórroga";
  if (p.estado === "penales") return "Penales";
  if (p.estado === "por_confirmar") return "Sin confirmar";
  if (p.estado === "finalizado") return "Final";
  if (p.estado === "suspendido") return "Suspendido";
  if (p.estado === "aplazado") return "Aplazado";
  if (p.estado === "cancelado") return "Cancelado";
  if (p.estado === "en_juego") {
    if (p.minuto == null) return "Minuto no disponible";
    return p.minuto_extra ? `${p.minuto}+${p.minuto_extra}'` : `${p.minuto}'`;
  }
  return "";
}

const FASES: Record<string, string> = {
  "1T": "1.er tiempo",
  descanso: "Descanso",
  "2T": "2.º tiempo",
  prorroga: "Prórroga",
  pausa_prorroga: "Pausa de la prórroga",
  penales: "Penales",
  final: "Final (90')",
  tras_prorroga: "Final tras prórroga",
  tras_penales: "Final tras penales",
};

/** Fase legible: "2.º tiempo", "Prórroga 105'"... null si la fuente no la informa. */
export function textoFase(p: PartidoVivo): string | null {
  if (!p.fase) return null;
  const base = FASES[p.fase] ?? p.fase;
  return p.fase === "prorroga" && p.minuto != null ? `${base} · ${p.minuto}'` : base;
}

/** ¿Se puede pedir la predicción en vivo? Solo con marcador confirmado por la fuente en vivo y el partido en juego. */
export const admitePrediccionEnVivo = (p: PartidoVivo) =>
  p.fuente === "API-Football" && p.marcador?.estado === "confirmado" && ESTADOS_EN_VIVO.includes(p.estado);

/** Firma de los datos confirmados: si cambia (gol, corrección, minuto, fase), la predicción en vivo se recalcula. */
export const firmaVivo = (p: PartidoVivo) => `${p.estado}|${p.fase}|${p.minuto}|${p.minuto_extra}|${p.marcador?.local}-${p.marcador?.visitante}`;

export type AvisoFuente = { tipo: "pendiente" | "desconectada" | "parcial" | "ninguno" | "ok"; texto: string };

/** Distingue "fuente desconectada", "cobertura parcial" y "ningún partido en juego". */
export function avisoFuente(r: RespuestaVivo, nEnVivo: number): AvisoFuente {
  const f = r.fuente_vivo;
  if (!f || f.estado === "pendiente_configurar") {
    return { tipo: "pendiente", texto: "Fuente en vivo pendiente de configurar: añade API_FOOTBALL_KEY a backend/.env y reinicia el backend. Mientras tanto, solo se ve lo que confirma FootyStats." };
  }
  if (f.estado === "desconectada") {
    return { tipo: "desconectada", texto: `Fuente en vivo desconectada${f.detalle ? `: ${f.detalle}` : ""}.` };
  }
  if (r.estado_consulta === "parcial") return { tipo: "parcial", texto: "Cobertura parcial: puede faltar algún partido o algún marcador." };
  if (nEnVivo === 0) return { tipo: "ninguno", texto: "Ningún partido en juego ahora en tus competiciones." };
  return { tipo: "ok", texto: "" };
}

export interface TextoMarcador {
  principal: string; // "1 – 0" o "Marcador no disponible"
  nota: string | null; // aclaración: sin confirmar, o último confirmado y cuándo se recibió
  confirmado: boolean;
}

export function textoMarcador(m: Marcador | null | undefined): TextoMarcador {
  if (!m || m.estado === "no_disponible" || m.local == null || m.visitante == null) {
    return { principal: "Marcador no disponible", nota: null, confirmado: false };
  }
  const principal = `${m.local} – ${m.visitante}`;
  if (m.estado === "provisional") {
    return { principal, nota: "Marcador sin confirmar: FootyStats no confirma el estado del partido", confirmado: false };
  }
  if (m.desactualizado && m.recibido_unix != null) {
    return { principal, nota: `Último marcador confirmado, recibido a las ${horaPeru(m.recibido_unix)}; la última consulta no lo trajo`, confirmado: true };
  }
  return { principal, nota: null, confirmado: true };
}

const ZONA_PERU = "America/Lima";
const fmtHora = new Intl.DateTimeFormat("es-PE", { timeZone: ZONA_PERU, hour: "2-digit", minute: "2-digit", hour12: false });
const fmtDia = new Intl.DateTimeFormat("es-PE", { timeZone: ZONA_PERU, weekday: "short", day: "numeric", month: "short" });
const fmtFechaCompleta = new Intl.DateTimeFormat("es-PE", { timeZone: ZONA_PERU, weekday: "long", day: "numeric", month: "long", year: "numeric" });

export const horaPeru = (unix: number) => fmtHora.format(new Date(unix * 1000));
export const diaPeru = (unix: number) => fmtDia.format(new Date(unix * 1000));
/** "viernes, 20 de noviembre de 2026": fecha completa en hora de Perú. */
export const fechaCompletaPeru = (unix: number) => {
  const t = fmtFechaCompleta.format(new Date(unix * 1000));
  return t.charAt(0).toUpperCase() + t.slice(1);
};

/** Diferencia (s) entre el reloj del navegador y el instante UTC de referencia que envía el servidor. */
export const desfaseNavegadorS = (ahoraServidorUnix: number, navegadorMs: number) => navegadorMs / 1000 - ahoraServidorUnix;

/** "Ahora" en UTC según la referencia del servidor, aunque el reloj del navegador esté mal. */
export const ahoraConfiable = (desfaseS: number, navegadorMs: number) => navegadorMs / 1000 - desfaseS;

export const antiguedadTexto = (desdeUnix: number, ahoraUnix: number): string => {
  const s = Math.max(0, Math.round(ahoraUnix - desdeUnix));
  return s < 90 ? `hace ${s} s` : `hace ${Math.round(s / 60)} min`;
};
