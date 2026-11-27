import type { CSSProperties, ReactNode } from "react";

/** Mercados adicionales de la predicción por partido (todo sale de /api/temporadas/{id}/partidos/{id}/prediccion). */
export type Veredicto = "supera_referencia" | "similar_a_referencia" | "peor_que_referencia" | "no_concluyente";

export interface CalidadStat {
  estado: string;
  veredicto?: Veredicto;
  linea_validada?: number;
  partidos_prueba?: number;
  motivo?: string;
}

interface LineaMas {
  linea: number;
  mas: number;
  menos?: number;
}

interface BloqueConteo {
  esperados: { local: number; visitante: number; total: number };
  lineas: LineaMas[];
  calidad?: CalidadStat | null;
}

export interface MercadosExtra {
  doble_oportunidad: { "1x": number; "12": number; x2: number };
  empate_no_apuesta: { local: number; visitante: number };
  goles_totales: Required<LineaMas>[];
  goles_equipo: { local: LineaMas[]; visitante: LineaMas[] };
  porteria_cero: { local: number; visitante: number };
  gana_por_2: { local: number; visitante: number };
  ambos_marcan_y_mas_2_5: number;
  marcadores: { local: number; visitante: number; p: number }[];
  primer_tiempo: {
    resultado: { local: number; empate: number; visitante: number };
    mas_0_5: number;
    mas_1_5: number;
    ambos_marcan: number;
    goles_esperados: { local: number; visitante: number };
    calidad?: CalidadStat | null;
  } | null;
  corners: BloqueConteo | null;
  tarjetas: BloqueConteo | null;
}

export interface CuotasMercado {
  resultado_1x2: { local: number; empate: number; visitante: number; margen: number; cuotas: number[] } | null;
  mas_2_5: { si: number; no: number; margen: number; cuotas: number[] } | null;
  ambos_marcan: { si: number; no: number; margen: number; cuotas: number[] } | null;
}

export interface PotencialesFS {
  o15?: number;
  o25?: number;
  o35?: number;
  btts?: number;
  goles?: number;
  corners?: number;
  tarjetas?: number;
  xg_l?: number;
  xg_v?: number;
}

interface CalidadMercadoCuotas {
  veredicto: Veredicto;
  logloss_cuotas?: number;
  logloss_modelo_mismos?: number;
  partidos_con_cuotas?: number;
}

interface Props {
  local: string;
  visitante: string;
  mercados: MercadosExtra;
  cuotas?: CuotasMercado | null;
  potenciales?: PotencialesFS | null;
  /** Veredictos medidos de los mercados de goles (1X2, más de 1.5 / 2.5 / 3.5, ambos marcan). */
  calidadGoles?: Partial<Record<string, CalidadMercadoCuotas>>;
  /** Probabilidades 1X2 / más de 2.5 / ambos marcan del modelo, para ponerlas junto a las cuotas. */
  modelo: { local: number; empate: number; visitante: number; mas25: number; btts: number };
}

const pct = (p: number) => `${(p * 100).toFixed(1)}%`;
const fmtLinea = (n: number) => n.toFixed(1);

const ETIQUETA: Record<Veredicto, { txt: string; color: string }> = {
  supera_referencia: { txt: "validado: supera la referencia", color: "#86efac" },
  similar_a_referencia: { txt: "validado: sin ventaja sobre la referencia", color: "#fcd34d" },
  peor_que_referencia: { txt: "validado: peor que la referencia", color: "#fca5a5" },
  no_concluyente: { txt: "sin validar (pocos partidos de prueba)", color: "#888" },
};

function Etiqueta({ v, extra }: { v?: Veredicto | null; extra?: string }) {
  const e = v ? ETIQUETA[v] : { txt: "sin validar", color: "#888" };
  return (
    <span style={{ fontSize: 10, color: e.color, border: `1px solid ${e.color}55`, borderRadius: 999, padding: "1px 7px", whiteSpace: "nowrap" }}>
      {e.txt}
      {extra ? ` · ${extra}` : ""}
    </span>
  );
}

const seccion: CSSProperties = { marginTop: 14 };
const tituloSec: CSSProperties = { display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, fontSize: 12, fontWeight: 600, color: "#e5e5e5", marginBottom: 6 };

