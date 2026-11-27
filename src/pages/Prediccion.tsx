import { useState, useEffect, useId, useRef } from "react";
import { supabase } from "@/integrations/supabase/client";
import {
  agruparPorEstadoYLiga,
  ahoraConfiable,
  antiguedadTexto,
  contarEnVivo,
  desfaseNavegadorS,
  diaPeru,
  filtrarPartidos,
  horaPeru,
  ligaDe,
  textoMarcador,
  textoMinuto,
  unicosPorId,
  admitePrediccionEnVivo,
  avisoFuente,
  firmaVivo,
  textoFase,
  type FiltroVivo,
  type RespuestaVivo,
} from "@/lib/enVivo";

// ─── Config ───────────────────────────────────────────────
// Se puede cambiar con VITE_API_URL en el .env del frontend (p. ej. al desplegar).
const API: string =
  (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_API_URL ?? "http://127.0.0.1:8001";

// Mientras no haya login integrado en esta página, las predicciones se guardan con este usuario.
const USER_ID_ANONIMO = 0;

// El debate de agentes con búsqueda web puede tardar; pasado este tiempo se corta.
const TIMEOUT_DEBATE_MS = 150_000;
const TIMEOUT_CHAT_MS = 45_000;

// ─── Types ────────────────────────────────────────────────
interface TopMarcador {
  marcador: string;
  prob: number;
}

// Mercados de apuesta que calcula el servidor (cuota justa = 1 / probabilidad, sin margen de la casa)
export type NivelFiabilidad = "alta" | "media" | "baja";

export interface MercadoItem {
  nombre: string;
  prob: number;
  cuota: number | null;
  nota?: string;
}

export interface MercadoLinea {
  linea: number;
  over: number;
  under: number;
  cuota_over: number | null;
  cuota_under: number | null;
}

export interface MercadoSerie {
  ambito: "total" | "local" | "visitante";
  etiqueta: string;
  lineas: MercadoLinea[];
}

export interface MercadoGrupo {
  id: string;
  titulo: string;
  fiabilidad: NivelFiabilidad;
  tipo: "lista" | "lineas";
  nota: string;
  validacion?: { partidos: number; mejora_logloss: number };
  items?: MercadoItem[];
  esperado?: { local: number; visitante: number; total: number };
  series?: MercadoSerie[];
}

export interface MercadoDestacado {
  grupo: string;
  grupo_titulo: string;
  nombre: string;
  prob: number;
  cuota: number;
  fiabilidad: NivelFiabilidad;
}

export interface Mercados {
  nota: string;
  validacion_partidos: number;
  destacados: MercadoDestacado[];
  grupos: MercadoGrupo[];
}

export interface PrediccionMatematica {
  equipo_local: string;
  equipo_visitante: string;
  prob_local: number;
  prob_empate: number;
  prob_visitante: number;
  goles_esperados: { local: number; visitante: number; total: number };
  marcador_probable: string;
  recomendacion: string;
  ambos_marcan: number;
  // null en modo libre: ahí no hay modelo entrenado y no se inventan métricas
  accuracy_modelo: number | null;
  f1_modelo: number | null;
  top_marcadores?: TopMarcador[];
  over_2_5?: number;
  elo_local?: number;
  elo_visitante?: number;
  partidos_evaluados?: number;
  datos_hasta?: string;
  // false cuando los agentes no respondieron y los goles son valores por defecto
  estimacion_fiable?: boolean;
  // solo en modo modelo ML: mercados de apuesta ampliados
  mercados?: Mercados;
  error?: string;
}

export interface DebateAgentes {
  analisis_estadistico: string;
  analisis_tactico: string;
  analisis_betting: string;
  conclusion_comentarista: string;
  marcador_consenso: string;
  apuesta_recomendada: string;
  confianza: string;
  punto_de_conflicto?: string;
  noticias_clave?: string;
  // cuántas de las 9 respuestas de agentes llegaron (0 = problema de claves en el servidor)
  _agentes_ok?: number;
}

type MessageRole = "user" | "asistente" | "panel_debate" | "system";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  data?: PrediccionMatematica;
  debate?: DebateAgentes;
  modo?: "libre" | "matematico";
}

// ─── Helpers ──────────────────────────────────────────────
const pct = (n: number) => (n * 100).toFixed(1) + "%";

const teamInitials = (name: string) =>
  name
    .split(" ")
    .filter(Boolean)
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 3);

const teamColor = (name: string): string => {
  const palette = [
    "#a855f7", "#3b82f6", "#10b981", "#f59e0b",
    "#ef4444", "#06b6d4", "#f97316", "#8b5cf6",
    "#ec4899", "#14b8a6",
  ];
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return palette[Math.abs(hash) % palette.length];
};

const generateId = () => Math.random().toString(36).substring(2, 9);

const getFavorito = (data: PrediccionMatematica): string => {
  const max = Math.max(data.prob_local, data.prob_empate, data.prob_visitante);
  if (max === data.prob_local) return `${data.equipo_local} (${pct(max)})`;
  if (max === data.prob_visitante) return `${data.equipo_visitante} (${pct(max)})`;
  return `Empate (${pct(max)})`;
};

/**
 * Detecta "A vs B", "A vs. B", "A contra B" o "A - B" y devuelve los dos equipos
 * (o null si el texto no es un partido). Se respeta lo que escribió el usuario: el servidor
 * ya ignora mayúsculas y tildes.
 */
// Cada frase de petición puede ir precedida de un artículo ("el resultado de", "el partido de").
const PREFIJO_ORDEN =
  /^(?:(?:el|la|los|las|de|del)\s+)?(?:por favor|me puedes dar|quiero saber|resultado de|partido de|predice|predicci[oó]n de|analiza|sobre|entre|qui[eé]n gana|quien gana|dime de|dime|el partido|juegan|como quedan|resultado|dame)\s+/i;

/**
 * Quita las frases de petición del inicio ("predice el partido de ...") sin tocar el nombre del
 * equipo: nunca se quita un "Las"/"El" inicial del nombre, así "Las Palmas" o "El Salvador" se conservan.
 */
const limpiarLocal = (texto: string): string => {
  let t = texto.replace(/^[¿¡\s]+/, "").trim();
  let quitado = false;
  while (PREFIJO_ORDEN.test(t)) {
    t = t.replace(PREFIJO_ORDEN, "");
    quitado = true;
  }
  // Tras una petición puede quedar una preposición suelta ("quiero saber de Betis"); "de"/"del" nunca son del nombre.
  return quitado ? t.replace(/^(de|del)\s+/i, "").trim() : t;
};

export const extraerPartido = (texto: string): { local: string; visitante: string } | null => {
  const partes = texto.split(/\s+vs\.?\s+|\s+contra\s+|\s+[-–—]\s+/i);
  if (partes.length < 2) return null;

  const local = limpiarLocal(partes[0]);
  const visitante = partes[1].replace(/[?¿!¡.\s]+$/g, "").trim();

  if (!local || !visitante) return null;
  return { local, visitante };
};

// Token de la sesión de Supabase: el servidor lo usa para guardar las predicciones del usuario.
// Si no hay sesión (invitado) no se manda nada y la app funciona igual, sin guardar historial.
async function cabeceraSesion(): Promise<Record<string, string>> {
  try {
    const { data } = await supabase.auth.getSession();
    const token = data.session?.access_token;
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

async function fetchJson(path: string, body: unknown, timeoutMs: number) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${API}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(await cabeceraSesion()) },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

// ─── Sub-components ───────────────────────────────────────
function TeamCircle({ name, size = 44 }: { name: string; size?: number }) {
  const color = teamColor(name);
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        background: color + "22",
        border: `1.5px solid ${color}55`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: size * 0.28,
        fontWeight: 600,
        color,
        flexShrink: 0,
      }}
    >
      {teamInitials(name)}
    </div>
  );
}

function ProbBar({
  local,
  empate,
  visitante,
  nameLocal,
  nameVisitante,
}: {
  local: number;
  empate: number;
  visitante: number;
  nameLocal: string;
  nameVisitante: string;
}) {
  const lPct = Math.round(local * 100);
  const ePct = Math.round(empate * 100);
  const vPct = Math.round(visitante * 100);
  // flex con las probabilidades reales (no redondeadas) para que ningún tramo desaparezca
  const cell = (flex: number, bg: string, color: string, label: string, extra?: React.CSSProperties) => (
    <div
      style={{ flex: Math.max(flex, 0.04), background: bg, display: "flex", alignItems: "center", justifyContent: "center", minWidth: 0, ...extra }}
    >
      <span style={{ fontSize: 13, fontWeight: 600, color }}>{label}</span>
    </div>
  );
  return (
    <div
      style={{ marginTop: 12 }}
      role="img"
      aria-label={`${nameLocal} ${lPct}%, empate ${ePct}%, ${nameVisitante} ${vPct}%`}
    >
      <div style={{ display: "flex", borderRadius: 8, overflow: "hidden", height: 36 }}>
        {cell(local, "#1e1030", "#c4b5fd", `${lPct}%`)}
        {cell(empate, "#1c1c1c", "#888", `${ePct}%`, { borderLeft: "1px solid #111", borderRight: "1px solid #111" })}
        {cell(visitante, "#0f1e30", "#93c5fd", `${vPct}%`)}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginTop: 4, fontSize: 10, color: "#666" }}>
        <span>{nameLocal}</span>
        <span>Empate</span>
        <span>{nameVisitante}</span>
      </div>
    </div>
  );
}

function StatGrid({ data, esLibre }: { data: PrediccionMatematica; esLibre: boolean }) {
  const stats: { label: string; value: string; color: string }[] = [
    { label: "Favorito", value: getFavorito(data), color: "#34d399" },
    { label: "Goles esperados", value: data.goles_esperados.total.toFixed(2), color: "#60a5fa" },
    { label: "Ambos marcan", value: data.ambos_marcan > 0.5 ? "SÍ" : "NO", color: data.ambos_marcan > 0.5 ? "#f87171" : "#fbbf24" },
    { label: "Prob. ambos marcan", value: pct(data.ambos_marcan), color: "#fbbf24" },
    { label: "Goles local", value: data.goles_esperados.local.toFixed(2), color: teamColor(data.equipo_local) },
    { label: "Goles visitante", value: data.goles_esperados.visitante.toFixed(2), color: teamColor(data.equipo_visitante) },
  ];
  if (typeof data.over_2_5 === "number") {
    stats.push({ label: "Más de 2.5 goles", value: pct(data.over_2_5), color: "#60a5fa" });
  }
  if (!esLibre && typeof data.elo_local === "number" && typeof data.elo_visitante === "number") {
    stats.push({ label: "Elo local / visitante", value: `${data.elo_local} / ${data.elo_visitante}`, color: "#a78bfa" });
  }
  // Las métricas solo se muestran si existen (modo libre no tiene modelo entrenado)
  if (!esLibre && typeof data.accuracy_modelo === "number") {
    stats.push({
      label: "Precisión del modelo",
      value: (data.accuracy_modelo * 100).toFixed(1) + "%",
      color: "#a78bfa",
    });
  }
  if (!esLibre && typeof data.f1_modelo === "number") {
    stats.push({ label: "F1 Score", value: data.f1_modelo.toFixed(2), color: "#a78bfa" });
  }

  const ultimaFila = stats.length % 2 === 0 ? 2 : 1;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", borderTop: "0.5px solid #222" }}>
      {stats.map((s, i) => (
        <div
          key={s.label}
          style={{
            padding: "10px 14px",
            borderRight: i % 2 === 0 ? "0.5px solid #1f1f1f" : "none",
            borderBottom: i < stats.length - ultimaFila ? "0.5px solid #1f1f1f" : "none",
          }}
        >
          <div style={{ fontSize: 10, color: "#666", textTransform: "uppercase", letterSpacing: "0.4px", marginBottom: 3 }}>
            {s.label}
          </div>
          <div style={{ fontSize: 13, fontWeight: 500, color: s.color }}>{s.value}</div>
        </div>
      ))}
    </div>
  );
}

function PredCard({ data, esLibre }: { data: PrediccionMatematica; esLibre: boolean }) {
  return (
    <div style={{ background: "#0d0d0d", borderRadius: 12, border: "0.5px solid #2a2a2a", overflow: "hidden", height: "100%" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 20px", borderBottom: "0.5px solid #1f1f1f" }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, flex: 1 }}>
          <TeamCircle name={data.equipo_local} />
          <span style={{ fontSize: 11, color: "#aaa", textAlign: "center", maxWidth: 80, lineHeight: 1.2, display: "block", marginTop: 2 }}>
            {data.equipo_local}
          </span>
        </div>

        <div style={{ textAlign: "center", padding: "0 10px" }}>
          <div style={{ fontSize: 28, fontWeight: 700, color: "#fff", letterSpacing: 3, fontFamily: "monospace" }}>
            {data.marcador_probable}
          </div>
          <div style={{ fontSize: 9, color: "#555", textTransform: "uppercase", letterSpacing: "0.5px", marginTop: 2 }}>
            marcador probable
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, flex: 1 }}>
          <TeamCircle name={data.equipo_visitante} />
          <span style={{ fontSize: 11, color: "#aaa", textAlign: "center", maxWidth: 80, lineHeight: 1.2, display: "block", marginTop: 2 }}>
            {data.equipo_visitante}
          </span>
        </div>
      </div>

      {data.top_marcadores && data.top_marcadores.length > 0 && (
        <div style={{ display: "flex", justifyContent: "center", flexWrap: "wrap", gap: 6, padding: "10px 14px 0" }}>
          {data.top_marcadores.map((m) => (
            <span
              key={m.marcador}
              style={{ fontSize: 11, color: "#aaa", background: "#1a1a1a", border: "0.5px solid #2a2a2a", borderRadius: 999, padding: "2px 10px" }}
            >
              {m.marcador} · {Math.round(m.prob * 100)}%
            </span>
          ))}
        </div>
      )}

      <div style={{ padding: "12px 14px", borderBottom: "0.5px solid #1f1f1f" }}>
        <ProbBar
          local={data.prob_local}
          empate={data.prob_empate}
          visitante={data.prob_visitante}
          nameLocal={data.equipo_local}
          nameVisitante={data.equipo_visitante}
        />
      </div>

      <StatGrid data={data} esLibre={esLibre} />

      {data.recomendacion && (
        <div style={{ padding: "12px 14px", borderTop: "0.5px solid #1f1f1f", fontSize: 12, color: "#fbbf24", display: "flex", alignItems: "flex-start", gap: 6, background: "#1c150333" }}>
          <span>★</span>
          <span>{data.recomendacion}</span>
        </div>
      )}

      {!esLibre && data.datos_hasta && (
        <div style={{ padding: "8px 14px", borderTop: "0.5px solid #1f1f1f", fontSize: 10, color: "#555" }}>
          Modelo entrenado con partidos hasta el {data.datos_hasta}
          {data.partidos_evaluados ? ` · validado con ${data.partidos_evaluados} partidos` : ""}
        </div>
      )}
    </div>
  );
}

