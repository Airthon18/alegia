import { describe, expect, it } from "vitest";
import {
  admitePrediccionEnVivo,
  agruparPorEstadoYLiga,
  avisoFuente,
  firmaVivo,
  textoFase,
  ahoraConfiable,
  contarEnVivo,
  desfaseNavegadorS,
  fechaCompletaPeru,
  filtrarPartidos,
  horaPeru,
  textoMarcador,
  textoMinuto,
  unicosPorId,
  type LigaApi,
  type Marcador,
  type PartidoVivo,
} from "./enVivo";

const MLS_ID = 16504;
const LIGAS: LigaApi[] = [
  { pais: "USA", nombre: "MLS", temporadas: [{ id: 13973 }, { id: MLS_ID }] },
  { pais: "Spain", nombre: "La Liga", temporadas: [{ id: 17199 }] },
  { pais: "Australia", nombre: "A-League", temporadas: [{ id: 17326 }] },
];

// Capturas del usuario (solo en pruebas). Jueves 19-nov-2026 22:00 en Perú = viernes 20-nov 03:00 UTC
const SEATTLE_INICIO = Date.UTC(2026, 10, 20, 3, 0, 0) / 1000;
// Western Sydney: viernes 20-nov 03:40 en Perú = 08:40 UTC (date_unix 1795164000)
const SYDNEY_INICIO = 1795164000;

const sinMarcador: Marcador = { estado: "no_disponible", local: null, visitante: null };
const confirmado = (l: number, v: number, extra: Partial<Marcador> = {}): Marcador => ({ estado: "confirmado", local: l, visitante: v, recibido_unix: SEATTLE_INICIO + 3600, ...extra });

const base = (o: Partial<PartidoVivo>): PartidoVivo => ({
  id: 1,
  local: "Seattle Sounders",
  visitante: "Real Salt Lake",
  inicio_unix: SEATTLE_INICIO,
  dia_peru: "2026-11-19",
  estado: "en_juego",
  etiqueta: "En vivo",
  minuto: 60,
  nota: null,
  marcador: confirmado(1, 0),
  competicion_id: MLS_ID,
  liga: { pais: "USA", nombre: "MLS" },
  consultado_unix: SEATTLE_INICIO + 3590,
  estado_desde_unix: SEATTLE_INICIO + 3590,
  pronostico_previo: false,
  ...o,
});

describe("hora de Perú frente a UTC", () => {
  it("jueves 22:00 en Perú, que en UTC ya es viernes, se muestra como jueves 22:00", () => {
    expect(horaPeru(SEATTLE_INICIO)).toBe("22:00");
    expect(fechaCompletaPeru(SEATTLE_INICIO)).toBe("Jueves, 19 de noviembre de 2026");
    expect(new Date(SEATTLE_INICIO * 1000).getUTCDate()).toBe(20);
  });
  it("Western Sydney: 08:40 UTC se muestra como viernes 20 de noviembre de 2026, 03:40 en Perú", () => {
    expect(fechaCompletaPeru(SYDNEY_INICIO)).toBe("Viernes, 20 de noviembre de 2026");
    expect(horaPeru(SYDNEY_INICIO)).toBe("03:40");
  });
});

describe("reloj del navegador desviado", () => {
  it("con el navegador 5 h adelantado, 'ahora' sale de la referencia del servidor y no de Date.now()", () => {
    const ahoraReal = Date.UTC(2026, 10, 20, 4, 35, 0) / 1000; // 23:35 del jueves en Lima
    const navegadorMs = (ahoraReal + 5 * 3600) * 1000; // el equipo cree que es 09:35 UTC
    const desfase = desfaseNavegadorS(ahoraReal, navegadorMs);
    expect(desfase).toBe(18000);
    expect(ahoraConfiable(desfase, navegadorMs)).toBe(ahoraReal);
  });
});