function Seccion({ titulo, etiqueta, children }: { titulo: string; etiqueta: ReactNode; children: ReactNode }) {
  return (
    <section style={seccion}>
      <div style={tituloSec}>
        <span>{titulo}</span>
        {etiqueta}
      </div>
      {children}
    </section>
  );
}

function Fila({ etiqueta, p, color = "#60a5fa" }: { etiqueta: string; p: number; color?: string }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(110px, 1.1fr) 3fr 52px", alignItems: "center", gap: 8, fontSize: 12, color: "#bbb", margin: "3px 0" }}>
      <span>{etiqueta}</span>
      <div style={{ height: 7, background: "#1c1c1c", borderRadius: 4, overflow: "hidden" }} aria-hidden>
        <div style={{ width: `${Math.max(0, Math.min(100, p * 100))}%`, height: "100%", background: color }} />
      </div>
      <strong style={{ color: "#fff", textAlign: "right" }}>{pct(p)}</strong>
    </div>
  );
}

const rejilla: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(230px, 100%), 1fr))", gap: "0 22px" };

function Conteo({ titulo, nombre, b, unidad }: { titulo: string; nombre: string; b: BloqueConteo | null; unidad: string }) {
  if (!b) {
    return (
      <Seccion titulo={titulo} etiqueta={<Etiqueta v={null} />}>
        <p style={{ margin: 0, fontSize: 11.5, color: "#777" }}>FootyStats no trae {nombre} suficientes en esta competición.</p>
      </Seccion>
    );
  }
  const c = b.calidad;
  return (
    <Seccion titulo={titulo} etiqueta={<Etiqueta v={c?.estado === "medida" ? c.veredicto : null} extra={c?.estado === "medida" && c.linea_validada ? `línea ${fmtLinea(c.linea_validada)}` : undefined} />}>
      <p style={{ margin: "0 0 4px", fontSize: 11.5, color: "#999" }}>
        {unidad}, media prevista por equipo: <strong style={{ color: "#fff" }}>{b.esperados.local.toFixed(1)} – {b.esperados.visitante.toFixed(1)}</strong> (total {b.esperados.total.toFixed(1)})
      </p>
      <div style={rejilla}>
        {b.lineas.map((l) => (
          <Fila key={l.linea} etiqueta={`Más de ${fmtLinea(l.linea)}`} p={l.mas} color="#a78bfa" />
        ))}
      </div>
    </Seccion>
  );
}