const COLOR_FIABILIDAD: Record<NivelFiabilidad, string> = { alta: "#34d399", media: "#fbbf24", baja: "#f87171" };

const cuotaTxt = (c: number | null | undefined) => (typeof c === "number" ? c.toFixed(2) : "—");

function BadgeFiabilidad({ nivel, detalle }: { nivel: NivelFiabilidad; detalle?: string }) {
  const color = COLOR_FIABILIDAD[nivel] ?? "#888";
  return (
    <span
      title={detalle}
      style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.4px", textTransform: "uppercase", color, border: `1px solid ${color}66`, background: color + "14", borderRadius: 999, padding: "2px 9px", whiteSpace: "nowrap" }}
    >
      Fiabilidad {nivel}
    </span>
  );
}

function FilaMercado({ item, color }: { item: MercadoItem; color: string }) {
  return (
    <div style={{ position: "relative", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, padding: "8px 12px", borderBottom: "0.5px solid #1a1a1a", overflow: "hidden" }}>
      <div aria-hidden style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${Math.min(Math.max(item.prob, 0), 1) * 100}%`, background: color + "18" }} />
      <span style={{ position: "relative", fontSize: 12.5, color: "#ddd", minWidth: 0 }}>
        {item.nombre}
        {item.nota && <span style={{ display: "block", fontSize: 10, color: "#666", marginTop: 1 }}>{item.nota}</span>}
      </span>
      <span style={{ position: "relative", display: "flex", alignItems: "baseline", gap: 10, flexShrink: 0 }}>
        <strong style={{ fontSize: 13, color: "#fff" }}>{pct(item.prob)}</strong>
        <span style={{ fontSize: 11, color: "#888", minWidth: 44, textAlign: "right" }}>@{cuotaTxt(item.cuota)}</span>
      </span>
    </div>
  );
}

function CeldaLinea({ prob, cuota, color }: { prob: number; cuota: number | null; color: string }) {
  return (
    <div style={{ padding: "7px 10px", background: prob >= 0.5 ? color + "14" : "transparent", display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 6 }}>
      <strong style={{ fontSize: 12.5, color: prob >= 0.5 ? "#fff" : "#aaa" }}>{pct(prob)}</strong>
      <span style={{ fontSize: 11, color: "#777" }}>@{cuotaTxt(cuota)}</span>
    </div>
  );
}

function TablaLineas({ serie, color }: { serie: MercadoSerie; color: string }) {
  return (
    <div style={{ background: "#0d0d0d", border: "0.5px solid #222", borderRadius: 10, overflow: "hidden" }}>
      <div style={{ padding: "8px 12px", fontSize: 11, fontWeight: 700, color: "#bbb", borderBottom: "0.5px solid #222", background: "#121212" }}>
        {serie.etiqueta}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "48px 1fr 1fr", fontSize: 10, color: "#666", textTransform: "uppercase", letterSpacing: "0.4px", padding: "6px 0", borderBottom: "0.5px solid #1a1a1a" }}>
        <span style={{ paddingLeft: 12 }}>Línea</span>
        <span style={{ paddingLeft: 10 }}>Más de</span>
        <span style={{ paddingLeft: 10 }}>Menos de</span>
      </div>
      {serie.lineas.map((l) => (
        <div key={l.linea} style={{ display: "grid", gridTemplateColumns: "48px 1fr 1fr", alignItems: "stretch", borderBottom: "0.5px solid #1a1a1a" }}>
          <span style={{ padding: "7px 0 7px 12px", fontSize: 12.5, fontWeight: 600, color: "#e5e5e5", fontFamily: "monospace" }}>{l.linea}</span>
          <CeldaLinea prob={l.over} cuota={l.cuota_over} color={color} />
          <CeldaLinea prob={l.under} cuota={l.cuota_under} color={color} />
        </div>
      ))}
    </div>
  );
}

export function PanelDebate({ msg }: { msg: ChatMessage }) {
  if (!msg.debate || !msg.data) return null;
  const { debate, data } = msg;
  const sinAgentes = debate._agentes_ok === 0;
  const noFiable = data.estimacion_fiable === false;

  // En el chat solo se muestra el veredicto final. El análisis de los agentes y los mercados
  // se calculan en el servidor pero no se enseñan aquí (los mercados están en "Por equipos").
  return (
    <div style={{ width: "100%", maxWidth: 820, animation: "fadeIn 0.4s ease-out", marginTop: 6 }}>
      {(sinAgentes || noFiable) && (
        <div role="alert" style={{ background: "#2a1212", border: "1px solid #7f1d1d", color: "#fca5a5", borderRadius: 12, padding: "10px 14px", fontSize: 13, marginBottom: 12, lineHeight: 1.5 }}>
          ⚠ Los agentes de IA no respondieron (revisa las claves de API del servidor).{" "}
          {noFiable ? "No hay una predicción fiable para este partido." : "El veredicto se basa solo en el modelo estadístico."}
        </div>
      )}

      <div style={{ background: "#221a0225", border: "1px solid #854d0e55", borderLeft: "4px solid #fbbf24", borderRadius: "4px 12px 12px 4px", padding: 14 }}>
        <div style={{ fontSize: 11, color: "#fbbf24", fontWeight: 700, marginBottom: 6, letterSpacing: "0.5px" }}>
          ⚡ VERDICTO DEL MODERADOR
        </div>
        <p style={{ margin: 0, fontSize: 14, lineHeight: 1.6, color: "#fde68a" }}>{msg.content}</p>
        {!sinAgentes && debate.apuesta_recomendada && debate.apuesta_recomendada !== "N/A" && (
          <p style={{ margin: "10px 0 0", fontSize: 13, color: "#fbbf24" }}>
            <strong>Apuesta sugerida:</strong> {debate.apuesta_recomendada}
            {debate.confianza ? ` · Confianza ${debate.confianza.toLowerCase()}` : ""}
          </p>
        )}
        <p style={{ margin: "10px 0 0", fontSize: 11, color: "#7c6a2a" }}>
          Análisis orientativo: ninguna apuesta es segura.
        </p>
      </div>
    </div>
  );
}

// ─── Bubble Styles ────────────────────────────────────────
const BUBBLE_STYLE: Record<string, React.CSSProperties> = {
  user: { background: "#ff6b35", color: "#fff", borderRadius: "18px 4px 18px 18px", alignSelf: "flex-end" },
  asistente: { background: "#1c1c1c", border: "0.5px solid #2e2e2e", borderRadius: "4px 18px 18px 18px", color: "#ececec" },
  system: { background: "transparent", color: "#ef4444", fontSize: 12, textAlign: "center", alignSelf: "center" },
};

// ─── Main Component ───────────────────────────────────────
function ChatPrediccion() {
  const chatEndRef = useRef<HTMLDivElement>(null);

  const [inputText, setInputText] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "init",
      role: "asistente",
      content:
        "¡Hola! Soy la IA de PrediLiga. Puedes preguntarme lo que quieras, o pedirme que analice un partido escribiendo algo como Barcelona vs Real Madrid o Argentina vs Francia.",
    },
  ]);
  const [isTyping, setIsTyping] = useState(false);
  const [typingText, setTypingText] = useState("");
  const [segundos, setSegundos] = useState(0);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  // Contador real del tiempo transcurrido (sustituye a los retrasos artificiales de antes)
  useEffect(() => {
    if (!isTyping) {
      setSegundos(0);
      return;
    }
    const t = setInterval(() => setSegundos((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [isTyping]);

  const addMsg = (msg: Omit<ChatMessage, "id">) => setMessages((p) => [...p, { ...msg, id: generateId() }]);

  const procesarMensaje = async () => {
    const texto = inputText.trim();
    if (!texto || isTyping) return;
    setInputText("");
    addMsg({ role: "user", content: texto });
    setIsTyping(true);

    const partido = extraerPartido(texto);

    // ── Conversación general ─────────────────────────────
    if (!partido) {
      setTypingText("Escribiendo...");
      try {
        const json = await fetchJson("/chat_ia", { mensaje: texto, user_id: USER_ID_ANONIMO }, TIMEOUT_CHAT_MS);
        if (json.error) throw new Error(json.error);
        addMsg({ role: "asistente", content: json.respuesta });
      } catch {
        addMsg({ role: "system", content: "No se pudo conectar con la IA. Comprueba que el servidor esté en marcha." });
      } finally {
        setIsTyping(false);
      }
      return;
    }

    // ── Análisis de partido ──────────────────────────────
    setTypingText("Analizando el partido: modelo estadístico y agentes de IA");
    try {
      const json = await fetchJson(
        "/prediccion_debate",
        { local: partido.local, visitante: partido.visitante, user_id: USER_ID_ANONIMO },
        TIMEOUT_DEBATE_MS,
      );

      if (json.error) {
        addMsg({ role: "asistente", content: `No pude calcular ese partido. El servidor dice: "${json.error}".` });
        return;
      }

      const debate: DebateAgentes = json.debate_agentes;
      addMsg({
        role: "panel_debate",
        content: debate.conclusion_comentarista,
        data: json.datos_matematicos as PrediccionMatematica,
        debate,
        modo: json.modo as "libre" | "matematico",
      });
    } catch (e) {
      const cortado = e instanceof DOMException && e.name === "AbortError";
      addMsg({
        role: "system",
        content: cortado
          ? "El análisis tardó demasiado y se canceló. Inténtalo de nuevo."
          : "Error de conexión con el servidor.",
      });
    } finally {
      setIsTyping(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", background: "#0d0d0d", fontFamily: "'DM Sans', system-ui, sans-serif", color: "#fff", width: "100%", height: "100%", minHeight: 0, overflow: "hidden" }}>
      <div
        role="log"
        aria-live="polite"
        style={{ flex: 1, padding: "24px 16px", display: "flex", flexDirection: "column", gap: 20, maxWidth: 1150, margin: "0 auto", width: "100%", overflowY: "auto", minHeight: 0 }}
      >
        {messages.map((msg) => {
          if (msg.role === "panel_debate" && msg.debate && msg.data) {
            return <PanelDebate key={msg.id} msg={msg} />;
          }

          const isUser = msg.role === "user";
          const isSystem = msg.role === "system";
          const bubbleStyle = BUBBLE_STYLE[msg.role] ?? {};
          return (
            <div key={msg.id} style={{ display: "flex", flexDirection: "column", alignItems: isUser ? "flex-end" : isSystem ? "center" : "flex-start" }}>
              <div style={{ display: "flex", gap: 10, alignItems: "flex-start", maxWidth: isUser ? "80%" : "85%" }}>
                {!isUser && !isSystem && (
                  <div style={{ width: 30, height: 30, borderRadius: "50%", background: "#1e1e2e", border: "0.5px solid #3a3a5a", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14, flexShrink: 0, marginTop: 2 }}>
                    🤖
                  </div>
                )}
                <div style={{ padding: "12px 16px", fontSize: 14, lineHeight: 1.5, whiteSpace: "pre-wrap", ...bubbleStyle }}>
                  {msg.content}
                </div>
              </div>
            </div>
          );
        })}

        {isTyping && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, animation: "fadeIn 0.2s ease-out" }}>
            <div style={{ width: 30, height: 30, borderRadius: "50%", background: "#1c1c1c", border: "0.5px solid #2e2e2e", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13, flexShrink: 0 }}>
              ⚡
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, background: "#1c1c1c", border: "0.5px solid #2e2e2e", borderRadius: 16, padding: "10px 16px" }}>
              {[0, 200, 400].map((delay) => (
                <span
                  key={delay}
                  style={{ width: 5, height: 5, borderRadius: "50%", background: "#ff6b35", display: "inline-block", animation: "pulse 1.2s infinite", animationDelay: `${delay}ms` }}
                />
              ))}
              <span style={{ fontSize: 12, color: "#999", marginLeft: 4, fontWeight: 500 }}>
                {typingText}
                {segundos >= 3 ? ` · ${segundos}s` : ""}
              </span>
            </div>
          </div>
        )}

        <div ref={chatEndRef} />
      </div>

      <div style={{ padding: 16, background: "#111", borderTop: "0.5px solid #222", display: "flex", gap: 12, alignItems: "center", maxWidth: 1150, margin: "0 auto", width: "100%", flexShrink: 0, borderRadius: "0 0 16px 16px" }}>
        <input
          type="text"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={(e) => {
            // isComposing: no enviar mientras se compone un carácter con teclado IME / tildes muertas
            if (e.key === "Enter" && !e.nativeEvent.isComposing) procesarMensaje();
          }}
          placeholder="Ej: Barcelona vs Sevilla o Argentina vs Francia..."
          aria-label="Escribe un partido o una pregunta"
          maxLength={300}
          disabled={isTyping}
          style={{ flex: 1, padding: "12px 18px", background: "#1c1c1c", border: "0.5px solid #2e2e2e", borderRadius: 24, color: "#ececec", fontSize: 14, outline: "none", fontFamily: "inherit", opacity: isTyping ? 0.5 : 1 }}
        />
        <button
          onClick={procesarMensaje}
          disabled={isTyping || !inputText.trim()}
          aria-label="Enviar"
          style={{ width: 42, height: 42, borderRadius: "50%", background: isTyping || !inputText.trim() ? "#2a2a2a" : "#ff6b35", border: "none", color: "#fff", cursor: isTyping || !inputText.trim() ? "not-allowed" : "pointer", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16, transition: "background 0.15s", flexShrink: 0 }}
        >
          ➤
        </button>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 80%, 100% { opacity: 0.3; transform: scale(0.9); }
          40% { opacity: 1; transform: scale(1.1); }
        }
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}


// ─── Pestaña "Por equipos": elige local y visitante de LaLiga ─────────────
const TITULOS_IDS_PRINCIPALES = new Set(["resultado", "goles"]);

function GrupoCard({ grupo, local, visitante }: { grupo: MercadoGrupo; local: string; visitante: string }) {
  const color = COLOR_FIABILIDAD[grupo.fiabilidad] ?? "#888";
  const detalle = grupo.validacion
    ? `Validado con ${grupo.validacion.partidos} partidos · mejora de ${grupo.validacion.mejora_logloss.toFixed(3)} en log loss sobre la frecuencia histórica`
    : undefined;
  return (
    <section
      aria-label={grupo.titulo}
      style={{ background: "#141414", border: "1px solid #222", borderTop: `3px solid ${color}`, borderRadius: 14, padding: 16, display: "flex", flexDirection: "column", gap: 10, minWidth: 0, boxSizing: "border-box", gridColumn: grupo.tipo === "lineas" ? "1 / -1" : undefined }}
    >
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#fff" }}>{grupo.titulo}</h3>
        <BadgeFiabilidad nivel={grupo.fiabilidad} detalle={detalle} />
      </div>
      <p style={{ margin: 0, fontSize: 11.5, lineHeight: 1.45, color: "#777" }}>
        {grupo.nota}
        {grupo.validacion ? ` (validado con ${grupo.validacion.partidos} partidos)` : ""}
      </p>

      {grupo.esperado && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 14, fontSize: 12, color: "#aaa" }}>
          <span>{local}: <strong style={{ color: teamColor(local) }}>{grupo.esperado.local.toFixed(2)}</strong></span>
          <span>{visitante}: <strong style={{ color: teamColor(visitante) }}>{grupo.esperado.visitante.toFixed(2)}</strong></span>
          <span>Total: <strong style={{ color: "#fff" }}>{grupo.esperado.total.toFixed(2)}</strong></span>
        </div>
      )}

      {grupo.tipo === "lineas" && grupo.series && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(260px, 100%), 1fr))", gap: 12 }}>
          {grupo.series.map((se) => (
            <TablaLineas key={se.ambito} serie={se} color={color} />
          ))}
        </div>
      )}

      {grupo.tipo === "lista" && grupo.items && (
        <div style={{ background: "#0d0d0d", border: "0.5px solid #222", borderRadius: 10, overflow: "hidden" }}>
          {grupo.items.map((it) => (
            <FilaMercado key={it.nombre} item={it} color={color} />
          ))}
        </div>
      )}
    </section>
  );
}

function SelectorEquipo({
  etiqueta,
  valor,
  onChange,
  equipos,
  deshabilitado,
}: {
  etiqueta: string;
  valor: string;
  onChange: (v: string) => void;
  equipos: string[];
  deshabilitado?: string;
}) {
  const id = useId();
  return (
    <div style={{ flex: "1 1 220px", display: "flex", alignItems: "center", gap: 12, background: "#141414", border: "1px solid #222", borderRadius: 14, padding: "12px 14px", minWidth: 0 }}>
      {valor ? <TeamCircle name={valor} size={46} /> : <div style={{ width: 46, height: 46, borderRadius: "50%", border: "1.5px dashed #333", flexShrink: 0 }} />}
      <div style={{ flex: 1, minWidth: 0 }}>
        <label htmlFor={id} style={{ display: "block", fontSize: 10, color: "#777", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 4, fontWeight: 600 }}>
          {etiqueta}
        </label>
        <select
          id={id}
          value={valor}
          onChange={(e) => onChange(e.target.value)}
          style={{ width: "100%", background: "#1c1c1c", color: "#ececec", border: "0.5px solid #2e2e2e", borderRadius: 10, padding: "9px 10px", fontSize: 14, fontFamily: "inherit", outline: "none", cursor: "pointer" }}
        >
          <option value="">Elige un equipo…</option>
          {equipos.map((e) => (
            <option key={e} value={e} disabled={e === deshabilitado}>
              {e}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

function PrediccionEquipos() {
  const [equipos, setEquipos] = useState<string[]>([]);
  const [errorEquipos, setErrorEquipos] = useState(false);
  const [local, setLocal] = useState("");
  const [visitante, setVisitante] = useState("");
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState("");
  const [data, setData] = useState<PrediccionMatematica | null>(null);
  const [segundos, setSegundos] = useState(0);

  const cargarEquipos = () => {
    setErrorEquipos(false);
    fetch(`${API}/equipos_liga`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((j) => setEquipos(Array.isArray(j.equipos) ? j.equipos : []))
      .catch(() => setErrorEquipos(true));
  };
  useEffect(cargarEquipos, []);

  useEffect(() => {
    if (!cargando) {
      setSegundos(0);
      return;
    }
    const t = setInterval(() => setSegundos((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [cargando]);

  const puede = !!local && !!visitante && local !== visitante && !cargando;

  const predecir = async () => {
    if (!puede) return;
    setCargando(true);
    setError("");
    try {
      const json = await fetchJson("/prediccion_equipos", { local, visitante }, 90_000);
      if (json.error) throw new Error(json.error);
      setData(json.datos_matematicos as PrediccionMatematica);
    } catch (e) {
      setData(null);
      const cortado = e instanceof DOMException && e.name === "AbortError";
      setError(cortado ? "Tardó demasiado. El servidor puede estar despertando: inténtalo de nuevo." : "No se pudo calcular el partido. Comprueba que el servidor esté en marcha.");
    } finally {
      setCargando(false);
    }
  };

  const intercambiar = () => {
    setLocal(visitante);
    setVisitante(local);
    setData(null);
  };

  const m = data?.mercados;
  const grupos = Array.isArray(m?.grupos) ? m!.grupos : [];
  const destacados = Array.isArray(m?.destacados) ? m!.destacados : [];
  const principales = grupos.filter((g) => TITULOS_IDS_PRINCIPALES.has(g.id));
  const resto = grupos.filter((g) => !TITULOS_IDS_PRINCIPALES.has(g.id));

  return (
    <div style={{ height: "100%", overflowY: "auto", background: "#0d0d0d", fontFamily: "'DM Sans', system-ui, sans-serif", color: "#fff" }}>
      <div style={{ maxWidth: 1150, margin: "0 auto", padding: "24px 16px 40px", display: "flex", flexDirection: "column", gap: 18 }}>
        <div>
          <div style={{ fontSize: 11, color: "#666", textTransform: "uppercase", letterSpacing: "1px", fontWeight: 600 }}>LaLiga · temporada actual</div>
          <h2 style={{ margin: "4px 0 0", fontSize: 22 }}>Elige los dos equipos</h2>
        </div>

        {errorEquipos && (
          <div role="alert" style={{ background: "#2a1212", border: "1px solid #7f1d1d", color: "#fca5a5", borderRadius: 12, padding: "10px 14px", fontSize: 13 }}>
            No se pudo cargar la lista de equipos.{" "}
            <button type="button" onClick={cargarEquipos} style={{ background: "none", border: "none", color: "#fff", textDecoration: "underline", cursor: "pointer", fontFamily: "inherit", fontSize: 13, padding: 0 }}>
              Reintentar
            </button>
          </div>
        )}

        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12 }}>
          <SelectorEquipo etiqueta="Local" valor={local} onChange={(v) => { setLocal(v); setData(null); }} equipos={equipos} deshabilitado={visitante} />
          <button
            type="button"
            onClick={intercambiar}
            disabled={!local && !visitante}
            aria-label="Intercambiar local y visitante"
            title="Intercambiar"
            style={{ width: 40, height: 40, borderRadius: "50%", background: "#1c1c1c", border: "0.5px solid #2e2e2e", color: "#ccc", cursor: !local && !visitante ? "not-allowed" : "pointer", fontSize: 16, flexShrink: 0 }}
          >
            ⇄
          </button>
          <SelectorEquipo etiqueta="Visitante" valor={visitante} onChange={(v) => { setVisitante(v); setData(null); }} equipos={equipos} deshabilitado={local} />
        </div>

        <button
          type="button"
          onClick={predecir}
          disabled={!puede}
          style={{ alignSelf: "flex-start", padding: "12px 26px", borderRadius: 24, border: "none", background: puede ? "#ff6b35" : "#2a2a2a", color: "#fff", fontSize: 14, fontWeight: 700, fontFamily: "inherit", cursor: puede ? "pointer" : "not-allowed" }}
        >
          {cargando ? `Calculando…${segundos >= 3 ? ` ${segundos}s` : ""}` : "⚡ Generar predicciones"}
        </button>

        {error && (
          <div role="alert" style={{ color: "#ef4444", fontSize: 13 }}>
            {error}
          </div>
        )}

        {data && (
          <div style={{ display: "flex", flexDirection: "column", gap: 14, animation: "fadeIn 0.4s ease-out" }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(320px, 100%), 1fr))", gap: 14, alignItems: "stretch" }}>
              <PredCard data={data} esLibre={false} />
              {destacados.length > 0 && (
                <section aria-label="Destacados del modelo" style={{ background: "#141414", border: "1px solid #222", borderRadius: 14, padding: 16, boxSizing: "border-box" }}>
                  <h3 style={{ margin: "0 0 4px", fontSize: 15 }}>⭐ Destacados del modelo</h3>
                  <p style={{ margin: "0 0 10px", fontSize: 11.5, color: "#777", lineHeight: 1.45 }}>{m?.nota}</p>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {destacados.map((d) => (
                      <div
                        key={`${d.grupo}-${d.nombre}`}
                        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, background: "#1a1a1a", border: `1px solid ${COLOR_FIABILIDAD[d.fiabilidad] ?? "#333"}44`, borderRadius: 10, padding: "8px 12px" }}
                      >
                        <span style={{ fontSize: 12.5, color: "#e5e5e5", minWidth: 0 }}>
                          {d.nombre}
                          <span style={{ display: "block", fontSize: 10, color: "#666" }}>{d.grupo_titulo} · fiabilidad {d.fiabilidad}</span>
                        </span>
                        <span style={{ flexShrink: 0, fontSize: 12.5 }}>
                          <strong>{pct(d.prob)}</strong> <span style={{ color: "#888" }}>@{cuotaTxt(d.cuota)}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(320px, 100%), 1fr))", gap: 14, alignItems: "start", gridAutoFlow: "dense" }}>
              {[...principales, ...resto].map((g) => (
                <GrupoCard key={g.id} grupo={g} local={data.equipo_local} visitante={data.equipo_visitante} />
              ))}
            </div>

            <p style={{ margin: 0, fontSize: 10.5, color: "#666" }}>
              @ = cuota justa del modelo (sin margen de la casa). Compárala con la cuota real: solo hay valor si la real es mayor. Análisis orientativo: ninguna apuesta es segura.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Pestaña "Próximos partidos": ligas y partidos reales de FootyStats ───────────
interface TemporadaFS {
  id: number;
  etiqueta: string;
}

interface LigaFS {
  pais: string;
  nombre: string;
  temporadas: TemporadaFS[];
  temporada_reciente_id: number;
}

interface PartidoFS {
  estado?: string; // en este listado siempre "proximo": lo que ya pasó su hora está en En vivo
  id: number;
  local: string;
  visitante: string;
  fecha_unix: number;
  jornada?: number | null;
}

type Veredicto = "supera_referencia" | "similar_a_referencia" | "peor_que_referencia" | "no_concluyente";

interface CalidadMercado {
  logloss_modelo: number;
  logloss_referencia: number;
  veredicto: Veredicto;
  logloss_cuotas?: number;
  logloss_modelo_mismos?: number;
  partidos_con_cuotas?: number;
}

interface CalidadModelo {
  estado: "medida" | "insuficiente_para_medir" | "no_medida";
  motivo?: string;
  partidos_prueba?: number;
  prueba_desde?: string;
  prueba_hasta?: string;
  variante?: string;
  mercados?: Record<string, CalidadMercado>;
}

interface PrediccionFS {
  estado: "ok" | "historial_insuficiente";
  mensaje?: string;
  detalle?: string[];
  partido: { local: string; visitante: string; jugado: boolean };
  resultado_1x2?: { local: number; empate: number; visitante: number };
  mas_2_5?: { si: number; no: number };
  ambos_marcan?: { si: number; no: number };
  goles_esperados?: { local: number; visitante: number };
  historial?: { partidos: number; temporadas: string[]; desde: string; hasta: string; desactualizado: boolean };
  modelo?: { variante: string };
  calidad?: CalidadModelo;
}

interface EstadoPrediccion {
  cargando: boolean;
  error?: string;
  data?: PrediccionFS;
}

// Reparte 100 % entre las probabilidades (en décimas) para que el total sea exactamente 100,0 %.
const porcentajesQueSuman100 = (probs: number[]): number[] => {
  const total = probs.reduce((a, b) => a + b, 0) || 1;
  const exactos = probs.map((p) => (p / total) * 1000);
  const base = exactos.map(Math.floor);
  let resto = 1000 - base.reduce((a, b) => a + b, 0);
  exactos
    .map((x, i) => ({ i, frac: x - Math.floor(x) }))
    .sort((a, b) => b.frac - a.frac)
    .forEach(({ i }) => {
      if (resto-- > 0) base[i] += 1;
    });
  return base.map((d) => d / 10);
};

const ETIQUETA_VEREDICTO: Record<Veredicto, string> = {
  supera_referencia: "supera la referencia",
  similar_a_referencia: "no se distingue de la referencia",
  peor_que_referencia: "es peor que la referencia",
  no_concluyente: "no concluyente",
};

const NOMBRE_MERCADO = { resultado_1x2: "1X2", mas_2_5: "Más de 2.5", ambos_marcan: "Ambos marcan" } as const;

function PanelPrediccion({ est }: { est: EstadoPrediccion }) {
  const caja: React.CSSProperties = { padding: "12px 14px 14px", borderTop: "0.5px solid #1f1f1f", background: "#101010" };
  if (est.cargando) {
    return (
      <div role="status" style={{ ...caja, fontSize: 13, color: "#999" }}>
        Calculando predicción… la primera vez en una liga descarga su historial.
      </div>
    );
  }
  if (est.error) {
    return (
      <div role="alert" style={{ ...caja, fontSize: 13, color: "#fca5a5" }}>
        {est.error}
      </div>
    );
  }
  const d = est.data;
  if (!d) return null;
  if (d.estado === "historial_insuficiente" || !d.resultado_1x2 || !d.mas_2_5 || !d.ambos_marcan) {
    return (
      <div role="alert" style={{ ...caja, fontSize: 13, color: "#fbbf24" }}>
        <strong>{d.mensaje ?? "Historial insuficiente para este partido"}</strong>
        <ul style={{ margin: "6px 0 0", paddingLeft: 18, color: "#c9a23a", lineHeight: 1.5 }}>
          {(d.detalle ?? []).map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      </div>
    );
  }
  const [pl, pe, pv] = porcentajesQueSuman100([d.resultado_1x2.local, d.resultado_1x2.empate, d.resultado_1x2.visitante]);
  const c = d.calidad;
  const medida = c?.estado === "medida";
  const resumenCalidad = medida && c?.mercados
    ? (Object.keys(NOMBRE_MERCADO) as (keyof typeof NOMBRE_MERCADO)[])
        .map((k) => `${NOMBRE_MERCADO[k]}: ${ETIQUETA_VEREDICTO[c.mercados![k].veredicto]}`)
        .join(" · ")
    : c?.motivo ?? "Todavía no hay una medición de la calidad para esta competición.";
  return (
    <div style={caja} aria-label={`Predicción ${d.partido.local} vs ${d.partido.visitante}`}>
      <div role="img" aria-label={`${d.partido.local} ${pl}%, empate ${pe}%, ${d.partido.visitante} ${pv}%`}>
        <div style={{ display: "flex", borderRadius: 8, overflow: "hidden", height: 34 }}>
          {[
            { v: pl, bg: "#1e1030", color: "#c4b5fd" },
            { v: pe, bg: "#1c1c1c", color: "#888" },
            { v: pv, bg: "#0f1e30", color: "#93c5fd" },
          ].map((s, i) => (
            <div key={i} style={{ flex: Math.max(s.v, 4), background: s.bg, display: "flex", alignItems: "center", justifyContent: "center", borderLeft: i ? "1px solid #111" : "none" }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: s.color }}>{s.v.toFixed(1)}%</span>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginTop: 4, fontSize: 10, color: "#777" }}>
          <span>Gana {d.partido.local}</span>
          <span>Empate</span>
          <span>Gana {d.partido.visitante}</span>
        </div>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 18, marginTop: 12, fontSize: 12.5, color: "#ccc" }}>
        <span>
          Más de 2.5 goles: <strong style={{ color: "#60a5fa" }}>{(d.mas_2_5.si * 100).toFixed(1)}%</strong>
        </span>
        <span>
          Ambos marcan: <strong style={{ color: "#fbbf24" }}>{(d.ambos_marcan.si * 100).toFixed(1)}%</strong>
        </span>
        {d.goles_esperados && (
          <span>
            Goles esperados: <strong style={{ color: "#fff" }}>{d.goles_esperados.local.toFixed(2)} – {d.goles_esperados.visitante.toFixed(2)}</strong>
          </span>
        )}
      </div>
      <div
        style={{ marginTop: 12, padding: "8px 10px", borderRadius: 8, border: `1px solid ${medida ? "#14532d" : "#854d0e"}`, background: medida ? "#052e1633" : "#221a0233", fontSize: 11.5, lineHeight: 1.5, color: medida ? "#86efac" : "#fcd34d" }}
      >
        <strong>{medida ? "Calidad medida" : "Calidad sin medir"}</strong>
        {medida && c
          ? ` con ${c.partidos_prueba} partidos posteriores al entrenamiento (${c.prueba_desde} a ${c.prueba_hasta}), frente a la frecuencia histórica de la liga. ${resumenCalidad}.`
          : `. ${resumenCalidad}`}
      </div>
      <p style={{ margin: "8px 0 0", fontSize: 10.5, color: "#666" }}>
        {d.partido.jugado ? "Partido ya jugado: la predicción usa solo datos anteriores a su fecha. " : ""}
        Historial usado: {d.historial?.partidos} partidos terminados ({d.historial?.temporadas.join(", ")}). Modelo de goles {d.modelo?.variante === "xg" ? "con xG" : "sin xG"}.
        {d.historial?.desactualizado ? " Aviso: no se pudo actualizar el historial; se usó la copia guardada." : ""} Análisis orientativo: ninguna apuesta es segura.
      </p>
    </div>
  );
}

// ─── Más mercados: Resultado, Goles, Córners, Tarjetas y Tiros ───────────────────────────
type VeredictoLinea = Veredicto | "sin_validar";

interface LineaConteo {
  linea: number;
  mas: number;
  menos: number;
  veredicto: VeredictoLinea;
  frecuencia_historica?: number | null;
  partidos_prueba?: number | null;
}

interface BloqueConteo {
  esperados: { local: number; visitante: number; total: number };
  lineas: LineaConteo[];
  calidad?: { estado: string; partidos_prueba?: number; total_esperado?: { error_abs_modelo: number; error_abs_referencia: number; media_real: number } } | null;
}

interface MercadosFS {
  doble_oportunidad: { "1x": number; "12": number; x2: number };
  empate_no_apuesta: { local: number; visitante: number };
  goles_totales: { linea: number; mas: number; menos: number }[];
  goles_equipo: { local: { linea: number; mas: number }[]; visitante: { linea: number; mas: number }[] };
  porteria_cero: { local: number; visitante: number };
  marcadores: { local: number; visitante: number; p: number }[];
  primer_tiempo?: { resultado: { local: number; empate: number; visitante: number }; mas_0_5: number; mas_1_5: number; goles_esperados: { local: number; visitante: number } } | null;
  corners?: BloqueConteo | null;
  tarjetas?: BloqueConteo | null;
  tiros?: BloqueConteo | null;
  tiros_puerta?: BloqueConteo | null;
  pendientes?: { mercado: string; nombre: string; motivo: string }[];
}

interface DetalleFS extends PrediccionFS {
  mercados?: MercadosFS | null;
  cuotas?: { resultado_1x2?: { local: number; empate: number; visitante: number; margen: number } | null } | null;
}

interface EstadoDetalle {
  abierto: boolean;
  cargando: boolean;
  error?: string;
  data?: DetalleFS;
}

type GrupoMercado = "resultado" | "goles" | "corners" | "tarjetas" | "tiros";
const GRUPOS: { id: GrupoMercado; titulo: string }[] = [
  { id: "resultado", titulo: "Resultado" },
  { id: "goles", titulo: "Goles" },
  { id: "corners", titulo: "Córners" },
  { id: "tarjetas", titulo: "Tarjetas" },
  { id: "tiros", titulo: "Tiros" },
];

const INSIGNIA: Record<VeredictoLinea, { texto: string; color: string }> = {
  supera_referencia: { texto: "Medido: supera la referencia", color: "#34d399" },
  similar_a_referencia: { texto: "Sin ventaja demostrada", color: "#fbbf24" },
  peor_que_referencia: { texto: "Peor que la referencia", color: "#f87171" },
  no_concluyente: { texto: "Pocos partidos de prueba", color: "#9ca3af" },
  sin_validar: { texto: "Sin validar", color: "#9ca3af" },
};

function Insignia({ v }: { v?: VeredictoLinea }) {
  const i = INSIGNIA[v ?? "sin_validar"];
  return (
    <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: "0.3px", color: i.color, border: `1px solid ${i.color}55`, background: i.color + "12", borderRadius: 999, padding: "1px 8px", whiteSpace: "nowrap" }}>
      {i.texto}
    </span>
  );
}

const pc = (p: number) => `${(p * 100).toFixed(1)}%`;

function FilaMercado2({ etiqueta, prob, v, nota }: { etiqueta: string; prob: number; v?: VeredictoLinea; nota?: string }) {
  return (
    <div style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, padding: "7px 10px", borderBottom: "0.5px solid #1a1a1a", overflow: "hidden" }}>
      <div aria-hidden style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${Math.min(Math.max(prob, 0), 1) * 100}%`, background: "#ff6b3514" }} />
      <span style={{ position: "relative", fontSize: 12.5, color: "#ddd", minWidth: 0 }}>
        {etiqueta}
        {nota && <span style={{ display: "block", fontSize: 10, color: "#666" }}>{nota}</span>}
      </span>
      <span style={{ position: "relative", display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
        <Insignia v={v} />
        <strong style={{ fontSize: 13, color: "#fff", minWidth: 50, textAlign: "right" }}>{pc(prob)}</strong>
      </span>
    </div>
  );
}

