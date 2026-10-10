"""
Base de datos en Supabase (Postgres) a través de su API REST. Solo usa httpx.

Variables de entorno (en Render y en backend/.env):
    SUPABASE_URL          https://<proyecto>.supabase.co
    SUPABASE_SERVICE_KEY  clave "service_role" (SECRETA: solo en el servidor, nunca en el frontend)

El frontend inicia sesión con Supabase y manda su token en la cabecera Authorization: Bearer <token>.
Aquí se valida ese token con Supabase para saber qué usuario es, y se guardan sus predicciones.
Sin token (invitado) o sin configuración, no se guarda nada y la app sigue funcionando.
"""
from __future__ import annotations

import logging
import os
import time

import httpx

log = logging.getLogger("prediliga.db")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

TABLA = "predicciones"
_TTL_TOKEN = 60  # segundos que se recuerda un token ya validado
_cache_tokens: dict[str, tuple[float, dict]] = {}


def configurada() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _cabeceras(extra: dict | None = None) -> dict:
    h = {"apikey": SUPABASE_SERVICE_KEY, "Content-Type": "application/json"}
    # Las claves antiguas son JWT y se envían también como Bearer; las nuevas (sb_secret_...) solo como apikey.
    if SUPABASE_SERVICE_KEY.startswith("eyJ"):
        h["Authorization"] = f"Bearer {SUPABASE_SERVICE_KEY}"
    if extra:
        h.update(extra)
    return h


def _cliente() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=8.0)


def _token_de(authorization: str | None) -> str | None:
    if not authorization:
        return None
    partes = authorization.split(None, 1)
    if len(partes) != 2 or partes[0].lower() != "bearer":
        return None
    return partes[1].strip() or None


async def usuario_desde_token(authorization: str | None) -> dict | None:
    """Devuelve {"id": uuid, "email": correo} del usuario de Supabase dueño del token, o None si no es válido."""
    token = _token_de(authorization)
    if not token or not configurada():
        return None

    ahora = time.time()
    hit = _cache_tokens.get(token)
    if hit and ahora - hit[0] < _TTL_TOKEN:
        return hit[1]

    try:
        async with _cliente() as c:
            r = await c.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {token}"},
            )
        if r.status_code != 200:
            return None
        datos = r.json()
        uid = datos.get("id")
        if not uid:
            return None
        usuario = {"id": uid, "email": (datos.get("email") or "").strip().lower() or None}
        if len(_cache_tokens) > 500:  # evita que la caché crezca sin límite
            _cache_tokens.clear()
        _cache_tokens[token] = (ahora, usuario)
        return usuario
    except Exception as e:
        log.warning("No se pudo validar el token con Supabase: %s", e)
        return None


def _num(x, nd: int = 4):
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


async def guardar_prediccion(usuario: dict, res: dict, origen: str, debate: dict | None = None) -> bool:
    """Guarda una predicción del usuario ({"id","email"}). Nunca lanza excepciones: devuelve True/False."""
    if not (configurada() and usuario and usuario.get("id")):
        return False
    goles = res.get("goles_esperados") or {}
    fila = {
        "user_id": usuario["id"],
        "user_email": usuario.get("email"),
        "origen": origen if origen in ("chat", "equipos") else "chat",
        "equipo_local": res.get("equipo_local"),
        "equipo_visitante": res.get("equipo_visitante"),
        "prob_local": _num(res.get("prob_local")),
        "prob_empate": _num(res.get("prob_empate")),
        "prob_visitante": _num(res.get("prob_visitante")),
        "marcador_probable": res.get("marcador_probable"),
        "goles_esperados_local": _num(goles.get("local"), 2),
        "goles_esperados_visitante": _num(goles.get("visitante"), 2),
        "ambos_marcan": _num(res.get("ambos_marcan")),
        "over_2_5": _num(res.get("over_2_5")),
        "recomendacion": res.get("recomendacion"),
    }
    if debate:
        fila["apuesta_sugerida"] = (debate.get("apuesta_recomendada") or None)
        fila["confianza"] = (debate.get("confianza") or None)
        fila["veredicto"] = (debate.get("conclusion_comentarista") or None)
    try:
        async with _cliente() as c:
            r = await c.post(
                f"{SUPABASE_URL}/rest/v1/{TABLA}",
                headers=_cabeceras({"Prefer": "return=minimal"}),
                json=fila,
            )
        if r.status_code >= 300:
            log.error("Supabase rechazó la predicción (%s): %s", r.status_code, r.text[:300])
            return False
        return True
    except Exception as e:
        log.warning("No se pudo guardar la predicción en Supabase: %s", e)
        return False


async def listar_predicciones(user_id: str, limite: int = 20) -> list[dict] | None:
    """Últimas predicciones del usuario (None si falla la consulta)."""
    if not (configurada() and user_id):
        return None
    try:
        async with _cliente() as c:
            r = await c.get(
                f"{SUPABASE_URL}/rest/v1/{TABLA}",
                headers=_cabeceras(),
                params={
                    "select": "*",
                    "user_id": f"eq.{user_id}",
                    "order": "created_at.desc",
                    "limit": str(max(1, min(int(limite), 100))),
                },
            )
        if r.status_code != 200:
            log.error("Supabase no devolvió el historial (%s): %s", r.status_code, r.text[:300])
            return None
        return r.json()
    except Exception as e:
        log.warning("No se pudo leer el historial: %s", e)
        return None