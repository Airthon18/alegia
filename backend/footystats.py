"""
Cliente de FootyStats (https://api.football-data-api.com) para PrediLiga.

La clave vive solo en el servidor (FOOTYSTATS_API_KEY en backend/.env). Va como parámetro de la
consulta, así que NUNCA se registra ni se incluye en un mensaje: los errores de httpx traen la URL
completa (con la clave), por eso aquí se capturan y se sustituyen por mensajes propios.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx

import reloj
from estados_partido import clasificar

log = logging.getLogger("prediliga.footystats")

# httpx registra a nivel INFO la URL completa de cada petición, y aquí la URL lleva la clave.
for _nombre in ("httpx", "httpcore"):
    logging.getLogger(_nombre).setLevel(logging.WARNING)

BASE_URL = "https://api.football-data-api.com"
TIMEOUT_S = 20
POR_PAGINA = 500  # partidos por página en league-matches
MAX_PAGINAS = 20  # tope de seguridad contra bucles de paginación


class FootyStatsError(Exception):
    """Error ya saneado (sin clave ni URL). `tipo` permite al endpoint distinguir la causa."""

    def __init__(self, tipo: str, mensaje: str):
        super().__init__(mensaje)
        self.tipo = tipo  # "credenciales" | "conexion" | "limite" | "respuesta"
        self.mensaje = mensaje


def configurada() -> bool:
    return bool(os.environ.get("FOOTYSTATS_API_KEY", "").strip())


async def _get(endpoint: str, **params) -> dict:
    clave = os.environ.get("FOOTYSTATS_API_KEY", "").strip()
    if not clave:
        raise FootyStatsError("credenciales", "Falta FOOTYSTATS_API_KEY en el servidor")

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as cliente:
            antes = reloj.hora_local()
            r = await cliente.get(f"{BASE_URL}/{endpoint}", params={"key": clave, **params})
            reloj.registrar(r.headers.get("date"), antes, reloj.hora_local())  # referencia UTC para comparar instantes
    except httpx.HTTPError as e:
        # No se usa str(e): incluye la URL con la clave.
        log.warning("FootyStats %s: fallo de conexión (%s)", endpoint, type(e).__name__)
        raise FootyStatsError("conexion", "No se pudo conectar con FootyStats") from None

    try:
        cuerpo = r.json()
    except ValueError:
        cuerpo = {}
    if not isinstance(cuerpo, dict):
        cuerpo = {}
    mensaje = str(cuerpo.get("message") or "").lower()
    restantes = (cuerpo.get("metadata") or {}).get("request_remaining")

    if r.status_code in (401, 403):
        raise FootyStatsError("credenciales", "FootyStats rechazó la clave de API")
    if r.status_code == 429 or (not cuerpo.get("success", True) and (str(restantes) == "0" or "limit" in mensaje)):
        raise FootyStatsError("limite", "Se alcanzó el límite de consultas de FootyStats; inténtalo más tarde")
    if r.status_code >= 500:
        log.warning("FootyStats %s: HTTP %s", endpoint, r.status_code)
        raise FootyStatsError("conexion", "FootyStats no está disponible en este momento")
    if r.status_code >= 400 or not cuerpo.get("success", False):
        log.warning("FootyStats %s: HTTP %s success=%s", endpoint, r.status_code, cuerpo.get("success"))
        raise FootyStatsError("respuesta", "FootyStats devolvió una respuesta inesperada")
    return cuerpo


def _inicio_temporada(anio: int) -> int:
    """2026 -> 2026; 20262027 -> 2026 (para ordenar temporadas de ambos formatos)."""
    return anio // 10000 if anio > 9999 else anio


def _etiqueta_temporada(anio: int) -> str:
    """20262027 -> '2026/27'; 2026 -> '2026'."""
    if anio > 9999:
        return f"{anio // 10000}/{str(anio % 10000)[-2:]}"
    return str(anio)


async def listar_ligas() -> list[dict]:
    """Competiciones activas de la cuenta (chosen_leagues_only), consultadas siempre a la API."""
    cuerpo = await _get("league-list", chosen_leagues_only="true")
    ligas = []
    for c in cuerpo.get("data") or []:
        pais = (c.get("country") or "").strip()
        nombre = (c.get("league_name") or c.get("name") or "").strip()
        if pais and nombre.startswith(pais + " "):
            nombre = nombre[len(pais) + 1 :]  # "Spain La Liga" -> "La Liga"
        temporadas = []
        for s in c.get("season") or []:
            try:
                temporadas.append({"id": int(s["id"]), "anio": int(s["year"])})
            except (KeyError, TypeError, ValueError):
                continue
        if not temporadas:
            continue
        # La lista de la API no viene ordenada por año (p. ej. Argentina trae una temporada vieja al final).
        temporadas.sort(key=lambda t: (_inicio_temporada(t["anio"]), t["anio"]))
        ligas.append(
            {
                "pais": pais,
                "nombre": nombre,
                "temporadas": [{"id": t["id"], "etiqueta": _etiqueta_temporada(t["anio"])} for t in temporadas],
                "temporada_reciente_id": temporadas[-1]["id"],
            }
        )
    ligas.sort(key=lambda l: (l["pais"], l["nombre"]))
    return ligas


async def _descargar_partidos(season_id: int) -> list[dict]:
    """Todos los partidos de la temporada tal como los devuelve FootyStats (recorre las páginas)."""
    crudos: list[dict] = []
    pagina = 1
    while pagina <= MAX_PAGINAS:
        cuerpo = await _get("league-matches", season_id=season_id, page=pagina, max_per_page=POR_PAGINA)
        crudos.extend(cuerpo.get("data") or [])
        max_pagina = int((cuerpo.get("pager") or {}).get("max_page") or 1)
        if pagina >= max_pagina:
            break
        pagina += 1
    return crudos


async def proximos_partidos(season_id: int, ahora: float | None = None) -> list[dict]:
    """
    Partidos de la temporada que todavía no empezaron (instante UTC futuro), ordenados por fecha.
    Un partido que ya pasó su hora de inicio sin que FootyStats lo confirme NO se mezcla aquí: aparece en "En vivo" dentro de
    "Estado por confirmar" (así no desaparece, y tampoco se hace pasar por próximo).
    """
    ahora = reloj.ahora() if ahora is None else ahora
    partidos: list[dict] = []
    for m in await _descargar_partidos(season_id):
        fecha = m.get("date_unix")
        if not isinstance(fecha, int):
            continue
        # Solo lo que aún no empezó. Si pasó la hora y FootyStats no confirma, el partido está en "En vivo > Estado por confirmar".
        if clasificar(m.get("status"), fecha, ahora)["estado"] != "proximo":
            continue
        partidos.append(
            {
                "id": m.get("id"),
                "local": m.get("home_name"),
                "visitante": m.get("away_name"),
                "fecha_unix": fecha,
                "fecha_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(fecha)),
                "jornada": m.get("game_week"),
                "estado": "proximo",
            }
        )
    partidos.sort(key=lambda p: p["fecha_unix"])
    return partidos


# ─────────────────────────────────────────────
#  PARTIDOS DEL DÍA (consulta global compartida para "En vivo")
# ─────────────────────────────────────────────
ZONA_PERU = "America/Lima"
TTL_DIA_S = 60  # una misma fecha no se vuelve a pedir a FootyStats antes de 60 s, sin importar cuántos usuarios haya
DATOS_ANTIGUOS_MAX_S = 15 * 60  # si falla la consulta, se sirve la última copia mientras tenga menos de 15 min
POR_PAGINA_DIA = 200  # máximo de todays-matches por página
TTL_LIGAS_S = 600

_cache_dia: dict[str, dict] = {}
_candados_dia: dict[str, asyncio.Lock] = {}
_cache_ligas: dict = {"t": 0.0, "ligas": []}


CAMPOS_MINUTO = ("minute", "match_minute", "live_minute", "elapsed")


def _compactar_dia(m: dict) -> dict | None:
    fecha, local, visita = _num(m.get("date_unix")), m.get("home_name"), m.get("away_name")
    if m.get("id") is None or fecha is None:
        return None
    return {
        "id": m["id"],
        "hn": local,
        "an": visita,
        "t": int(fecha),
        "s": m.get("status"),
        "hg": _nn(m.get("homeGoalCount")),
        "ag": _nn(m.get("awayGoalCount")),
        # Minuto: solo si el proveedor lo envía en un campo explícito. En el plan actual no existe ninguno de estos campos.
        "min": next((int(v) for v in (_nn(m.get(c)) for c in CAMPOS_MINUTO) if v is not None), None),
        "comp": m.get("competition_id"),  # coincide con el id de temporada de /api/ligas
        "jor": m.get("game_week"),
    }


async def partidos_del_dia(fecha: str) -> dict:
    """
    Partidos de TODAS las competiciones de la cuenta para un día en hora de Perú (todays-matches).
    Una sola consulta (más páginas si hay más de 200) cubre todas las ligas, y el resultado se comparte entre usuarios.
    Devuelve {"partidos", "consultado", "completo", "obsoleto"}:
      completo=False: falló alguna página después de la primera (cobertura parcial).
      obsoleto=True: falló la consulta y se sirve la última copia guardada.
    """
    candado = _candados_dia.setdefault(fecha, asyncio.Lock())
    async with candado:  # varios clientes a la vez comparten una sola consulta
        ahora = reloj.ahora()
        guardado = _cache_dia.get(fecha)
        if guardado and ahora - guardado["consultado"] < TTL_DIA_S:
            return guardado
        try:
            partidos, completo, pagina = [], True, 1
            while pagina <= MAX_PAGINAS:
                try:
                    cuerpo = await _get("todays-matches", date=fecha, timezone=ZONA_PERU, page=pagina)
                except FootyStatsError:
                    if pagina == 1:
                        raise
                    completo = False  # ya hay datos de la primera página: se muestra lo que haya y se avisa
                    break
                partidos.extend(c for c in map(_compactar_dia, cuerpo.get("data") or []) if c)
                if pagina >= int((cuerpo.get("pager") or {}).get("max_page") or 1):
                    break
                pagina += 1
        except FootyStatsError as e:
            if guardado and e.tipo in ("conexion", "limite", "respuesta") and ahora - guardado["consultado"] < DATOS_ANTIGUOS_MAX_S:
                log.warning("todays-matches %s: se sirve la copia de hace %ds (%s)", fecha, int(ahora - guardado["consultado"]), e.tipo)
                return {**guardado, "obsoleto": True}
            raise
        nuevo = {"partidos": partidos, "consultado": ahora, "completo": completo, "obsoleto": False}
        _cache_dia[fecha] = nuevo
        return nuevo


async def ligas_cacheadas() -> list[dict]:
    """Lista de competiciones con caché corta (10 min) para uso interno; /api/ligas sigue consultando siempre."""
    ahora = time.time()
    if ahora - _cache_ligas["t"] > TTL_LIGAS_S or not _cache_ligas["ligas"]:
        _cache_ligas["ligas"] = await listar_ligas()
        _cache_ligas["t"] = ahora
    return _cache_ligas["ligas"]


# ─────────────────────────────────────────────
#  HISTORIAL LOCAL (una descarga por temporada, no una por clic)
# ─────────────────────────────────────────────
CACHE_DIR = Path(os.environ.get("FOOTYSTATS_CACHE", Path(__file__).with_name("cache_footystats")))
REFRESCO_MIN_S = 3600  # una temporada en curso no se vuelve a descargar antes de 1 h
CADUCIDAD_S = 24 * 3600  # ...y como mucho se refresca una vez al día aunque no haya novedades
MARGEN_PARTIDO_S = 3 * 3600  # un partido se da por terminado 3 h después de su hora de inicio
TEMPORADA_CERRADA_S = 30 * 24 * 3600  # 30 días después de su último partido, no cambia más

_candados: dict[int, asyncio.Lock] = {}


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


_CUOTAS = {"1": "odds_ft_1", "x": "odds_ft_x", "2": "odds_ft_2", "o25": "odds_ft_over25", "u25": "odds_ft_under25", "by": "odds_btts_yes", "bn": "odds_btts_no"}
_POTENCIALES = {
    "o15": "o15_potential", "o25": "o25_potential", "o35": "o35_potential", "btts": "btts_potential", "goles": "avg_potential",
    "corners": "corners_potential", "tarjetas": "cards_potential", "xg_l": "team_a_xg_prematch", "xg_v": "team_b_xg_prematch",
}
VERSION_CACHE = 4  # 4: minutos de cada gol (estados históricos para validar la predicción en vivo); 3: tiros; 2: córners, tarjetas, cuotas


def _nn(x):
    """Número no negativo, o None (FootyStats usa -1 para 'sin dato')."""
    x = _num(x)
    return x if x is not None and x >= 0 else None


def _pos(x, total):
    """Estadística por equipo, válida solo si el total del partido está registrado (> 0)."""
    t = _num(total)
    return _nn(x) if t is not None and t > 0 else None


def _par(a, b, cero_es_ausente: bool):
    """
    Estadística de los dos equipos de un partido. Devuelve (None, None) si falta (-1) en alguno de los dos.
    Si cero_es_ausente, que ambos equipos tengan 0 también es "sin dato" (0 córners, 0 tiros en todo un partido
    no ocurre: FootyStats rellena así los partidos sin estadísticas). Un cero de un solo equipo sí es real.
    """
    a, b = _nn(a), _nn(b)
    if a is None or b is None or (cero_es_ausente and a + b == 0):
        return None, None
    return a, b


def _minutos_gol(valor, goles, registrados) -> list[float] | None:
    """
    Minutos de los goles de un equipo ("23", "45+7", "90+3") como números ordenables: 45+7 -> 45.07.
    None si FootyStats no registró los minutos o si no cuadran con el total de goles (dato incompleto, no se usa).
    """
    if registrados != 1:
        return None
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except ValueError:
            return None
    minutos = []
    for g in valor or []:
        m = re.match(r"^\s*(\d+)(?:\s*\+\s*(\d+))?", str(g))
        if not m:
            return None
        minutos.append(int(m.group(1)) + (int(m.group(2)) / 100 if m.group(2) else 0))
    return sorted(minutos) if goles is not None and len(minutos) == goles else None


def _cuota(x):
    x = _num(x)
    return x if x is not None and x > 1 else None


def _compactar(m: dict) -> dict | None:
    """Solo los campos que usa el predictor (los partidos crudos pesan ~100 campos cada uno)."""
    fecha, local, visita = _num(m.get("date_unix")), _num(m.get("homeID")), _num(m.get("awayID"))
    if fecha is None or local is None or visita is None or m.get("id") is None:
        return None
    hx, ax = _num(m.get("team_a_xg")), _num(m.get("team_b_xg"))
    return {
        "id": m["id"],
        "h": int(local),
        "a": int(visita),
        "hn": m.get("home_name"),
        "an": m.get("away_name"),
        "t": int(fecha),
        "s": m.get("status"),
        "hg": _num(m.get("homeGoalCount")),
        "ag": _num(m.get("awayGoalCount")),
        # xG <= 0 significa "sin dato" en FootyStats
        "hx": hx if hx and hx > 0 else None,
        "ax": ax if ax and ax > 0 else None,
        "jor": m.get("game_week"),
        # Estadísticas extra (-1 / 0 significan "sin dato" en FootyStats)
        "hc": _pos(m.get("team_a_corners"), m.get("totalCornerCount")),
        "ac": _pos(m.get("team_b_corners"), m.get("totalCornerCount")),
        "hk": _nn(m.get("team_a_cards_num")),
        "ak": _nn(m.get("team_b_cards_num")),
        "ct": _num(m.get("card_timings_recorded")),  # 1: la fuente registró las tarjetas de ese partido
        "hs": _par(m.get("team_a_shots"), m.get("team_b_shots"), True)[0],
        "as": _par(m.get("team_a_shots"), m.get("team_b_shots"), True)[1],
        "hq": _par(m.get("team_a_shotsOnTarget"), m.get("team_b_shotsOnTarget"), True)[0],
        "aq": _par(m.get("team_a_shotsOnTarget"), m.get("team_b_shotsOnTarget"), True)[1],
        "h1": _nn(m.get("ht_goals_team_a")),
        "a1": _nn(m.get("ht_goals_team_b")),
        "ml": _minutos_gol(m.get("homeGoals"), _nn(m.get("homeGoalCount")), m.get("goal_timings_recorded")),
        "mv": _minutos_gol(m.get("awayGoals"), _nn(m.get("awayGoalCount")), m.get("goal_timings_recorded")),
        # Cuotas de cierre/previas que trae FootyStats (<= 1 significa "sin cuota")
        "q": {k: _cuota(m.get(f)) for k, f in _CUOTAS.items()},
        # "Potenciales": las estimaciones propias de FootyStats antes del partido
        "pot": {k: _num(m.get(f)) for k, f in _POTENCIALES.items()},
    }


def _ruta_cache(season_id: int) -> Path:
    return CACHE_DIR / f"temporada_{int(season_id)}.json"


def _leer_cache(season_id: int) -> dict | None:
    try:
        cache = json.loads(_ruta_cache(season_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cache if cache.get("v") == VERSION_CACHE else None  # copia antigua: sin los campos nuevos, se vuelve a bajar


def _guardar_cache(season_id: int, cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    destino = _ruta_cache(season_id)
    tmp = destino.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(destino)  # escritura atómica: nunca queda un JSON a medias


def _caducada(cache: dict, ahora: float) -> bool:
    edad = ahora - cache.get("descargado", 0)
    partidos = cache.get("partidos") or []
    if not partidos:
        return edad > REFRESCO_MIN_S
    if ahora - max(p["t"] for p in partidos) > TEMPORADA_CERRADA_S:
        return False
    pendientes = [p["t"] for p in partidos if p["s"] != "complete"]
    if not pendientes:
        return False  # temporada terminada
    # En curso: solo hay novedades si ya debió terminar algún partido que la copia aún da por pendiente.
    if edad > REFRESCO_MIN_S and ahora > min(pendientes) + MARGEN_PARTIDO_S:
        return True
    return edad > CADUCIDAD_S


async def historial_temporada(season_id: int, forzar: bool = False) -> dict:
    """
    Partidos (terminados y pendientes) de una temporada, desde disco si la copia sigue vigente.
    Si falla la descarga y hay copia antigua, se devuelve esa con "desactualizado": True.
    """
    candado = _candados.setdefault(season_id, asyncio.Lock())
    async with candado:  # dos clics a la vez no descargan dos veces
        ahora = reloj.ahora()
        cache = _leer_cache(season_id)
        if cache and not forzar and not _caducada(cache, ahora):
            return cache
        try:
            crudos = await _descargar_partidos(season_id)
        except FootyStatsError as e:
            if cache and e.tipo in ("conexion", "limite"):
                log.warning("Historial %s: se usa la copia local (%s)", season_id, e.tipo)
                return {**cache, "desactualizado": True}
            raise
        partidos = [c for c in map(_compactar, crudos) if c]
        nuevo = {"v": VERSION_CACHE, "season_id": season_id, "descargado": ahora, "partidos": partidos}
        _guardar_cache(season_id, nuevo)
        log.info("Historial %s descargado: %d partidos", season_id, len(partidos))
        return nuevo