function Bloque({ titulo, nota, children }: { titulo: string; nota?: string; children: React.ReactNode }) {
  return (
    <section style={{ background: "#0d0d0d", border: "0.5px solid #222", borderRadius: 10, overflow: "hidden" }}>
      <div style={{ padding: "8px 10px", background: "#121212", borderBottom: "0.5px solid #222" }}>
        <div style={{ fontSize: 11.5, fontWeight: 700, color: "#bbb" }}>{titulo}</div>
        {nota && <div style={{ fontSize: 10, color: "#666", marginTop: 2 }}>{nota}</div>}
      </div>
      {children}
    </section>
  );
}

function GrupoConteo({ nombre, unidad, bloque, pendiente }: { nombre: string; unidad: string; bloque?: BloqueConteo | null; pendiente?: string }) {
  if (!bloque) {
    return (
      <div role="note" style={{ border: "1px solid #854d0e", background: "#221a0233", color: "#fcd34d", borderRadius: 10, padding: "10px 12px", fontSize: 12.5, lineHeight: 1.5 }}>
        <strong>Pendiente: {nombre}.</strong> {pendiente ?? "No hay datos suficientes en FootyStats para esta competición."}
      </div>
    );
  }
  const te = bloque.calidad?.total_esperado;
  return (
    <>
      <Bloque titulo={`Total esperado de ${unidad}`} nota="Es una estimación del número de eventos, no una probabilidad.">
        <div style={{ padding: "10px 12px", display: "flex", flexWrap: "wrap", gap: 18, alignItems: "baseline" }}>
          <span style={{ fontSize: 22, fontWeight: 700, color: "#fff" }}>{bloque.esperados.total.toFixed(1)}</span>
          <span style={{ fontSize: 12, color: "#aaa" }}>
            Local {bloque.esperados.local.toFixed(1)} · Visitante {bloque.esperados.visitante.toFixed(1)}
          </span>
          {te && (
            <span style={{ fontSize: 10.5, color: "#777" }}>
              Error medio en partidos de prueba: {te.error_abs_modelo.toFixed(2)} (promedio de la liga como referencia: {te.error_abs_referencia.toFixed(2)})
            </span>
          )}
        </div>
      </Bloque>
      <Bloque titulo={`Probabilidad de superar una línea de ${unidad}`} nota="Cada línea se valida por separado con partidos posteriores al entrenamiento.">
        {bloque.lineas.map((l) => (
          <div key={l.linea}>
            <FilaMercado2 etiqueta={`Más de ${l.linea} ${unidad}`} prob={l.mas} v={l.veredicto} nota={l.partidos_prueba ? `${l.partidos_prueba} partidos de prueba · frecuencia histórica de la liga ${pc(l.frecuencia_historica ?? 0)}` : undefined} />
            <FilaMercado2 etiqueta={`Menos de ${l.linea} ${unidad}`} prob={l.menos} v={l.veredicto} />
          </div>
        ))}
      </Bloque>
    </>
  );
}

