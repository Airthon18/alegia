"""
PrediLiga API — multi-agente híbrida.

Archivos esperados en la misma carpeta:
    ia.py     -> panel multiagente (debatir_prediccion, conversar_general)
    model.py  -> modelo ML de La Liga (predecir_partidos, obtener_equipos) + laliga_es_final.csv
    .env      -> claves y configuración (ver .env.example)
"""
from __future__ import annotations

import difflib
import logging
import os
import re
import unicodedata
from contextlib import contextmanager

try:  # carga .env si python-dotenv está instalado
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from mysql.connector import IntegrityError, pooling
from passlib.context import CryptContext
from pydantic import BaseModel, Field

from ia import conversar_general, debatir_prediccion, estado_proveedores
from model import obtener_equipos, predecir_partidos

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("prediliga")

# ─────────────────────────────────────────────
#  CONFIGURACIÓN (todo por variables de entorno)
# ─────────────────────────────────────────────
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME", "cristiano_db"),
}

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
#  SEGURIDAD
# ─────────────────────────────────────────────
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def get_password_hash(password) -> str:
    return pwd_context.hash(str(password))


def verify_password(plain_password, hashed_password) -> bool:
    return pwd_context.verify(str(plain_password), hashed_password)


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 6

# ─────────────────────────────────────────────
#  MODELOS DE ENTRADA
# ─────────────────────────────────────────────
class ChatIARequest(BaseModel):
    user_id: int
    mensaje: str = Field(min_length=1, max_length=2000)
    historial: list = []  # el chat actual no usa el historial todavía


class UsuarioAuth(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class DebateRequest(BaseModel):
    local: str = Field(min_length=1, max_length=60)
    visitante: str = Field(min_length=1, max_length=60)
    user_id: int


# ─────────────────────────────────────────────
#  BASE DE DATOS (pool de conexiones, creado al primer uso)
# ─────────────────────────────────────────────
_pool: pooling.MySQLConnectionPool | None = None


def _get_pool() -> pooling.MySQLConnectionPool:
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(pool_name="prediliga", pool_size=5, **DB_CONFIG)
    return _pool


@contextmanager
def db_cursor(dictionary: bool = False, commit: bool = False):
    """Cursor con cierre garantizado (antes, un error dejaba la conexión abierta)."""
    conn = _get_pool().get_connection()
    cursor = conn.cursor(dictionary=dictionary)
    try:
        yield cursor
        if commit:
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        cursor.close()
        conn.close()  # devuelve la conexión al pool


def guardar_prediccion_db(res: dict, user_id: int) -> None:
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO predictions
                (user_id, equipo_local, equipo_visitante, prob_local, prob_empate,
                 prob_visitante, marcador_probable, recomendacion)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    user_id,
                    res.get("equipo_local"),
                    res.get("equipo_visitante"),
                    float(res.get("prob_local", 0)),
                    float(res.get("prob_empate", 0)),
                    float(res.get("prob_visitante", 0)),
                    res.get("marcador_probable", ""),
                    res.get("recomendacion", ""),
                ),
            )
    except Exception:
        log.exception("No se pudo guardar la predicción en MySQL")


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
    return {"ok": True, "equipos": len(obtener_equipos()), "claves": estado_proveedores()}


# --- AUTH ---
@app.post("/registro")
def registrar_usuario(user: UsuarioAuth):
    email = user.email.strip()
    if not _EMAIL_RE.match(email):
        return {"error": "Correo no válido"}
    if len(user.password) < MIN_PASSWORD:
        return {"error": f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres"}
    try:
        with db_cursor(dictionary=True, commit=True) as cur:
            cur.execute("SELECT id FROM users WHERE email=%s", (email,))
            if cur.fetchone():
                return {"error": "Correo ya registrado"}
            cur.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, %s)",
                (email, get_password_hash(user.password)),
            )
            user_id = cur.lastrowid
        return {"ok": True, "user": {"id": user_id, "email": email}}
    except IntegrityError:  # dos registros simultáneos con el mismo correo
        return {"error": "Correo ya registrado"}
    except Exception:
        log.exception("Error en /registro")
        return {"error": ERROR_INTERNO}


