"""
Reloj de referencia en UTC.

Los partidos de FootyStats traen su inicio como instante UTC (date_unix, en segundos). Para saber si un partido ya empezó
hay que comparar ese instante con "ahora" en UTC. Si el reloj del equipo está mal (por ejemplo, mostrando la hora UTC como si
fuera la hora local de Lima, 5 h adelantado), TODA comparación falla aunque los timestamps del proveedor sean correctos:
un partido de dentro de 4 h aparece como "ya empezó".

Por eso "ahora" se calcula con una referencia externa: la cabecera HTTP Date (UTC) que devuelve FootyStats en cada respuesta,
y que se compara con el reloj local en el mismo instante. No se corrige ningún partido: solo se mide cuánto se equivoca el reloj
del equipo y se usa la hora de referencia para comparar instantes. America/Lima se usa únicamente para mostrar fechas y horas.
"""
from __future__ import annotations

import email.utils
import logging
import time

import httpx

log = logging.getLogger("prediliga.reloj")

TOLERANCIA_S = 120  # diferencias menores se consideran ruido de red y no se corrigen
URL_REFERENCIA = "https://api.football-data-api.com/"  # solo se usa su cabecera Date; no consume cuota
VIGENCIA_MEDICION_S = 300

_medicion: dict = {"desfase_s": None, "tomada_local": 0.0, "fuente": None}


def hora_local() -> float:
    """Reloj del sistema (puede estar mal). Se aísla para poder sustituirlo en las pruebas."""
    return time.time()


def registrar(cabecera_date: str | None, local_antes: float, local_despues: float, fuente: str = "FootyStats") -> bool:
    """Guarda cuánto se adelanta (+) o atrasa (-) el reloj local respecto a la cabecera Date de una respuesta."""
    if not cabecera_date:
        return False
    try:
        referencia = email.utils.parsedate_to_datetime(cabecera_date).timestamp()
    except (TypeError, ValueError):
        return False
    medio = (local_antes + local_despues) / 2
    # Date tiene resolución de 1 s: se compensa media unidad para no sesgar la medición
    _medicion.update({"desfase_s": medio - (referencia + 0.5), "tomada_local": medio, "fuente": fuente})
    return True


def desfase_s() -> float:
    """Segundos que el reloj local va adelantado (+) o atrasado (-). 0 si todavía no hay referencia."""
    return _medicion["desfase_s"] or 0.0


def hay_referencia() -> bool:
    return _medicion["desfase_s"] is not None


def ahora() -> float:
    """Instante actual en UTC (segundos). Corregido con la referencia cuando el reloj local se desvía más que la tolerancia."""
    local = hora_local()
    d = desfase_s()
    return local - d if abs(d) > TOLERANCIA_S else local


def info() -> dict:
    local = hora_local()
    d = desfase_s()
    return {
        "local_unix": int(local),
        "referencia_unix": int(local - d) if hay_referencia() else None,
        "desfase_s": round(d, 1) if hay_referencia() else None,
        "corregido": hay_referencia() and abs(d) > TOLERANCIA_S,
        "fuente": _medicion["fuente"],
    }


def texto_desfase(d: float) -> str:
    h, m = divmod(round(abs(d) / 60), 60)  # a minutos enteros: 17999,5 s se muestra como 5 h 0 min
    cuerpo = f"{h} h {m} min" if h else f"{m} min"
    return f"{cuerpo} {'adelantado' if d > 0 else 'atrasado'}"


async def asegurar() -> None:
    """Mide el reloj si no hay medición reciente (una petición HEAD sin clave, no cuenta contra la cuota de la API)."""
    if hay_referencia() and hora_local() - _medicion["tomada_local"] < VIGENCIA_MEDICION_S:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            antes = hora_local()
            r = await c.head(URL_REFERENCIA)
            registrar(r.headers.get("date"), antes, hora_local(), "FootyStats (HEAD)")
    except httpx.HTTPError as e:
        log.warning("No se pudo medir el reloj de referencia (%s)", type(e).__name__)


def aviso() -> str | None:
    """Texto para mostrar cuando el reloj local está desviado; None si está en hora."""
    d = desfase_s()
    if not hay_referencia() or abs(d) <= TOLERANCIA_S:
        return None
    return (
        f"El reloj de este equipo está {texto_desfase(d)} respecto a UTC. Para saber si un partido ya empezó se usa la hora de "
        f"referencia de FootyStats (UTC); conviene corregir la hora del sistema."
    )