function PanelMercados({ est, onCerrar }: { est: EstadoDetalle; onCerrar: () => void }) {
  const [grupo, setGrupo] = useState<GrupoMercado>("resultado");
  const caja: React.CSSProperties = { padding: "12px 14px 14px", borderTop: "0.5px solid #1f1f1f", background: "#0c0c0c" };
  if (est.cargando) {
    return (
      <div role="status" style={{ ...caja, fontSize: 13, color: "#999" }}>
        Calculando todos los mercados…
      </div>
    );
  }
  if (est.error) {
    return (
      <div role="alert" style={{ ...caja, fontSize: 13, color: "#fca5a5" }}>
        {est.error}
      </div>
    );
  }
  const d = est.data;
  const m = d?.mercados;
  if (!d || !m || !d.resultado_1x2) return null;
  const cal = d.calidad?.mercados ?? {};
  const extra = (d.calidad as { extra?: Record<string, { estado: string; motivo?: string; lineas?: { linea: number; veredicto: Veredicto }[] }> } | undefined)?.extra ?? {};
  const v = (k: string): VeredictoLinea => (cal[k]?.veredicto as VeredictoLinea) ?? "sin_validar";
  const pend = (k: string) => m.pendientes?.find((x) => x.mercado === k)?.motivo;
  const lineaGoles = (L: number) => m.goles_totales.find((x) => x.linea === L);
  const vPrimer = (L: number): VeredictoLinea => (extra.goles_1t?.lineas?.find((x) => x.linea === L)?.veredicto as VeredictoLinea) ?? "sin_validar";
  const [pl, pe, pv] = porcentajesQueSuman100([d.resultado_1x2.local, d.resultado_1x2.empate, d.resultado_1x2.visitante]);
  const local = d.partido.local;
  const visita = d.partido.visitante;

  return (
    <div style={caja} aria-label={`Mercados ${local} vs ${visita}`} data-detalle-partido={`${local} vs ${visita}`}>
      <div role="tablist" aria-label="Grupos de mercados" style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
        {GRUPOS.map((g) => (
          <button
            key={g.id}
            type="button"
            role="tab"
            aria-selected={grupo === g.id}
            onClick={() => setGrupo(g.id)}
            style={{ cursor: "pointer", fontFamily: "inherit", fontSize: 12.5, fontWeight: grupo === g.id ? 700 : 500, color: grupo === g.id ? "#fff" : "#999", background: grupo === g.id ? "#26262a" : "#141414", border: `1px solid ${grupo === g.id ? "#ff6b35" : "#262626"}`, borderRadius: 999, padding: "5px 14px" }}
          >
            {g.titulo}
          </button>
        ))}
        <button type="button" onClick={onCerrar} style={{ marginLeft: "auto", cursor: "pointer", fontFamily: "inherit", fontSize: 12, color: "#999", background: "none", border: "none", textDecoration: "underline" }}>
          Ocultar mercados
        </button>
      </div>

      <div role="tabpanel" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {grupo === "resultado" && (
          <>
            <Bloque titulo="Resultado final (1X2)">
              <FilaMercado2 etiqueta={`Gana ${local}`} prob={pl / 100} v={v("resultado_1x2")} />
              <FilaMercado2 etiqueta="Empate" prob={pe / 100} v={v("resultado_1x2")} />
              <FilaMercado2 etiqueta={`Gana ${visita}`} prob={pv / 100} v={v("resultado_1x2")} />
            </Bloque>
            <Bloque titulo="Doble oportunidad">
              <FilaMercado2 etiqueta={`${local} o empate (1X)`} prob={m.doble_oportunidad["1x"]} v={v("doble_1x")} />
              <FilaMercado2 etiqueta={`${local} o ${visita} (12)`} prob={m.doble_oportunidad["12"]} v={v("doble_12")} />
              <FilaMercado2 etiqueta={`${visita} o empate (X2)`} prob={m.doble_oportunidad.x2} v={v("doble_x2")} />
            </Bloque>
            <Bloque titulo="Empate no apuesta" nota="Probabilidad de ganar si se descarta el empate. Se deriva del 1X2; no se valida aparte.">
              <FilaMercado2 etiqueta={local} prob={m.empate_no_apuesta.local} />
              <FilaMercado2 etiqueta={visita} prob={m.empate_no_apuesta.visitante} />
            </Bloque>
            {d.cuotas?.resultado_1x2 && (
              <p style={{ margin: 0, fontSize: 11, color: "#777" }}>
                Para comparar, las cuotas de FootyStats sin margen implican: {local} {pc(d.cuotas.resultado_1x2.local)} · empate {pc(d.cuotas.resultado_1x2.empate)} · {visita} {pc(d.cuotas.resultado_1x2.visitante)}.
              </p>
            )}
          </>
        )}

        {grupo === "goles" && (
          <>
            <Bloque titulo="Total de goles del partido">
              {[1.5, 2.5, 3.5].map((L) => {
                const x = lineaGoles(L);
                const clave = L === 1.5 ? "mas_1_5" : L === 2.5 ? "mas_2_5" : "mas_3_5";
                return x ? (
                  <div key={L}>
                    <FilaMercado2 etiqueta={`Más de ${L} goles`} prob={x.mas} v={v(clave)} />
                    <FilaMercado2 etiqueta={`Menos de ${L} goles`} prob={x.menos} v={v(clave)} />
                  </div>
                ) : null;
              })}
            </Bloque>
            <Bloque titulo="Ambos equipos marcan">
              <FilaMercado2 etiqueta="Sí" prob={d.ambos_marcan?.si ?? 0} v={v("ambos_marcan")} />
              <FilaMercado2 etiqueta="No" prob={d.ambos_marcan?.no ?? 0} v={v("ambos_marcan")} />
            </Bloque>
            <Bloque titulo="Cada equipo marca" nota="Probabilidad de marcar al menos un gol.">
              <FilaMercado2 etiqueta={`${local} marca`} prob={m.goles_equipo.local[0]?.mas ?? 0} v={v("marca_local")} />
              <FilaMercado2 etiqueta={`${visita} marca`} prob={m.goles_equipo.visitante[0]?.mas ?? 0} v={v("marca_visitante")} />
            </Bloque>
            <Bloque titulo="Portería a cero" nota="Un equipo mantiene la portería a cero cuando el rival no marca.">
              <FilaMercado2 etiqueta={`${local} no encaja`} prob={m.porteria_cero.local} v={v("marca_visitante")} />
              <FilaMercado2 etiqueta={`${visita} no encaja`} prob={m.porteria_cero.visitante} v={v("marca_local")} />
            </Bloque>
            <Bloque titulo="Marcadores exactos más probables" nota="Es el mercado más difícil: incluso el más probable rara vez supera el 15 %.">
              {m.marcadores.slice(0, 3).map((x) => (
                <FilaMercado2 key={`${x.local}-${x.visitante}`} etiqueta={`${local} ${x.local} – ${x.visitante} ${visita}`} prob={x.p} v={v("marcador_exacto")} />
              ))}
            </Bloque>
            {m.primer_tiempo ? (
              <Bloque titulo="Primer tiempo">
                <FilaMercado2 etiqueta="Más de 0.5 goles en el primer tiempo" prob={m.primer_tiempo.mas_0_5} v={vPrimer(0.5)} />
                <FilaMercado2 etiqueta="Más de 1.5 goles en el primer tiempo" prob={m.primer_tiempo.mas_1_5} v={vPrimer(1.5)} />
              </Bloque>
            ) : (
              pend("primer_tiempo") && (
                <div role="note" style={{ fontSize: 12, color: "#fcd34d" }}>
                  <strong>Pendiente: primer tiempo.</strong> {pend("primer_tiempo")}
                </div>
              )
            )}
          </>
        )}

        {grupo === "corners" && <GrupoConteo nombre="córners" unidad="córners" bloque={m.corners} pendiente={pend("corners")} />}
        {grupo === "tarjetas" && <GrupoConteo nombre="tarjetas" unidad="tarjetas" bloque={m.tarjetas} pendiente={pend("tarjetas")} />}
        {grupo === "tiros" && (
          <>
            <GrupoConteo nombre="tiros" unidad="tiros" bloque={m.tiros} pendiente={pend("tiros")} />
            <GrupoConteo nombre="tiros a puerta" unidad="tiros a puerta" bloque={m.tiros_puerta} pendiente={pend("tiros_puerta")} />
          </>
        )}
      </div>

      <p style={{ margin: "12px 0 0", fontSize: 10.5, color: "#666", lineHeight: 1.5 }}>
        «Medido» significa que, con partidos posteriores a los usados para entrenar, ese mercado tuvo menos error que la frecuencia histórica de la liga. «Sin ventaja demostrada» no quiere decir que sea malo, solo que la medición no lo distingue de la referencia. Análisis orientativo: ninguna apuesta es segura.
      </p>
    </div>
  );
}

