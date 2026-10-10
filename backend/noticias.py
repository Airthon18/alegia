"""
Noticias reales de fútbol desde feeds RSS públicos (sin claves de API).

Cada noticia enlaza al artículo original; el servidor solo guarda en caché unos minutos
el título, el resumen y la imagen que el propio medio publica en su RSS.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

log = logging.getLogger("prediliga.noticias")

# Feeds (nombre del medio, URL). Si uno falla se ignora y se usan los demás.
FEEDS: list[tuple[str, str]] = [
    ("Marca", "https://e00-marca.uecdn.es/rss/futbol/primera-division.xml"),
    ("Marca", "https://e00-marca.uecdn.es/rss/futbol/real-madrid.xml"),
    ("Marca", "https://e00-marca.uecdn.es/rss/futbol/barcelona.xml"),
    ("Marca", "https://e00-marca.uecdn.es/rss/futbol/atletico.xml"),
]

CACHE_SEGUNDOS = 10 * 60
MAX_NOTICIAS = 30
MAX_BYTES_FEED = 3_000_000
RESUMEN_MAX = 260

_NS = {
    "media": "http://search.yahoo.com/mrss/",
    "dc": "http://purl.org/dc/elements/1.1/",
}
_TAGS = re.compile(r"<[^>]+>")
_ESPACIOS = re.compile(r"\s+")

_cache: dict = {"t": 0.0, "datos": None}
_lock = asyncio.Lock()


def _texto(s: str | None) -> str:
    """Quita etiquetas HTML y entidades: '<b>Hola</b> &amp; adiós' -> 'Hola & adiós'."""
    if not s:
        return ""
    return _ESPACIOS.sub(" ", html.unescape(_TAGS.sub(" ", s))).strip()


def _recortar(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def _url_http(u: str | None) -> str | None:
    """Solo se aceptan enlaces http(s): nada de javascript: ni data:."""
    u = (u or "").strip()
    return u if u.lower().startswith(("http://", "https://")) else None


def parsear_rss(xml_texto: str | bytes, fuente: str) -> list[dict]:
    """Convierte un RSS 2.0 en una lista de noticias. Lanza ET.ParseError si el XML es inválido."""
    raiz = ET.fromstring(xml_texto)
    salida: list[dict] = []
    for it in raiz.iterfind(".//item"):
        titulo = _texto(it.findtext("title"))
        enlace = _url_http(it.findtext("link") or it.findtext("guid"))
        if not titulo or not enlace:
            continue

        resumen = _texto(it.findtext("media:description", namespaces=_NS)) or _texto(it.findtext("description"))
        # la descripción de algunos medios acaba en "Leer" / "Leer más"
        resumen = re.sub(r"\s*Leer( más)?\.?$", "", resumen).strip()

        imagen = None
        for tag in ("media:content", "media:thumbnail"):
            el = it.find(tag, _NS)
            if el is not None:
                imagen = _url_http(el.get("url"))
                if imagen:
                    break
        if not imagen:
            enc = it.find("enclosure")
            if enc is not None and (enc.get("type") or "").startswith("image"):
                imagen = _url_http(enc.get("url"))

        fecha = None
        pub = it.findtext("pubDate")
        if pub:
            try:
                d = parsedate_to_datetime(pub)
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                fecha = d.astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                fecha = None

        cats = []
        for c in it.iterfind("category"):
            t = _texto(c.text)
            if t and t not in cats:
                cats.append(t)

        salida.append(
            {
                "titulo": titulo,
                "resumen": _recortar(resumen, RESUMEN_MAX),
                "enlace": enlace,
                "imagen": imagen,
                "fecha": fecha,
                "fuente": fuente,
                "categorias": cats[:6],
            }
        )
    return salida


def combinar(listas: list[list[dict]]) -> list[dict]:
    """Une varias listas, quita duplicados (mismo enlace) y ordena de más reciente a más antigua."""
    vistos: set[str] = set()
    todas: list[dict] = []
    for lista in listas:
        for n in lista:
            if n["enlace"] in vistos:
                continue
            vistos.add(n["enlace"])
            todas.append(n)
    todas.sort(key=lambda n: n["fecha"] or "", reverse=True)
    return todas[:MAX_NOTICIAS]


async def _descargar(cliente: httpx.AsyncClient, fuente: str, url: str) -> list[dict]:
    try:
        r = await cliente.get(url)
        r.raise_for_status()
        if len(r.content) > MAX_BYTES_FEED:
            raise ValueError("feed demasiado grande")
        return parsear_rss(r.content, fuente)
    except Exception as e:  # un feed caído no debe tumbar el resto
        log.warning("Feed %s no disponible: %s", url, e)
        return []


async def obtener_noticias() -> dict:
    """Noticias en caché (10 min). Si todos los feeds fallan, devuelve la última copia buena."""
    async with _lock:
        ahora = time.time()
        if _cache["datos"] and ahora - _cache["t"] < CACHE_SEGUNDOS:
            return _cache["datos"]

        async with httpx.AsyncClient(
            timeout=8.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; alegIA/1.0; +https://alegia.site)"},
        ) as cliente:
            listas = await asyncio.gather(*[_descargar(cliente, f, u) for f, u in FEEDS])

        noticias = combinar(list(listas))
        if noticias:
            datos = {
                "noticias": noticias,
                "fuentes": sorted({n["fuente"] for n in noticias}),
                "actualizado": datetime.now(timezone.utc).isoformat(),
            }
            _cache["datos"], _cache["t"] = datos, ahora
            return datos
        if _cache["datos"]:  # sin red: se sirve la copia vieja antes que nada
            return {**_cache["datos"], "desactualizado": True}
        return {"noticias": [], "fuentes": [], "actualizado": None}