describe("contador y agrupación", () => {
  const partidos = [
    base({ id: 1 }),
    base({ id: 2, local: "Real Madrid", visitante: "Celta", competicion_id: 17199, liga: { pais: "Spain", nombre: "La Liga" }, estado: "descanso", minuto: null }),
    base({ id: 3, estado: "por_confirmar", minuto: null, marcador: sinMarcador }),
    base({ id: 5, estado: "finalizado", marcador: confirmado(2, 1), minuto: null }),
    base({ id: 6, competicion_id: 999, liga: null, estado: "en_juego" }),
    base({ id: 7, estado: "aplazado", marcador: sinMarcador, minuto: null }),
  ];

  it("el contador incluye únicamente los partidos con evidencia en vivo", () => {
    expect(contarEnVivo(partidos)).toBe(3); // 1 (en juego), 2 (descanso) y 6 (en juego); no el por confirmar ni el finalizado
  });

  it("separa en vivo, por confirmar, interrumpidos y finalizados; no hay sección de próximos", () => {
    const sec = agruparPorEstadoYLiga(partidos, LIGAS);
    expect(sec.map((s) => s.id)).toEqual(["en_juego", "por_confirmar", "interrumpidos", "finalizados"]);
    expect(sec[0].total).toBe(3);
    expect(sec[0].ligas.map((l) => l.titulo)).toEqual(["Otras competiciones", "Spain – La Liga", "USA – MLS"]);
  });

  it("un partido próximo que llegara por error no genera sección en vivo", () => {
    expect(agruparPorEstadoYLiga([base({ estado: "proximo", marcador: sinMarcador })], LIGAS)).toEqual([]);
  });

  it("una lista vacía no genera secciones", () => {
    expect(agruparPorEstadoYLiga([], LIGAS)).toEqual([]);
  });
});

describe("filtrar", () => {
  const partidos = [base({ id: 1 }), base({ id: 2, competicion_id: 17199, liga: { pais: "Spain", nombre: "La Liga" } }), base({ id: 3, competicion_id: 17326, liga: null })];
  it("todas mis competiciones no filtra nada", () => expect(filtrarPartidos(partidos, LIGAS, { tipo: "todas" })).toHaveLength(3));
  it("filtra por MLS", () => expect(filtrarPartidos(partidos, LIGAS, { tipo: "liga", pais: "USA", nombre: "MLS" }).map((p) => p.id)).toEqual([1]));
  it("filtra por país usando /api/ligas aunque el servidor no haya nombrado la liga", () =>
    expect(filtrarPartidos(partidos, LIGAS, { tipo: "pais", pais: "Australia" }).map((p) => p.id)).toEqual([3]));
});

describe("marcador", () => {
  it("sin confirmar dice 'Marcador no disponible' y no inventa un 0–0", () => {
    expect(textoMarcador(sinMarcador).principal).toBe("Marcador no disponible");
    expect(textoMarcador({ estado: "no_disponible", local: 0, visitante: 0 }).principal).toBe("Marcador no disponible");
    expect(textoMarcador(undefined).principal).toBe("Marcador no disponible");
  });
  it("un 0–0 confirmado sí se muestra", () => {
    const t = textoMarcador(confirmado(0, 0));
    expect(t.principal).toBe("0 – 0");
    expect(t.confirmado).toBe(true);
  });
  it("el provisional se rotula como sin confirmar", () => {
    const t = textoMarcador({ estado: "provisional", local: 1, visitante: 0 });
    expect(t.confirmado).toBe(false);
    expect(t.nota).toContain("sin confirmar");
  });
  it("si solo hay un dato anterior, indica cuándo se recibió", () => {
    const t = textoMarcador(confirmado(1, 0, { desactualizado: true, recibido_unix: SEATTLE_INICIO + 3600 }));
    expect(t.principal).toBe("1 – 0");
    expect(t.nota).toContain("23:00");
  });
});

describe("transición sin duplicarse", () => {
  it("por confirmar → en vivo → finalizado deja siempre una sola fila", () => {
    let estado: PartidoVivo[] = [];
    const respuestas = [
      base({ estado: "por_confirmar", minuto: null, marcador: sinMarcador }),
      base({ estado: "en_juego", marcador: confirmado(1, 0) }),
      base({ estado: "finalizado", minuto: null, marcador: confirmado(2, 1) }),
    ];
    for (const r of respuestas) {
      estado = unicosPorId([...estado, r]);
      expect(estado).toHaveLength(1);
    }
    expect(estado[0].estado).toBe("finalizado");
  });
  it("una respuesta incompleta del proveedor (sin lista) no rompe", () => {
    expect(unicosPorId(undefined)).toEqual([]);
    expect(unicosPorId(null)).toEqual([]);
  });
});

