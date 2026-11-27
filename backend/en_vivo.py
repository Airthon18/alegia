"""
"En vivo": partidos con evidencia explícita y vigente del proveedor, más los que pasaron su hora y siguen sin confirmar.

Una consulta global (todays-matches, con timezone=America/Lima) por cada día relevante cubre todas las ligas a la vez, y
footystats.partidos_del_dia comparte el resultado entre usuarios durante 60 s. Cada partido es un ID: al cambiar de estado
(por confirmar -> en vivo -> finalizado) es el mismo registro y no se duplica.

Los partidos futuros NO se listan aquí: están en "Próximos partidos".
"""
from __future__ import annotations

import asyncio
import logging

import api_football
import footystats
import marcadores
import reloj
import vinculos
from estados_partido import EN_VIVO, ETIQUETAS, VENTANA_POR_CONFIRMAR_S, VIGENCIA_EN_VIVO_S, clasificar, dia_lima, evaluar_marcador, fechas_lima

log = logging.getLogger("prediliga.en_vivo")

PROXIMOS_CERCANOS_S = 2 * 3600  # solo se cuentan (no se listan) los que empiezan en las próximas 2 h
FINALIZADOS_VISIBLES_S = 6 * 3600  # los terminados se siguen mostrando 6 h después de su inicio
SENAL_PERDIDA_S = 30 * 60  # pasado este tiempo sin dato en vivo, el partido vuelve a "Estado por confirmar"

# Último estado en vivo de cada partido (por ID de FootyStats), para calcular su predicción en vivo
ULTIMO: dict[int, dict] = {}


async def _consultar_fuente(obtener_vivo, ahora: float) -> tuple[dict, dict | None]:
    """Estado de la fuente en vivo (API-Football) y sus datos. Nunca lanza: un fallo de la fuente no tumba la pantalla."""
    if obtener_vivo is None:
        if not api_football.configurada():
            return {"estado": "pendiente_configurar", "detalle": "Falta API_FOOTBALL_KEY en backend/.env"}, None
        obtener_vivo = api_football.partidos_en_vivo
    try:
        r = await obtener_vivo()
    except api_football.APIFootballError as e:
        return {"estado": "pendiente_configurar" if e.tipo == "sin_configurar" else "desconectada", "tipo": e.tipo, "detalle": e.mensaje}, None
    error = r.get("error") or {}
    return {
        "estado": "desconectada" if r.get("obsoleto") else "conectada",
        "tipo": error.get("tipo"),
        "detalle": error.get("mensaje"),
        "consultado_unix": int(r["consultado"]),
        "proxima_unix": int(r["proxima"]),
        "intervalo_s": int(r["ttl"]),
        "cuota": r.get("cuota"),
    }, r


def _visible(estado: str, inicio: int, ahora: float) -> bool:
    if estado == "proximo":
        return False
    if estado in EN_VIVO or estado == "por_confirmar":
        return ahora - inicio <= VENTANA_POR_CONFIRMAR_S
    return ahora - inicio <= FINALIZADOS_VISIBLES_S  # finalizado, suspendido, cancelado, aplazado


def _mapa_ligas(ligas: list[dict]) -> dict[int, dict]:
    """id de temporada -> {pais, nombre}. El competition_id de todays-matches es el id de temporada de league-list."""
    return {t["id"]: {"pais": l["pais"], "nombre": l["nombre"]} for l in ligas for t in l["temporadas"]}