export default function MasMercados({ local, visitante, mercados: m, cuotas, potenciales, calidadGoles, modelo }: Props) {
  const v = (k: string) => calidadGoles?.[k]?.veredicto;
  const pt = m.primer_tiempo;
  const cu1 = calidadGoles?.resultado_1x2;
  const cuotasGanaModelo = cu1?.logloss_cuotas != null && cu1.logloss_modelo_mismos != null ? cu1.logloss_modelo_mismos < cu1.logloss_cuotas : null;
  const filaCuota = (nombre: string, mod: number, mer?: number) => (
    <tr key={nombre}>
      <td style={{ padding: "3px 10px 3px 0", color: "#bbb" }}>{nombre}</td>
      <td style={{ padding: "3px 10px", textAlign: "right", color: "#fff" }}>{pct(mod)}</td>
      <td style={{ padding: "3px 0 3px 10px", textAlign: "right", color: "#fff" }}>{mer != null ? pct(mer) : "—"}</td>
      <td style={{ padding: "3px 0 3px 10px", textAlign: "right", color: mer != null && mod - mer > 0.03 ? "#86efac" : mer != null && mod - mer < -0.03 ? "#fca5a5" : "#777" }}>
        {mer != null ? `${mod - mer >= 0 ? "+" : ""}${((mod - mer) * 100).toFixed(1)}` : ""}
      </td>
    </tr>
  );

  return (
    <details open style={{ marginTop: 14, borderTop: "0.5px solid #222", paddingTop: 10 }} data-mas-mercados>
      <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 600, color: "#e5e5e5" }}>Más mercados de este partido</summary>

      <Seccion titulo="Resultado y goles" etiqueta={<Etiqueta v={v("resultado_1x2")} extra="1X2 y derivados" />}>
        <div style={rejilla}>
          <Fila etiqueta={`${local} o empate (1X)`} p={m.doble_oportunidad["1x"]} color="#c4b5fd" />
          <Fila etiqueta="Gana uno de los dos (12)" p={m.doble_oportunidad["12"]} color="#c4b5fd" />
          <Fila etiqueta={`Empate o ${visitante} (X2)`} p={m.doble_oportunidad.x2} color="#c4b5fd" />
          <Fila etiqueta={`${local} (empate no apuesta)`} p={m.empate_no_apuesta.local} color="#c4b5fd" />
          <Fila etiqueta={`${visitante} (empate no apuesta)`} p={m.empate_no_apuesta.visitante} color="#93c5fd" />
        </div>
      </Seccion>

      <Seccion titulo="Goles totales" etiqueta={<Etiqueta v={v("mas_2_5")} extra="líneas 1.5, 2.5 y 3.5 medidas" />}>
        <div style={rejilla}>
          {m.goles_totales.map((l) => (
            <Fila key={l.linea} etiqueta={`Más de ${fmtLinea(l.linea)}`} p={l.mas} />
          ))}
        </div>
        <p style={{ margin: "4px 0 0", fontSize: 11, color: "#777" }}>Las líneas 0.5 y 4.5 salen del mismo modelo pero no se midieron aparte.</p>
      </Seccion>

      <Seccion titulo="Por equipo" etiqueta={<Etiqueta v={null} />}>
        <div style={rejilla}>
          {m.goles_equipo.local.map((l) => (
            <Fila key={`l${l.linea}`} etiqueta={`${local} marca +${fmtLinea(l.linea)}`} p={l.mas} color="#c4b5fd" />
          ))}
          {m.goles_equipo.visitante.map((l) => (
            <Fila key={`v${l.linea}`} etiqueta={`${visitante} marca +${fmtLinea(l.linea)}`} p={l.mas} color="#93c5fd" />
          ))}
          <Fila etiqueta={`${local} deja la portería a cero`} p={m.porteria_cero.local} color="#34d399" />
          <Fila etiqueta={`${visitante} deja la portería a cero`} p={m.porteria_cero.visitante} color="#34d399" />
          <Fila etiqueta={`${local} gana por 2 o más`} p={m.gana_por_2.local} color="#fbbf24" />
          <Fila etiqueta={`${visitante} gana por 2 o más`} p={m.gana_por_2.visitante} color="#fbbf24" />
          <Fila etiqueta="Ambos marcan y más de 2.5" p={m.ambos_marcan_y_mas_2_5} color="#fbbf24" />
        </div>
      </Seccion>

      <Seccion titulo="Marcadores más probables" etiqueta={<Etiqueta v={null} />}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {m.marcadores.map((s) => (
            <span key={`${s.local}-${s.visitante}`} style={{ fontSize: 12, color: "#ddd", background: "#1a1a1a", border: "1px solid #262626", borderRadius: 8, padding: "4px 10px" }}>
              <strong style={{ color: "#fff" }}>{s.local}–{s.visitante}</strong> · {pct(s.p)}
            </span>
          ))}
        </div>
      </Seccion>

      {pt && (
        <Seccion titulo="Primer tiempo" etiqueta={<Etiqueta v={pt.calidad?.estado === "medida" ? pt.calidad.veredicto : null} extra={pt.calidad?.estado === "medida" && pt.calidad.linea_validada ? `línea ${fmtLinea(pt.calidad.linea_validada)}` : undefined} />}>
          <p style={{ margin: "0 0 4px", fontSize: 11.5, color: "#999" }}>
            Goles esperados al descanso: <strong style={{ color: "#fff" }}>{pt.goles_esperados.local.toFixed(2)} – {pt.goles_esperados.visitante.toFixed(2)}</strong>
          </p>
          <div style={rejilla}>
            <Fila etiqueta={`${local} gana al descanso`} p={pt.resultado.local} color="#c4b5fd" />
            <Fila etiqueta="Empate al descanso" p={pt.resultado.empate} color="#888" />
            <Fila etiqueta={`${visitante} gana al descanso`} p={pt.resultado.visitante} color="#93c5fd" />
            <Fila etiqueta="Más de 0.5 goles en el 1T" p={pt.mas_0_5} />
            <Fila etiqueta="Más de 1.5 goles en el 1T" p={pt.mas_1_5} />
            <Fila etiqueta="Ambos marcan en el 1T" p={pt.ambos_marcan} color="#fbbf24" />
          </div>
        </Seccion>
      )}

      <Conteo titulo="Córners" nombre="córners" b={m.corners} unidad="Córners" />
      <Conteo titulo="Tarjetas" nombre="tarjetas" b={m.tarjetas} unidad="Tarjetas" />

      {cuotas && (
        <Seccion titulo="Modelo frente a las cuotas del mercado" etiqueta={
          <span style={{ fontSize: 10, color: cuotasGanaModelo === null ? "#888" : cuotasGanaModelo ? "#86efac" : "#fcd34d", border: "1px solid #44444488", borderRadius: 999, padding: "1px 7px" }}>
            {cuotasGanaModelo === null ? "sin comparar" : cuotasGanaModelo ? "medido: el modelo supera a las cuotas en 1X2" : "medido: las cuotas rinden mejor que el modelo en 1X2"}
          </span>
        }>
          <table style={{ borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "#777", fontSize: 10.5, textAlign: "right" }}>
                <th style={{ textAlign: "left", fontWeight: 500, padding: "0 10px 3px 0" }} />
                <th style={{ fontWeight: 500, padding: "0 10px 3px" }}>Modelo</th>
                <th style={{ fontWeight: 500, padding: "0 0 3px 10px" }}>Cuotas</th>
                <th style={{ fontWeight: 500, padding: "0 0 3px 10px" }}>Dif. (pts)</th>
              </tr>
            </thead>
            <tbody>
              {cuotas.resultado_1x2 && [filaCuota(`Gana ${local}`, modelo.local, cuotas.resultado_1x2.local), filaCuota("Empate", modelo.empate, cuotas.resultado_1x2.empate), filaCuota(`Gana ${visitante}`, modelo.visitante, cuotas.resultado_1x2.visitante)]}
              {cuotas.mas_2_5 && filaCuota("Más de 2.5 goles", modelo.mas25, cuotas.mas_2_5.si)}
              {cuotas.ambos_marcan && filaCuota("Ambos marcan", modelo.btts, cuotas.ambos_marcan.si)}
            </tbody>
          </table>
          <p style={{ margin: "6px 0 0", fontSize: 11, color: "#777", lineHeight: 1.5 }}>
            Cuotas de FootyStats convertidas a probabilidad sin el margen de la casa{cuotas.resultado_1x2 ? ` (margen 1X2: ${(cuotas.resultado_1x2.margen * 100).toFixed(1)}%)` : ""}.
            {cu1?.partidos_con_cuotas
              ? ` En ${cu1.partidos_con_cuotas} partidos de prueba con cuotas, ${cuotasGanaModelo ? "el modelo se equivocó menos que las cuotas" : "las cuotas se equivocaron menos que el modelo"} en el 1X2 (log loss ${cu1.logloss_modelo_mismos} del modelo frente a ${cu1.logloss_cuotas} de las cuotas).`
              : " No hay suficientes cuotas históricas para compararlas en esta liga."}
          </p>
        </Seccion>
      )}

      {potenciales && (
        <Seccion titulo="Estimaciones propias de FootyStats" etiqueta={<Etiqueta v={null} extra="datos de la API, no del modelo" />}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {[
              potenciales.o15 != null && ["Más de 1.5", `${potenciales.o15}%`],
              potenciales.o25 != null && ["Más de 2.5", `${potenciales.o25}%`],
              potenciales.o35 != null && ["Más de 3.5", `${potenciales.o35}%`],
              potenciales.btts != null && ["Ambos marcan", `${potenciales.btts}%`],
              potenciales.goles != null && ["Goles previstos", potenciales.goles.toFixed(2)],
              potenciales.corners != null && ["Córners previstos", potenciales.corners.toFixed(1)],
              potenciales.tarjetas != null && ["Tarjetas previstas", potenciales.tarjetas.toFixed(1)],
              potenciales.xg_l != null && potenciales.xg_v != null && ["xG previo", `${potenciales.xg_l.toFixed(2)} – ${potenciales.xg_v.toFixed(2)}`],
            ]
              .filter((x): x is string[] => Array.isArray(x))
              .map(([k, val]) => (
                <span key={k} style={{ fontSize: 12, color: "#ddd", background: "#1a1a1a", border: "1px solid #262626", borderRadius: 8, padding: "4px 10px" }}>
                  {k}: <strong style={{ color: "#fff" }}>{val}</strong>
                </span>
              ))}
          </div>
        </Seccion>
      )}
    </details>
  );
}
