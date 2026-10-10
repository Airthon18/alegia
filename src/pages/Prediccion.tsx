import { useState, useEffect, useId, useRef } from "react";
import { supabase } from "@/integrations/supabase/client";

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
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
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
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 14, alignItems: "stretch" }}>
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

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 14, alignItems: "start", gridAutoFlow: "dense" }}>
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

// ─── Página: dos apartados (chat IA y selección de equipos) ───────────────
type Pestana = "chat" | "equipos";

export default function Prediccion() {
  // Inicio puede pedir abrir directamente una pestaña (sessionStorage, clave alegia_prediccion_tab).
  const [tab, setTab] = useState<Pestana>(() => {
    try {
      return sessionStorage.getItem("alegia_prediccion_tab") === "equipos" ? "equipos" : "chat";
    } catch {
      return "chat";
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
    { id: "chat", label: "💬 Chat IA" },
    { id: "equipos", label: "⚽ Por equipos (LaLiga)" },
  ];
  return (
    <div style={{ display: "flex", flexDirection: "column", background: "#0d0d0d", width: "100%", height: "calc(100vh - var(--header-h, 60px))", minHeight: "calc(100vh - var(--header-h, 60px))", overflow: "hidden", fontFamily: "'DM Sans', system-ui, sans-serif" }}>
      <div role="tablist" aria-label="Tipo de predicción" style={{ display: "flex", gap: 8, padding: "12px 16px 0", maxWidth: 1150, margin: "0 auto", width: "100%", boxSizing: "border-box", flexShrink: 0, overflowX: "auto" }}>
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
      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}