"""
Predicción de partidos reales de FootyStats (1X2, más de 2.5 goles y ambos marcan).

Independiente de model.py (que carga los CSV de LaLiga al importarse): aquí solo se usa el historial
de partidos terminados de la misma competición, guardado en disco por footystats.py.

Modelo: regresión de Poisson con ataque y defensa por equipo, ventaja de local y peso decreciente
con la antigüedad (misma idea que model.py), y corrección Dixon-Coles de marcadores bajos.
Las estadísticas de un partido usan SOLO partidos anteriores a su fecha.

Variantes del objetivo: "goles" (goles reales) o "xg" (75 % xG + 25 % goles, como model.py; solo si la
liga trae xG en casi todo el historial). Cada liga elige variante e hiperparámetros con un bloque de
validación y se mide en un bloque posterior que no se usó para elegir; el resultado se compara con una
referencia sencilla (frecuencias históricas de la propia liga).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time

import numpy as np
from scipy.stats import nbinom, poisson
from sklearn.linear_model import PoissonRegressor

import footystats
import reloj
import pronosticos_previos
import vivo_modelo

log = logging.getLogger("prediliga.prediccion_fs")

HISTORIAL_TEMPORADAS = 3  # temporada del partido + las 2 anteriores
MIN_PARTIDOS_EQUIPO = 8  # partidos previos terminados que necesita cada equipo
MIN_PARTIDOS_LIGA = 120  # historial mínimo de la competición para ajustar el modelo
MIN_PARTIDOS_VALIDAR = 200  # por debajo de esto no hay forma honesta de separar entrenamiento y prueba
MIN_PRUEBA = 100  # partidos de prueba para hablar de "calidad medida"
FRAC_VALIDACION = 0.60  # [0, 60 %) entrena · [60, 80 %) elige hiperparámetros · [80, 100 %] prueba
FRAC_PRUEBA = 0.80
BLOQUE = 25  # el modelo se reajusta cada 25 partidos durante la validación
COBERTURA_XG = 0.70
PESO_XG = 0.75
DC_RHO = -0.05
MAX_GOLES = 10
REVALIDAR_TRAS = 40  # partidos nuevos en el historial que obligan a repetir la validación
HIPERPARAMETROS = [(vida, alfa) for vida in (120, 365) for alfa in (0.005, 0.03, 0.1)]
DEFECTO = {"variante": "goles", "vida_media_dias": 180, "alfa": 0.03}

MERCADOS = ("resultado_1x2", "mas_2_5", "ambos_marcan", "mas_1_5", "mas_3_5", "doble_1x", "doble_x2", "doble_12", "marca_local", "marca_visitante")
VERSION_VALIDACION = 3
# Estadísticas con modelo propio (ataque/defensa por equipo). Se validan con la línea más equilibrada del historial.
ESTADISTICAS = {"goles_1t": ("h1", "a1"), "corners": ("hc", "ac"), "tarjetas": ("hk", "ak"), "tiros": ("hs", "as"), "tiros_puerta": ("hq", "aq")}
NOMBRE_ESTADISTICA = {"goles_1t": "goles del primer tiempo", "corners": "córners", "tarjetas": "tarjetas", "tiros": "tiros", "tiros_puerta": "tiros a puerta"}
LINEAS_POR_ESTADISTICA = 5  # líneas validadas por estadística, centradas en la mediana de la liga
COBERTURA_EXTRA = 0.70
CERO_SOSPECHOSO = 0.12  # si más de este % de los partidos con tarjetas de una temporada son 0-0, esos ceros son relleno
VIDA_EXTRA, ALFA_EXTRA = 365, 0.03
_k = np.arange(MAX_GOLES + 1)
_validacion_lock = asyncio.Lock()
_ligas_cache: dict = {"t": 0.0, "ligas": []}
LIGAS_TTL_S = 3600


# ─────────────────────────────────────────────
#  MODELO
# ─────────────────────────────────────────────
def _normalizar(*cuotas):
    """Probabilidades implícitas sin el margen de la casa, y ese margen. None si falta alguna cuota."""
    if any(x is None or x <= 1 for x in cuotas):
        return None
    inv = [1 / x for x in cuotas]
    t = sum(inv)
    return [v / t for v in inv], t - 1


def _probs_cuotas(q) -> list[float]:
    nan = float("nan")
    q = q or {}
    r1 = _normalizar(q.get("1"), q.get("x"), q.get("2"))
    r2 = _normalizar(q.get("o25"), q.get("u25"))
    r3 = _normalizar(q.get("by"), q.get("bn"))
    return [*(r1[0] if r1 else [nan] * 3), r2[0][0] if r2 else nan, r3[0][0] if r3 else nan]


class Historial:
    """Partidos terminados de la competición ordenados por fecha, como arrays de numpy."""

    def __init__(self, partidos: list[dict]):
        fin = [p for p in partidos if p["s"] == "complete" and p["hg"] is not None and p["ag"] is not None and p["hg"] >= 0 and p["ag"] >= 0]
        fin.sort(key=lambda p: (p["t"], p["id"]))
        self.n = len(fin)
        self.t = np.array([p["t"] for p in fin], dtype=float)
        self.hg = np.array([p["hg"] for p in fin], dtype=float)
        self.ag = np.array([p["ag"] for p in fin], dtype=float)
        # minutos de cada gol (None si FootyStats no los registró): estados históricos para validar la predicción en vivo
        self.ml = [p.get("ml") for p in fin]
        self.mv = [p.get("mv") for p in fin]
        self.hx = np.array([p["hx"] if p["hx"] is not None else np.nan for p in fin], dtype=float)
        self.ax = np.array([p["ax"] if p["ax"] is not None else np.nan for p in fin], dtype=float)
        ids = sorted({p["h"] for p in fin} | {p["a"] for p in fin})
        self.equipos = {e: i for i, e in enumerate(ids)}
        self.h = np.array([self.equipos[p["h"]] for p in fin], dtype=int)
        self.a = np.array([self.equipos[p["a"]] for p in fin], dtype=int)
        self.nombres = {}
        for p in fin:
            self.nombres[p["h"]] = p["hn"]
            self.nombres[p["a"]] = p["an"]
        con_xg = ~np.isnan(self.hx) & ~np.isnan(self.ax)
        self.cobertura_xg = float(con_xg.mean()) if self.n else 0.0
        nan = float("nan")
        self.sid = np.array([p.get("sid", 0) for p in fin])
        self.stat, self.cobertura = {}, {}
        for nombre, (kh, ka) in ESTADISTICAS.items():
            a = np.array([p.get(kh) if p.get(kh) is not None else nan for p in fin], dtype=float)
            b = np.array([p.get(ka) if p.get(ka) is not None else nan for p in fin], dtype=float)
            if nombre == "tarjetas":
                # Un 0-0 en tarjetas puede ser real (partido limpio) o relleno de la fuente. Se distingue por temporada:
                # en las ligas que sí registran tarjetas los 0-0 son pocos (2-7 %); si son más del 12 % son relleno.
                for sid in np.unique(self.sid):
                    en_temp = self.sid == sid
                    con_dato = en_temp & ~np.isnan(a) & ~np.isnan(b)
                    ceros = con_dato & (a + b == 0)
                    if con_dato.sum() >= 20 and ceros.sum() / con_dato.sum() > CERO_SOSPECHOSO:
                        a[ceros], b[ceros] = nan, nan
            self.stat[nombre] = (a, b)
            self.cobertura[nombre] = float((~np.isnan(a) & ~np.isnan(b)).mean()) if self.n else 0.0
        # probabilidades implícitas de las cuotas (sin margen): [local, empate, visitante, más de 2.5, ambos marcan]
        self.cuotas = np.array([_probs_cuotas(p.get("q")) for p in fin], dtype=float).reshape(-1, 5)

    def antes_de(self, t_ref: float) -> np.ndarray:
        """Índices de los partidos terminados estrictamente antes de t_ref."""
        return np.nonzero(self.t < t_ref)[0]


def _filas(hist: Historial, idx: np.ndarray, local: np.ndarray, visita: np.ndarray) -> np.ndarray:
    """Diseño: [ataque (T) | defensa (T) | ventaja local]. Dos filas por partido (local y visitante)."""
    T = len(hist.equipos)
    m = len(idx)
    X = np.zeros((2 * m, 2 * T + 1))
    r = np.arange(m)
    X[r, local] = 1
    X[r, T + visita] = 1
    X[r, 2 * T] = 1
    X[m + r, visita] = 1
    X[m + r, T + local] = 1
    return X


def _ajustar(hist: Historial, idx: np.ndarray, vida_media: float, alfa: float, variante: str) -> PoissonRegressor:
    yh, ya = hist.hg[idx], hist.ag[idx]
    if variante == "xg":
        ok = ~np.isnan(hist.hx[idx]) & ~np.isnan(hist.ax[idx])
        yh = np.where(ok, PESO_XG * np.nan_to_num(hist.hx[idx]) + (1 - PESO_XG) * yh, yh)
        ya = np.where(ok, PESO_XG * np.nan_to_num(hist.ax[idx]) + (1 - PESO_XG) * ya, ya)
    return _ajustar_objetivo(hist, idx, yh, ya, vida_media, alfa)


def _ajustar_objetivo(hist: Historial, idx: np.ndarray, yh: np.ndarray, ya: np.ndarray, vida_media: float, alfa: float) -> PoissonRegressor:
    local, visita = hist.h[idx], hist.a[idx]
    t_ref = hist.t[idx].max()
    w = np.exp(-math.log(2) * (t_ref - hist.t[idx]) / 86400 / vida_media)
    modelo = PoissonRegressor(alpha=alfa, max_iter=300)
    modelo.fit(_filas(hist, idx, local, visita), np.concatenate([yh, ya]), sample_weight=np.concatenate([w, w]))
    return modelo


def _lambdas(modelo: PoissonRegressor, hist: Historial, local: np.ndarray, visita: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    X = _filas(hist, np.arange(len(local)), local, visita)
    mu = modelo.predict(X)
    return mu[: len(local)], mu[len(local) :]


def _matriz(lh: np.ndarray, la: np.ndarray) -> np.ndarray:
    """(n, goles local, goles visitante): Poisson + corrección Dixon-Coles de marcadores bajos."""
    ph = poisson.pmf(_k[None, :], lh[:, None])
    pa = poisson.pmf(_k[None, :], la[:, None])
    M = ph[:, :, None] * pa[:, None, :]  # M[n, goles local, goles visitante]
    M[:, 0, 0] *= 1 - lh * la * DC_RHO
    M[:, 0, 1] *= 1 + lh * DC_RHO
    M[:, 1, 0] *= 1 + la * DC_RHO
    M[:, 1, 1] *= 1 - DC_RHO
    M /= M.sum(axis=(1, 2), keepdims=True)
    return M


_I, _J = np.meshgrid(_k, _k, indexing="ij")


def _probs_de_matriz(M: np.ndarray) -> np.ndarray:
    """
    (n, 12): local, empate, visitante, más de 2.5, ambos marcan, más de 1.5, más de 3.5,
    local o empate, empate o visitante, local o visitante, marca el local, marca el visitante.
    """
    i, j = _I, _J
    p1, px, p2 = M[:, i > j].sum(1), M[:, i == j].sum(1), M[:, i < j].sum(1)
    return np.stack(
        [p1, px, p2, M[:, (i + j) >= 3].sum(1), M[:, (i >= 1) & (j >= 1)].sum(1), M[:, (i + j) >= 2].sum(1), M[:, (i + j) >= 4].sum(1),
         p1 + px, px + p2, p1 + p2, M[:, i >= 1].sum(1), M[:, j >= 1].sum(1)],
        axis=1,
    )


def _probabilidades(lh: np.ndarray, la: np.ndarray) -> np.ndarray:
    return _probs_de_matriz(_matriz(lh, la))


# ─────────────────────────────────────────────
#  ESTADÍSTICAS DE CONTEO (primer tiempo, córners, tarjetas)
# ─────────────────────────────────────────────
def _ajustar_conteo(hist: Historial, idx: np.ndarray, nombre: str, vida: float, alfa: float):
    """Modelo ataque/defensa de la estadística + dispersión del total (binomial negativa si hay sobredispersión)."""
    yh_all, ya_all = hist.stat[nombre]
    ok = ~np.isnan(yh_all[idx]) & ~np.isnan(ya_all[idx])
    ix = idx[ok]
    if len(ix) < MIN_PARTIDOS_LIGA:
        return None
    yh, ya = yh_all[ix], ya_all[ix]
    modelo = _ajustar_objetivo(hist, ix, yh, ya, vida, alfa)
    mh, ma = _lambdas(modelo, hist, hist.h[ix], hist.a[ix])
    mu = mh + ma
    num, den = float((((yh + ya - mu) ** 2) - mu).sum()), float((mu**2).sum())
    r = float(np.clip(den / num, 0.5, 1e4)) if num > 0 else None  # None: Poisson
    return modelo, r


def _p_mas(mu: np.ndarray, r: float | None, linea: float) -> np.ndarray:
    k = math.floor(linea)
    if r is None:
        return 1 - poisson.cdf(k, mu)
    return 1 - nbinom.cdf(k, r, r / (r + mu))


# ─────────────────────────────────────────────
#  VALIDACIÓN (partido a partido, solo con información anterior)
# ─────────────────────────────────────────────
def _resultados(hist: Historial, idx: np.ndarray) -> np.ndarray:
    """(n, 10): clase 1X2 (0 local, 1 empate, 2 visitante) y los demás mercados en el orden de MERCADOS (0/1)."""
    hg, ag = hist.hg[idx], hist.ag[idx]
    return np.stack(
        [np.where(hg > ag, 0, np.where(hg == ag, 1, 2)), (hg + ag) >= 3, (hg > 0) & (ag > 0), (hg + ag) >= 2, (hg + ag) >= 4,
         hg >= ag, hg <= ag, hg != ag, hg > 0, ag > 0],
        axis=1,
    ).astype(int)


def _caminar(hist: Historial, ini: int, fin: int, vida: float, alfa: float, variante: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Predice los partidos [ini, fin) reajustando cada BLOQUE con lo anterior.
    Devuelve (índices, probabilidades de MERCADOS, probabilidad del marcador exacto real, ¿estaba entre los 3 más probables?).
    """
    indices, probs, p_marc, top3 = [], [], [], []
    for s in range(ini, fin, BLOQUE):
        previo = hist.antes_de(hist.t[s])
        if len(previo) < MIN_PARTIDOS_LIGA:
            continue
        cuenta = np.bincount(np.concatenate([hist.h[previo], hist.a[previo]]), minlength=len(hist.equipos))
        bloque = np.arange(s, min(s + BLOQUE, fin))
        bloque = bloque[(cuenta[hist.h[bloque]] >= MIN_PARTIDOS_EQUIPO) & (cuenta[hist.a[bloque]] >= MIN_PARTIDOS_EQUIPO)]
        if len(bloque) == 0:
            continue
        modelo = _ajustar(hist, previo, vida, alfa, variante)
        lh, la = _lambdas(modelo, hist, hist.h[bloque], hist.a[bloque])
        M = _matriz(lh, la)
        gl, gv = np.minimum(hist.hg[bloque], MAX_GOLES).astype(int), np.minimum(hist.ag[bloque], MAX_GOLES).astype(int)
        plano = M.reshape(len(bloque), -1)
        real = gl * (MAX_GOLES + 1) + gv
        indices.append(bloque)
        probs.append(_probs_de_matriz(M))
        p_marc.append(plano[np.arange(len(bloque)), real])
        top3.append((np.argsort(-plano, axis=1)[:, :3] == real[:, None]).any(1))
    if not indices:
        return np.array([], dtype=int), np.zeros((0, 12)), np.zeros(0), np.zeros(0, dtype=bool)
    return np.concatenate(indices), np.concatenate(probs), np.concatenate(p_marc), np.concatenate(top3)