// FootyStats nombra los países en inglés: estos alias permiten buscar "España" o "Alemania".
const ALIAS_PAIS: Record<string, string> = {
  USA: "Estados Unidos EEUU",
  Germany: "Alemania",
  Europe: "Europa",
  Netherlands: "Países Bajos Holanda",
  France: "Francia",
  Italy: "Italia",
  Belgium: "Bélgica",
  Brazil: "Brasil",
  Japan: "Japón",
  Mexico: "México",
  "Republic of Ireland": "Irlanda",
  "Northern Ireland": "Irlanda del Norte",
  Philippines: "Filipinas",
  Peru: "Perú",
  "South Korea": "Corea del Sur",
  "South America": "Sudamérica",
  Africa: "África",
  Panama: "Panamá",
  International: "Internacional",
  UAE: "Emiratos Árabes Unidos",
  Spain: "España",
};

interface EntradaSelector {
  clave: string;
  etiqueta: string;
  busca?: string;
}

function SelectorLiga({
  ligas,
  valor,
  cargando,
  onElegir,
  extras,
  titulo = "Liga · busca por país o competición",
}: {
  ligas: LigaFS[];
  valor: string;
  cargando: boolean;
  onElegir: (clave: string) => void;
  extras?: EntradaSelector[]; // opciones adicionales al inicio (p. ej. "Todas mis competiciones" o un país completo)
  titulo?: string;
}) {
  const id = useId();
  const [abierto, setAbierto] = useState(false);
  const [texto, setTexto] = useState("");
  const [activo, setActivo] = useState(0);
  const norm = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const etiqueta = (l: LigaFS) => `${l.pais} – ${l.nombre}`;
  const entradas: (EntradaSelector & { busca: string })[] = [
    ...(extras ?? []).map((x) => ({ ...x, busca: `${x.etiqueta} ${x.busca ?? ""}` })),
    ...ligas.map((l) => ({ clave: String(l.temporada_reciente_id), etiqueta: etiqueta(l), busca: `${etiqueta(l)} ${ALIAS_PAIS[l.pais] ?? ""}` })),
  ];
  const seleccionada = entradas.find((x) => x.clave === valor);
  const consulta = norm(texto).trim();
  const filtradas = consulta ? entradas.filter((x) => norm(x.busca).includes(consulta)) : entradas;

  const elegir = (x: EntradaSelector) => {
    onElegir(x.clave);
    setAbierto(false);
    setTexto("");
  };

  return (
    <div style={{ position: "relative", maxWidth: 520 }}>
      <div style={{ background: "#141414", border: "1px solid #222", borderRadius: 14, padding: "12px 14px" }}>
        <label htmlFor={id} style={{ display: "block", fontSize: 10, color: "#777", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 4, fontWeight: 600 }}>
          {titulo}
        </label>
        <input
          id={id}
          type="text"
          role="combobox"
          aria-expanded={abierto}
          aria-controls={`${id}-lista`}
          aria-autocomplete="list"
          autoComplete="off"
          disabled={cargando || ligas.length === 0}
          placeholder={cargando ? "Cargando ligas…" : "Escribe: Perú, La Liga, Champions…"}
          value={abierto ? texto : seleccionada ? seleccionada.etiqueta : ""}
          onFocus={() => {
            setTexto("");
            setActivo(0);
            setAbierto(true);
          }}
          onBlur={() => setAbierto(false)}
          onChange={(e) => {
            setTexto(e.target.value);
            setActivo(0);
            setAbierto(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setAbierto(true);
              setActivo((a) => Math.min(a + 1, filtradas.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActivo((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter" && abierto && filtradas[activo] && !e.nativeEvent.isComposing) {
              e.preventDefault();
              elegir(filtradas[activo]);
            } else if (e.key === "Escape") {
              setAbierto(false);
            }
          }}
          style={{ width: "100%", boxSizing: "border-box", background: "#1c1c1c", color: "#ececec", border: "0.5px solid #2e2e2e", borderRadius: 10, padding: "9px 10px", fontSize: 14, fontFamily: "inherit", outline: "none" }}
        />
      </div>
      {abierto && (
        <ul
          id={`${id}-lista`}
          role="listbox"
          aria-label="Ligas disponibles"
          style={{ position: "absolute", zIndex: 20, left: 0, right: 0, top: "100%", margin: "4px 0 0", padding: 4, listStyle: "none", maxHeight: 320, overflowY: "auto", background: "#141414", border: "1px solid #2a2a2a", borderRadius: 12, boxShadow: "0 8px 24px #000a" }}
        >
          {filtradas.length === 0 && <li style={{ padding: "10px 12px", fontSize: 13, color: "#888" }}>Ninguna competición coincide con «{texto}»</li>}
          {filtradas.map((l, i) => (
            <li
              key={l.clave}
              role="option"
              aria-selected={l.clave === valor}
              onMouseDown={(e) => {
                e.preventDefault(); // evita que el input pierda el foco antes de elegir
                elegir(l);
              }}
              onMouseEnter={() => setActivo(i)}
              style={{ padding: "8px 10px", borderRadius: 8, fontSize: 13.5, cursor: "pointer", color: "#ececec", background: i === activo ? "#26262a" : "transparent" }}
            >
              {l.etiqueta}
            </li>
          ))}
        </ul>
      )}
      {!cargando && ligas.length > 0 && (
        <div style={{ marginTop: 6, fontSize: 11, color: "#666" }}>
          {ligas.length} competiciones en tu cuenta de FootyStats
        </div>
      )}
    </div>
  );
}

// El servidor distingue la causa del fallo (credenciales, conexión, límite) con el campo "tipo".
class ErrorApi extends Error {
  constructor(public tipo: string, mensaje: string) {
    super(mensaje);
  }
}

const MENSAJE_ERROR_API: Record<string, string> = {
  credenciales: "La clave de FootyStats del servidor no es válida. Revisa FOOTYSTATS_API_KEY en backend/.env.",
  limite: "Se alcanzó el límite de consultas de FootyStats. Inténtalo de nuevo más tarde.",
  no_encontrado: "Ese partido ya no aparece en el calendario de FootyStats. Vuelve a elegir la liga.",
  conexion: "El servidor no pudo conectar con FootyStats. Inténtalo de nuevo en unos minutos.",
  servidor: "No se pudo conectar con el servidor de PrediLiga. Comprueba que esté en marcha.",
};

async function getApi<T>(path: string, timeoutMs = 30_000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, { signal: ctrl.signal });
  } catch {
    throw new ErrorApi("servidor", MENSAJE_ERROR_API.servidor);
  } finally {
    clearTimeout(timer);
  }
  const json = await res.json().catch(() => null);
  if (!res.ok) {
    const tipo = typeof json?.tipo === "string" ? json.tipo : "servidor";
    throw new ErrorApi(tipo, MENSAJE_ERROR_API[tipo] ?? "Error inesperado del servidor.");
  }
  return json as T;
}

const ZONA_PERU = "America/Lima";
const fmtDiaPeru = new Intl.DateTimeFormat("es-PE", { timeZone: ZONA_PERU, weekday: "long", day: "numeric", month: "long", year: "numeric" });
const fmtHoraPeru = new Intl.DateTimeFormat("es-PE", { timeZone: ZONA_PERU, hour: "2-digit", minute: "2-digit", hour12: false });

function ProximosPartidos() {
  const [predicciones, setPredicciones] = useState<Record<number, EstadoPrediccion>>({});
  const [detalles, setDetalles] = useState<Record<number, EstadoDetalle>>({});
  const [ligas, setLigas] = useState<LigaFS[]>([]);
  const [cargandoLigas, setCargandoLigas] = useState(true);
  const [errorLigas, setErrorLigas] = useState("");
  const [seleccion, setSeleccion] = useState("");
  const [partidos, setPartidos] = useState<PartidoFS[] | null>(null);
  const [cargandoPartidos, setCargandoPartidos] = useState(false);
  const [errorPartidos, setErrorPartidos] = useState("");
  const peticion = useRef(0); // descarta respuestas de una liga que el usuario ya cambió

  const cargarLigas = () => {
    setCargandoLigas(true);
    setErrorLigas("");
    getApi<{ ligas: LigaFS[] }>("/api/ligas")
      .then((j) => setLigas(Array.isArray(j.ligas) ? j.ligas : []))
      .catch((e: ErrorApi) => setErrorLigas(e.message))
      .finally(() => setCargandoLigas(false));
  };
  useEffect(cargarLigas, []);

  const cargarPartidos = (clave: string) => {
    const elegida = ligas.find((l) => String(l.temporada_reciente_id) === clave);
    const n = ++peticion.current;
    setPartidos(null);
    setPredicciones({});
    setDetalles({});
    setErrorPartidos("");
    setCargandoPartidos(false);
    if (!elegida) return;
    setCargandoPartidos(true);
    getApi<{ partidos: PartidoFS[] }>(`/api/temporadas/${elegida.temporada_reciente_id}/partidos`)
      .then((j) => n === peticion.current && setPartidos(Array.isArray(j.partidos) ? j.partidos : []))
      .catch((e: ErrorApi) => n === peticion.current && setErrorPartidos(e.message))
      .finally(() => n === peticion.current && setCargandoPartidos(false));
  };

  const elegir = (clave: string) => {
    setSeleccion(clave);
    cargarPartidos(clave);
  };

  const verPrediccion = (partidoId: number) => {
    const season = liga?.temporada_reciente_id;
    if (!season || predicciones[partidoId]?.cargando) return;
    const n = peticion.current; // si cambia de liga mientras tanto, la respuesta se descarta
    setPredicciones((p) => ({ ...p, [partidoId]: { cargando: true } }));
    getApi<PrediccionFS>(`/api/temporadas/${season}/partidos/${partidoId}/prediccion`, 120_000)
      .then((data) => n === peticion.current && setPredicciones((p) => ({ ...p, [partidoId]: { cargando: false, data } })))
      .catch((e: ErrorApi) => n === peticion.current && setPredicciones((p) => ({ ...p, [partidoId]: { cargando: false, error: e.message } })));
    if (detalles[partidoId]?.abierto) cargarDetalle(partidoId); // "Actualizar" también refresca los mercados abiertos
  };

  // El detalle (todos los mercados) se pide solo al abrirlo; el servidor reutiliza el historial guardado.
  const cargarDetalle = (partidoId: number) => {
    const season = liga?.temporada_reciente_id;
    if (!season) return;
    const n = peticion.current;
    setDetalles((p) => ({ ...p, [partidoId]: { abierto: true, cargando: true } }));
    getApi<DetalleFS>(`/api/temporadas/${season}/partidos/${partidoId}/prediccion?detalle=true`, 180_000)
      .then((data) => n === peticion.current && setDetalles((p) => ({ ...p, [partidoId]: { abierto: true, cargando: false, data } })))
      .catch((e: ErrorApi) => n === peticion.current && setDetalles((p) => ({ ...p, [partidoId]: { abierto: true, cargando: false, error: e.message } })));
  };

  const alternarDetalle = (partidoId: number) => {
    const actual = detalles[partidoId];
    if (actual?.abierto) setDetalles((p) => ({ ...p, [partidoId]: { ...actual, abierto: false } }));
    else if (actual?.data) setDetalles((p) => ({ ...p, [partidoId]: { ...actual, abierto: true } }));
    else cargarDetalle(partidoId);
  };

  const liga = ligas.find((l) => String(l.temporada_reciente_id) === seleccion);
  const temporada = liga?.temporadas.find((t) => t.id === liga.temporada_reciente_id);

  // Agrupa los partidos por día de Perú
  const dias: { dia: string; items: PartidoFS[] }[] = [];
  for (const p of partidos ?? []) {
    const dia = fmtDiaPeru.format(new Date(p.fecha_unix * 1000));
    const ultimo = dias[dias.length - 1];
    if (ultimo?.dia === dia) ultimo.items.push(p);
    else dias.push({ dia, items: [p] });
  }

  const aviso = (texto: string, reintentar?: () => void) => (
    <div role="alert" style={{ background: "#2a1212", border: "1px solid #7f1d1d", color: "#fca5a5", borderRadius: 12, padding: "10px 14px", fontSize: 13 }}>
      {texto}{" "}
      {reintentar && (
        <button type="button" onClick={reintentar} style={{ background: "none", border: "none", color: "#fff", textDecoration: "underline", cursor: "pointer", fontFamily: "inherit", fontSize: 13, padding: 0 }}>
          Reintentar
        </button>
      )}
    </div>
  );

  return (
    <div style={{ height: "100%", overflowY: "auto", background: "#0d0d0d", fontFamily: "'DM Sans', system-ui, sans-serif", color: "#fff" }}>
      <div style={{ maxWidth: 1150, margin: "0 auto", padding: "24px 16px 40px", display: "flex", flexDirection: "column", gap: 18 }}>
        <div>
          <div style={{ fontSize: 11, color: "#666", textTransform: "uppercase", letterSpacing: "1px", fontWeight: 600 }}>FootyStats · hora de Perú</div>
          <h2 style={{ margin: "4px 0 0", fontSize: 22 }}>Elige una liga</h2>
        </div>

        {errorLigas && aviso(errorLigas, cargarLigas)}

        <SelectorLiga ligas={ligas} valor={seleccion} cargando={cargandoLigas} onElegir={elegir} />

        {liga && (
          <div style={{ fontSize: 12, color: "#888" }}>
            {liga.pais} – {liga.nombre}
            {temporada ? ` · temporada ${temporada.etiqueta}` : ""}
          </div>
        )}

        {cargandoPartidos && (
          <div role="status" style={{ fontSize: 13, color: "#999" }}>
            Cargando partidos…
          </div>
        )}
        {errorPartidos && aviso(errorPartidos, () => cargarPartidos(seleccion))}

        {partidos && partidos.length === 0 && (
          <div style={{ background: "#141414", border: "1px solid #222", borderRadius: 14, padding: 16, fontSize: 13, color: "#aaa" }}>
            Sin partidos programados para esta liga en su temporada más reciente.
          </div>
        )}

        {dias.map(({ dia, items }) => (
          <section key={dia} aria-label={dia} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h3 style={{ margin: 0, fontSize: 12, color: "#888", fontWeight: 600 }}>{dia.charAt(0).toUpperCase() + dia.slice(1)}</h3>
            <div style={{ background: "#141414", border: "1px solid #222", borderRadius: 14, overflow: "hidden" }}>
              {items.map((p, i) => (
                <div key={p.id} data-partido-id={p.id} style={{ borderTop: i ? "0.5px solid #1f1f1f" : "none" }}>
                  <div className="fila-partido" style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 14px" }}>
                    <span className="fp-hora" style={{ width: 48, flexShrink: 0, fontFamily: "monospace", fontSize: 14, color: "#ff6b35", fontWeight: 700 }}>
                      {fmtHoraPeru.format(new Date(p.fecha_unix * 1000))}
                    </span>
                    <span className="fp-equipo" style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 8, justifyContent: "flex-end", textAlign: "right", fontSize: 13.5 }}>
                      <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{p.local}</span>
                      <TeamCircle name={p.local} size={30} />
                    </span>
                    <span className="fp-equipo" style={{ fontSize: 11, color: "#555", flexShrink: 0 }}>vs</span>
                    <span className="fp-equipo" style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 8, fontSize: 13.5 }}>
                      <TeamCircle name={p.visitante} size={30} />
                      <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{p.visitante}</span>
                    </span>
                    <button
                      className="fp-btn"
                      type="button"
                      onClick={() => verPrediccion(p.id)}
                      disabled={predicciones[p.id]?.cargando}
                      aria-label={`${predicciones[p.id] ? "Actualizar" : "Ver"} predicción de ${p.local} vs ${p.visitante}`}
                      style={{ flexShrink: 0, padding: "7px 14px", borderRadius: 999, border: "1px solid #ff6b35", background: predicciones[p.id] ? "#26262a" : "#ff6b35", color: "#fff", fontSize: 12.5, fontWeight: 700, fontFamily: "inherit", cursor: predicciones[p.id]?.cargando ? "wait" : "pointer" }}
                    >
                      {predicciones[p.id]?.cargando ? "Calculando…" : predicciones[p.id] ? "Actualizar" : "Ver predicción"}
                    </button>
                  </div>
                  {predicciones[p.id] && <PanelPrediccion est={predicciones[p.id]} />}
                  {predicciones[p.id]?.data?.estado === "ok" && (
                    <div style={{ padding: "0 14px 12px", background: "#101010" }}>
                      <button
                        type="button"
                        onClick={() => alternarDetalle(p.id)}
                        aria-expanded={!!detalles[p.id]?.abierto}
                        disabled={detalles[p.id]?.cargando}
                        style={{ cursor: detalles[p.id]?.cargando ? "wait" : "pointer", fontFamily: "inherit", fontSize: 12.5, fontWeight: 700, color: "#fff", background: "#1c1c1c", border: "1px solid #3a3a3a", borderRadius: 999, padding: "6px 16px" }}
                      >
                        {detalles[p.id]?.cargando ? "Cargando mercados…" : detalles[p.id]?.abierto ? "Ocultar mercados" : "Ver más mercados"}
                      </button>
                    </div>
                  )}
                  {detalles[p.id]?.abierto && <PanelMercados est={detalles[p.id]} onCerrar={() => alternarDetalle(p.id)} />}
                </div>
              ))}
            </div>
          </section>
        ))}

        {partidos && partidos.length > 0 && (
          <p style={{ margin: 0, fontSize: 10.5, color: "#666" }}>
            Horarios en hora de Perú (UTC−5). La predicción usa el historial de partidos terminados de esta misma competición (no el modelo de LaLiga).
          </p>
        )}
      </div>
    </div>
  );
}

