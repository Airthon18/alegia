"""
Predicción en vivo: qué puede pasar en lo que queda de los 90 minutos, partiendo del marcador confirmado.

  goles finales = goles actuales + goles que faltan
  goles que faltan ~ Poisson(lambda_previa x fracción de goles que todavía suele marcarse en esa liga)

lambda_previa es la fuerza previa de cada equipo (los goles esperados del modelo previo, calculados solo con partidos anteriores
al inicio). La fracción restante sale de los minutos reales de los goles del historial de la liga (FootyStats), con los
descuentos incluidos: un gol "45+7" cuenta como 45,07 y uno "90+3" como 90,03.

Limitaciones conocidas (por eso se valida con estados históricos antes de presentarla como validada):
  * no usa expulsiones, cambios ni el dominio del partido, que esta fuente no entrega en vivo;
  * no ajusta por el estado del marcador (un equipo que pierde suele arriesgar más).

Todo es puro y en minutos de juego; la prórroga y los penales no forman parte del resultado a 90 minutos.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import poisson

MAX_GOLES_RESTANTES = 10
FIN_1T = 45.99  # tras el descuento del primer tiempo
FIN_90 = 90.99  # tras el descuento del segundo tiempo
PESO_GENERICO = 20  # goles "virtuales" del perfil genérico que suavizan el perfil de la liga

# Fases que cierran el resultado a 90 minutos: ya no pueden cambiarlo
FASES_CERRADAS_90 = {"prorroga", "pausa_prorroga", "penales", "final", "tras_prorroga", "tras_penales"}


def posicion(fase: str | None, minuto: int | None, extra: int | None = None) -> float | None:
    """
    Minuto de juego como número comparable con los minutos de gol: 1T 30' -> 30; 45+2 -> 45,02; descanso -> 45,99;
    2T 90+3 -> 90,03. Devuelve FIN_90 si el resultado a 90' ya está cerrado y None si no se puede saber.
    """
    if fase in FASES_CERRADAS_90:
        return FIN_90
    if fase == "descanso":
        return FIN_1T
    if minuto is None:
        return None
    x = (extra or 0) / 100
    if fase == "1T":
        return 45 + x if minuto >= 45 else float(minuto)
    if fase == "2T":
        return 90 + x if minuto >= 90 else float(max(minuto, 46))
    return float(minuto)


def restante_generico(p: float) -> float:
    """Fracción de goles que queda por marcar con un perfil uniforme (respaldo si la liga no tiene minutos de gol)."""
    base = min(int(p), 90)
    return max(0.0, 1 - base / 91)


class Perfil:
    """Distribución empírica del minuto de los goles en los 90' de una liga (sin prórroga)."""

    def __init__(self, minutos: list[float]):
        self.minutos = np.sort(np.array([m for m in minutos if m < 91], dtype=float))
        self.n = len(self.minutos)
        self.origen = "liga" if self.n >= 200 else "generico"

    def restante(self, p: float) -> float:
        if p >= FIN_90:
            return 0.0
        quedan = self.n - np.searchsorted(self.minutos, p, side="right")  # goles con minuto > p
        if self.n == 0:
            return restante_generico(p)
        return float((quedan + PESO_GENERICO * restante_generico(p)) / (self.n + PESO_GENERICO))


def goles_a(minutos: list[float], p: float) -> int:
    """Goles marcados hasta la posición p (incluida)."""
    return sum(1 for m in minutos if m <= p)


def goles_90(minutos: list[float]) -> int:
    return sum(1 for m in minutos if m < 91)


def prediccion(lam_l: float, lam_v: float, goles_l: int, goles_v: int, p: float, perfil: Perfil) -> dict:
    """Probabilidades al final de los 90' dado el marcador en la posición p."""
    f = perfil.restante(p)
    ml, mv = lam_l * f, lam_v * f
    k = np.arange(MAX_GOLES_RESTANTES + 1)
    pl, pv = poisson.pmf(k, ml), poisson.pmf(k, mv)
    pl[-1] += 1 - pl.sum()  # la cola se acumula en el último valor
    pv[-1] += 1 - pv.sum()
    M = np.outer(pl, pv)  # M[i, j]: el local marca i más y el visitante j más
    fl = goles_l + k[:, None]
    fv = goles_v + k[None, :]
    total = fl + fv
    p1, px, p2 = float(M[fl > fv].sum()), float(M[fl == fv].sum()), float(M[fl < fv].sum())
    orden = np.dstack(np.unravel_index(np.argsort(-M, axis=None)[:3], M.shape))[0]
    ya_mas_25 = goles_l + goles_v >= 3
    ya_ambos = goles_l > 0 and goles_v > 0
    return {
        "resultado_90": {"local": p1, "empate": px, "visitante": p2},
        "mas_2_5": {"prob": 1.0 if ya_mas_25 else float(M[total >= 3].sum()), "cumplido": ya_mas_25, "goles_que_faltan": max(0, 3 - goles_l - goles_v)},
        "ambos_marcan": {"prob": 1.0 if ya_ambos else float(M[(fl > 0) & (fv > 0)].sum()), "cumplido": ya_ambos},
        "marcadores": [{"local": int(goles_l + i), "visitante": int(goles_v + j), "p": float(M[i, j])} for i, j in orden],
        "goles_restantes_esperados": {"local": ml, "visitante": mv},
        "fraccion_restante": f,
    }