async def en_vivo(ahora: float | None = None, obtener_dia=None, obtener_ligas=None, ids_con_pronostico=None, obtener_vivo=None) -> dict:
    """
    Estado actual de los partidos de hoy (hora de Perú) y de los que siguen sin confirmar desde ayer.

    estado_consulta:
      "ok"      todas las consultas respondieron completas
      "parcial" alguna fecha o página falló, hay datos antiguos o no se pudieron nombrar las ligas
    aviso_reloj: texto si el reloj de este equipo está desviado respecto a UTC (se usa la referencia, no es un fallo de cobertura)
    Si todas las consultas fallan se lanza FootyStatsError, que es distinto de "no hay partidos en vivo".
    """
    if ahora is None:
        await reloj.asegurar()  # mide el reloj local contra la cabecera Date (UTC) de FootyStats
        ahora = reloj.ahora()
    obtener_dia = obtener_dia or footystats.partidos_del_dia
    obtener_ligas = obtener_ligas or footystats.ligas_cacheadas

    fechas = fechas_lima(ahora)
    resultados = await asyncio.gather(*(obtener_dia(f) for f in fechas), return_exceptions=True)
    avisos: list[str] = []
    cuerpos = []
    primer_error: Exception | None = None
    for fecha, r in zip(fechas, resultados):
        if isinstance(r, footystats.FootyStatsError):
            primer_error = primer_error or r
            avisos.append(f"No se pudo consultar el día {fecha}: {r.mensaje}")
        elif isinstance(r, BaseException):
            raise r
        else:
            cuerpos.append(r)
            if not r.get("completo", True):
                avisos.append(f"Respuesta incompleta de FootyStats para el día {fecha}: faltan partidos.")
            if r.get("obsoleto"):
                avisos.append(f"Datos del día {fecha} con {int(ahora - r['consultado'])} s de antigüedad (la consulta falló).")
    if not cuerpos:
        raise primer_error or footystats.FootyStatsError("respuesta", "FootyStats no devolvió datos")

    aviso_reloj = reloj.aviso()  # va aparte: un reloj desviado no es una cobertura parcial de los partidos
    try:
        mapa = _mapa_ligas(await obtener_ligas())
    except footystats.FootyStatsError as e:
        mapa = {}
        avisos.append(f"No se pudieron obtener los nombres de las competiciones: {e.mensaje}")
    con_pronostico = set(ids_con_pronostico() if ids_con_pronostico else [])

    # Sin duplicados: un partido es un ID, aunque aparezca en dos fechas consultadas
    por_id: dict = {}
    for cuerpo in cuerpos:
        for m in cuerpo["partidos"]:
            previo = por_id.get(m["id"])
            if previo is None or cuerpo["consultado"] >= previo[1]:
                por_id[m["id"]] = (m, cuerpo["consultado"])

    # Fuente en vivo: API-Football, emparejada con FootyStats por competición, equipos y hora UTC
    fuente, vivo = await _consultar_fuente(obtener_vivo, ahora)
    enlaces: dict = {}
    if vivo is not None:
        v = vinculos.vincular([m for m, _ in por_id.values()], vivo["partidos"], mapa, ahora)
        enlaces = v["enlaces"]
        mis_ligas_af = {x["af_liga"] for x in vinculos.ligas_vinculadas().values()}
        sin_par_mias = [x for x in v["sin_par"] if x["liga"]["id"] in mis_ligas_af]
        fuente.update({"vinculados": len(enlaces), "ambiguos": len(v["ambiguos"]), "sin_par_en_tus_ligas": len(sin_par_mias), "otros_en_vivo": len(v["sin_par"]) - len(sin_par_mias)})
        if v["ambiguos"]:
            avisos.append(f"{len(v['ambiguos'])} partido(s) en vivo no se asignaron porque la coincidencia con el calendario era ambigua.")
        if sin_par_mias:
            avisos.append(f"{len(sin_par_mias)} partido(s) en vivo de tus competiciones no aparecen en el calendario de FootyStats de hoy.")
        if fuente["estado"] == "desconectada":
            avisos.append(f"La fuente en vivo no respondió; se usan sus últimos datos ({fuente.get('detalle') or 'sin detalle'}).")
    vigencia = max(VIGENCIA_EN_VIVO_S, 2 * (fuente.get("intervalo_s") or 0) + 60)

    partidos = []
    proximos_cercanos = 0
    for m, consultado in por_id.values():
        af = enlaces.get(m["id"])
        extra_vivo = {"fuente": "FootyStats", "fase": None, "minuto_extra": None, "af_id": None, "resultado_90": None, "prorroga": None, "penales": None, "ronda": None, "senal_atrasada": False}
        if af is not None:
            # Evidencia explícita del proveedor en vivo: estado, minuto y goles confirmados
            edad = ahora - vivo["consultado"]
            estado_af = af["estado"]
            est = {"estado": estado_af, "etiqueta": ETIQUETAS.get(estado_af, estado_af), "minuto": af["minuto"], "nota": None}
            if estado_af in EN_VIVO and edad > SENAL_PERDIDA_S:
                est = clasificar("incomplete", m["t"], ahora)
                est["nota"] = f"La señal en vivo se perdió: último dato hace {int(edad // 60)} min"
            else:
                extra_vivo.update({"fuente": "API-Football", "fase": af["fase"], "minuto_extra": af["minuto_extra"], "af_id": af["af_id"], "resultado_90": af["resultado_90"], "prorroga": af["prorroga"], "penales": af["penales"], "ronda": af["liga"].get("ronda"), "senal_atrasada": estado_af in EN_VIVO and edad > vigencia})
                if estado_af == "proximo":
                    est = clasificar("incomplete", m["t"], ahora)
            consultado = vivo["consultado"]
            goles = af["goles"] or {}
            marcador_actual = evaluar_marcador(est["estado"], goles.get("local"), goles.get("visitante"))
        else:
            est = clasificar(m["s"], m["t"], ahora, m.get("min"), edad_dato_s=ahora - consultado)
            marcador_actual = evaluar_marcador(est["estado"], m["hg"], m["ag"])
        if est["estado"] == "proximo":
            proximos_cercanos += m["t"] - ahora <= PROXIMOS_CERCANOS_S
            continue
        if not _visible(est["estado"], m["t"], ahora):
            continue
        conc = marcadores.conciliar(m["id"], est["estado"], marcador_actual, consultado, m["t"], ahora)
        liga = mapa.get(m["comp"])
        partidos.append(
            {
                "id": m["id"],
                "local": m["hn"],
                "visitante": m["an"],
                "inicio_unix": m["t"],
                "dia_peru": dia_lima(m["t"]),
                "estado": est["estado"],
                "etiqueta": est["etiqueta"],
                "minuto": est["minuto"],  # solo el que envía el proveedor; None si no lo trae
                "nota": est["nota"],
                "marcador": conc["marcador"],
                "competicion_id": m["comp"],
                "liga": liga,
                "jornada": m["jor"],
                "consultado_unix": int(consultado),  # cuándo se le preguntó a FootyStats
                "estado_desde_unix": conc["estado_desde_unix"],  # desde cuándo se ve este estado
                "pronostico_previo": m["id"] in con_pronostico,
                **extra_vivo,
            }
        )
        ULTIMO[m["id"]] = {**partidos[-1], "comp": m["comp"]}
    if any(p["liga"] is None for p in partidos) and mapa:
        avisos.append("Hay partidos de competiciones que no figuran en tu lista actual; se muestran como «Otras competiciones».")
    partidos.sort(key=lambda p: (p["inicio_unix"], p["id"]))
    return {
        "consultado_unix": int(min(c["consultado"] for c in cuerpos)),
        "ahora_unix": int(ahora),  # instante UTC de referencia con el que se clasificó
        "estado_consulta": "parcial" if avisos else "ok",
        "avisos": avisos,
        "aviso_reloj": aviso_reloj,
        "fechas_consultadas": fechas,
        "en_vivo_total": sum(p["estado"] in EN_VIVO for p in partidos),
        "por_confirmar_total": sum(p["estado"] == "por_confirmar" for p in partidos),
        "proximos_cercanos": proximos_cercanos,
        "reloj": reloj.info(),
        "fuente_vivo": fuente,
        "partidos": partidos,
    }