// ─── Pestaña "En vivo": partidos con estado en vivo confirmado por FootyStats y partidos por confirmar ───────────────
const REFRESCO_VIVO_MS = 60_000; // el servidor comparte una consulta cada 60 s: refrescar más seguido no trae datos nuevos
const TOLERANCIA_RELOJ_S = 120;

interface PronosticoPrevioGuardado {
  guardado_unix: number;
  resumen?: PrediccionFS;
  detalle?: PrediccionFS;
}

const COLOR_ESTADO: Record<string, string> = {
  en_juego: "#ef4444",
  descanso: "#f59e0b",
  prorroga: "#ef4444",
  penales: "#ef4444",
  por_confirmar: "#fbbf24",
  finalizado: "#888",
  suspendido: "#f87171",
  aplazado: "#f87171",
  cancelado: "#f87171",
};

const textoDesfase = (s: number) => {
  const min = Math.round(Math.abs(s) / 60);
  const h = Math.floor(min / 60);
  return `${h ? `${h} h ` : ""}${min % 60} min ${s > 0 ? "adelantado" : "atrasado"}`;
};

// ─── Predicción en vivo: qué puede pasar en lo que queda de los 90' con el marcador confirmado ───────────
interface PrediccionEnVivoFS {
  estado: string;
  mensaje?: string;
  calculado_con: { fase: string | null; minuto: number | null; minuto_extra: number | null; marcador: { local: number; visitante: number }; marcador_actual: { local: number; visitante: number }; dato_unix: number | null };
  estimado_unix: number;
  resultado_90: { local: number; empate: number; visitante: number };
  mas_2_5: { prob: number; cumplido: boolean; goles_que_faltan: number };
  ambos_marcan: { prob: number; cumplido: boolean };
  marcadores_finales: { local: number; visitante: number; p: number }[];
  tiempo_extra: { prorroga: { local: number; visitante: number } | null; penales: { local: number; visitante: number } | null };
  validacion: { estado: string; partidos_prueba?: number; estados_evaluados?: number; motivo?: string | null; mercados?: Record<string, { veredicto: Veredicto }> };
  etiqueta: string;
  pronostico_previo: PrediccionFS;
  pronostico_previo_origen: string;
  senal_atrasada?: boolean;
  partido: { local: string; visitante: string };
}

const FASE_CORTA: Record<string, string> = { "1T": "1.er tiempo", descanso: "descanso", "2T": "2.º tiempo", prorroga: "prórroga", pausa_prorroga: "pausa de la prórroga", penales: "penales", final: "final" };