@app.post("/login")
def login_usuario(user: UsuarioAuth):
    try:
        with db_cursor(dictionary=True) as cur:
            cur.execute("SELECT * FROM users WHERE email=%s", (user.email.strip(),))
            db_user = cur.fetchone()

        if not db_user or not verify_password(user.password, db_user["password_hash"]):
            return {"error": "Credenciales incorrectas"}
        return {"ok": True, "user": {"id": db_user["id"], "email": db_user["email"]}}
    except Exception:
        log.exception("Error en /login")
        return {"error": ERROR_INTERNO}


@app.get("/equipos")
def equipos():
    return obtener_equipos()


# --- CEREBRO MULTI-AGENTE ---
@app.post("/prediccion_debate")
async def prediccion_debate(req: DebateRequest):
    try:
        local_txt, visita_txt = req.local.strip(), req.visitante.strip()
        if not local_txt or not visita_txt:
            return {"error": "Debes indicar equipo local y visitante"}
        texto_usuario = f"{local_txt} vs {visita_txt}"

        local_oficial = resolver_equipo(local_txt)
        visitante_oficial = resolver_equipo(visita_txt)

        if local_oficial and visitante_oficial and local_oficial != visitante_oficial:
            log.info("MODO MATEMÁTICO: %s vs %s", local_oficial, visitante_oficial)
            # El modelo y MySQL son síncronos: fuera del event loop para no bloquear otras peticiones
            resultado_ml = await run_in_threadpool(predecir_partidos, local_oficial, visitante_oficial)

            if "error" in resultado_ml:
                log.error("Fallo en el modelo ML: %s", resultado_ml["error"])
                resultado_ml = {"error": "Fallo en modelo interno"}
            else:
                resultado_ml.setdefault("goles_esperados", {"local": 0, "visitante": 0, "total": 0})
                resultado_ml.setdefault("recomendacion", "Sin datos")
                resultado_ml.setdefault("ambos_marcan", 0.0)
                await run_in_threadpool(guardar_prediccion_db, resultado_ml, req.user_id)
        else:
            # Plan B automático: Mundiales, Champions, selecciones...
            log.info("MODO LIBRE: %s", texto_usuario)
            resultado_ml = {"error": "Modo IA Libre Activo"}

        respuesta = await debatir_prediccion(resultado_ml, texto_usuario)

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
async def chat_ia_endpoint(data: ChatIARequest):
    try:
        return {"respuesta": await conversar_general(data.mensaje)}
    except Exception:
        log.exception("Error en /chat_ia")
        return {"error": ERROR_INTERNO}


@app.get("/mis_predicciones")
def obtener_mis_predicciones(user_id: int, limite: int = 10):
    limite = max(1, min(limite, 50))
    try:
        with db_cursor(dictionary=True) as cur:
            cur.execute(
                "SELECT * FROM predictions WHERE user_id=%s ORDER BY created_at DESC LIMIT %s",
                (user_id, limite),
            )
            data = cur.fetchall()
        return {"predicciones": data}
    except Exception:
        log.exception("Error en /mis_predicciones")
        return {"error": ERROR_INTERNO}


if __name__ == "__main__":
    # Se pasa el objeto `app` y sin reload: así model.py se importa y entrena UNA sola vez.
    # (Con uvicorn.run("app:app", reload=True) el módulo se importaba en el proceso padre y otra vez
    # en el hijo, entrenando dos veces, y el hijo fallaba al lanzar procesos desde el import.)
    # Para desarrollo con recarga automática usa:  python -m uvicorn app:app --reload --port 8001
    import uvicorn

    uvicorn.run(app, host=os.environ.get("API_HOST", "0.0.0.0"), port=int(os.environ.get("API_PORT", 8001)))