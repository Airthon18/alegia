"""
Cristiano — panel multiagente de análisis de fútbol.

Variables de entorno requeridas (ponlas en un .env o en tu entorno):
    GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, SERPAPI_KEY
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import statistics
from dataclasses import dataclass, field
from datetime import date

import httpx

log = logging.getLogger("cristiano")

# ─────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────


class LLMError(Exception):
    pass


@dataclass
class Provider:
    name: str
    url: str
    key_env: str
    model: str
    extra_headers: dict = field(default_factory=dict)
    max_concurrent: int = 3
    _sem: asyncio.Semaphore | None = field(default=None, repr=False)

    @property
    def key(self) -> str:
        k = os.environ.get(self.key_env, "")
        if not k:
            raise LLMError(f"Falta la variable de entorno {self.key_env}")
        return k

    @property
    def sem(self) -> asyncio.Semaphore:
        # se crea perezosamente para que pertenezca al event loop activo
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.max_concurrent)
        return self._sem


# Los modelos se pueden cambiar sin tocar el código con GEMINI_MODEL, GROQ_MODEL y OPENROUTER_MODEL.
# (anthropic/claude-3-haiku ya no existe en OpenRouter: devolvía 404.)
PROVIDERS = {
    "gemini": Provider(
        "gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "GEMINI_API_KEY",
        os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
    ),
    "groq": Provider(
        "groq",
        "https://api.groq.com/openai/v1/chat/completions",
        "GROQ_API_KEY",
        os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
    ),
    "openrouter": Provider(
        "openrouter",
        "https://openrouter.ai/api/v1/chat/completions",
        "OPENROUTER_API_KEY",
        os.environ.get("OPENROUTER_MODEL", "anthropic/claude-haiku-5.5"),
        extra_headers={"HTTP-Referer": "http://localhost:8000", "X-Title": "Cristiano Agent"},
    ),
}


def estado_proveedores() -> dict:
    """Qué claves están definidas (solo True/False, nunca el valor). Útil para diagnosticar en /health."""
    return {p.name: bool(os.environ.get(p.key_env)) for p in PROVIDERS.values()} | {
        "serpapi": bool(os.environ.get("SERPAPI_KEY"))
    }

# Si un proveedor falla, se prueba con los siguientes de la lista.
FALLBACKS = {
    "groq": ["openrouter", "gemini"],
    "openrouter": ["groq", "gemini"],
    "gemini": ["groq", "openrouter"],
}

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Busca noticias e información actualizada sobre equipos de fútbol, jugadores, "
            "lesiones, estadísticas recientes, estados de forma y contexto del partido."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Consulta de búsqueda."}},
            "required": ["query"],
        },
    },
}

LAMBDA_MIN, LAMBDA_MAX = 0.2, 4.5
DEFAULT_LAMBDAS = (1.35, 1.05)  # ventaja local estadística típica
DC_RHO = -0.05  # corrección Dixon-Coles para marcadores bajos


# ═════════════════════════════════════════════
#  MATEMÁTICAS — Poisson bivariada + Dixon-Coles
# ═════════════════════════════════════════════

def poisson_pmf(lam: float, k: int) -> float:
    return math.exp(k * math.log(lam) - lam - math.lgamma(k + 1))


def _dc_tau(x: int, y: int, lh: float, la: float, rho: float) -> float:
    """Ajuste Dixon-Coles: corrige la sobre/sub-estimación de 0-0, 1-0, 0-1, 1-1."""
    if x == 0 and y == 0:
        return 1 - lh * la * rho
    if x == 0 and y == 1:
        return 1 + lh * rho
    if x == 1 and y == 0:
        return 1 + la * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def calcular_distribucion_poisson(
    lambda_local: float, lambda_visita: float, max_goles: int = 10, rho: float = DC_RHO
) -> dict:
    lh = min(max(lambda_local, LAMBDA_MIN), LAMBDA_MAX)
    la = min(max(lambda_visita, LAMBDA_MIN), LAMBDA_MAX)

    matriz = {}
    for x in range(max_goles + 1):
        px = poisson_pmf(lh, x)
        for y in range(max_goles + 1):
            matriz[(x, y)] = max(px * poisson_pmf(la, y) * _dc_tau(x, y, lh, la, rho), 0.0)

    total = sum(matriz.values())
    matriz = {k: v / total for k, v in matriz.items()}

    p_local = sum(v for (x, y), v in matriz.items() if x > y)
    p_empate = sum(v for (x, y), v in matriz.items() if x == y)
    p_visita = 1.0 - p_local - p_empate
    p_btts = sum(v for (x, y), v in matriz.items() if x >= 1 and y >= 1)
    p_over25 = sum(v for (x, y), v in matriz.items() if x + y >= 3)

    top = sorted(matriz.items(), key=lambda kv: kv[1], reverse=True)[:3]
    return {
        "prob_local": round(p_local, 4),
        "prob_empate": round(p_empate, 4),
        "prob_visitante": round(p_visita, 4),
        "prob_btts": round(p_btts, 4),
        "prob_over_2_5": round(p_over25, 4),
        "marcador_probable": f"{top[0][0][0]}-{top[0][0][1]}",
        "top_marcadores": [{"marcador": f"{x}-{y}", "prob": round(p, 4)} for (x, y), p in top],
    }


def _clamp(v: float) -> float:
    return round(min(max(v, LAMBDA_MIN), LAMBDA_MAX), 2)


_NUM = r"(\d+(?:[.,]\d+)?)"
_RE_CANONICO = re.compile(
    rf"goles?\s+esperados?\s*[:\-]\s*{_NUM}\s*(?:local|loc)?\s*/\s*{_NUM}", re.IGNORECASE
)


def extraer_lambdas_con_fuente(textos: list[str], marcador_consenso: str = "") -> tuple[float, float, str]:
    """
    Devuelve (lambda_local, lambda_visitante, fuente). Orden de prioridad:
      1. Patrón canónico "Goles esperados: X.X local / Y.Y visitante" (mediana entre agentes;
         la mediana resiste a un agente que alucine un valor extremo)        -> fuente "agentes"
      2. Marcador consenso "X-Y" con corrección de modo Poisson (λ ≈ goles + 0.35) -> "marcador"
      3. Valores por defecto (los agentes no respondieron)                    -> "por_defecto"
    """
    locales, visitas = [], []
    for texto in textos:
        for m in _RE_CANONICO.finditer(texto):
            try:
                l, v = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
            except ValueError:
                continue
            if 0 < l <= 6 and 0 < v <= 6:  # descarta valores absurdos
                locales.append(l)
                visitas.append(v)

    if locales:
        return _clamp(statistics.median(locales)), _clamp(statistics.median(visitas)), "agentes"

    m = re.search(r"(\d+)\s*[-–]\s*(\d+)", marcador_consenso or "")
    if m:
        gl, gv = int(m.group(1)), int(m.group(2))
        return _clamp(gl + 0.35 if gl else 0.75), _clamp(gv + 0.35 if gv else 0.55), "marcador"

    return (*DEFAULT_LAMBDAS, "por_defecto")


def extraer_lambdas_del_debate(textos: list[str], marcador_consenso: str = "") -> tuple[float, float]:
    """Igual que extraer_lambdas_con_fuente, sin la fuente (se conserva por compatibilidad)."""
    return extraer_lambdas_con_fuente(textos, marcador_consenso)[:2]


# ═════════════════════════════════════════════
#  BÚSQUEDA WEB (SerpApi) con caché
# ═════════════════════════════════════════════

_cache_busqueda: dict[str, str] = {}


async def ejecutar_busqueda(client: httpx.AsyncClient, query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "Consulta vacía."
    if query in _cache_busqueda:
        return _cache_busqueda[query]

    key = os.environ.get("SERPAPI_KEY")
    if not key:
        return "Búsqueda no disponible (falta SERPAPI_KEY)."
    try:
        r = await client.get(
            "https://serpapi.com/search",
            params={"q": query, "api_key": key, "hl": "es", "num": 8, "tbs": "qdr:m"},
            timeout=12.0,
        )
        r.raise_for_status()
        resultados = r.json().get("organic_results", [])[:5]
        if not resultados:
            return "No se encontraron resultados."
        out = "\n".join(
            f"- [{x.get('date', 's/f')}] {x.get('title', '')}: {x.get('snippet', '')}" for x in resultados
        )
        _cache_busqueda[query] = out
        return out
    except Exception as e:
        log.warning("Búsqueda fallida (%s): %s", query, e)
        return f"Error en búsqueda: {e}"


# ═════════════════════════════════════════════
#  CLIENTE LLM — retry con backoff + fallback
# ═════════════════════════════════════════════

async def _chat_once(
    client: httpx.AsyncClient,
    prov: Provider,
    messages: list[dict],
    *,
    tools: list | None,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    retries: int,
    timeout: float,
) -> dict:
    headers = {"Authorization": f"Bearer {prov.key}", "Content-Type": "application/json", **prov.extra_headers}
    payload: dict = {
        "model": prov.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last_err: Exception | None = None
    for intento in range(retries):
        try:
            async with prov.sem:
                r = await client.post(prov.url, headers=headers, json=payload, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                try:
                    espera = float(r.headers.get("Retry-After", 2 ** (intento + 1)))
                except ValueError:
                    espera = 2 ** (intento + 1)
                log.warning("%s HTTP %s, reintento en %.0fs", prov.name, r.status_code, espera)
                last_err = LLMError(f"HTTP {r.status_code}")
                await asyncio.sleep(min(espera, 30))
                continue
            if 400 <= r.status_code < 500:
                # Errores del cliente (clave inválida, modelo inexistente...): reintentar no sirve de nada
                motivo = {
                    401: "clave de API inválida o caducada",
                    403: "acceso denegado (permisos o región)",
                    404: f"modelo '{prov.model}' no encontrado",
                }.get(r.status_code, "petición rechazada")
                raise LLMError(f"{prov.name}: HTTP {r.status_code} — {motivo}")
            r.raise_for_status()
            return r.json()["choices"][0]
        except LLMError:
            raise
        except (httpx.HTTPError, KeyError, ValueError) as e:
            last_err = e
            await asyncio.sleep(2 ** intento)
    raise LLMError(f"{prov.name} falló tras {retries} intentos: {last_err}")


async def chat(
    client: httpx.AsyncClient,
    provider: str,
    messages: list[dict],
    *,
    tools: list | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    json_mode: bool = False,
    retries: int = 3,
    timeout: float = 30.0,
) -> dict:
    """Devuelve el 'choice' completo. Prueba el proveedor principal y luego los fallbacks."""
    errores = []
    for nombre in [provider, *FALLBACKS.get(provider, [])]:
        try:
            return await _chat_once(
                client, PROVIDERS[nombre], messages,
                tools=tools, temperature=temperature, max_tokens=max_tokens,
                json_mode=json_mode, retries=retries, timeout=timeout,
            )
        except LLMError as e:
            errores.append(str(e))
            log.warning("Proveedor %s descartado: %s", nombre, e)
    raise LLMError(" | ".join(errores))


async def llamar_llm(
    client: httpx.AsyncClient, provider: str, system: str, user: str, nombre: str, **kw
) -> str:
    """Llamada simple. Devuelve texto, o '' si todo falla (nunca un string de error mezclado)."""
    try:
        choice = await chat(
            client, provider,
            [{"role": "system", "content": system}, {"role": "user", "content": user}], **kw,
        )
        return (choice["message"].get("content") or "").strip()
    except LLMError as e:
        log.error("[%s] sin respuesta: %s", nombre, e)
        return ""


async def llamar_agente_con_busqueda(
    client: httpx.AsyncClient, provider: str, system: str, user: str, nombre: str,
    max_iteraciones: int = 3,
) -> tuple[str, list[dict]]:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    noticias: list[dict] = []

    try:
        for i in range(max_iteraciones):
            # En la última vuelta se quitan las tools para forzar el análisis final.
            ultima = i == max_iteraciones - 1
            choice = await chat(client, provider, messages, tools=None if ultima else [WEB_SEARCH_TOOL])
            msg = choice["message"]
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return (msg.get("content") or "").strip(), noticias

            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})

            queries = []
            for tc in tool_calls:
                try:
                    queries.append(json.loads(tc["function"]["arguments"] or "{}").get("query", ""))
                except (json.JSONDecodeError, KeyError):
                    queries.append("")
            log.info("[%s] buscando: %s", nombre, queries)

            resultados = await asyncio.gather(*(ejecutar_busqueda(client, q) for q in queries))
            for tc, q, res in zip(tool_calls, queries, resultados):
                noticias.append({"agente": nombre, "query": q, "resultados": res})
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": res})

        messages.append({"role": "user", "content": "Ahora da tu análisis final con todo lo anterior."})
        choice = await chat(client, provider, messages)
        return (choice["message"].get("content") or "").strip(), noticias
    except LLMError as e:
        log.error("[%s] agente falló: %s", nombre, e)
        return "", noticias


# ═════════════════════════════════════════════
#  PROMPTS
# ═════════════════════════════════════════════

CIERRE_LAMBDAS = (
    "\nOBLIGATORIO al final, en línea propia y con este formato exacto:\n"
    "Goles esperados: X.X local / Y.Y visitante\n"
    "(decimales reales según tus datos; no uses siempre 1.0 / 1.0)"
)

# Se formatean con {fecha} para que las búsquedas usen la temporada correcta.
PROMPT_ESTADISTICO = (
    "Eres un Analista Cuantitativo de Fútbol. Hoy es {fecha}.\n"
    "PRIMERO usa web_search (estadísticas recientes, goles de los últimos partidos, forma actual; "
    "incluye el año en la consulta). LUEGO analiza con esos datos y los del modelo ML si existen.\n"
    "No hables de táctica ni de apuestas. Directo y profesional. SIN EMOTICONS." + CIERRE_LAMBDAS
)
PROMPT_TACTICO = (
    "Eres un Analista Táctico de Fútbol (estilo Bielsa / Guardiola). Hoy es {fecha}.\n"
    "PRIMERO usa web_search (bajas, lesiones, alineación probable, formación). "
    "LUEGO analiza cómo afectan al desarrollo táctico.\n"
    "No hables de apuestas ni te centres solo en números. SIN EMOTICONS." + CIERRE_LAMBDAS
)
PROMPT_BETTING = (
    "Eres un Tipster Profesional de Apuestas Deportivas. Hoy es {fecha}.\n"
    "PRIMERO usa web_search (cuotas actuales, historial directo). "
    "LUEGO identifica dónde hay valor real comparando cuota con probabilidad estimada.\n"
    "Si recibes MERCADOS DEL MODELO, no te limites al 1X2: puedes recomendar goles, hándicap, ambos marcan, "
    "córners, tarjetas, faltas, tiros o fueras de juego. Usa SOLO las probabilidades y cuotas justas de ese bloque, "
    "compáralas con las cuotas reales que encuentres (hay valor si la cuota real supera la justa) y desconfía de "
    "los mercados con fiabilidad baja.\n"
    "Da un veredicto claro. Recuerda que ninguna apuesta es segura. SIN EMOTICONS." + CIERRE_LAMBDAS
)

PROMPT_REPLICA = {
    "estadistico": "Eres el Analista Estadístico. Señala EXACTAMENTE UN punto donde los números contradicen "
                   "o matizan lo que dijeron los otros. Si coinciden, admítelo en una frase. "
                   "Máximo 4 oraciones. No repitas tu análisis. SIN EMOTICONS.",
    "tactico": "Eres el Analista Táctico. Señala EXACTAMENTE UN punto donde la táctica real invalida o "
               "complica lo que dijeron los otros. Máximo 4 oraciones. SIN EMOTICONS.",
    "betting": "Eres el Tipster. Señala EXACTAMENTE UN punto donde su visión afecta el valor de tu apuesta "
               "y ajusta tu recomendación si hace falta. Máximo 4 oraciones. SIN EMOTICONS.",
}
PROMPT_FINAL = {
    "estadistico": "Eres el Analista Estadístico. Da tu VEREDICTO FINAL en 3 oraciones. Sé contundente. "
                   "SIN EMOTICONS." + CIERRE_LAMBDAS,
    "tactico": "Eres el Analista Táctico. Da tu VEREDICTO FINAL en 3 oraciones. Sé contundente. "
               "SIN EMOTICONS." + CIERRE_LAMBDAS,
    "betting": "Eres el Tipster. Da tu VEREDICTO FINAL en 3 oraciones con la apuesta más valiosa y tu "
               "confianza (Alta / Media / Baja). SIN EMOTICONS." + CIERRE_LAMBDAS,
}

PROMPT_SINTETIZADOR = """
Eres el Moderador de un panel de expertos en fútbol. Presenciaste un debate de 3 rondas entre un
Estadístico, un Táctico y un Tipster. Al inicio están las NOTICIAS REALES que buscó cada agente.