function PanelPrediccionEnVivo({ id, firma, senalAtrasada }: { id: number; firma: string; senalAtrasada: boolean }) {
  const [d, setD] = useState<PrediccionEnVivoFS | null>(null);
  const [error, setError] = useState("");
  const [cargando, setCargando] = useState(false);
  // Se recalcula cada vez que cambian los datos confirmados (gol, corrección, minuto o fase)
  useEffect(() => {
    let vivo = true;
    setCargando(true);
    getApi<PrediccionEnVivoFS>(`/api/partidos/${id}/prediccion-en-vivo`, 60_000)
      .then((r) => {
        if (!vivo) return;
        if (r.estado !== "ok") {
          setError(r.mensaje ?? "No se pudo calcular la predicción en vivo.");
        } else {
          setD(r);
          setError("");
        }
      })
      .catch((e: ErrorApi) => vivo && setError(e.message))
      .finally(() => vivo && setCargando(false));
    return () => {
      vivo = false;
    };
  }, [id, firma]);

  const caja: React.CSSProperties = { padding: "12px 14px 14px", borderTop: "0.5px solid #1f1f1f", background: "#0f0d14" };
  if (!d) {
    return (
      <div role={error ? "alert" : "status"} style={{ ...caja, fontSize: 13, color: error ? "#fca5a5" : "#999" }}>
        {error || "Calculando la predicción en vivo…"}
      </div>
    );
  }
  const c = d.calculado_con;
  const [pl, pe, pv] = porcentajesQueSuman100([d.resultado_90.local, d.resultado_90.empate, d.resultado_90.visitante]);
  const previo = d.pronostico_previo;
  const validada = d.etiqueta.startsWith("Validada");
  const minutoTxt = c.minuto == null ? "" : c.minuto_extra ? ` al ${c.minuto}+${c.minuto_extra}'` : ` al ${c.minuto}'`;
  return (
    <div style={caja} data-prediccion-vivo={id} aria-label={`Predicción en vivo ${d.partido.local} vs ${d.partido.visitante}`}>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <strong style={{ fontSize: 13, color: "#fff" }} data-calculado-con>
          Calculada con {c.marcador.local}–{c.marcador.visitante}{minutoTxt} ({FASE_CORTA[c.fase ?? ""] ?? c.fase ?? "fase sin informar"})
        </strong>
        <span style={{ fontSize: 11, color: "#888" }}>
          Estimada a las {horaPeru(d.estimado_unix)}
          {cargando ? " · actualizando…" : ""}
        </span>
      </div>
      {(senalAtrasada || d.senal_atrasada) && (
        <div role="alert" style={{ marginTop: 6, fontSize: 11.5, color: "#fcd34d" }}>
          Señal atrasada: el último dato confirmado se recibió{c.dato_unix ? ` a las ${horaPeru(c.dato_unix)}` : ""}; esta es la última estimación posible ({horaPeru(d.estimado_unix)}).
        </div>
      )}
      <div style={{ marginTop: 8, display: "inline-block", fontSize: 10.5, fontWeight: 700, padding: "2px 10px", borderRadius: 999, border: `1px solid ${validada ? "#14532d" : "#854d0e"}`, color: validada ? "#86efac" : "#fcd34d" }} data-etiqueta-vivo>
        {d.etiqueta}
        {d.validacion.partidos_prueba ? ` · ${d.validacion.partidos_prueba} partidos y ${d.validacion.estados_evaluados} estados históricos` : ""}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(260px, 100%), 1fr))", gap: 12, marginTop: 12 }}>
        <section aria-label="En vivo" style={{ background: "#141414", border: "1px solid #2a2a2a", borderRadius: 10, padding: 12 }}>
          <div style={{ fontSize: 11, color: "#ef4444", fontWeight: 700, marginBottom: 8 }}>● AHORA (resultado al final de los 90')</div>
          <div role="img" aria-label={`Gana ${d.partido.local} ${pl}%, empate ${pe}%, gana ${d.partido.visitante} ${pv}%`} style={{ display: "flex", borderRadius: 6, overflow: "hidden", height: 28 }}>
            {[{ v: pl, bg: "#1e1030", c: "#c4b5fd" }, { v: pe, bg: "#1c1c1c", c: "#999" }, { v: pv, bg: "#0f1e30", c: "#93c5fd" }].map((x, i) => (
              <div key={i} style={{ flex: Math.max(x.v, 4), background: x.bg, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700, color: x.c }}>
                {x.v.toFixed(1)}%
              </div>
            ))}
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#777", marginTop: 3 }}>
            <span>Gana {d.partido.local}</span>
            <span>Empate</span>
            <span>Gana {d.partido.visitante}</span>
          </div>
          <div style={{ marginTop: 10, fontSize: 12.5, color: "#ddd", display: "flex", flexDirection: "column", gap: 4 }}>
            <span data-mas25>
              Más de 2.5 goles:{" "}
              {d.mas_2_5.cumplido ? (
                <strong style={{ color: "#34d399" }}>Cumplido ✓</strong>
              ) : (
                <strong>{(d.mas_2_5.prob * 100).toFixed(1)}%</strong>
              )}
              {!d.mas_2_5.cumplido && <span style={{ color: "#888" }}> · hace falta {d.mas_2_5.goles_que_faltan === 1 ? "al menos 1 gol más" : `${d.mas_2_5.goles_que_faltan} goles más`}</span>}
            </span>
            <span data-ambos>
              Ambos marcan: {d.ambos_marcan.cumplido ? <strong style={{ color: "#34d399" }}>Cumplido ✓</strong> : <strong>{(d.ambos_marcan.prob * 100).toFixed(1)}%</strong>}
            </span>
          </div>
          <div style={{ marginTop: 10, fontSize: 11, color: "#888" }}>Marcadores finales más probables (a los 90')</div>
          <div data-marcadores-finales style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
            {d.marcadores_finales.map((m) => (
              <span key={`${m.local}-${m.visitante}`} style={{ fontSize: 12, color: "#ddd", background: "#1c1c1c", border: "1px solid #2e2e2e", borderRadius: 999, padding: "2px 10px" }}>
                {m.local}–{m.visitante} · {(m.p * 100).toFixed(1)}%
              </span>
            ))}
          </div>
        </section>

        <section aria-label="Pronóstico previo" style={{ background: "#121212", border: "1px dashed #333", borderRadius: 10, padding: 12 }}>
          <div style={{ fontSize: 11, color: "#a78bfa", fontWeight: 700, marginBottom: 8 }}>PRONÓSTICO PREVIO ({d.pronostico_previo_origen})</div>
          {previo?.resultado_1x2 ? (
            <div style={{ fontSize: 12.5, color: "#bbb", display: "flex", flexDirection: "column", gap: 4 }} data-previo-1x2>
              <span>
                Gana {d.partido.local}: <strong>{(previo.resultado_1x2.local * 100).toFixed(1)}%</strong> · Empate: <strong>{(previo.resultado_1x2.empate * 100).toFixed(1)}%</strong> · Gana {d.partido.visitante}:{" "}
                <strong>{(previo.resultado_1x2.visitante * 100).toFixed(1)}%</strong>
              </span>
              {previo.mas_2_5 && <span>Más de 2.5 goles: {(previo.mas_2_5.si * 100).toFixed(1)}%</span>}
              {previo.ambos_marcan && <span>Ambos marcan: {(previo.ambos_marcan.si * 100).toFixed(1)}%</span>}
            </div>
          ) : (
            <div style={{ fontSize: 12, color: "#888" }}>Sin pronóstico previo.</div>
          )}
        </section>
      </div>

      {(d.tiempo_extra.prorroga || d.tiempo_extra.penales) && (
        <div style={{ marginTop: 10, fontSize: 12, color: "#c4b5fd" }} data-tiempo-extra>
          Eliminatoria: el resultado a los 90' fue {c.marcador.local}–{c.marcador.visitante}.
          {d.tiempo_extra.prorroga && ` Prórroga: ${d.tiempo_extra.prorroga.local}–${d.tiempo_extra.prorroga.visitante}.`}
          {d.tiempo_extra.penales && ` Penales: ${d.tiempo_extra.penales.local}–${d.tiempo_extra.penales.visitante}.`} Las probabilidades de arriba son solo del resultado a 90'.
        </div>
      )}
      <p style={{ margin: "10px 0 0", fontSize: 10.5, color: "#666", lineHeight: 1.5 }}>
        Parte de la fuerza previa de ambos equipos y de cuántos goles suele haber en esta liga en lo que queda de partido. No tiene en cuenta expulsiones, cambios ni el dominio del juego.
        {!validada && d.validacion.motivo ? ` ${d.validacion.motivo}` : ""} Análisis orientativo: ninguna apuesta es segura.
      </p>
    </div>
  );
}

