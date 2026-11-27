"""
Estado real de un partido y confiabilidad de su marcador, a partir de lo que entrega FootyStats.

FootyStats documenta cuatro estados de partido: complete, suspended, canceled e incomplete. "incomplete" significa
"el resultado final todavía no está confirmado": sirve igual para un partido que no empezó y para uno que sí empezó.
En el plan de esta cuenta, un partido en curso sigue siendo "incomplete" con goles 0-0, sin minuto, sin estado en vivo y sin
hora de actualización (comprobado con respuestas reales del calendario y del detalle).

Reglas (no se deduce NUNCA un estado en vivo, un descanso ni un minuto a partir del tiempo transcurrido):
  * "En vivo" (en_juego, descanso, prorroga, penales): solo con un estado explícito del proveedor, y vigente (recibido hace poco).
  * "incomplete" con inicio en el futuro (instante UTC): "Próximo".
  * "incomplete" con el inicio ya pasado: "Estado por confirmar" (visible, pero sin afirmar que se está jugando).
  * El minuto solo se muestra si el proveedor lo envía en un campo explícito.
  * Un marcador solo es "confirmado" si el partido terminó (complete) o si el proveedor declara el estado en vivo. Los goles
    0-0 de un "incomplete" son el valor por defecto del proveedor, no un resultado.

Las funciones son puras: "ahora" es un parámetro (un instante UTC, ver reloj.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Perú no tiene horario de verano: UTC-5 todo el año. Solo sirve para MOSTRAR fechas y horas y para agrupar por día de Perú.
LIMA = timezone(timedelta(hours=-5))

VIGENCIA_EN_VIVO_S = 5 * 60  # un estado en vivo recibido hace más de 5 min deja de ser evidencia vigente
VENTANA_POR_CONFIRMAR_S = 36 * 3600  # pasado este tiempo sin confirmación se considera dato abandonado y se oculta

_COMPLETO = {"complete", "completed", "finished", "ft", "full_time"}
_SUSPENDIDO = {"suspended", "abandoned", "interrupted"}
_CANCELADO = {"canceled", "cancelled"}
_APLAZADO = {"postponed"}
_DESCANSO = {"halftime", "half_time", "ht", "break"}
_PRORROGA = {"extra_time", "extratime", "et", "aet"}
_PENALES = {"penalties", "penalty", "penalty_shootout", "pen", "pens"}
_EN_JUEGO = {"inprogress", "in_progress", "live", "playing", "1h", "2h"}
EN_VIVO = ("en_juego", "descanso", "prorroga", "penales")

ETIQUETAS = {
    "proximo": "Próximo",
    "en_juego": "En vivo",
    "descanso": "Descanso",
    "prorroga": "Prórroga",
    "penales": "Penales",
    "por_confirmar": "Estado por confirmar",
    "finalizado": "Finalizado",
    "suspendido": "Suspendido",
    "cancelado": "Cancelado",
    "aplazado": "Aplazado",
}


def _norm(status) -> str:
    return str(status or "").strip().lower().replace("-", "_").replace(" ", "_")


def clasificar(status, inicio_unix, ahora: float, minuto=None, edad_dato_s: float = 0.0) -> dict:
    """
    Devuelve {estado, etiqueta, minuto, nota}.
    `minuto` es el que envió el proveedor (o None); no se calcula. `edad_dato_s` es cuántos segundos hace que se recibió el dato.
    """
    s = _norm(status)

    def res(estado, minuto_=None, nota=None):
        return {"estado": estado, "etiqueta": ETIQUETAS[estado], "minuto": minuto_, "nota": nota}

    # Estados que el proveedor identifica de forma explícita
    if s in _COMPLETO:
        return res("finalizado")
    if s in _CANCELADO:
        return res("cancelado")
    if s in _SUSPENDIDO:
        return res("suspendido")
    if s in _APLAZADO:
        return res("aplazado")
    explicito = None
    if s in _DESCANSO:
        explicito = "descanso"
    elif s in _PRORROGA:
        explicito = "prorroga"
    elif s in _PENALES:
        explicito = "penales"
    elif s in _EN_JUEGO:
        explicito = "en_juego"
    if explicito:
        if edad_dato_s > VIGENCIA_EN_VIVO_S:  # evidencia vencida: ya no se puede afirmar que sigue en juego
            return res("por_confirmar", nota=f"El último estado «{ETIQUETAS[explicito]}» se recibió hace {int(edad_dato_s // 60)} min y ya no está vigente")
        return res(explicito, minuto_=minuto if isinstance(minuto, int) else None)

    # "incomplete" (o desconocido): solo importa si el instante de inicio (UTC) ya pasó
    if not isinstance(inicio_unix, (int, float)):
        return res("por_confirmar", nota="El proveedor no trae la hora de inicio")
    if inicio_unix > ahora:
        return res("proximo")
    return res("por_confirmar", nota="Ya pasó la hora de inicio programada y FootyStats todavía no confirma el estado del partido")


def evaluar_marcador(estado: str, goles_local, goles_visitante) -> dict:
    """
    {"estado": confirmado | provisional | no_disponible, "local", "visitante"}.
    confirmado: el partido terminó o el proveedor declara que está en vivo.
    provisional: hay goles, pero el proveedor no confirma el estado del partido.
    no_disponible: sin goles confirmados; el 0-0 de un partido "incomplete" es el valor por defecto, no un resultado.
    """
    l, v = goles_local, goles_visitante
    tiene = isinstance(l, (int, float)) and isinstance(v, (int, float))
    if estado == "proximo":
        return {"estado": "no_disponible", "local": None, "visitante": None}
    if estado == "finalizado" and tiene:
        return {"estado": "confirmado", "local": int(l), "visitante": int(v)}
    if estado in EN_VIVO and tiene:
        return {"estado": "confirmado", "local": int(l), "visitante": int(v)}
    if tiene and (l > 0 or v > 0):
        return {"estado": "provisional", "local": int(l), "visitante": int(v)}
    return {"estado": "no_disponible", "local": None, "visitante": None}


def fechas_lima(ahora: float) -> list[str]:
    """
    Días (hora de Perú) que hay que consultar para no perder ningún partido en curso o por confirmar:
    ayer y hoy, y mañana cuando faltan menos de 2 h para la medianoche. Se consulta siempre con timezone=America/Lima:
    un partido del jueves 22:00 en Perú es viernes 03:00 en UTC y, sin zona, FootyStats lo devolvería bajo la fecha del viernes.
    """
    lima = datetime.fromtimestamp(ahora, LIMA)
    dias = [lima.date() - timedelta(days=1), lima.date()]
    if lima.hour >= 22:
        dias.append(lima.date() + timedelta(days=1))
    return [d.isoformat() for d in dias]


def dia_lima(unix: float) -> str:
    return datetime.fromtimestamp(unix, LIMA).date().isoformat()
