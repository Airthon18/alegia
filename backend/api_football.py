"""
Fuente de marcadores en vivo: API-Football (https://www.api-football.com, v3).

FootyStats sigue siendo la fuente del calendario, el historial y el modelo. API-Football solo aporta lo que FootyStats no da en
este plan: estado en juego, minuto y goles confirmados mientras se juega.

  * La clave se lee de API_FOOTBALL_KEY (backend/.env) y viaja en la cabecera x-apisports-key: nunca en la URL ni en los logs.
  * Una sola consulta global (fixtures?live=all) cubre todas las ligas; el resultado se comparte entre todos los usuarios.
  * El intervalo entre consultas se ajusta a la cuota diaria que informa el propio proveedor (cabeceras x-ratelimit-*):
    con 100 consultas al día se pregunta cada ~14 min; con planes mayores, cada 15 s como mínimo.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

import reloj

log = logging.getLogger("prediliga.api_football")

BASE_URL = "https://v3.football.api-sports.io"
TIMEOUT_S = 20
TTL_MIN_S = 15
TTL_MAX_S = 900
RESERVA_CONSULTAS = 5  # consultas diarias que se dejan sin usar para diagnósticos (cobertura, etc.)
DATOS_ANTIGUOS_MAX_S = 30 * 60

# Estado corto de API-Football -> (estado de PrediBet, fase)
ESTADOS = {
    "TBD": ("proximo", None),
    "NS": ("proximo", None),
    "1H": ("en_juego", "1T"),
    "HT": ("descanso", "descanso"),
    "2H": ("en_juego", "2T"),
    "ET": ("prorroga", "prorroga"),
    "BT": ("prorroga", "pausa_prorroga"),
    "P": ("penales", "penales"),
    "LIVE": ("en_juego", None),
    "SUSP": ("suspendido", None),
    "INT": ("suspendido", None),
    "FT": ("finalizado", "final"),
    "AET": ("finalizado", "tras_prorroga"),
    "PEN": ("finalizado", "tras_penales"),
    "PST": ("aplazado", None),
    "CANC": ("cancelado", None),
    "ABD": ("suspendido", None),
    "AWD": ("finalizado", "final"),
    "WO": ("finalizado", "final"),
}
EN_JUEGO = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE"}


class APIFootballError(Exception):
    def __init__(self, tipo: str, mensaje: str):
        super().__init__(mensaje)
        self.tipo = tipo  # sin_configurar | credenciales | limite | conexion | respuesta
        self.mensaje = mensaje


def configurada() -> bool:
    return bool(os.environ.get("API_FOOTBALL_KEY", "").strip())


def _entero(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def leer_cuota(cabeceras) -> dict:
    """Cuota diaria y por minuto según las cabeceras del proveedor (None si no las envía)."""
    g = {k.lower(): v for k, v in dict(cabeceras or {}).items()}
    return {
        "dia_limite": _entero(g.get("x-ratelimit-requests-limit")),
        "dia_restantes": _entero(g.get("x-ratelimit-requests-remaining")),
        "minuto_limite": _entero(g.get("x-ratelimit-limit")),
        "minuto_restantes": _entero(g.get("x-ratelimit-remaining")),
    }


def ttl_para_cuota(cuota: dict, ahora: float) -> float:
    """
    Segundos entre consultas para no agotar la cuota diaria antes de que se renueve (00:00 UTC).
    Reparte las consultas que quedan entre el tiempo que falta; respeta también el límite por minuto.
    """
    restantes = cuota.get("dia_restantes")
    utc = datetime.fromtimestamp(ahora, timezone.utc)
    hasta_reset = (datetime.combine(utc.date() + timedelta(days=1), datetime.min.time(), timezone.utc) - utc).total_seconds()
    if restantes is not None and restantes <= RESERVA_CONSULTAS:
        return float(max(hasta_reset, TTL_MIN_S))  # cuota agotada: no se vuelve a preguntar hasta que se renueve
    ttl = 60.0 if restantes is None else hasta_reset / (restantes - RESERVA_CONSULTAS)  # sin datos de cuota, prudente
    if cuota.get("minuto_limite"):
        ttl = max(ttl, 60 / cuota["minuto_limite"])
    return float(min(max(ttl, TTL_MIN_S), TTL_MAX_S))


def _error_de_cuerpo(errores) -> APIFootballError | None:
    """API-Football responde 200 con un campo "errors" (lista vacía si todo fue bien, o un objeto con el motivo)."""
    if not errores:
        return None
    texto = " ".join(str(v) for v in (errores.values() if isinstance(errores, dict) else errores)).lower()
    claves = " ".join(errores.keys()).lower() if isinstance(errores, dict) else ""
    if "token" in claves or "key" in texto or "application key" in texto or "subscription" in texto:
        return APIFootballError("credenciales", "API-Football rechazó la clave o la suscripción")
    if "requests" in claves or "ratelimit" in claves or "limit" in texto:
        return APIFootballError("limite", "Se alcanzó el límite de consultas de API-Football")
    return APIFootballError("respuesta", "API-Football devolvió un error")


async def _get(endpoint: str, **params) -> tuple[dict, dict]:
    clave = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not clave:
        raise APIFootballError("sin_configurar", "Falta API_FOOTBALL_KEY en backend/.env")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as cliente:
            antes = reloj.hora_local()
            r = await cliente.get(f"{BASE_URL}/{endpoint}", params=params, headers={"x-apisports-key": clave})
            reloj.registrar(r.headers.get("date"), antes, reloj.hora_local(), "API-Football")
    except httpx.HTTPError as e:
        log.warning("API-Football %s: fallo de conexión (%s)", endpoint, type(e).__name__)
        raise APIFootballError("conexion", "No se pudo conectar con API-Football") from None
    cuota = leer_cuota(r.headers)
    if r.status_code in (401, 403):
        raise APIFootballError("credenciales", "API-Football rechazó la clave")
    if r.status_code == 429:
        raise APIFootballError("limite", "Se alcanzó el límite de consultas de API-Football")
    if r.status_code >= 500:
        raise APIFootballError("conexion", "API-Football no está disponible")
    try:
        cuerpo = r.json()
    except ValueError:
        raise APIFootballError("respuesta", "API-Football devolvió una respuesta no válida") from None
    err = _error_de_cuerpo(cuerpo.get("errors"))
    if err:
        raise err
    return cuerpo, cuota


def normalizar(f: dict) -> dict | None:
    """Un partido de API-Football con los campos que usa PrediBet. None si le falta lo esencial."""
    fx, liga, eq, goles, score = f.get("fixture") or {}, f.get("league") or {}, f.get("teams") or {}, f.get("goals") or {}, f.get("score") or {}
    estado = fx.get("status") or {}
    corto = estado.get("short")
    if fx.get("id") is None or fx.get("timestamp") is None or not (eq.get("home") or {}).get("name") or not (eq.get("away") or {}).get("name"):
        return None
    nuestro, fase = ESTADOS.get(corto, ("por_confirmar", None))

    def par(d):
        d = d or {}
        return {"local": d["home"], "visitante": d["away"]} if d.get("home") is not None and d.get("away") is not None else None

    return {
        "af_id": fx["id"],
        "inicio_unix": int(fx["timestamp"]),
        "estado_corto": corto,
        "estado": nuestro,
        "fase": fase,
        "minuto": estado.get("elapsed"),
        "minuto_extra": estado.get("extra"),
        "local": eq["home"]["name"],
        "visitante": eq["away"]["name"],
        "goles": par(goles),
        "resultado_90": par(score.get("fulltime")) if corto in ("ET", "BT", "P", "AET", "PEN") or score.get("extratime", {}).get("home") is not None else None,
        "prorroga": par(score.get("extratime")),
        "penales": par(score.get("penalty")),
        "liga": {"id": liga.get("id"), "nombre": liga.get("name"), "pais": liga.get("country"), "temporada": liga.get("season"), "ronda": liga.get("round")},
    }


_cache: dict = {}
_candado = asyncio.Lock()


def reiniciar() -> None:
    _cache.clear()


async def partidos_en_vivo() -> dict:
    """
    Partidos en juego en este momento en todo API-Football, compartidos entre usuarios.
    {partidos, consultado, ttl, proxima, cuota, obsoleto, error}. Si la consulta falla y hay copia de menos de 30 min, se sirve
    marcada como obsoleta; si no, se lanza APIFootballError.
    """
    async with _candado:
        ahora = reloj.ahora()
        if _cache and ahora - _cache["consultado"] < _cache["ttl"]:
            return {**_cache, "obsoleto": False}
        try:
            cuerpo, cuota = await _get("fixtures", live="all")
        except APIFootballError as e:
            if _cache and e.tipo != "sin_configurar" and ahora - _cache["consultado"] < DATOS_ANTIGUOS_MAX_S:
                log.warning("API-Football: se sirve la copia de hace %ds (%s)", int(ahora - _cache["consultado"]), e.tipo)
                # tras un error de límite, no se vuelve a insistir hasta el siguiente intervalo
                _cache["ttl"] = max(_cache["ttl"], ttl_para_cuota(_cache.get("cuota") or {}, ahora) if e.tipo != "limite" else 600)
                return {**_cache, "obsoleto": True, "error": {"tipo": e.tipo, "mensaje": e.mensaje}}
            raise
        ttl = ttl_para_cuota(cuota, ahora)
        _cache.update({
            "partidos": [p for p in map(normalizar, cuerpo.get("response") or []) if p],
            "consultado": ahora,
            "ttl": ttl,
            "proxima": ahora + ttl,
            "cuota": cuota,
            "error": None,
        })
        return {**_cache, "obsoleto": False}


async def ligas_actuales() -> list[dict]:
    """Ligas de API-Football con temporada en curso y su cobertura (una consulta). Para comprobar la cobertura de las 47."""
    cuerpo, _ = await _get("leagues", current="true")
    salida = []
    for x in cuerpo.get("response") or []:
        liga, pais, temporadas = x.get("league") or {}, x.get("country") or {}, x.get("seasons") or []
        actual = next((t for t in temporadas if t.get("current")), temporadas[-1] if temporadas else {})
        cobertura = (actual.get("coverage") or {}).get("fixtures") or {}
        salida.append({"id": liga.get("id"), "nombre": liga.get("name"), "tipo": liga.get("type"), "pais": pais.get("name"), "temporada": actual.get("year"), "eventos": bool(cobertura.get("events"))})
    return salida


async def rondas(liga_id: int, temporada: int) -> list[str]:
    cuerpo, _ = await _get("fixtures/rounds", league=liga_id, season=temporada)
    return [str(r) for r in cuerpo.get("response") or []]