function EnVivo({ activa, onContar }: { activa: boolean; onContar?: (n: number | null) => void }) {
  const [ligas, setLigas] = useState<LigaFS[]>([]);
  const [errorLigas, setErrorLigas] = useState("");
  const [resp, setResp] = useState<RespuestaVivo | null>(null);
  const [desfaseNav, setDesfaseNav] = useState(0); // reloj del navegador menos instante UTC de referencia del servidor
  const [error, setError] = useState("");
  const [cargando, setCargando] = useState(false);
  const [filtro, setFiltro] = useState(""); // "" = todas · "pais:X" · id de temporada de una liga
  const [previos, setPrevios] = useState<Record<number, { cargando: boolean; error?: string; guardado?: PronosticoPrevioGuardado }>>({});
  const [vivoAbierto, setVivoAbierto] = useState<Record<number, boolean>>({});
  const [intervaloMs, setIntervaloMs] = useState(REFRESCO_VIVO_MS);
  const [reloj, setReloj] = useState(0);
  const montado = useRef(true);

  // Las competiciones salen siempre de /api/ligas: si cambia mi selección en FootyStats, este filtro cambia con ella.
  const cargarLigas = () => {
    setErrorLigas("");
    getApi<{ ligas: LigaFS[] }>("/api/ligas")
      .then((j) => montado.current && setLigas(Array.isArray(j.ligas) ? j.ligas : []))
      .catch((e: ErrorApi) => montado.current && setErrorLigas(e.message));
  };

  const cargarVivo = () => {
    setCargando(true);
    getApi<RespuestaVivo>("/api/en-vivo", 45_000)
      .then((j) => {
        if (!montado.current) return;
        const d = desfaseNavegadorS(j.ahora_unix, Date.now());
        setDesfaseNav(d);
        setReloj(ahoraConfiable(d, Date.now()));
        setResp({ ...j, partidos: unicosPorId(j.partidos) }); // un ID = una fila, pase lo que pase entre respuestas
        const s = j.fuente_vivo?.estado === "conectada" ? j.fuente_vivo.intervalo_s ?? 60 : 60;
        setIntervaloMs(Math.min(Math.max(s, 15), 60) * 1000);
        setError("");
        onContar?.(j.en_vivo_total);
      })
      .catch((e: ErrorApi) => montado.current && setError(e.message)) // se conserva lo último que se vio
      .finally(() => montado.current && setCargando(false));
  };

  useEffect(() => {
    montado.current = true;
    return () => {
      montado.current = false;
    };
  }, []);
  useEffect(cargarLigas, []);

  // Se actualiza sola mientras la pestaña está abierta y visible.
  useEffect(() => {
    if (!activa) return;
    cargarVivo();
    const t = setInterval(() => {
      if (document.visibilityState === "visible") cargarVivo();
    }, intervaloMs);
    const alVolver = () => document.visibilityState === "visible" && cargarVivo();
    document.addEventListener("visibilitychange", alVolver);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", alVolver);
    };
  }, [activa, intervaloMs]);
  useEffect(() => {
    if (!activa) return;
    const t = setInterval(() => setReloj(ahoraConfiable(desfaseNav, Date.now())), 15_000);
    return () => clearInterval(t);
  }, [activa, desfaseNav]);

  const paises = [...new Set(ligas.map((l) => l.pais))].sort();
  const extras = [
    { clave: "", etiqueta: "Todas mis competiciones" },
    ...paises.map((p) => ({ clave: `pais:${p}`, etiqueta: `${p} · todas sus competiciones`, busca: ALIAS_PAIS[p] ?? "" })),
  ];
  const filtroObj: FiltroVivo = !filtro
    ? { tipo: "todas" }
    : filtro.startsWith("pais:")
      ? { tipo: "pais", pais: filtro.slice(5) }
      : (() => {
          const l = ligas.find((x) => String(x.temporada_reciente_id) === filtro);
          return l ? { tipo: "liga", pais: l.pais, nombre: l.nombre } : { tipo: "todas" };
        })();

  const visibles = resp ? filtrarPartidos(resp.partidos, ligas, filtroObj) : [];
  const secciones = agruparPorEstadoYLiga(visibles, ligas);
  const nEnVivo = contarEnVivo(visibles); // el contador cuenta SOLO partidos con evidencia explícita de estar en vivo
  const competicionesConPartidos = new Set(visibles.map((p) => ligaDe(p, ligas)?.nombre ?? "?")).size;

  const abrirPrevio = (id: number) => {
    if (previos[id]?.guardado) {
      setPrevios((p) => {
        const { [id]: _quitado, ...resto } = p; // segundo clic: se cierra
        return resto;
      });
      return;
    }
    setPrevios((p) => ({ ...p, [id]: { cargando: true } }));
    getApi<PronosticoPrevioGuardado>(`/api/partidos/${id}/pronostico-previo`)
      .then((g) => setPrevios((p) => ({ ...p, [id]: { cargando: false, guardado: g } })))
      .catch((e: ErrorApi) => setPrevios((p) => ({ ...p, [id]: { cargando: false, error: e.tipo === "sin_pronostico" ? "No hay un pronóstico guardado antes del inicio de este partido." : e.message } })));
  };

  const banner = (texto: string, color = "#fca5a5", fondo = "#2a1212", borde = "#7f1d1d", accion?: { texto: string; fn: () => void }) => (
    <div role="alert" style={{ background: fondo, border: `1px solid ${borde}`, color, borderRadius: 12, padding: "10px 14px", fontSize: 13, lineHeight: 1.5 }}>
      {texto}{" "}
      {accion && (
        <button type="button" onClick={accion.fn} style={{ background: "none", border: "none", color: "#fff", textDecoration: "underline", cursor: "pointer", fontFamily: "inherit", fontSize: 13, padding: 0 }}>
          {accion.texto}
        </button>
      )}
    </div>
  );

  return (
    <div style={{ height: "100%", overflowY: "auto", background: "#0d0d0d", fontFamily: "'DM Sans', system-ui, sans-serif", color: "#fff" }}>
      <div style={{ maxWidth: 1150, margin: "0 auto", padding: "24px 16px 40px", display: "flex", flexDirection: "column", gap: 16 }}>
        <div>
          <div style={{ fontSize: 11, color: "#666", textTransform: "uppercase", letterSpacing: "1px", fontWeight: 600 }}>FootyStats · horas en Perú (UTC−5)</div>
          <h2 style={{ margin: "4px 0 0", fontSize: 22 }}>
            Partidos en vivo <span data-contador-vivo style={{ color: nEnVivo ? "#ef4444" : "#777" }}>({nEnVivo})</span>
          </h2>
        </div>

        {errorLigas && banner(`No se pudo cargar la lista de competiciones. ${errorLigas}`, undefined, undefined, undefined, { texto: "Reintentar", fn: cargarLigas })}
        <SelectorLiga ligas={ligas} valor={filtro} cargando={false} onElegir={setFiltro} extras={extras} titulo="Filtrar · país o competición" />

        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12, fontSize: 12, color: "#888" }}>
          {resp && (
            <span>
              Consultado a las {horaPeru(resp.consultado_unix)} ({antiguedadTexto(resp.consultado_unix, reloj)}) · se consulta solo cada minuto
            </span>
          )}
          {cargando && <span role="status">Actualizando…</span>}
          <button type="button" onClick={cargarVivo} disabled={cargando} style={{ cursor: cargando ? "wait" : "pointer", fontFamily: "inherit", fontSize: 12, color: "#ccc", background: "#1c1c1c", border: "1px solid #333", borderRadius: 999, padding: "4px 12px" }}>
            Actualizar ahora
          </button>
          {ligas.length > 0 && resp && (
            <span>
              {ligas.length} competiciones en tu cuenta · {competicionesConPartidos} con partidos en esta lista
            </span>
          )}
        </div>

        {/* Una consulta fallida NO es "no hay partidos": se dice qué falló y se conserva lo último visto */}
        {error && banner(`No se pudo actualizar. ${error}${resp ? ` Se muestra lo último recibido (consultado a las ${horaPeru(resp.consultado_unix)}).` : ""}`, undefined, undefined, undefined, { texto: "Reintentar", fn: cargarVivo })}
        {resp?.aviso_reloj && (
          <div role="alert" data-aviso-reloj-servidor style={{ background: "#221a02", border: "1px solid #854d0e", color: "#fcd34d", borderRadius: 12, padding: "10px 14px", fontSize: 13, lineHeight: 1.5 }}>
            {resp.aviso_reloj}
          </div>
        )}
        {resp && !resp.aviso_reloj && Math.abs(desfaseNav) > TOLERANCIA_RELOJ_S && ( // si el servidor ya avisó del reloj, no se repite
          <div role="alert" data-aviso-reloj style={{ background: "#221a02", border: "1px solid #854d0e", color: "#fcd34d", borderRadius: 12, padding: "10px 14px", fontSize: 13, lineHeight: 1.5 }}>
            <strong>El reloj de este equipo está {textoDesfase(desfaseNav)}.</strong> Las horas y el tiempo transcurrido se calculan con la referencia UTC del servidor; conviene corregir la hora del sistema.
          </div>
        )}
        {resp && (avisoFuente(resp, nEnVivo).tipo === "pendiente" || avisoFuente(resp, nEnVivo).tipo === "desconectada") && (
          <div
            role="alert"
            data-fuente={avisoFuente(resp, nEnVivo).tipo}
            style={{ background: avisoFuente(resp, nEnVivo).tipo === "pendiente" ? "#221a02" : "#2a1212", border: `1px solid ${avisoFuente(resp, nEnVivo).tipo === "pendiente" ? "#854d0e" : "#7f1d1d"}`, color: avisoFuente(resp, nEnVivo).tipo === "pendiente" ? "#fcd34d" : "#fca5a5", borderRadius: 12, padding: "10px 14px", fontSize: 13, lineHeight: 1.5 }}
          >
            {avisoFuente(resp, nEnVivo).texto}
          </div>
        )}
        {resp?.fuente_vivo?.estado === "conectada" && (
          <div style={{ fontSize: 11.5, color: "#777" }} data-fuente="conectada">
            Fuente en vivo: API-Football · consultada a las {horaPeru(resp.fuente_vivo.consultado_unix ?? resp.consultado_unix)} · próxima consulta cada {resp.fuente_vivo.intervalo_s} s según la cuota
            {resp.fuente_vivo.cuota?.dia_restantes != null ? ` (quedan ${resp.fuente_vivo.cuota.dia_restantes} consultas hoy)` : ""}
          </div>
        )}
        {resp && resp.estado_consulta === "parcial" && (
          <div role="alert" data-fuente="parcial" style={{ background: "#221a02", border: "1px solid #854d0e", color: "#fcd34d", borderRadius: 12, padding: "10px 14px", fontSize: 13, lineHeight: 1.5 }}>
            <strong>Cobertura parcial.</strong> Puede faltar algún partido o algún marcador.
            <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
              {resp.avisos.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </div>
        )}

        {!resp && !error && <div role="status" style={{ fontSize: 13, color: "#999" }}>Cargando partidos…</div>}

        {resp && nEnVivo === 0 && !error && (
          <div data-ninguno style={{ background: "#141414", border: "1px solid #222", borderRadius: 14, padding: 16, fontSize: 13, color: "#aaa", lineHeight: 1.5 }}>
            {avisoFuente(resp, nEnVivo).tipo === "ninguno"
              ? "Ningún partido en juego" + (filtro ? " en esta selección." : " ahora en tus competiciones.")
              : avisoFuente(resp, nEnVivo).tipo === "parcial"
                ? "No se encontraron partidos en juego, pero la cobertura es parcial: puede haber alguno que no se vea."
                : "No hay partidos en vivo confirmados: sin la fuente en vivo, FootyStats no informa el estado en juego."}
            {resp.proximos_cercanos > 0 && ` ${resp.proximos_cercanos} partido(s) empiezan en las próximas 2 horas: están en «Próximos partidos».`}
          </div>
        )}

        {secciones.map((sec) => (
          <section key={sec.id} aria-label={sec.titulo} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: sec.id === "por_confirmar" ? "#fbbf24" : "#fff" }}>
                {sec.titulo} <span style={{ color: "#777", fontWeight: 500 }}>({sec.total})</span>
              </h3>
              {sec.ayuda && <div style={{ fontSize: 11.5, color: "#888", marginTop: 2 }}>{sec.ayuda}</div>}
            </div>
            {sec.ligas.map((g) => (
              <div key={g.clave} style={{ background: "#141414", border: "1px solid #222", borderRadius: 14, overflow: "hidden" }}>
                <div style={{ padding: "8px 14px", background: "#121212", borderBottom: "0.5px solid #222", fontSize: 12, fontWeight: 700, color: "#bbb" }}>{g.titulo}</div>
                {g.partidos.map((p, i) => {
                  const marc = textoMarcador(p.marcador);
                  const vivo = ["en_juego", "descanso", "prorroga", "penales"].includes(p.estado);
                  return (
                    <div key={p.id} data-vivo-id={p.id} data-estado={p.estado} style={{ borderTop: i ? "0.5px solid #1f1f1f" : "none" }}>
                      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12, padding: "10px 14px" }}>
                        <span style={{ width: 118, flexShrink: 0, fontSize: 12, fontWeight: 700, color: COLOR_ESTADO[p.estado] ?? "#aaa" }}>
                          {vivo && <span aria-hidden>● </span>}
                          {textoMinuto(p)}
                          {textoFase(p) && p.estado !== "descanso" && <span style={{ display: "block", fontSize: 10.5, color: "#bbb" }}>{textoFase(p)}</span>}
                          <span style={{ display: "block", fontSize: 10, fontWeight: 500, color: "#777" }}>
                            inicio {diaPeru(p.inicio_unix)} {horaPeru(p.inicio_unix)}
                          </span>
                        </span>
                        <span style={{ flex: "1 1 150px", minWidth: 0, display: "flex", alignItems: "center", gap: 8, justifyContent: "flex-end", textAlign: "right", fontSize: 13.5 }}>
                          {p.local}
                          <TeamCircle name={p.local} size={28} />
                        </span>
                        <span
                          data-marcador={p.marcador.estado}
                          style={{ minWidth: 70, textAlign: "center", fontFamily: marc.principal.includes("–") ? "monospace" : "inherit", fontSize: marc.principal.includes("–") ? 17 : 11, fontWeight: 700, color: marc.confirmado ? "#fff" : "#999" }}
                        >
                          {marc.principal}
                        </span>
                        <span style={{ flex: "1 1 150px", minWidth: 0, display: "flex", alignItems: "center", gap: 8, fontSize: 13.5 }}>
                          <TeamCircle name={p.visitante} size={28} />
                          {p.visitante}
                        </span>
                        {admitePrediccionEnVivo(p) && (
                          <button
                            type="button"
                            onClick={() => setVivoAbierto((x) => ({ ...x, [p.id]: !x[p.id] }))}
                            aria-expanded={!!vivoAbierto[p.id]}
                            aria-label={`Ver predicción en vivo de ${p.local} vs ${p.visitante}`}
                            style={{ flexShrink: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 12, fontWeight: 700, color: "#fff", background: vivoAbierto[p.id] ? "#26262a" : "#b91c1c", border: "1px solid #ef4444", borderRadius: 999, padding: "5px 14px" }}
                          >
                            {vivoAbierto[p.id] ? "Ocultar predicción en vivo" : "Ver predicción en vivo"}
                          </button>
                        )}
                        {p.pronostico_previo && (
                          <button
                            type="button"
                            onClick={() => abrirPrevio(p.id)}
                            aria-expanded={!!previos[p.id]?.guardado}
                            aria-label={`Pronóstico previo de ${p.local} vs ${p.visitante}`}
                            style={{ flexShrink: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 12, fontWeight: 700, color: "#fff", background: "#1c1c1c", border: "1px solid #3a3a3a", borderRadius: 999, padding: "5px 14px" }}
                          >
                            {previos[p.id]?.cargando ? "Cargando…" : previos[p.id]?.guardado ? "Ocultar pronóstico previo" : "Pronóstico previo"}
                          </button>
                        )}
                      </div>
                      <div style={{ padding: "0 14px 8px 144px", fontSize: 10.5, color: "#8a7a3a", lineHeight: 1.5 }}>
                        {p.nota && <div>{p.nota}</div>}
                        {marc.nota && <div>{marc.nota}</div>}
                        {p.senal_atrasada && <div style={{ color: "#fcd34d" }}>Señal atrasada: el último dato en vivo es de las {horaPeru(p.consultado_unix)}</div>}
                        {(p.prorroga || p.penales) && (
                          <div style={{ color: "#c4b5fd" }} data-eliminatoria>
                            {p.resultado_90 ? `A los 90': ${p.resultado_90.local}–${p.resultado_90.visitante}` : "Resultado a 90' no informado"}
                            {p.prorroga ? ` · Prórroga: ${p.prorroga.local}–${p.prorroga.visitante}` : ""}
                            {p.penales ? ` · Penales: ${p.penales.local}–${p.penales.visitante}` : ""}
                          </div>
                        )}
                        <div style={{ color: "#666" }}>
                          Fuente: {p.fuente ?? "FootyStats"} · consultado a las {horaPeru(p.consultado_unix)} · estado visto desde las {horaPeru(p.estado_desde_unix)}
                          {p.marcador.recibido_unix != null && p.marcador.estado !== "no_disponible" ? ` · marcador recibido a las ${horaPeru(p.marcador.recibido_unix)}` : ""}
                        </div>
                      </div>
                      {vivoAbierto[p.id] && admitePrediccionEnVivo(p) && <PanelPrediccionEnVivo id={p.id} firma={firmaVivo(p)} senalAtrasada={!!p.senal_atrasada} />}
                      {previos[p.id]?.error && (
                        <div role="alert" style={{ padding: "8px 14px", fontSize: 12, color: "#fca5a5" }}>
                          {previos[p.id]?.error}
                        </div>
                      )}
                      {previos[p.id]?.guardado && (
                        <div data-previo-id={p.id}>
                          <div style={{ padding: "8px 14px", fontSize: 11.5, color: "#c4b5fd", background: "#14102a", borderTop: "0.5px solid #1f1f1f" }}>
                            Pronóstico previo guardado el {diaPeru(previos[p.id].guardado!.guardado_unix)} a las {horaPeru(previos[p.id].guardado!.guardado_unix)} (hora de Perú), antes del inicio. No se recalcula con lo que ya ocurrió.
                          </div>
                          <PanelPrediccion est={{ cargando: false, data: previos[p.id].guardado!.resumen ?? previos[p.id].guardado!.detalle }} />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
          </section>
        ))}

        {resp && (
          <p style={{ margin: 0, fontSize: 10.5, color: "#666", lineHeight: 1.5 }}>
            FootyStats solo documenta los estados «terminado», «suspendido», «cancelado» y «sin terminar». Un partido solo se muestra como en vivo cuando el proveedor lo declara explícitamente y el dato es reciente; el minuto aparece únicamente si el proveedor lo envía. Los partidos que ya pasaron su hora y siguen sin confirmar están en «Estado por confirmar», y los que aún no empiezan, en «Próximos partidos». Un marcador 0–0 sin confirmar es el valor por defecto del proveedor, no un resultado.
          </p>
        )}
      </div>
    </div>
  );
}

// ─── Página: apartados (en vivo, próximos partidos, chat IA y selección de equipos) ───────────────
type Pestana = "envivo" | "chat" | "equipos" | "proximos";

export default function Prediccion() {
  // Inicio puede pedir abrir directamente una pestaña (sessionStorage, clave alegia_prediccion_tab).
  const [nVivo, setNVivo] = useState<number | null>(null); // partidos en vivo confirmados (último dato conocido)
  const [tab, setTab] = useState<Pestana>(() => {
    try {
      const pedida = sessionStorage.getItem("alegia_prediccion_tab");
      // Sin petición de Inicio, la pantalla abre en los partidos que se están jugando ahora.
      return pedida === "equipos" || pedida === "chat" ? pedida : "envivo";
    } catch {
      return "envivo";
    }
  });
  useEffect(() => {
    try {
      sessionStorage.removeItem("alegia_prediccion_tab");
    } catch {
      /* sin storage */
    }
  }, []);
  const tabs: { id: Pestana; label: string }[] = [
    { id: "envivo", label: nVivo == null ? "🔴 En vivo" : `🔴 En vivo (${nVivo})` },
    { id: "proximos", label: "📅 Próximos partidos" },
    { id: "chat", label: "💬 Chat IA" },
    { id: "equipos", label: "⚽ Por equipos (LaLiga)" },
  ];
  return (
    <div style={{ display: "flex", flexDirection: "column", background: "#0d0d0d", width: "100%", height: "calc(100dvh - var(--header-h, 60px))", minHeight: "calc(100dvh - var(--header-h, 60px))", overflow: "hidden", fontFamily: "'DM Sans', system-ui, sans-serif" }}>
      <div role="tablist" aria-label="Tipo de predicción" className="pred-tabs" style={{ display: "flex", gap: 8, padding: "12px 16px 0", maxWidth: 1150, margin: "0 auto", width: "100%", boxSizing: "border-box", flexShrink: 0, overflowX: "auto" }}>
        {tabs.map((t) => {
          const sel = tab === t.id;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={sel}
              onClick={() => setTab(t.id)}
              style={{ cursor: "pointer", fontFamily: "inherit", fontSize: 13.5, fontWeight: sel ? 700 : 500, color: sel ? "#fff" : "#999", background: sel ? "#26262a" : "#141414", border: `1px solid ${sel ? "#ff6b35" : "#262626"}`, borderRadius: 999, padding: "8px 18px", whiteSpace: "nowrap" }}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      <div style={{ flex: 1, minHeight: 0, display: tab === "chat" ? "block" : "none" }}>
        <ChatPrediccion />
      </div>
      <div style={{ flex: 1, minHeight: 0, display: tab === "equipos" ? "block" : "none" }}>
        <PrediccionEquipos />
      </div>
      <div style={{ flex: 1, minHeight: 0, display: tab === "envivo" ? "block" : "none" }}>
        <EnVivo activa={tab === "envivo"} onContar={setNVivo} />
      </div>
      <div style={{ flex: 1, minHeight: 0, display: tab === "proximos" ? "block" : "none" }}>
        <ProximosPartidos />
      </div>
      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: translateY(0); }
        }
        /* Móvil: las 4 pestañas en una rejilla 2×2 para que todas se vean sin desplazar */
        @media (max-width: 640px) {
          .pred-tabs { display: grid !important; grid-template-columns: 1fr 1fr; gap: 6px !important; padding: 10px 12px 0 !important; overflow: visible !important; }
          .pred-tabs > button { padding: 8px 6px !important; font-size: 12.5px !important; white-space: normal !important; text-align: center; }
          /* Fila de próximo partido: hora + botón arriba, equipos abajo */
          .fila-partido { flex-wrap: wrap; row-gap: 5px !important; }
          .fila-partido > .fp-hora { order: 0; }
          .fila-partido > .fp-btn { order: 1; margin-left: auto; }
          .fila-partido::after { content: ""; order: 2; flex-basis: 100%; height: 0; }
          .fila-partido > .fp-equipo { order: 3; }
        }
      `}</style>
    </div>
  );
}