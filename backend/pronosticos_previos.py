"""
Pronósticos guardados ANTES del inicio de cada partido.

Cuando alguien pide una predicción de un partido que todavía no empezó, se guarda tal como se mostró. Así, cuando el
partido pasa a "En vivo" o termina, se puede abrir ese pronóstico previo sin recalcularlo ni mezclarlo con lo que
ya ocurrió. Una vez pasada la hora de inicio no se sobrescribe.
"""
from __future__ import annotations

import json
import threading
import time

import footystats
import reloj

_lock = threading.Lock()
_ARCHIVO = "pronosticos_previos.json"


def _ruta():
    return footystats.CACHE_DIR / _ARCHIVO


def _leer() -> dict:
    try:
        return json.loads(_ruta().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar(match_id: int, season_id: int, inicio_unix: int, respuesta: dict, detalle: bool, ahora: float | None = None) -> bool:
    """Guarda la respuesta si el partido aún no empezó. Devuelve True si se guardó."""
    ahora = reloj.ahora() if ahora is None else ahora  # instante UTC de referencia, no el reloj del sistema
    if inicio_unix <= ahora:
        return False
    with _lock:
        datos = _leer()
        previo = datos.get(str(match_id), {})
        previo.update({"match_id": match_id, "season_id": season_id, "inicio_unix": inicio_unix, "guardado_unix": int(ahora)})
        previo["detalle" if detalle else "resumen"] = respuesta
        datos[str(match_id)] = previo
        footystats.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _ruta().with_suffix(".tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(_ruta())
    return True


def obtener(match_id: int) -> dict | None:
    return _leer().get(str(match_id))


def ids_guardados() -> set[int]:
    return {int(k) for k in _leer()}