def _referencia(hist: Historial, hasta: int, n: int) -> np.ndarray:
    """Referencia sencilla: frecuencias de la liga en los partidos usados para entrenar, iguales para todos."""
    y = _resultados(hist, np.arange(hasta))
    f1x2 = np.bincount(y[:, 0], minlength=3) / len(y)
    return np.tile(np.concatenate([f1x2, y[:, 1:].mean(0)]), (n, 1))


def _referencia_marcadores(hist: Historial, hasta: int) -> np.ndarray:
    """Frecuencia de cada marcador exacto en el entrenamiento (suavizada), como matriz plana de 11 x 11."""
    gl, gv = np.minimum(hist.hg[:hasta], MAX_GOLES).astype(int), np.minimum(hist.ag[:hasta], MAX_GOLES).astype(int)
    c = np.bincount(gl * (MAX_GOLES + 1) + gv, minlength=(MAX_GOLES + 1) ** 2) + 0.5
    return c / c.sum()


def _log(p: np.ndarray) -> np.ndarray:
    return np.log(np.clip(p, 1e-9, 1.0))


def _perdidas(probs: np.ndarray, y: np.ndarray) -> np.ndarray:
    """(n, 5): log loss por partido de cada mercado."""
    n = len(y)
    cols = [probs[np.arange(n), y[:, 0]]] + [np.where(y[:, k] == 1, probs[:, k + 2], 1 - probs[:, k + 2]) for k in range(1, 10)]
    return -_log(np.stack(cols, axis=1))