describe("texto del minuto", () => {
  it("usa el minuto del proveedor y no calcula ninguno", () => {
    expect(textoMinuto(base({ minuto: 60 }))).toBe("60'");
    expect(textoMinuto(base({ minuto: null }))).toBe("Minuto no disponible");
  });
  it("estado por confirmar no muestra minuto", () => {
    expect(textoMinuto(base({ estado: "por_confirmar", minuto: null }))).toBe("Sin confirmar");
  });
  it("descanso, aplazado y final", () => {
    expect(textoMinuto(base({ estado: "descanso", minuto: null }))).toBe("Descanso");
    expect(textoMinuto(base({ estado: "aplazado", minuto: null }))).toBe("Aplazado");
    expect(textoMinuto(base({ estado: "finalizado", minuto: null }))).toBe("Final");
  });
});

describe("fuente en vivo (API-Football)", () => {
  const vivo = (o: Partial<PartidoVivo>) => base({ fuente: "API-Football", fase: "2T", minuto: 76, marcador: confirmado(1, 1), ...o });
  const resp = (o: Partial<import("./enVivo").RespuestaVivo>) =>
    ({ consultado_unix: 0, ahora_unix: 0, estado_consulta: "ok", avisos: [], aviso_reloj: null, fechas_consultadas: [], en_vivo_total: 0, por_confirmar_total: 0, proximos_cercanos: 0, reloj: { local_unix: 0, referencia_unix: 0, desfase_s: 0, corregido: false }, partidos: [], ...o }) as import("./enVivo").RespuestaVivo;

  it("minuto con descuento y fase", () => {
    expect(textoMinuto(vivo({ minuto: 90, minuto_extra: 3 }))).toBe("90+3'");
    expect(textoFase(vivo({}))).toBe("2.º tiempo");
    expect(textoFase(vivo({ estado: "prorroga", fase: "prorroga", minuto: 105 }))).toBe("Prórroga · 105'");
  });

  it("solo ofrece la predicción en vivo con marcador confirmado por la fuente en vivo", () => {
    expect(admitePrediccionEnVivo(vivo({}))).toBe(true);
    expect(admitePrediccionEnVivo(vivo({ fuente: "FootyStats" }))).toBe(false);
    expect(admitePrediccionEnVivo(vivo({ marcador: { estado: "no_disponible", local: null, visitante: null } }))).toBe(false);
    expect(admitePrediccionEnVivo(vivo({ estado: "por_confirmar" }))).toBe(false);
  });

  it("la firma cambia con un gol o una corrección del proveedor", () => {
    const a = firmaVivo(vivo({}));
    expect(firmaVivo(vivo({ marcador: confirmado(2, 1) }))).not.toBe(a);
    expect(firmaVivo(vivo({ minuto: 77 }))).not.toBe(a);
    expect(firmaVivo(vivo({}))).toBe(a);
  });

  it("distingue fuente pendiente, desconectada, cobertura parcial y ningún partido en juego", () => {
    expect(avisoFuente(resp({}), 0).texto).toContain("Fuente en vivo pendiente de configurar");
    expect(avisoFuente(resp({ fuente_vivo: { estado: "pendiente_configurar" } }), 0).tipo).toBe("pendiente");
    expect(avisoFuente(resp({ fuente_vivo: { estado: "desconectada", detalle: "API-Football rechazó la clave" } }), 0).texto).toBe("Fuente en vivo desconectada: API-Football rechazó la clave.");
    expect(avisoFuente(resp({ fuente_vivo: { estado: "conectada" }, estado_consulta: "parcial" }), 1).tipo).toBe("parcial");
    expect(avisoFuente(resp({ fuente_vivo: { estado: "conectada" } }), 0).texto).toBe("Ningún partido en juego ahora en tus competiciones.");
    expect(avisoFuente(resp({ fuente_vivo: { estado: "conectada" } }), 2).tipo).toBe("ok");
  });
});
