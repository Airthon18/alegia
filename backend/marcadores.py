"""
Memoria del último marcador CONFIRMADO de cada partido y de cuándo se vio cada estado.

Una respuesta posterior que venga vacía, sin confirmar (0-0 por defecto) o más antigua que lo ya guardado no borra un marcador
confirmado: se conserva y se marca como "desactualizado", con la hora en que se recibió. Así se distinguen tres horas:
  * hora de consulta: cuándo se le preguntó a FootyStats (consultado_unix),
  * hora del dato: cuándo se recibió ese marcador o ese estado (recibido_unix / estado_desde_unix),
  * hora de inicio del partido (inicio_unix).
"""
from __future__ import annotations

import json
import threading

import footystats

CON_DISCO = True  # las pruebas lo desactivan
RETENCION_S = 3 * 24 * 3600
_ARCHIVO = "marcadores_confirmados.json"
_lock = threading.Lock()
_mem: dict[int, dict] | None = None


def _ruta():
    return footystats.CACHE_DIR / _ARCHIVO


def _cargar() -> dict[int, dict]:
    global _mem
    if _mem is None:
        _mem = {}
        if CON_DISCO:
            try:
                _mem = {int(k): v for k, v in json.loads(_ruta().read_text(encoding="utf-8")).items()}
            except (OSError, ValueError):
                _mem = {}
    return _mem


def reiniciar() -> None:
    global _mem
    _mem = {}


def _guardar(ahora: float) -> None:
    if not CON_DISCO:
        return
    datos = {str(k): v for k, v in (_mem or {}).items() if ahora - v.get("inicio_unix", ahora) < RETENCION_S}
    footystats.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _ruta().with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(_ruta())


def conciliar(match_id: int, estado: str, actual: dict, consultado: float, inicio_unix: int, ahora: float) -> dict:
    """
    actual: salida de estados_partido.evaluar_marcador. consultado: cuándo se recibió esta respuesta.
    Devuelve {marcador, estado_desde_unix} donde marcador = {estado, local, visitante, recibido_unix, desactualizado}.
    """
    with _lock:
        mem = _cargar()
        prev = mem.get(match_id) or {}
        cambio = False
        marcador_prev = prev.get("marcador")
        estado_desde = prev.get("estado_desde_unix", consultado)
        if prev.get("estado") != estado:
            estado_desde, cambio = consultado, True

        if actual["estado"] == "confirmado":
            if marcador_prev and consultado < marcador_prev.get("consulta_unix", 0):
                resultado = {**marcador_prev, "desactualizado": True}  # respuesta más antigua que la ya guardada: se ignora
            else:
                igual = marcador_prev and (marcador_prev["local"], marcador_prev["visitante"]) == (actual["local"], actual["visitante"])
                recibido = marcador_prev["recibido_unix"] if igual else consultado
                nuevo = {"estado": "confirmado", "local": actual["local"], "visitante": actual["visitante"], "recibido_unix": int(recibido), "consulta_unix": int(consultado)}
                cambio = cambio or not igual or nuevo["consulta_unix"] != marcador_prev.get("consulta_unix")
                marcador_prev = nuevo
                resultado = {**nuevo, "desactualizado": False}
        elif marcador_prev and marcador_prev.get("estado") == "confirmado":
            # Respuesta vacía o sin confirmar: se conserva el último marcador confirmado y se avisa de su antigüedad
            resultado = {**marcador_prev, "desactualizado": True}
        else:
            resultado = {**actual, "recibido_unix": int(consultado), "desactualizado": False}

        mem[match_id] = {"estado": estado, "estado_desde_unix": int(estado_desde), "marcador": marcador_prev, "inicio_unix": inicio_unix}
        if cambio:
            _guardar(ahora)
        resultado.pop("consulta_unix", None)
        return {"marcador": resultado, "estado_desde_unix": int(estado_desde)}