INSTRUCCIONES:
1. Lee las líneas "Goles esperados: X.X local / Y.Y visitante" de los veredictos de la Ronda 3.
2. Promedia esos valores -> goles_esperados_local y goles_esperados_visitante (números, no strings).
3. marcador_consenso = marcador más probable según esos promedios. NO uses "1-1" por defecto.
4. Usa SOLO noticias que aparezcan en el bloque de noticias; no inventes datos ni fechas.
5. "apuesta_recomendada" puede ser de cualquier mercado (goles, hándicap, ambos marcan, córners, tarjetas,
   faltas, tiros...). Si hay un bloque MERCADOS DEL MODELO, cita solo probabilidades y cuotas justas de ese bloque,
   indica el mercado con su línea y evita apoyarte en mercados de fiabilidad baja.

Responde ÚNICAMENTE con JSON válido, con estas claves:
{
  "analisis_estadistico": "posición final en 2 líneas",
  "analisis_tactico": "posición final en 2 líneas",
  "analisis_betting": "posición final en 2 líneas",
  "punto_de_conflicto": "el desacuerdo más importante",
  "noticias_clave": "2-3 noticias reales con fecha si la hay",
  "conclusion_comentarista": "conclusión unificada, máximo 4 líneas",
  "goles_esperados_local": 1.5,
  "goles_esperados_visitante": 1.0,
  "marcador_consenso": "X-Y",
  "apuesta_recomendada": "recomendación concreta",
  "confianza": "Alta / Media / Baja"
}
"""


# ═════════════════════════════════════════════
#  UTILIDADES
# ═════════════════════════════════════════════

def extraer_json(texto: str) -> dict:
    """Extrae el primer objeto JSON de un texto (con o sin ```json)."""
    texto = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", texto or "")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", texto, re.DOTALL)
    if m:
        texto = m.group(1)
    else:
        i, j = texto.find("{"), texto.rfind("}")
        if i == -1 or j <= i:
            raise ValueError("No hay JSON en la respuesta")
        texto = texto[i : j + 1]
    return json.loads(texto)


def _a_float(v) -> float | None:
    try:
        f = float(str(v).replace(",", "."))
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


_SEP_VS = re.compile(r"\s+(?:vs\.?|v|contra)\s+|\s+-\s+", re.IGNORECASE)


def nombres_equipos(texto: str) -> tuple[str, str]:
    partes = _SEP_VS.split(texto.strip(), maxsplit=1)
    if len(partes) == 2:
        return partes[0].strip().title()[:30], partes[1].strip().title()[:30]
    return "Local", "Visitante"


def _pct(p) -> str:
    return f"{p * 100:.0f}%"


def resumen_mercados(mercados: dict | None) -> str:
    """Texto compacto con los mercados del modelo (probabilidad, cuota justa y fiabilidad) para los agentes."""
    if not mercados or not mercados.get("grupos"):
        return ""
    lineas = [
        "MERCADOS DEL MODELO (cuota justa = 1/probabilidad, sin margen de la casa; la fiabilidad indica cuánto "
        "supera el modelo a la frecuencia histórica en partidos que no vio al entrenar):"
    ]
    for g in mercados["grupos"]:
        if g.get("tipo") == "lineas":
            esp = g.get("esperado", {})
            partes = []
            for se in g.get("series", []):
                if se["ambito"] != "total" or not se.get("lineas"):
                    continue
                for fl in se["lineas"][:: 2 if len(se["lineas"]) > 3 else 1]:  # no todas las líneas: ahorra tokens
                    partes.append(f"+{fl['linea']}: {_pct(fl['over'])} (@{fl['cuota_over']})")
            lineas.append(
                f"- {g['titulo']} [fiabilidad {g['fiabilidad']}] esperado {esp.get('local')}-{esp.get('visitante')} "
                f"(total {esp.get('total')}); " + " | ".join(partes)
            )
        elif g.get("id") == "exactos":
            continue
        else:
            partes = [f"{it['nombre']}: {_pct(it['prob'])} (@{it['cuota']})" for it in g.get("items", [])[:6] if it.get("cuota")]
            lineas.append(f"- {g['titulo']} [fiabilidad {g['fiabilidad']}]: " + " | ".join(partes))
    if mercados.get("destacados"):
        lineas.append("Destacados del modelo (probables y con cuota que aún paga): " + " | ".join(
            f"{d['nombre']} {_pct(d['prob'])} (@{d['cuota']}, fiab. {d['fiabilidad']})" for d in mercados["destacados"]))
    return "\n".join(lineas)


def construir_contexto(datos: dict, texto_usuario: str, es_libre: bool) -> str:
    if es_libre:
        return (
            f'El usuario solicita analizar: "{texto_usuario}".\n'
            "Usa la búsqueda web y tu conocimiento de selecciones y torneos para dar tu veredicto." + CIERRE_LAMBDAS
        )
    local, visita = datos.get("equipo_local", ""), datos.get("equipo_visitante", "")
    gl = datos.get("goles_esperados", {}).get("local", 0)
    gv = datos.get("goles_esperados", {}).get("visitante", 0)
    lineas = [
        f"Partido: {local} vs {visita}",
        f"Probabilidades ML: Local {datos.get('prob_local', 0) * 100:.1f}% | "
        f"Empate {datos.get('prob_empate', 0) * 100:.1f}% | "
        f"Visitante {datos.get('prob_visitante', 0) * 100:.1f}%",
        f"Goles esperados (modelo de fuerza de equipo con xG): Local {gl} | Visitante {gv}",
        f"Marcador probable: {datos.get('marcador_probable', 'N/A')}",
    ]
    if datos.get("top_marcadores"):
        lineas.append("Marcadores más probables: " + ", ".join(
            f"{m['marcador']} ({m['prob'] * 100:.0f}%)" for m in datos["top_marcadores"]))
    if datos.get("over_2_5") is not None:
        lineas.append(f"Más de 2.5 goles: {datos['over_2_5'] * 100:.1f}%")
    lineas.append(f"Ambos marcan: {datos.get('ambos_marcan', 0) * 100:.1f}%")
    if datos.get("elo_local") is not None and datos.get("elo_visitante") is not None:
        lineas.append(f"Rating Elo: {local} {datos['elo_local']} | {visita} {datos['elo_visitante']} (media de la liga ~1500)")
    if datos.get("datos_hasta"):
        lineas.append(f"Los datos del modelo llegan hasta el {datos['datos_hasta']}; contrasta con noticias más recientes.")
    bloque = resumen_mercados(datos.get("mercados"))
    if bloque:
        lineas.append(bloque)
    return "\n".join(lineas) + "\n\nUsa la búsqueda web para complementar estos datos." + CIERRE_LAMBDAS


# ═════════════════════════════════════════════
#  ORQUESTADOR
# ═════════════════════════════════════════════

async def debatir_prediccion(datos_partido: dict, texto_usuario: str) -> dict:
    es_libre = "error" in datos_partido or not datos_partido.get("equipo_local")
    contexto = construir_contexto(datos_partido, texto_usuario, es_libre)
    fecha = date.today().strftime("%d/%m/%Y")

    async with httpx.AsyncClient() as client:
        # ── Ronda 1: análisis con búsqueda ───────────────────────────────
        log.info("Ronda 1: análisis con búsqueda web")
        (r1_est, n_est), (r1_tac, n_tac), (r1_bet, n_bet) = await asyncio.gather(
            llamar_agente_con_busqueda(client, "groq", PROMPT_ESTADISTICO.format(fecha=fecha), contexto, "Estadístico-R1"),
            llamar_agente_con_busqueda(client, "openrouter", PROMPT_TACTICO.format(fecha=fecha), contexto, "Táctico-R1"),
            llamar_agente_con_busqueda(client, "groq", PROMPT_BETTING.format(fecha=fecha), contexto, "Betting-R1"),
        )
        noticias = n_est + n_tac + n_bet
        bloque_noticias = "\n\n".join(
            f"[{n['agente']}] QUERY: {n['query']}\n{n['resultados']}" for n in noticias
        )

        # ── Ronda 2: réplicas cruzadas ───────────────────────────────────
        log.info("Ronda 2: réplicas cruzadas")
        r2_est, r2_tac, r2_bet = await asyncio.gather(
            llamar_llm(client, "openrouter", PROMPT_REPLICA["estadistico"],
                       f"TÁCTICO:\n{r1_tac}\n\nTIPSTER:\n{r1_bet}", "Estadístico-R2", max_tokens=500),
            llamar_llm(client, "groq", PROMPT_REPLICA["tactico"],
                       f"ESTADÍSTICO:\n{r1_est}\n\nTIPSTER:\n{r1_bet}", "Táctico-R2", max_tokens=500),
            llamar_llm(client, "openrouter", PROMPT_REPLICA["betting"],
                       f"ESTADÍSTICO:\n{r1_est}\n\nTÁCTICO:\n{r1_tac}", "Betting-R2", max_tokens=500),
        )

        # ── Ronda 3: veredictos finales ──────────────────────────────────
        log.info("Ronda 3: veredictos finales")

        def _ctx_final(propio_r1, propio_r2, otros: dict[str, str]) -> str:
            ot = "\n\n".join(f"Réplica del {k}:\n{v}" for k, v in otros.items())
            return f"Tu análisis inicial:\n{propio_r1}\n\nTu réplica:\n{propio_r2}\n\n{ot}\n\nDa tu veredicto final."

        r3_est, r3_tac, r3_bet = await asyncio.gather(
            llamar_llm(client, "groq", PROMPT_FINAL["estadistico"],
                       _ctx_final(r1_est, r2_est, {"táctico": r2_tac, "tipster": r2_bet}), "Estadístico-R3", max_tokens=600),
            llamar_llm(client, "openrouter", PROMPT_FINAL["tactico"],
                       _ctx_final(r1_tac, r2_tac, {"estadístico": r2_est, "tipster": r2_bet}), "Táctico-R3", max_tokens=600),
            llamar_llm(client, "groq", PROMPT_FINAL["betting"],
                       _ctx_final(r1_bet, r2_bet, {"estadístico": r2_est, "táctico": r2_tac}), "Betting-R3", max_tokens=600),
        )

        # ── Moderador ────────────────────────────────────────────────────
        log.info("Moderador: sintetizando")
        bloque_mercados = "" if es_libre else resumen_mercados(datos_partido.get("mercados"))
        mensaje_mod = (
            f"=== NOTICIAS REALES (SerpApi) ===\n{bloque_noticias or 'Sin noticias.'}\n\n"
            + (f"=== MODELO ===\n{bloque_mercados}\n\n" if bloque_mercados else "")
            + f"=== DEBATE {'(MODO LIBRE)' if es_libre else '(MODO MODELO ML)'} ===\n\n"
            f"--- RONDA 1 ---\nESTADÍSTICO: {r1_est}\nTÁCTICO: {r1_tac}\nTIPSTER: {r1_bet}\n\n"
            f"--- RONDA 2 ---\nESTADÍSTICO: {r2_est}\nTÁCTICO: {r2_tac}\nTIPSTER: {r2_bet}\n\n"
            f"--- RONDA 3 (FINALES) ---\nESTADÍSTICO: {r3_est}\nTÁCTICO: {r3_tac}\nTIPSTER: {r3_bet}\n"
        )
        raw = await llamar_llm(
            client, "gemini", PROMPT_SINTETIZADOR, mensaje_mod, "Moderador",
            max_tokens=3000, temperature=0.2, json_mode=True, timeout=45.0,
        )

    # ── Parseo del JSON del moderador (con fallback) ─────────────────────
    try:
        debate = extraer_json(raw)
        if isinstance(debate.get("noticias_clave"), list):
            debate["noticias_clave"] = " | ".join(map(str, debate["noticias_clave"]))
    except Exception as e:
        log.error("JSON del moderador inválido: %s", e)
        sin_resp = "Este agente no pudo responder (revisa las claves de API en el servidor)."
        debate = {
            "analisis_estadistico": r3_est or r1_est or sin_resp,
            "analisis_tactico": r3_tac or r1_tac or sin_resp,
            "analisis_betting": r3_bet or r1_bet or sin_resp,
            "punto_de_conflicto": "No determinado (el moderador no devolvió JSON válido).",
            "noticias_clave": bloque_noticias[:600] or "Sin noticias.",
            "conclusion_comentarista": (
                "El debate se completó, pero no se pudo generar la síntesis."
                if any((r1_est, r1_tac, r1_bet))
                else "Los agentes de IA no respondieron, así que no hay análisis cualitativo."
            ),
            "goles_esperados_local": None,
            "goles_esperados_visitante": None,
            "marcador_consenso": "",
            "apuesta_recomendada": "N/A",
            "confianza": "Baja",
        }

    # ── Cálculo matemático final ─────────────────────────────────────────
    if es_libre:
        lam_l = _a_float(debate.get("goles_esperados_local"))
        lam_v = _a_float(debate.get("goles_esperados_visitante"))
        if lam_l and lam_v and lam_l <= 6 and lam_v <= 6:
            lam_l, lam_v, fuente = _clamp(lam_l), _clamp(lam_v), "moderador"
        else:
            lam_l, lam_v, fuente = extraer_lambdas_con_fuente(
                [r1_est, r1_tac, r1_bet, r2_est, r2_tac, r2_bet, r3_est, r3_tac, r3_bet],
                debate.get("marcador_consenso", ""),
            )

        dist = calcular_distribucion_poisson(lam_l, lam_v)
        nom_l, nom_v = nombres_equipos(texto_usuario)
        datos_mat = {
            "equipo_local": nom_l,
            "equipo_visitante": nom_v,
            "prob_local": dist["prob_local"],
            "prob_empate": dist["prob_empate"],
            "prob_visitante": dist["prob_visitante"],
            "goles_esperados": {"local": lam_l, "visitante": lam_v, "total": round(lam_l + lam_v, 2)},
            "marcador_probable": dist["marcador_probable"],
            "top_marcadores": dist["top_marcadores"],
            "recomendacion": debate.get("apuesta_recomendada", "N/A"),
            "ambos_marcan": dist["prob_btts"],
            "over_2_5": dist["prob_over_2_5"],
            # En modo libre NO hay modelo entrenado, así que no se inventan métricas de precisión
            # (antes se enviaban 0.54 y 0.49 fijos). El frontend las oculta cuando son null.
            "accuracy_modelo": None,
            "f1_modelo": None,
            # De dónde salen los goles esperados: "moderador" / "agentes" / "marcador" / "por_defecto".
            # Con "por_defecto" los agentes no respondieron y las cifras NO son una predicción.
            "fuente_goles": fuente,
            "estimacion_fiable": fuente != "por_defecto",
        }
        debate["marcador_consenso"] = dist["marcador_probable"]
        debate["goles_esperados_local"] = lam_l
        debate["goles_esperados_visitante"] = lam_v
    else:
        datos_mat = datos_partido

    debate["_rondas"] = {
        "r1": {"estadistico": r1_est, "tactico": r1_tac, "betting": r1_bet},
        "r2": {"estadistico": r2_est, "tactico": r2_tac, "betting": r2_bet},
        "r3": {"estadistico": r3_est, "tactico": r3_tac, "betting": r3_bet},
    }
    debate["_noticias_serpapi"] = noticias
    # Cuántas de las 9 respuestas de agentes llegaron (0 = problema de claves/proveedores)
    debate["_agentes_ok"] = sum(bool(t) for t in (r1_est, r1_tac, r1_bet, r2_est, r2_tac, r2_bet, r3_est, r3_tac, r3_bet))

    return {
        "debate_agentes": debate,
        "datos_matematicos": datos_mat,
        "modo": "libre" if es_libre else "matematico",
    }


async def conversar_general(mensaje: str) -> str:
    sistema = (
        "Eres Cristiano, un asistente virtual experto en fútbol, amigable y conversacional. "
        "Saluda y comenta que estás listo para analizar cualquier enfrentamiento de La Liga, "
        "Champions League o Mundiales. SIN EMOTICONS."
    )
    async with httpx.AsyncClient() as client:
        r = await llamar_llm(client, "groq", sistema, mensaje, "Asistente General")
    return r or "Ahora mismo no puedo responder, inténtalo de nuevo en un momento."


# ═════════════════════════════════════════════
#  ENTRYPOINTS
# ═════════════════════════════════════════════

def _run(coro):
    """asyncio.run, pero seguro si ya hay un event loop activo (Jupyter, etc.)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()


def preguntar_ia_multiagente(datos_partido: dict, texto_usuario: str) -> dict:
    return _run(debatir_prediccion(datos_partido, texto_usuario))


def preguntar_ia(mensaje: str) -> str:
    return _run(conversar_general(mensaje))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = preguntar_ia_multiagente({}, "Real Madrid vs Barcelona")
    print(json.dumps(res["datos_matematicos"], indent=2, ensure_ascii=False))