def _aciertos(probs: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.stack([probs[:, :3].argmax(1) == y[:, 0]] + [(probs[:, k + 2] > 0.5) == (y[:, k] == 1) for k in range(1, 10)], axis=1)


def _veredicto(dif: np.ndarray, suficiente: bool) -> tuple[float, float, str]:
    """dif > 0: el modelo pierde menos log loss que la referencia. Devuelve (mejora media, error estándar, veredicto)."""
    m = len(dif)
    media = float(dif.mean()) if m else 0.0
    error = float(dif.std(ddof=1) / math.sqrt(m)) if m > 1 else float("inf")
    if not suficiente:
        return media, error, "no_concluyente"
    if media > 2 * error:
        return media, error, "supera_referencia"
    if media < -2 * error:
        return media, error, "peor_que_referencia"
    return media, error, "similar_a_referencia"


def _validar(hist: Historial) -> dict:
    """Elige variante e hiperparámetros en el bloque de validación y mide en el bloque de prueba."""
    n = hist.n
    i_val, i_test = int(n * FRAC_VALIDACION), int(n * FRAC_PRUEBA)
    variantes = ["goles"] + (["xg"] if hist.cobertura_xg >= COBERTURA_XG else [])

    elegidas = {}
    for var in variantes:
        mejor = None
        for vida, alfa in HIPERPARAMETROS:
            idx, pr, _, _ = _caminar(hist, i_val, i_test, vida, alfa, var)
            if len(idx) < 30:
                continue
            ref = _referencia(hist, i_val, len(idx))
            y = _resultados(hist, idx)
            # La elección usa solo los 5 mercados originales (1X2, 2.5, ambos marcan, 1.5, 3.5)
            puntaje = float(_perdidas(pr, y)[:, :5].mean(0).sum())
            if mejor is None or puntaje < mejor[0]:
                mejor = (puntaje, vida, alfa, float(_perdidas(ref, y)[:, :5].mean(0).sum()))
        if mejor:
            elegidas[var] = mejor
    if not elegidas:
        return {"estado": "no_medida", "motivo": "No hay suficientes partidos de validación con historial previo de ambos equipos."}

    resultados = {}
    for var, (punt_val, vida, alfa, punt_ref_val) in elegidas.items():
        idx, pr, p_marc, top3 = _caminar(hist, i_test, n, vida, alfa, var)
        resultados[var] = {"vida": vida, "alfa": alfa, "puntaje_validacion": punt_val, "idx": idx, "pr": pr, "p_marc": p_marc, "top3": top3}

    ganadora = min(elegidas, key=lambda v: elegidas[v][0])  # se elige con validación, nunca con la prueba
    g = resultados[ganadora]
    if len(g["idx"]) == 0:
        return {"estado": "no_medida", "motivo": "No hay partidos de prueba con historial previo suficiente."}

    y = _resultados(hist, g["idx"])
    ref = _referencia(hist, i_test, len(g["idx"]))
    perd_m, perd_r = _perdidas(g["pr"], y), _perdidas(ref, y)
    ac_m, ac_r = _aciertos(g["pr"], y).mean(0), _aciertos(ref, y).mean(0)
    m = len(g["idx"])
    suficiente = m >= MIN_PRUEBA
    cu = hist.cuotas[g["idx"]]
    detalle = {}
    for k, nombre in enumerate(MERCADOS):
        media, error, veredicto = _veredicto(perd_r[:, k] - perd_m[:, k], suficiente)
        detalle[nombre] = {
            "logloss_modelo": round(float(perd_m[:, k].mean()), 4),
            "logloss_referencia": round(float(perd_r[:, k].mean()), 4),
            "mejora": round(media, 4),
            "error_estandar": round(error, 4),
            "acierto_modelo": round(float(ac_m[k]), 3),
            "acierto_referencia": round(float(ac_r[k]), 3),
            "veredicto": veredicto,
        }
        # ¿y frente a las cuotas del mercado (sin margen)? Solo en los partidos que tienen cuota.
        if k <= 2:
            if k == 0:
                ok = ~np.isnan(cu[:, :3]).any(1)
                pc = cu[ok][np.arange(int(ok.sum())), y[ok, 0]]
            else:
                ok = ~np.isnan(cu[:, 2 + k])
                pc = np.where(y[:, k] == 1, cu[:, 2 + k], 1 - cu[:, 2 + k])[ok]
            if ok.sum() >= 30:
                detalle[nombre]["partidos_con_cuotas"] = int(ok.sum())
                detalle[nombre]["logloss_cuotas"] = round(float((-_log(pc)).mean()), 4)
                detalle[nombre]["logloss_modelo_mismos"] = round(float(perd_m[ok, k].mean()), 4)
    # Marcador exacto: probabilidad que el modelo daba al marcador real, frente a la frecuencia histórica de cada marcador
    freq = _referencia_marcadores(hist, i_test)
    gl, gv = np.minimum(hist.hg[g["idx"]], MAX_GOLES).astype(int), np.minimum(hist.ag[g["idx"]], MAX_GOLES).astype(int)
    p_ref = freq[gl * (MAX_GOLES + 1) + gv]
    top_ref = set(np.argsort(-freq)[:3])
    acierto_ref = np.isin(gl * (MAX_GOLES + 1) + gv, list(top_ref))
    media, error, veredicto = _veredicto(_log(g["p_marc"]) - _log(p_ref), suficiente)
    detalle["marcador_exacto"] = {
        "logloss_modelo": round(float(-_log(g["p_marc"]).mean()), 4),
        "logloss_referencia": round(float(-_log(p_ref).mean()), 4),
        "mejora": round(media, 4),
        "error_estandar": round(error, 4),
        "acierto_modelo": round(float(g["top3"].mean()), 3),  # el marcador real estaba entre los 3 más probables
        "acierto_referencia": round(float(acierto_ref.mean()), 3),
        "veredicto": veredicto,
    }
    otras = {
        v: {"logloss_1x2_prueba": round(float(_perdidas(r["pr"], _resultados(hist, r["idx"]))[:, 0].mean()), 4), "partidos": int(len(r["idx"]))}
        for v, r in resultados.items()
        if len(r["idx"])
    }
    return {
        "estado": "medida" if suficiente else "insuficiente_para_medir",
        "variante": ganadora,
        "vida_media_dias": g["vida"],
        "alfa": g["alfa"],
        "partidos_prueba": m,
        "partidos_entrenamiento": i_test,
        "prueba_desde": time.strftime("%Y-%m-%d", time.gmtime(float(hist.t[g["idx"][0]]))),
        "prueba_hasta": time.strftime("%Y-%m-%d", time.gmtime(float(hist.t[g["idx"][-1]]))),
        "mercados": detalle,
        "variantes_probadas": otras,
        "cobertura_xg": round(hist.cobertura_xg, 3),
    }


def _lineas_liga(entren: np.ndarray) -> list[float]:
    """Líneas a validar y mostrar: 5 alrededor de la mediana del total en el entrenamiento (7.5, 8.5, 9.5...)."""
    centro = math.floor(float(np.median(entren))) + 0.5
    return [centro + d for d in range(-(LINEAS_POR_ESTADISTICA // 2), LINEAS_POR_ESTADISTICA // 2 + 1) if centro + d > 0]


def _validar_extra(hist: Historial) -> dict:
    """
    Goles del primer tiempo, córners, tarjetas, tiros y tiros a puerta. Mismo esquema que los goles: se entrena con
    lo anterior a cada partido y se mide en el último 20 %. Cada línea (más de X) se valida POR SEPARADO frente a la
    frecuencia con que esa línea se superó en el entrenamiento, y el total esperado se mide aparte (error absoluto).
    Parámetros fijos, sin elegir variante.
    """
    n = hist.n
    i_test = int(n * FRAC_PRUEBA)
    salida = {}
    for nombre in ESTADISTICAS:
        yh_all, ya_all = hist.stat[nombre]
        tot_all = yh_all + ya_all
        cob = hist.cobertura[nombre]
        if cob < COBERTURA_EXTRA:
            salida[nombre] = {"estado": "sin_datos", "cobertura": round(cob, 3), "motivo": f"FootyStats trae {NOMBRE_ESTADISTICA[nombre]} en solo el {cob:.0%} de los partidos terminados de esta competición (mínimo {COBERTURA_EXTRA:.0%})."}
            continue
        entren = tot_all[:i_test][~np.isnan(tot_all[:i_test])]
        if len(entren) < MIN_PARTIDOS_LIGA:
            salida[nombre] = {"estado": "no_medida", "cobertura": round(cob, 3), "motivo": f"Solo {len(entren)} partidos con {NOMBRE_ESTADISTICA[nombre]} para entrenar (mínimo {MIN_PARTIDOS_LIGA})."}
            continue
        lineas = _lineas_liga(entren)
        mus, ys, ps = [], [], []
        disp = "poisson"
        for s0 in range(i_test, n, BLOQUE):
            previo = hist.antes_de(hist.t[s0])
            ajuste = _ajustar_conteo(hist, previo, nombre, VIDA_EXTRA, ALFA_EXTRA)
            if ajuste is None:
                continue
            modelo, r = ajuste
            cuenta = np.bincount(np.concatenate([hist.h[previo], hist.a[previo]]), minlength=len(hist.equipos))
            bloque = np.arange(s0, min(s0 + BLOQUE, n))
            bloque = bloque[(cuenta[hist.h[bloque]] >= MIN_PARTIDOS_EQUIPO) & (cuenta[hist.a[bloque]] >= MIN_PARTIDOS_EQUIPO) & ~np.isnan(tot_all[bloque])]
            if len(bloque) == 0:
                continue
            mh, ma = _lambdas(modelo, hist, hist.h[bloque], hist.a[bloque])
            mus.append(mh + ma)
            ys.append(tot_all[bloque])
            ps.append(np.stack([_p_mas(mh + ma, r, L) for L in lineas], axis=1))
            if r is not None:
                disp = "binomial_negativa"
        if not mus:
            salida[nombre] = {"estado": "no_medida", "cobertura": round(cob, 3), "motivo": "No hay partidos de prueba con historial previo suficiente."}
            continue
        mu, tot, P = np.concatenate(mus), np.concatenate(ys), np.concatenate(ps)
        m = len(tot)
        suficiente = m >= MIN_PRUEBA
        res_lineas = []
        for k, L in enumerate(lineas):
            y = (tot > L).astype(int)
            base = float(np.clip((entren > L).mean(), 1e-6, 1 - 1e-6))
            pm, pr = np.where(y == 1, P[:, k], 1 - P[:, k]), np.where(y == 1, base, 1 - base)
            media, error, veredicto = _veredicto(_log(pm) - _log(pr), suficiente)
            res_lineas.append({
                "linea": L,
                "partidos_prueba": m,
                "frecuencia_historica": round(base, 3),
                "logloss_modelo": round(float((-_log(pm)).mean()), 4),
                "logloss_referencia": round(float((-_log(pr)).mean()), 4),
                "mejora": round(media, 4),
                "error_estandar": round(error, 4),
                "acierto_modelo": round(float(((P[:, k] > 0.5) == (y == 1)).mean()), 3),
                "acierto_referencia": round(float(((base > 0.5) == (y == 1)).mean()), 3),
                "veredicto": veredicto,
            })
        mae_m, mae_r = float(np.abs(tot - mu).mean()), float(np.abs(tot - entren.mean()).mean())
        salida[nombre] = {
            "estado": "medida" if suficiente else "insuficiente_para_medir",
            "cobertura": round(cob, 3),
            "partidos_prueba": m,
            "lineas": res_lineas,
            "total_esperado": {"error_abs_modelo": round(mae_m, 3), "error_abs_referencia": round(mae_r, 3), "media_real": round(float(tot.mean()), 2)},
            "dispersion": disp,
        }
    return salida


# ─────────────────────────────────────────────
#  PREDICCIÓN POR ID DE PARTIDO
# ─────────────────────────────────────────────
async def _liga_de_temporada(season_id: int) -> dict | None:
    """Competición a la que pertenece la temporada (lista en memoria 1 h: evita 1 consulta por clic)."""
    def buscar():
        return next((l for l in _ligas_cache["ligas"] if any(t["id"] == season_id for t in l["temporadas"])), None)

    if time.time() - _ligas_cache["t"] > LIGAS_TTL_S:
        _ligas_cache["ligas"] = await footystats.listar_ligas()
        _ligas_cache["t"] = time.time()
    liga = buscar()
    if liga is None and time.time() - _ligas_cache["t"] > 60:  # la cuenta pudo cambiar de selección
        _ligas_cache["ligas"] = await footystats.listar_ligas()
        _ligas_cache["t"] = time.time()
        liga = buscar()
    return liga


def _ruta_validacion(ids: list[int]):
    return footystats.CACHE_DIR / ("validacion_" + "_".join(map(str, ids)) + ".json")


def _validacion_guardada(ids: list[int], n: int) -> dict | None:
    try:
        v = json.loads(_ruta_validacion(ids).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if v.get("v") != VERSION_VALIDACION:
        return None  # validación hecha con una versión anterior (menos mercados)
    return v if 0 <= n - v.get("n_historial", 0) < REVALIDAR_TRAS else None


async def validacion_liga(ids: list[int], hist: Historial, con_extra: bool = False, con_en_vivo: bool = False) -> dict:
    """Validación de la liga, de disco si está vigente. La parte de córners/tarjetas/tiros solo se calcula si se pide."""

    def lista(g):
        return g is not None and (not con_extra or "extra" in g["validacion"]) and (not con_en_vivo or "en_vivo" in g["validacion"])

    g = _validacion_guardada(ids, hist.n)
    if lista(g):
        return g["validacion"]
    async with _validacion_lock:  # sklearn en hilo aparte; una validación a la vez
        g = _validacion_guardada(ids, hist.n)
        if lista(g):
            return g["validacion"]
        val = g["validacion"] if g else None
        if val is None:
            if hist.n < MIN_PARTIDOS_VALIDAR:
                val = {"estado": "no_medida", "motivo": f"Solo hay {hist.n} partidos terminados; se necesitan {MIN_PARTIDOS_VALIDAR} para separar entrenamiento y prueba."}
            else:
                val = await asyncio.to_thread(_validar, hist)
            val["n_historial"] = hist.n
        if con_extra and "extra" not in val:
            val["extra"] = await asyncio.to_thread(_validar_extra, hist) if hist.n >= MIN_PARTIDOS_VALIDAR else {}
        if con_en_vivo and "en_vivo" not in val:
            parametros = {k: val.get(k, DEFECTO[k]) for k in DEFECTO} if val.get("estado") in ("medida", "insuficiente_para_medir") else dict(DEFECTO)
            val["en_vivo"] = await asyncio.to_thread(_validar_en_vivo, hist, parametros)
        footystats.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _ruta_validacion(ids).write_text(json.dumps({"v": VERSION_VALIDACION, "n_historial": val["n_historial"], "validacion": val}, ensure_ascii=False), encoding="utf-8")
        return val


def _r4(x) -> float:
    return round(float(x), 4)


def _mercados_goles(M: np.ndarray) -> dict:
    """Todos los mercados que salen de la misma matriz de marcadores (n = 1): no necesitan otro modelo."""
    i, j = _I, _J
    tot = i + j
    P = lambda mascara: _r4(M[mascara].sum())  # noqa: E731
    p1, px, p2 = P(i > j), P(i == j), P(i < j)
    top = np.dstack(np.unravel_index(np.argsort(-M, axis=None)[:6], M.shape))[0]
    return {
        "doble_oportunidad": {"1x": _r4(p1 + px), "12": _r4(p1 + p2), "x2": _r4(px + p2)},
        "empate_no_apuesta": {"local": _r4(p1 / (p1 + px)), "visitante": _r4(p2 / (p2 + px))},
        "goles_totales": [{"linea": L, "mas": P(tot > L), "menos": P(tot < L)} for L in (0.5, 1.5, 2.5, 3.5, 4.5)],
        "goles_equipo": {
            "local": [{"linea": L, "mas": P(i > L)} for L in (0.5, 1.5, 2.5)],
            "visitante": [{"linea": L, "mas": P(j > L)} for L in (0.5, 1.5, 2.5)],
        },
        "porteria_cero": {"local": P(j == 0), "visitante": P(i == 0)},
        "gana_por_2": {"local": P(i - j >= 2), "visitante": P(j - i >= 2)},
        "ambos_marcan_y_mas_2_5": P((i >= 1) & (j >= 1) & (tot >= 3)),
        "marcadores": [{"local": int(a), "visitante": int(b), "p": _r4(M[a, b])} for a, b in top],
    }


def _bloque_conteo(hist: Historial, previo: np.ndarray, nombre: str, local: int, visita: int, calidad: dict | None) -> dict | None:
    """
    Total esperado (y por equipo) más probabilidad de superar cada línea. Las líneas son las mismas que se validaron.
    None si no hay datos suficientes (el motivo lo da _pendientes).
    """
    if hist.cobertura[nombre] < COBERTURA_EXTRA:
        return None
    ajuste = _ajustar_conteo(hist, previo, nombre, VIDA_EXTRA, ALFA_EXTRA)
    if ajuste is None:
        return None
    modelo, r = ajuste
    mh, ma = _lambdas(modelo, hist, np.array([local]), np.array([visita]))
    mh, ma = float(mh[0]), float(ma[0])
    mu = np.array([mh + ma])
    por_linea = {round(l["linea"], 1): l for l in (calidad or {}).get("lineas", [])}
    if por_linea:
        lineas = sorted(por_linea)
    else:  # sin validación: líneas centradas en el esperado, marcadas como no validadas
        lineas = [k + 0.5 for k in range(max(0, int(round(mu[0])) - 2), int(round(mu[0])) + 3)]
    resumen = {k: v for k, v in (calidad or {}).items() if k != "lineas"} or None
    return {
        "esperados": {"local": round(mh, 2), "visitante": round(ma, 2), "total": round(float(mu[0]), 2)},
        "lineas": [
            {
                "linea": L,
                "mas": _r4(_p_mas(mu, r, L)[0]),
                "menos": _r4(1 - _p_mas(mu, r, L)[0]),
                "veredicto": por_linea.get(L, {}).get("veredicto", "sin_validar"),
                "frecuencia_historica": por_linea.get(L, {}).get("frecuencia_historica"),
                "partidos_prueba": por_linea.get(L, {}).get("partidos_prueba"),
            }
            for L in lineas
        ],
        "calidad": resumen,
    }


def _pendientes(hist: Historial, bloques: dict) -> list[dict]:
    """Mercados que no se pudieron calcular, con el motivo (dato ausente en la fuente o historial corto)."""
    pend = []
    for nombre in ESTADISTICAS:
        clave = "primer_tiempo" if nombre == "goles_1t" else nombre
        if bloques.get(clave) is not None:
            continue
        cob = hist.cobertura[nombre]
        if cob < COBERTURA_EXTRA:
            motivo = f"FootyStats trae {NOMBRE_ESTADISTICA[nombre]} en solo el {cob:.0%} de los partidos terminados de esta competición (mínimo {COBERTURA_EXTRA:.0%}). Es un dato ausente en la fuente, no un cero."
        else:
            motivo = f"Hay menos de {MIN_PARTIDOS_LIGA} partidos con {NOMBRE_ESTADISTICA[nombre]} registrados antes de esta fecha."
        pend.append({"mercado": clave, "nombre": NOMBRE_ESTADISTICA[nombre], "motivo": motivo})
    return pend


def _bloque_primer_tiempo(hist: Historial, previo: np.ndarray, local: int, visita: int, calidad: dict | None) -> dict | None:
    if hist.cobertura["goles_1t"] < COBERTURA_EXTRA:
        return None
    ajuste = _ajustar_conteo(hist, previo, "goles_1t", VIDA_EXTRA, ALFA_EXTRA)
    if ajuste is None:
        return None
    modelo, _ = ajuste
    mh, ma = _lambdas(modelo, hist, np.array([local]), np.array([visita]))
    M = _matriz(mh, ma)[0]
    i, j = _I, _J
    return {
        "resultado": {"local": _r4(M[i > j].sum()), "empate": _r4(M[i == j].sum()), "visitante": _r4(M[i < j].sum())},
        "mas_0_5": _r4(M[(i + j) >= 1].sum()),
        "mas_1_5": _r4(M[(i + j) >= 2].sum()),
        "ambos_marcan": _r4(M[(i >= 1) & (j >= 1)].sum()),
        "goles_esperados": {"local": round(float(mh[0]), 2), "visitante": round(float(ma[0]), 2)},
        "calidad": calidad,
    }


def _bloque_cuotas(q: dict | None) -> dict | None:
    """Lo que opina el mercado (cuotas de FootyStats sin margen) para comparar con el modelo."""
    q = q or {}
    r1, r2, r3 = _normalizar(q.get("1"), q.get("x"), q.get("2")), _normalizar(q.get("o25"), q.get("u25")), _normalizar(q.get("by"), q.get("bn"))
    if not (r1 or r2 or r3):
        return None
    return {
        "resultado_1x2": {"local": _r4(r1[0][0]), "empate": _r4(r1[0][1]), "visitante": _r4(r1[0][2]), "margen": _r4(r1[1]), "cuotas": [q["1"], q["x"], q["2"]]} if r1 else None,
        "mas_2_5": {"si": _r4(r2[0][0]), "no": _r4(r2[0][1]), "margen": _r4(r2[1]), "cuotas": [q["o25"], q["u25"]]} if r2 else None,
        "ambos_marcan": {"si": _r4(r3[0][0]), "no": _r4(r3[0][1]), "margen": _r4(r3[1]), "cuotas": [q["by"], q["bn"]]} if r3 else None,
    }


def _insuficiente(partido: dict, detalle: list[str], extra: dict | None = None) -> dict:
    return {
        "estado": "historial_insuficiente",
        "mensaje": "Historial insuficiente para este partido",
        "detalle": detalle,
        "partido": partido,
        **(extra or {}),
    }


def _fecha(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


async def predecir_partido(season_id: int, match_id: int, detalle: bool = False) -> dict | None:
    """
    Predicción para un partido de FootyStats. None si el partido no existe en esa temporada.
    Con detalle=False solo el resumen (1X2, más de 2.5, ambos marcan); con detalle=True todos los mercados.
    """
    liga = await _liga_de_temporada(season_id)
    if not liga:
        return None
    temporadas = liga["temporadas"]
    pos = next(i for i, t in enumerate(temporadas) if t["id"] == season_id)
    usadas = temporadas[max(0, pos - HISTORIAL_TEMPORADAS + 1) : pos + 1]
    ids = [t["id"] for t in usadas]

    datos = [await footystats.historial_temporada(i) for i in ids]
    objetivo = next((p for p in datos[-1]["partidos"] if p["id"] == match_id), None)
    if objetivo is None:  # puede ser un partido añadido después de la última descarga
        datos[-1] = await footystats.historial_temporada(season_id, forzar=True)
        objetivo = next((p for p in datos[-1]["partidos"] if p["id"] == match_id), None)
        if objetivo is None:
            return None

    partido = {
        "id": objetivo["id"],
        "local": objetivo["hn"],
        "visitante": objetivo["an"],
        "fecha_unix": objetivo["t"],
        "jugado": objetivo["s"] == "complete",
        "liga": f"{liga['pais']} – {liga['nombre']}",
    }
    todos = [dict(p, sid=d["season_id"]) for d in datos for p in d["partidos"]]
    hist = Historial(todos)
    previo = hist.antes_de(objetivo["t"])  # solo lo anterior al partido
    desactualizado = any(d.get("desactualizado") for d in datos)

    # ¿hay historial suficiente?
    cuenta = np.bincount(np.concatenate([hist.h[previo], hist.a[previo]]), minlength=len(hist.equipos)) if len(previo) else np.zeros(len(hist.equipos), dtype=int)
    faltas = []
    if len(previo) < MIN_PARTIDOS_LIGA:
        faltas.append(f"La competición tiene {len(previo)} partidos terminados antes de esta fecha (mínimo {MIN_PARTIDOS_LIGA}) en las últimas {len(usadas)} temporadas.")
    for rol, eid, nombre in (("local", objetivo["h"], objetivo["hn"]), ("visitante", objetivo["a"], objetivo["an"])):
        n_eq = int(cuenta[hist.equipos[eid]]) if eid in hist.equipos else 0
        if n_eq < MIN_PARTIDOS_EQUIPO:
            faltas.append(f"{nombre} ({rol}) tiene {n_eq} partidos previos terminados en esta competición (mínimo {MIN_PARTIDOS_EQUIPO}); suele pasar con equipos recién ascendidos o que vienen de otra liga.")
    if faltas:
        return _insuficiente(partido, faltas)

    validacion = await validacion_liga(ids, hist, con_extra=detalle)
    params = {k: validacion.get(k, DEFECTO[k]) for k in DEFECTO} if validacion.get("estado") in ("medida", "insuficiente_para_medir") else dict(DEFECTO)
    if params["variante"] == "xg" and hist.cobertura_xg < COBERTURA_XG:
        params["variante"] = "goles"

    def calcular():
        modelo = _ajustar(hist, previo, params["vida_media_dias"], params["alfa"], params["variante"])
        lh, la = _lambdas(modelo, hist, np.array([hist.equipos[objetivo["h"]]]), np.array([hist.equipos[objetivo["a"]]]))
        mercados = None
        if detalle:
            extra = validacion.get("extra") or {}
            eq_l, eq_v = hist.equipos[objetivo["h"]], hist.equipos[objetivo["a"]]
            mercados = _mercados_goles(_matriz(lh, la)[0])
            mercados["primer_tiempo"] = _bloque_primer_tiempo(hist, previo, eq_l, eq_v, extra.get("goles_1t"))
            for nombre in ("corners", "tarjetas", "tiros", "tiros_puerta"):
                mercados[nombre] = _bloque_conteo(hist, previo, nombre, eq_l, eq_v, extra.get(nombre))
            mercados["pendientes"] = _pendientes(hist, mercados)
        return float(lh[0]), float(la[0]), _probabilidades(lh, la)[0], mercados

    lh, la, p, mercados = await asyncio.to_thread(calcular)
    respuesta = {
        "estado": "ok",
        "partido": partido,
        "resultado_1x2": {"local": round(float(p[0]), 4), "empate": round(float(p[1]), 4), "visitante": round(float(p[2]), 4)},
        "mas_2_5": {"si": round(float(p[3]), 4), "no": round(float(1 - p[3]), 4)},
        "ambos_marcan": {"si": round(float(p[4]), 4), "no": round(float(1 - p[4]), 4)},
        "goles_esperados": {"local": round(lh, 2), "visitante": round(la, 2)},
        "mercados": mercados,
        "cuotas": _bloque_cuotas(objetivo.get("q")) if detalle else None,
        "potenciales_footystats": ({k: v for k, v in (objetivo.get("pot") or {}).items() if v is not None} or None) if detalle else None,
        "detalle": detalle,
        "historial": {
            "partidos": int(len(previo)),
            "temporadas": [t["etiqueta"] for t in usadas],
            "desde": _fecha(float(hist.t[previo[0]])),
            "hasta": _fecha(float(hist.t[previo[-1]])),
            "partidos_local": int(cuenta[hist.equipos[objetivo["h"]]]),
            "partidos_visitante": int(cuenta[hist.equipos[objetivo["a"]]]),
            "desactualizado": desactualizado,
        },
        "modelo": params,
        "calidad": validacion,
    }
    # Antes del inicio, se guarda lo que se mostró para poder abrirlo luego como "Pronóstico previo"
    await asyncio.to_thread(pronosticos_previos.guardar, match_id, season_id, objetivo["t"], respuesta, detalle)
    return respuesta


# ─────────────────────────────────────────────
#  PREDICCIÓN EN VIVO
# ─────────────────────────────────────────────
PUNTOS_CONTROL = (0.0, 15.0, 30.0, vivo_modelo.FIN_1T, 60.0, 75.0, 85.0)  # minutos en los que se reconstruye el estado histórico
VERSION_EN_VIVO = 1


def _perfil(hist: Historial, idx: np.ndarray) -> vivo_modelo.Perfil:
    """Minutos de los goles (90') de los partidos idx: cuándo suelen marcarse los goles en esta liga."""
    minutos = [m for i in idx for lista in (hist.ml[i], hist.mv[i]) if lista is not None for m in lista]
    return vivo_modelo.Perfil(minutos)


def _validar_en_vivo(hist: Historial, params: dict) -> dict:
    """
    Evalúa la predicción en vivo con estados REALES de partidos ya jugados, usando solo lo disponible en cada momento:
      * la fuerza previa de los equipos se ajusta con los partidos anteriores al encuentro (mismo esquema que el modelo previo);
      * el marcador en el minuto t se reconstruye con los minutos de los goles marcados hasta t;
      * el perfil de minutos de gol sale solo del bloque de entrenamiento.
    Se compara con una referencia sencilla de la misma liga: la misma mecánica con los goles medios de la liga
    (sin distinguir equipos). Se mide el resultado a 90' y "más de 2.5" en el último 20 % de los partidos.
    """
    n = hist.n
    i_test = int(n * FRAC_PRUEBA)
    con_minutos = [i for i in range(n) if hist.ml[i] is not None and hist.mv[i] is not None]
    if len(con_minutos) < MIN_PARTIDOS_VALIDAR:
        return {"v": VERSION_EN_VIVO, "estado": "experimental", "motivo": f"FootyStats trae los minutos de gol de solo {len(con_minutos)} partidos de esta competición; no alcanza para validar con estados históricos."}
    entren = np.array([i for i in con_minutos if i < i_test])
    perfil = _perfil(hist, entren)
    lam_ref_l, lam_ref_v = float(hist.hg[:i_test].mean()), float(hist.ag[:i_test].mean())

    perd_m, perd_r = [], []  # por partido: media de log loss en los puntos de control (resultado 90', más de 2.5)
    por_punto = {p: [[], [], [], []] for p in PUNTOS_CONTROL}
    for s0 in range(i_test, n, BLOQUE):
        previo = hist.antes_de(hist.t[s0])
        if len(previo) < MIN_PARTIDOS_LIGA:
            continue
        cuenta = np.bincount(np.concatenate([hist.h[previo], hist.a[previo]]), minlength=len(hist.equipos))
        bloque = [i for i in range(s0, min(s0 + BLOQUE, n)) if hist.ml[i] is not None and hist.mv[i] is not None and cuenta[hist.h[i]] >= MIN_PARTIDOS_EQUIPO and cuenta[hist.a[i]] >= MIN_PARTIDOS_EQUIPO]
        if not bloque:
            continue
        modelo = _ajustar(hist, previo, params["vida_media_dias"], params["alfa"], params["variante"])
        lh, la = _lambdas(modelo, hist, hist.h[bloque], hist.a[bloque])
        for k, i in enumerate(bloque):
            fl, fv = vivo_modelo.goles_90(hist.ml[i]), vivo_modelo.goles_90(hist.mv[i])
            clase = 0 if fl > fv else 1 if fl == fv else 2
            mas = fl + fv >= 3
            lm, lr = [], []
            for p in PUNTOS_CONTROL:
                gl, gv = vivo_modelo.goles_a(hist.ml[i], p), vivo_modelo.goles_a(hist.mv[i], p)
                for lam_l, lam_v, destino, idx_pp in ((float(lh[k]), float(la[k]), lm, 0), (lam_ref_l, lam_ref_v, lr, 2)):
                    r = vivo_modelo.prediccion(lam_l, lam_v, gl, gv, p, perfil)
                    p1x2 = [r["resultado_90"]["local"], r["resultado_90"]["empate"], r["resultado_90"]["visitante"]][clase]
                    pm = r["mas_2_5"]["prob"] if mas else 1 - r["mas_2_5"]["prob"]
                    ll = (-math.log(max(p1x2, 1e-9)), -math.log(max(pm, 1e-9)))
                    destino.append(ll)
                    por_punto[p][idx_pp].append(ll[0])
                    por_punto[p][idx_pp + 1].append(ll[1])
            perd_m.append(np.mean(lm, axis=0))
            perd_r.append(np.mean(lr, axis=0))
    if not perd_m:
        return {"v": VERSION_EN_VIVO, "estado": "experimental", "motivo": "No hay partidos de prueba con historial previo suficiente."}
    pm_, pr_ = np.array(perd_m), np.array(perd_r)
    m = len(pm_)
    suficiente = m >= MIN_PRUEBA
    mercados = {}
    for k, nombre in enumerate(("resultado_90", "mas_2_5")):
        media, error, veredicto = _veredicto(pr_[:, k] - pm_[:, k], suficiente)
        mercados[nombre] = {
            "logloss_modelo": round(float(pm_[:, k].mean()), 4),
            "logloss_referencia": round(float(pr_[:, k].mean()), 4),
            "mejora": round(media, 4),
            "error_estandar": round(error, 4),
            "veredicto": veredicto,
        }
    puntos = [
        {
            "minuto": "descanso" if p == vivo_modelo.FIN_1T else int(p),
            "logloss_resultado_modelo": round(float(np.mean(v[0])), 4),
            "logloss_resultado_referencia": round(float(np.mean(v[2])), 4),
            "logloss_mas_2_5_modelo": round(float(np.mean(v[1])), 4),
            "logloss_mas_2_5_referencia": round(float(np.mean(v[3])), 4),
        }
        for p, v in por_punto.items()
        if v[0]
    ]
    return {
        "v": VERSION_EN_VIVO,
        "estado": "validada" if suficiente else "experimental",
        "motivo": None if suficiente else f"Solo {m} partidos de prueba con minutos de gol (mínimo {MIN_PRUEBA}).",
        "partidos_prueba": m,
        "estados_evaluados": m * len(PUNTOS_CONTROL),
        "perfil_minutos": perfil.origen,
        "goles_en_perfil": perfil.n,
        "mercados": mercados,
        "por_minuto": puntos,
        "referencia": "misma mecánica con los goles medios de la liga (sin distinguir equipos)",
    }


def _etiqueta_en_vivo(v: dict) -> str:
    """Solo se dice "validada" si se evaluó con estados históricos y el resultado a 90' superó a la referencia."""
    if v.get("estado") != "validada":
        return "Estimación en vivo experimental"
    veredicto = (v.get("mercados") or {}).get("resultado_90", {}).get("veredicto")
    return {
        "supera_referencia": "Validada con estados históricos: supera la referencia",
        "similar_a_referencia": "Evaluada con estados históricos: sin ventaja demostrada sobre la referencia",
        "peor_que_referencia": "Evaluada con estados históricos: peor que la referencia",
    }.get(veredicto, "Estimación en vivo experimental")


async def _contexto(season_id: int, match_id: int):
    """Liga, temporadas usadas, historial y partido objetivo (mismo criterio que predecir_partido)."""
    liga = await _liga_de_temporada(season_id)
    if not liga:
        return None
    temporadas = liga["temporadas"]
    pos = next(i for i, t in enumerate(temporadas) if t["id"] == season_id)
    ids = [t["id"] for t in temporadas[max(0, pos - HISTORIAL_TEMPORADAS + 1) : pos + 1]]
    datos = [await footystats.historial_temporada(i) for i in ids]
    objetivo = next((p for p in datos[-1]["partidos"] if p["id"] == match_id), None)
    if objetivo is None:
        return None
    hist = Historial([dict(p, sid=d["season_id"]) for d in datos for p in d["partidos"]])
    return liga, ids, hist, objetivo


async def prediccion_en_vivo(season_id: int, match_id: int, vivo: dict) -> dict | None:
    """
    Predicción con el marcador confirmado en vivo. `vivo` es el estado que entrega la fuente en vivo:
    {fase, minuto, minuto_extra, marcador{local, visitante}, resultado_90, prorroga, penales, dato_unix, consultado_unix}.
    """
    previa = await predecir_partido(season_id, match_id)
    if previa is None:
        return None
    if previa.get("estado") != "ok":
        return {"estado": "historial_insuficiente", "mensaje": previa.get("mensaje"), "detalle": previa.get("detalle"), "partido": previa.get("partido")}
    ctx = await _contexto(season_id, match_id)
    if ctx is None:
        return None
    liga, ids, hist, objetivo = ctx
    validacion = await validacion_liga(ids, hist, con_en_vivo=True)
    previo = hist.antes_de(objetivo["t"])
    perfil = _perfil(hist, previo)

    fase = vivo.get("fase")
    marcador = vivo.get("marcador") or {}
    if fase in vivo_modelo.FASES_CERRADAS_90:
        r90 = vivo.get("resultado_90")  # en prórroga o penales, el resultado a 90' ya está cerrado
        if not r90:
            return {"estado": "sin_resultado_90", "mensaje": "La fuente en vivo no informa el resultado al final de los 90 minutos."}
        gl, gv = r90["local"], r90["visitante"]
    else:
        gl, gv = marcador.get("local"), marcador.get("visitante")
    p = vivo_modelo.posicion(fase, vivo.get("minuto"), vivo.get("minuto_extra"))
    if gl is None or gv is None or p is None:
        return {"estado": "sin_datos_confirmados", "mensaje": "No hay marcador y minuto confirmados para calcular la predicción en vivo."}

    lam_l, lam_v = previa["goles_esperados"]["local"], previa["goles_esperados"]["visitante"]
    r = vivo_modelo.prediccion(lam_l, lam_v, int(gl), int(gv), p, perfil)
    guardado = pronosticos_previos.obtener(match_id)
    v_en_vivo = (validacion or {}).get("en_vivo") or {"estado": "experimental", "motivo": "Sin validación con estados históricos."}
    return {
        "estado": "ok",
        "partido": previa["partido"],
        "calculado_con": {
            "fase": fase,
            "minuto": vivo.get("minuto"),
            "minuto_extra": vivo.get("minuto_extra"),
            "marcador": {"local": int(gl), "visitante": int(gv)},
            "marcador_actual": marcador,  # en prórroga puede diferir del de 90'
            "dato_unix": vivo.get("dato_unix"),
            "consultado_unix": vivo.get("consultado_unix"),
        },
        "estimado_unix": int(reloj.ahora()),
        "resultado_90": {k: round(v, 4) for k, v in r["resultado_90"].items()},
        "mas_2_5": {**r["mas_2_5"], "prob": round(r["mas_2_5"]["prob"], 4)},
        "ambos_marcan": {**r["ambos_marcan"], "prob": round(r["ambos_marcan"]["prob"], 4)},
        "marcadores_finales": [{**x, "p": round(x["p"], 4)} for x in r["marcadores"]],
        "goles_restantes_esperados": {k: round(v, 2) for k, v in r["goles_restantes_esperados"].items()},
        "fuerza_previa": {"local": lam_l, "visitante": lam_v},
        "perfil_minutos": perfil.origen,
        "tiempo_extra": {"prorroga": vivo.get("prorroga"), "penales": vivo.get("penales")},
        "validacion": v_en_vivo,
        "etiqueta": _etiqueta_en_vivo(v_en_vivo),
        # el pronóstico previo se conserva aparte para compararlos
        "pronostico_previo": (guardado or {}).get("resumen") or previa,
        "pronostico_previo_origen": "guardado antes del inicio" if guardado else "modelo previo recalculado con datos anteriores al inicio",
    }
