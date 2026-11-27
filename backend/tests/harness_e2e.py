"""
Servidor de pruebas para el navegador: la aplicación REAL (app.py) con un FootyStats simulado y un reloj controlado.

    python tests/harness_e2e.py          # escucha en el puerto 8002

El reloj del "equipo" se fija 5 h por delante de la hora real, igual que en el error observado (la hora UTC mostrada como si
fuera la de Lima), y la cabecera Date simulada de FootyStats dice la hora UTC real: así se comprueba que el backend usa la
referencia y no el reloj local. Los escenarios se cambian con GET /__escenario/{nombre}.
Los equipos y marcadores son los de las capturas del usuario y solo existen aquí.
"""
import email.utils
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_mod  # noqa: E402
import footystats  # noqa: E402
import marcadores  # noqa: E402
import reloj  # noqa: E402
from estados_partido import LIMA, dia_lima  # noqa: E402


def lima(dia, hora, minuto=0):
    return datetime(2026, 11, dia, hora, minuto, tzinfo=LIMA).timestamp()


SYDNEY = lima(20, 3, 40)  # viernes 20 03:40 Lima (08:40 UTC, date_unix 1795164000)
SEATTLE = lima(19, 22, 0)  # jueves 19 22:00 Lima (viernes 03:00 UTC)
A_LEAGUE, MLS, LA_LIGA = 17326, 16504, 17199
SESGO_RELOJ_S = 5 * 3600  # el equipo va 5 h adelantado

ESTADO = {"real": lima(19, 23, 35), "escenario": "capturas"}
marcadores.CON_DISCO = False


def partido(id_, comp, local, visita, inicio, status="incomplete", goles=(0, 0), extra=None):
    m = {"id": id_, "homeID": id_ * 10, "awayID": id_ * 10 + 1, "home_name": local, "away_name": visita, "date_unix": int(inicio), "status": status,
         "homeGoalCount": goles[0], "awayGoalCount": goles[1], "competition_id": comp, "game_week": 5}
    m.update(extra or {})
    return m


def escenario():
    n = ESTADO["escenario"]
    sydney = partido(8597530, A_LEAGUE, "Western Sydney Wanderers", "Adelaide United", SYDNEY)
    if n == "capturas":  # 23:35 del jueves en Lima
        return [sydney, partido(8749311, MLS, "Seattle Sounders", "Real Salt Lake", SEATTLE)]
    if n == "seattle_en_vivo":  # respuesta que confirma 1-0 al minuto 60
        return [sydney, partido(8749311, MLS, "Seattle Sounders", "Real Salt Lake", SEATTLE, "inprogress", (1, 0), {"minute": 60})]
    if n == "vacia":  # el proveedor responde sin confirmar (0-0 por defecto)
        return [sydney, partido(8749311, MLS, "Seattle Sounders", "Real Salt Lake", SEATTLE)]
    if n == "variados":
        return [
            sydney,
            partido(8749311, MLS, "Seattle Sounders", "Real Salt Lake", SEATTLE, "inprogress", (0, 0), {"minute": 60}),  # en vivo con 0-0
            partido(9000001, LA_LIGA, "FC Barcelona", "Villarreal", lima(19, 21, 0), "halftime", (1, 1)),  # descanso
            partido(9000002, LA_LIGA, "Real Madrid", "Celta de Vigo", lima(19, 20, 0), "postponed"),  # aplazado
            partido(9000003, LA_LIGA, "Sevilla FC", "Real Betis", lima(19, 19, 30), "complete", (2, 1)),  # finalizado
            partido(9000004, LA_LIGA, "Valencia CF", "Rayo Vallecano", lima(19, 21, 30)),  # sin confirmar
        ]
    return []


def ligas_json():
    def liga(pais, nombre, sid):
        return {"country": pais, "name": f"{pais} {nombre}", "league_name": None, "season": [{"id": sid, "year": 2026}]}

    return [liga("Australia", "A-League", A_LEAGUE), liga("USA", "MLS", MLS), liga("Spain", "La Liga", LA_LIGA)]


async def _get_falso(endpoint, **params):
    if endpoint == "league-list":
        return {"success": True, "data": ligas_json(), "pager": {"max_page": 1}}
    if endpoint == "todays-matches":
        d = params["date"]
        return {"success": True, "data": [m for m in escenario() if dia_lima(m["date_unix"]) == d], "pager": {"max_page": 1}}
    if endpoint == "league-matches":
        return {"success": True, "data": [m for m in escenario() if m["competition_id"] == params["season_id"]], "pager": {"max_page": 1}}
    raise footystats.FootyStatsError("respuesta", "endpoint no simulado")


async def _asegurar_falso():
    # Lo que haría una respuesta real de FootyStats: la cabecera Date trae la hora UTC verdadera
    local = reloj.hora_local()
    reloj.registrar(email.utils.formatdate(ESTADO["real"], usegmt=True), local, local, "simulación")


footystats._get = _get_falso
reloj.hora_local = lambda: ESTADO["real"] + SESGO_RELOJ_S
reloj.asegurar = _asegurar_falso


