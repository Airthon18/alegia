"""
PrediLiga API — multi-agente híbrida.

Archivos esperados en la misma carpeta:
    ia.py     -> panel multiagente (debatir_prediccion, conversar_general)
    model.py  -> modelo ML de La Liga (predecir_partidos, obtener_equipos) + laliga_es_final.csv
    .env      -> claves y configuración (ver .env.example)
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import os
import re
import unicodedata

try:  # carga .env si python-dotenv está instalado
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, Header
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import db
import api_football
import en_vivo
import footystats
import prediccion_fs
import pronosticos_previos
import reloj
import vinculos
from footystats import FootyStatsError
from ia import conversar_general, debatir_prediccion, estado_proveedores
from model import obtener_equipos, obtener_equipos_actuales, predecir_partidos
from noticias import obtener_noticias

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("prediliga")

# ─────────────────────────────────────────────
#  CONFIGURACIÓN (todo por variables de entorno)
# ─────────────────────────────────────────────
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:8080,http://127.0.0.1:8080,http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if o.strip()
]

ERROR_INTERNO = "Error interno del servidor"

# ─────────────────────────────────────────────
#  MODELOS DE ENTRADA
# ─────────────────────────────────────────────
class ChatIARequest(BaseModel):
    user_id: int = 0  # ya no se usa: el usuario se identifica por el token de Supabase
    mensaje: str = Field(min_length=1, max_length=2000)
    historial: list = []  # el chat actual no usa el historial todavía


class EquiposRequest(BaseModel):
    local: str = Field(min_length=1, max_length=60)
    visitante: str = Field(min_length=1, max_length=60)


class DebateRequest(BaseModel):
    local: str = Field(min_length=1, max_length=60)
    visitante: str = Field(min_length=1, max_length=60)
    user_id: int = 0  # ya no se usa: el usuario se identifica por el token de Supabase


# ─────────────────────────────────────────────
#  RESOLUCIÓN DE EQUIPOS DE LA LIGA
# ─────────────────────────────────────────────
_STOP = {"fc", "cf", "cd", "ud", "de", "la", "el", "club", "sad"}


def _norm(texto: str) -> str:
    """Minúsculas, sin tildes ni signos: 'Atlético  de Madrid!' -> 'atletico de madrid'."""
    s = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def resolver_equipo(nombre: str) -> str | None:
    """
    Devuelve el nombre oficial del equipo de La Liga, o None si no es de La Liga o es ambiguo
    (en ese caso el endpoint pasa al modo libre, p. ej. selecciones o Champions).

    Antes, cualquier palabra suelta ("real", "de", "fc") coincidía con el primer equipo que la
    contuviera. Ahora una palabra solo vale si identifica a un único equipo.
    """
    q = _norm(nombre)
    if not q:
        return None
    norm = {eq: _norm(eq) for eq in obtener_equipos()}

    # 1. nombre completo exacto
    for eq, n in norm.items():
        if n == q:
            return eq

    # 2. los términos significativos de la consulta identifican a un único equipo
    tokens = [t for t in q.split() if t not in _STOP and len(t) >= 3]
    if tokens:
        candidatos = [eq for eq, n in norm.items() if all(t in n.split() for t in tokens)]
        if len(candidatos) == 1:
            return candidatos[0]
        if len(candidatos) > 1:
            return None  # ambiguo ("real", "madrid"...)

    # 3. error tipográfico, con umbral alto para no cruzar nombres ajenos (Francia -> Valencia)
    cercano = difflib.get_close_matches(q, list(norm.values()), n=1, cutoff=0.75)
    if cercano:
        return next(eq for eq, n in norm.items() if n == cercano[0])
    return None


# Tareas de guardado en segundo plano (se guarda referencia para que no las recoja el recolector)
_tareas: set = set()


def _en_segundo_plano(coro) -> None:
    t = asyncio.create_task(coro)
    _tareas.add(t)
    t.add_done_callback(_tareas.discard)


# ─────────────────────────────────────────────
#  APP
# ─────────────────────────────────────────────
app = FastAPI(title="PrediLiga API Multi-Agente Híbrida", version="4.6.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    # "claves": qué APIs tienen clave definida (solo True/False, nunca el valor)
    return {
        "ok": True,
        "equipos": len(obtener_equipos()),
        "claves": estado_proveedores(),
        "base_de_datos": db.configurada(),
    }


@app.get("/equipos")
def equipos():
    return obtener_equipos()


@app.get("/noticias")
async def noticias():
    """Últimas noticias de fútbol (RSS público, en caché 10 min)."""
    try:
        return await obtener_noticias()
    except Exception:
        log.exception("Error en /noticias")
        return {"error": ERROR_INTERNO}


@app.get("/equipos_liga")
def equipos_liga():
    """Equipos de la temporada actual (para el selector de la pestaña 'Por equipos')."""
    return {"liga": "LaLiga", "equipos": obtener_equipos_actuales()}


# Estado HTTP por causa de fallo de FootyStats: así el frontend distingue credenciales, conexión y límite.
_ESTADO_FOOTYSTATS = {"credenciales": 502, "conexion": 503, "limite": 429, "respuesta": 502}


def _error_footystats(e: FootyStatsError) -> JSONResponse:
    return JSONResponse({"error": e.mensaje, "tipo": e.tipo}, status_code=_ESTADO_FOOTYSTATS.get(e.tipo, 502))


@app.get("/api/ligas")
async def api_ligas():
    """Competiciones activas de la cuenta de FootyStats (se consulta a la API en cada petición)."""
    try:
        return {"ligas": await footystats.listar_ligas()}
    except FootyStatsError as e:
        return _error_footystats(e)
    except Exception:
        log.exception("Error en /api/ligas")
        return JSONResponse({"error": ERROR_INTERNO, "tipo": "interno"}, status_code=500)


@app.get("/api/temporadas/{season_id}/partidos")
async def api_partidos_temporada(season_id: int):
    """Próximos partidos de una temporada, ordenados por fecha (lista vacía si no hay pendientes)."""
    try:
        return {"season_id": season_id, "partidos": await footystats.proximos_partidos(season_id)}
    except FootyStatsError as e:
        return _error_footystats(e)
    except Exception:
        log.exception("Error en /api/temporadas/%s/partidos", season_id)
        return JSONResponse({"error": ERROR_INTERNO, "tipo": "interno"}, status_code=500)


@app.get("/api/reloj")
async def api_reloj():
    """Diagnóstico: reloj del servidor frente a la hora UTC de referencia (cabecera Date de FootyStats)."""
    await reloj.asegurar()
    return {**reloj.info(), "ahora_utc_unix": int(reloj.ahora()), "aviso": reloj.aviso()}


@app.get("/api/en-vivo")
async def api_en_vivo():
    """
    Partidos en curso (y los que están por empezar o acaban de terminar) de TODAS las competiciones de la cuenta.
    Una consulta global compartida entre usuarios (caché de 60 s). Si FootyStats falla, responde con el tipo de error
    (credenciales, conexión, límite) en lugar de una lista vacía: "falló la consulta" no es "no hay partidos".
    """
    try:
        return await en_vivo.en_vivo(ids_con_pronostico=pronosticos_previos.ids_guardados)
    except FootyStatsError as e:
        return _error_footystats(e)
    except Exception:
        log.exception("Error en /api/en-vivo")
        return JSONResponse({"error": ERROR_INTERNO, "tipo": "interno"}, status_code=500)


_cobertura_cache: dict = {"t": 0.0, "ligas_af": None}


@app.get("/api/en-vivo/cobertura")
async def api_cobertura_en_vivo():
    """
    Cobertura de tus competiciones en la fuente en vivo (API-Football): "confirmada" si ya se emparejó un partido real,
    "probable" si existe una liga equivalente, "sin_equivalente" o "sin_verificar" (sin clave). Una consulta al día como mucho.
    """
    try:
        ligas = await footystats.ligas_cacheadas()
    except FootyStatsError as e:
        return _error_footystats(e)
    if not api_football.configurada():
        return {"fuente_vivo": "pendiente_configurar", "detalle": "Falta API_FOOTBALL_KEY en backend/.env", "ligas": vinculos.cobertura(ligas, None)}
    try:
        if _cobertura_cache["ligas_af"] is None or reloj.ahora() - _cobertura_cache["t"] > 24 * 3600:
            _cobertura_cache.update({"ligas_af": await api_football.ligas_actuales(), "t": reloj.ahora()})
    except api_football.APIFootballError as e:
        return {"fuente_vivo": "desconectada", "detalle": e.mensaje, "tipo": e.tipo, "ligas": vinculos.cobertura(ligas, None)}
    return {"fuente_vivo": "conectada", "consultado_unix": int(_cobertura_cache["t"]), "ligas": vinculos.cobertura(ligas, _cobertura_cache["ligas_af"])}


@app.get("/api/partidos/{match_id}/prediccion-en-vivo")
async def api_prediccion_en_vivo(match_id: int):
    """
    Predicción con el marcador y el minuto CONFIRMADOS por la fuente en vivo. Parte de la fuerza previa de los equipos y de
    lo que queda de los 90 minutos. Incluye el pronóstico previo por separado para compararlos.
    """
    try:
        estado = en_vivo.ULTIMO.get(match_id)
        if estado is None:
            await en_vivo.en_vivo(ids_con_pronostico=pronosticos_previos.ids_guardados)  # usa la caché compartida
            estado = en_vivo.ULTIMO.get(match_id)
        if estado is None:
            return JSONResponse({"error": "Ese partido no figura entre los partidos de hoy", "tipo": "no_encontrado"}, status_code=404)
        marcador = estado.get("marcador") or {}
        if estado["estado"] not in ("en_juego", "descanso", "prorroga", "penales", "finalizado") or marcador.get("estado") != "confirmado":
            return JSONResponse({"error": "No hay marcador y minuto confirmados en vivo para este partido", "tipo": "sin_datos_confirmados"}, status_code=409)
        vivo = {
            "fase": estado.get("fase") or ("final" if estado["estado"] == "finalizado" else None),
            "minuto": estado.get("minuto"),
            "minuto_extra": estado.get("minuto_extra"),
            "marcador": {"local": marcador["local"], "visitante": marcador["visitante"]},
            "resultado_90": estado.get("resultado_90") or ({"local": marcador["local"], "visitante": marcador["visitante"]} if estado["estado"] == "finalizado" else None),
            "prorroga": estado.get("prorroga"),
            "penales": estado.get("penales"),
            "dato_unix": marcador.get("recibido_unix"),
            "consultado_unix": estado.get("consultado_unix"),
        }
        res = await prediccion_fs.prediccion_en_vivo(estado["comp"], match_id, vivo)
        if res is None:
            return JSONResponse({"error": "No se encontró el partido en el historial de FootyStats", "tipo": "no_encontrado"}, status_code=404)
        return {**res, "estado_partido": estado["estado"], "senal_atrasada": estado.get("senal_atrasada", False), "fuente": estado.get("fuente")}
    except FootyStatsError as e:
        return _error_footystats(e)
    except Exception:
        log.exception("Error en /api/partidos/%s/prediccion-en-vivo", match_id)
        return JSONResponse({"error": ERROR_INTERNO, "tipo": "interno"}, status_code=500)


@app.get("/api/partidos/{match_id}/pronostico-previo")
async def api_pronostico_previo(match_id: int):
    """Pronóstico que se mostró antes del inicio del partido (no se recalcula)."""
    guardado = pronosticos_previos.obtener(match_id)
    if not guardado:
        return JSONResponse({"error": "No hay un pronóstico guardado antes del inicio de este partido", "tipo": "sin_pronostico"}, status_code=404)
    return guardado


@app.get("/api/temporadas/{season_id}/partidos/{match_id}/prediccion")
async def api_prediccion_partido(season_id: int, match_id: int, detalle: bool = False):
    """Predicción de un partido real con el historial de FootyStats: resumen, o todos los mercados con ?detalle=true."""
    try:
        res = await prediccion_fs.predecir_partido(season_id, match_id, detalle)
        if res is None:
            return JSONResponse({"error": "Partido no encontrado en esa temporada", "tipo": "no_encontrado"}, status_code=404)
        return res
    except FootyStatsError as e:
        return _error_footystats(e)
    except Exception:
        log.exception("Error en /api/temporadas/%s/partidos/%s/prediccion", season_id, match_id)
        return JSONResponse({"error": ERROR_INTERNO, "tipo": "interno"}, status_code=500)


@app.post("/prediccion_equipos")
async def prediccion_equipos(req: EquiposRequest, authorization: str | None = Header(default=None)):
    """Predicción solo con el modelo (sin agentes de IA): rápida y sin consumir claves."""
    try:
        local = resolver_equipo(req.local)
        visitante = resolver_equipo(req.visitante)
        if not local or not visitante:
            return {"error": "Equipo no encontrado en LaLiga"}
        if local == visitante:
            return {"error": "Elige dos equipos distintos"}
        res = await run_in_threadpool(predecir_partidos, local, visitante)
        if "error" in res:
            log.error("Fallo en el modelo ML: %s", res["error"])
            return {"error": "Fallo en modelo interno"}
        usuario = await db.usuario_desde_token(authorization)
        if usuario:  # solo usuarios con sesión: el historial se guarda sin hacer esperar la respuesta
            _en_segundo_plano(db.guardar_prediccion(usuario, res, "equipos", consulta=f"{local} vs {visitante}"))
        return {"estado": "exito", "datos_matematicos": res}
    except Exception:
        log.exception("Error en /prediccion_equipos")
        return {"error": ERROR_INTERNO}


# --- CEREBRO MULTI-AGENTE ---
@app.post("/prediccion_debate")
async def prediccion_debate(req: DebateRequest, authorization: str | None = Header(default=None)):
    try:
        local_txt, visita_txt = req.local.strip(), req.visitante.strip()
        if not local_txt or not visita_txt:
            return {"error": "Debes indicar equipo local y visitante"}
        texto_usuario = f"{local_txt} vs {visita_txt}"

        local_oficial = resolver_equipo(local_txt)
        visitante_oficial = resolver_equipo(visita_txt)

        if local_oficial and visitante_oficial and local_oficial != visitante_oficial:
            log.info("MODO MATEMÁTICO: %s vs %s", local_oficial, visitante_oficial)
            # El modelo es síncrono: fuera del event loop para no bloquear otras peticiones
            resultado_ml = await run_in_threadpool(predecir_partidos, local_oficial, visitante_oficial)

            if "error" in resultado_ml:
                log.error("Fallo en el modelo ML: %s", resultado_ml["error"])
                resultado_ml = {"error": "Fallo en modelo interno"}
            else:
                resultado_ml.setdefault("goles_esperados", {"local": 0, "visitante": 0, "total": 0})
                resultado_ml.setdefault("recomendacion", "Sin datos")
                resultado_ml.setdefault("ambos_marcan", 0.0)
        else:
            # Plan B automático: Mundiales, Champions, selecciones...
            log.info("MODO LIBRE: %s", texto_usuario)
            resultado_ml = {"error": "Modo IA Libre Activo"}

        respuesta = await debatir_prediccion(resultado_ml, texto_usuario)

        usuario = await db.usuario_desde_token(authorization)
        if usuario:
            debate = respuesta.get("debate_agentes") or {}
            if "error" not in resultado_ml:
                _en_segundo_plano(db.guardar_prediccion(usuario, resultado_ml, "chat", debate, consulta=texto_usuario))
            else:  # partido fuera de LaLiga (selecciones, Champions...): se guarda la consulta y el veredicto
                _en_segundo_plano(db.guardar_consulta(usuario, "chat_libre", texto_usuario, debate=debate))

        return {
            "estado": "exito",
            "modo": respuesta.get("modo", "matematico"),
            "datos_matematicos": respuesta.get("datos_matematicos", {}),
            "debate_agentes": respuesta.get("debate_agentes", {}),
        }
    except Exception:
        log.exception("Error en /prediccion_debate")
        return {"error": ERROR_INTERNO}


@app.post("/chat_ia")
async def chat_ia_endpoint(data: ChatIARequest, authorization: str | None = Header(default=None)):
    try:
        respuesta = await conversar_general(data.mensaje)
        usuario = await db.usuario_desde_token(authorization)
        if usuario and isinstance(respuesta, str):
            _en_segundo_plano(db.guardar_consulta(usuario, "mensaje", data.mensaje, respuesta))
        return {"respuesta": respuesta}
    except Exception:
        log.exception("Error en /chat_ia")
        return {"error": ERROR_INTERNO}


@app.get("/mis_predicciones")
async def obtener_mis_predicciones(limite: int = 20, authorization: str | None = Header(default=None)):
    """Historial del usuario con sesión iniciada (token de Supabase en Authorization: Bearer ...)."""
    usuario = await db.usuario_desde_token(authorization)
    if not usuario:
        return {"error": "Inicia sesión para ver tu historial"}
    filas = await db.listar_predicciones(usuario["id"], limite)
    if filas is None:
        return {"error": ERROR_INTERNO}
    return {"predicciones": filas}


if __name__ == "__main__":
    # Se pasa el objeto `app` y sin reload: así model.py se importa y entrena UNA sola vez.
    # (Con uvicorn.run("app:app", reload=True) el módulo se importaba en el proceso padre y otra vez
    # en el hijo, entrenando dos veces, y el hijo fallaba al lanzar procesos desde el import.)
    # Para desarrollo con recarga automática usa:  python -m uvicorn app:app --reload --port 8001
    import uvicorn

    uvicorn.run(app, host=os.environ.get("API_HOST", "0.0.0.0"), port=int(os.environ.get("API_PORT", 8001)))