@app_mod.app.get("/__escenario/{nombre}")
async def cambiar(nombre: str, real: float | None = None, reiniciar: bool = False):
    ESTADO["escenario"] = nombre
    ESTADO["real"] = real if real is not None else (lima(19, 23, 35) if nombre == "capturas" else lima(19, 23, 0))
    footystats._cache_dia.clear()
    if reiniciar:
        marcadores.reiniciar()
    return {"escenario": nombre, "real_utc": ESTADO["real"], "reloj_local": reloj.hora_local()}


# ───────── API-Football simulada + historial sintético (todo es simulación, nada viene de proveedores reales) ─────────
import os  # noqa: E402

import numpy as np  # noqa: E402

import api_football  # noqa: E402
import en_vivo  # noqa: E402
import prediccion_fs  # noqa: E402
import pronosticos_previos  # noqa: E402
import vinculos  # noqa: E402

vinculos.CON_DISCO = False
AF = {"escenario": "ninguno"}


def fx(goles, corto, elapsed, extra=None, **kw):
    nulo = (None, None)
    return {
        "fixture": {"id": 111, "timestamp": int(SEATTLE), "status": {"long": "x", "short": corto, "elapsed": elapsed, "extra": extra}},
        "league": {"id": 253, "name": "Major League Soccer", "country": "USA", "season": 2026, "round": kw.get("ronda", "Play-offs - Round 1")},
        "teams": {"home": {"id": 1, "name": "Seattle Sounders"}, "away": {"id": 2, "name": "Real Salt Lake"}},
        "goals": {"home": goles[0], "away": goles[1]},
        "score": {"halftime": {"home": 0, "away": 0}, "fulltime": {"home": kw.get("ft", nulo)[0], "away": kw.get("ft", nulo)[1]},
                  "extratime": {"home": kw.get("et", nulo)[0], "away": kw.get("et", nulo)[1]}, "penalty": {"home": kw.get("pen", nulo)[0], "away": kw.get("pen", nulo)[1]}},
    }


def fixtures_af():
    n = AF["escenario"]
    if n == "seattle_76":
        return [fx((1, 1), "2H", 76)]
    if n == "seattle_79":
        return [fx((2, 1), "2H", 79)]
    if n == "seattle_80_corregido":
        return [fx((1, 1), "2H", 80)]
    if n == "penales":
        return [fx((2, 2), "P", 120, ft=(1, 1), et=(1, 1), pen=(4, 3))]
    return []


async def _af_get(endpoint, **params):
    if AF["escenario"] == "desconectada":
        raise api_football.APIFootballError("credenciales", "API-Football rechazó la clave")
    if endpoint == "leagues":
        return {"response": [{"league": {"id": 253, "name": "Major League Soccer", "type": "League"}, "country": {"name": "USA"}, "seasons": [{"year": 2026, "current": True, "coverage": {"fixtures": {"events": True}}}]}]}, {}
    return {"response": fixtures_af()}, {"dia_restantes": 7000, "dia_limite": 7500}


api_football._get = _af_get


@app_mod.app.get("/__af/{nombre}")
async def cambiar_af(nombre: str):
    """Cambia lo que 'responde' la API-Football simulada. 'sin_clave' quita la clave."""
    AF["escenario"] = nombre
    if nombre == "sin_clave":
        os.environ.pop("API_FOOTBALL_KEY", None)
    else:
        os.environ["API_FOOTBALL_KEY"] = "clave-simulada"
    api_football.reiniciar()
    return {"af": nombre, "clave": api_football.configurada()}


# Historial sintético: 320 partidos con minutos de gol, para que la validación con estados históricos corra
_rng = np.random.default_rng(7)
_partidos = []
for i in range(320):
    gl, gv = int(_rng.poisson(1.5)), int(_rng.poisson(1.1))
    _partidos.append({"id": i, "h": i % 8, "a": (i + 3) % 8, "hn": f"E{i % 8}", "an": f"E{(i + 3) % 8}", "t": 1_700_000_000 + i * 43200, "s": "complete", "hg": gl, "ag": gv, "hx": None, "ax": None,
                      "ml": sorted(float(x) for x in _rng.uniform(1, 90, gl)), "mv": sorted(float(x) for x in _rng.uniform(1, 90, gv)), "sid": 1})
_hist = prediccion_fs.Historial(_partidos)
_objetivo = {"id": 8749311, "h": 100, "a": 101, "hn": "Seattle Sounders", "an": "Real Salt Lake", "t": int(SEATTLE), "s": "incomplete", "hg": 0, "ag": 0, "hx": None, "ax": None}


async def _previa(season_id, match_id, detalle=False):
    return {"estado": "ok", "partido": {"id": match_id, "local": "Seattle Sounders", "visitante": "Real Salt Lake"}, "goles_esperados": {"local": 1.6, "visitante": 1.1},
            "resultado_1x2": {"local": 0.48, "empate": 0.27, "visitante": 0.25}, "mas_2_5": {"si": 0.52, "no": 0.48}, "ambos_marcan": {"si": 0.55, "no": 0.45}}


async def _contexto(season_id, match_id):
    return {"pais": "USA", "nombre": "MLS"}, [1], _hist, _objetivo


prediccion_fs.predecir_partido = _previa
prediccion_fs._contexto = _contexto
pronosticos_previos.obtener = lambda match_id: None  # el pronóstico previo sale de _previa


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app_mod.app, host="127.0.0.1", port=8002)
