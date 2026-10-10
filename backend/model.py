"""
Modelo ML de La Liga (PrediLiga) — v3.

Combina dos modelos complementarios:
  1. Fuerza de equipo (Dixon-Coles): ataque y defensa de cada equipo estimados con una regresión
     de Poisson sobre goles y xG (75 % xG), dando más peso a los partidos recientes.
     Da los goles esperados, la matriz de marcadores, Over 2.5 y ambos marcan.
  2. Clasificador 1X2 (HistGradientBoosting) con solo 5 variables: diferencia de Elo y forma
     reciente de xG a favor y en contra de cada equipo.
  (+ LSTM opcional si TensorFlow está instalado y mejora la validación.)
  3. Mercados de apuesta ampliados: de la matriz de marcadores salen doble oportunidad, hándicap,
     líneas de goles, ambos marcan, portería a cero, combinados y marcador exacto; y un modelo de
     conteo (Poisson por equipo + Binomial Negativa) da córners, tarjetas, faltas, tiros, tiros a
     puerta y fueras de juego. Cada mercado lleva su probabilidad, su cuota justa (1 / probabilidad,
     sin margen de la casa) y un nivel de fiabilidad medido en la validación partido a partido.

Elección validada partido a partido (cada predicción usa solo partidos anteriores): en la temporada
2025-26, que no se usó para elegir nada, log loss 0.974 frente a 0.987 del modelo anterior.

API pública (compatible con app.py):
    obtener_equipos()
    predecir_partidos(local, visitante, dia_semana="sabado")

El CSV se busca junto a este archivo, o en la ruta de la variable de entorno LALIGA_CSV.
Orden de clases en todas las matrices de probabilidad: [visitante(0), local(1), empate(2)].
"""
from __future__ import annotations

import os
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import PoissonRegressor
from sklearn.metrics import accuracy_score, f1_score, log_loss
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=ConvergenceWarning)

# ─────────────────────────────────────────────
#  CONFIGURACIÓN (valores elegidos en validación 2024-25)
# ─────────────────────────────────────────────
CSV_PATH = Path(os.environ.get("LALIGA_CSV", Path(__file__).with_name("laliga_es_final.csv")))

# Elo
ELO_K = 15                  # velocidad de actualización
ELO_VENTAJA_LOCAL = 50      # puntos de Elo que vale jugar en casa
ELO_ARRASTRE = 0.80         # fracción del rating que se conserva al cambiar de temporada
REGRESAR_ELO_SI_NUEVA_TEMPORADA = True  # al predecir en una temporada sin datos aún, aplicar la regresión

# Forma reciente (media exponencial)
EWM_VIDA_MEDIA = 6          # partidos
EWM_MIN = 3

# Fuerza de equipo
RATING_PESO_XG = 0.75       # objetivo = 75 % xG + 25 % goles (el xG es menos ruidoso que los goles)
RATING_DECAIMIENTO = 0.006  # por día: un partido de hace ~4 meses pesa la mitad
RATING_ALPHA = 3e-4         # regularización
DC_RHO = -0.05              # corrección Dixon-Coles de marcadores bajos
MAX_GOLES = 10
LAMBDA_MIN, LAMBDA_MAX = 0.2, 4.5

# Mezcla final
W_RATINGS = 0.50            # peso del modelo de fuerza de equipo (el clasificador recibe el resto)
W_LSTM = 0.20               # peso del LSTM si se activa

# Evaluación walk-forward
TEMPORADAS_EVAL = 2         # se evalúan las últimas N temporadas, partido a partido
BLOQUE_EVAL = 38            # se reentrena cada 38 partidos (~4 jornadas)

# Mercados de conteo: columna del CSV, decaimiento temporal (xi) y regularización (alpha) elegidos en la
# validación 2024-25 (confirmados en 2025-26), y las líneas con las que se mide cada mercado.
MERCADOS_CONTEO = {
    "corners": dict(
        titulo="Córners", unidad="córners", col="match_overview_corner_kicks",
        xi=0.006, alpha=0.03, lineas=[8.5, 9.5, 10.5, 11.5, 12.5]),
    "amarillas": dict(
        titulo="Tarjetas amarillas", unidad="tarjetas amarillas", col="match_overview_yellow_cards",
        xi=0.003, alpha=0.03, lineas=[2.5, 3.5, 4.5, 5.5, 6.5]),
    "rojas": dict(
        titulo="Tarjeta roja", unidad="tarjetas rojas", col="match_overview_red_cards",
        xi=0.003, alpha=0.03, lineas=[0.5], n_total=1, n_equipo=1),
    "faltas": dict(
        titulo="Faltas", unidad="faltas", col="match_overview_fouls",
        xi=0.006, alpha=0.03, lineas=[21.5, 23.5, 25.5, 27.5, 29.5]),
    "tiros": dict(
        titulo="Tiros totales", unidad="tiros", col="match_overview_total_shots",
        xi=0.003, alpha=0.03, lineas=[20.5, 22.5, 24.5, 26.5, 28.5]),
    "a_puerta": dict(
        titulo="Tiros a puerta", unidad="tiros a puerta", col="shots_shots_on_target",
        xi=0.003, alpha=0.03, lineas=[6.5, 7.5, 8.5, 9.5, 10.5]),
    "fueras_juego": dict(
        titulo="Fueras de juego", unidad="fueras de juego", col="attack_offsides",
        xi=0.006, alpha=0.03, lineas=[2.5, 3.5, 4.5, 5.5]),
}
DESTACADO_PROB_MIN, DESTACADO_PROB_MAX = 0.55, 0.78   # "destacados": probables pero con cuota que aún paga algo
DESTACADOS_MAX, DESTACADOS_POR_GRUPO = 6, 1
LINEAS_GOLES_TOTAL = [0.5, 1.5, 2.5, 3.5, 4.5]
LINEAS_GOLES_EQUIPO = [0.5, 1.5, 2.5]
# Fiabilidad de un mercado = cuánto mejora el log loss frente a la frecuencia histórica de ese mercado
# (media de la mejora partido a partido y su intervalo de confianza al 95 %).
NIVEL_ALTA_MEJORA = 0.03
NIVEL_MEDIA_MEJORA = 0.015

CLF_FEATURES = ["elo_diff", "D_xg_f", "D_xg_a", "xg_ataque_local", "xg_ataque_visit"]
LSTM_STEPS = 5


# ─────────────────────────────────────────────
#  1. CARGA
# ─────────────────────────────────────────────
df = pd.read_csv(CSV_PATH)
df = df.drop(columns=[c for c in df.columns if c.startswith("Unnamed")])
df["fecha"] = pd.to_datetime(df["fecha"])
_orden = ["fecha", "match_id"] if "match_id" in df.columns else ["fecha"]
df = df.sort_values(_orden, kind="stable").reset_index(drop=True).copy()

# Etiqueta 1X2 desde los goles (en laliga_es_final.csv "resultado" usa 0=visitante, 1=empate, 2=local)
_res = np.where(df["goles_local"] > df["goles_visitante"], 1,
                np.where(df["goles_local"] < df["goles_visitante"], 0, 2))
if "resultado" in df.columns and not (df["resultado"].to_numpy() == _res).all():
    print("Aviso: 'resultado' del CSV no usa la codificación interna (0=visitante, 1=local, 2=empate); "
          "se recalcula a partir de los goles.")
df["resultado"] = _res
df["season"] = np.where(df["fecha"].dt.month >= 7, df["fecha"].dt.year, df["fecha"].dt.year - 1)

_XG = "match_overview_expected_goals"
if f"{_XG}_local" not in df.columns or f"{_XG}_visitante" not in df.columns:
    print("Aviso: el CSV no tiene xG; se usan los goles en su lugar.")
    df[f"{_XG}_local"] = df["goles_local"].astype(float)
    df[f"{_XG}_visitante"] = df["goles_visitante"].astype(float)

lista_equipos = sorted(set(df["local"]) | set(df["visitante"]))
_N = len(df)
y_all = df["resultado"].to_numpy()
_ULTIMA_FECHA = df["fecha"].max()
_ULTIMA_TEMPORADA = int(df["season"].max())


def _temporada(f) -> int:
    return f.year if f.month >= 7 else f.year - 1


# ─────────────────────────────────────────────
#  2. HISTORIAL POR EQUIPO (formato largo) Y FORMA RECIENTE
# ─────────────────────────────────────────────
_STATS = {
    "g": "goles",
    "xg": _XG,
    "sot": "shots_shots_on_target",
    "bc": "match_overview_big_chances",
    "poss": "match_overview_ball_possession",
}
_STATS = {k: v for k, v in _STATS.items() if f"{v}_local" in df.columns and f"{v}_visitante" in df.columns}


def _formato_largo(d: pd.DataFrame) -> pd.DataFrame:
    partes = []
    for lado, otro, es_local in (("local", "visitante", 1), ("visitante", "local", 0)):
        x = pd.DataFrame({
            "midx": d.index.to_numpy(),
            "season": d["season"].to_numpy(),
            "team": d[lado].to_numpy(),
            "home": es_local,
        })
        for k, base in _STATS.items():
            x[k + "_f"] = d[f"{base}_{lado}"].astype(float).to_numpy()   # a favor
            x[k + "_a"] = d[f"{base}_{otro}"].astype(float).to_numpy()   # en contra
        x["pts"] = np.where(x["g_f"] > x["g_a"], 3.0, np.where(x["g_f"] == x["g_a"], 1.0, 0.0))
        partes.append(x)
    return pd.concat(partes, ignore_index=True).sort_values(["team", "midx"], kind="stable").reset_index(drop=True)


_long = _formato_largo(df)

# Forma de xG con shift(1): el valor de un partido solo usa partidos anteriores
_ew = _long.groupby("team")[["xg_f", "xg_a"]].transform(
    lambda s: s.shift(1).ewm(halflife=EWM_VIDA_MEDIA, min_periods=EWM_MIN).mean()
)
_long["e_xg_f"], _long["e_xg_a"] = _ew["xg_f"], _ew["xg_a"]


# ─────────────────────────────────────────────
#  3. ELO
# ─────────────────────────────────────────────
_EQUIPOS_TEMPORADA = {int(s): set(g["local"]) | set(g["visitante"]) for s, g in df.groupby("season")}


def _regresar(r: dict, equipos: set) -> None:
    media = float(np.mean([r[t] for t in equipos]))
    for t in equipos:
        r[t] = media + ELO_ARRASTRE * (r[t] - media)


def _calcular_elo(d: pd.DataFrame):
    """Elo antes de cada partido. Al cambiar de temporada regresa hacia la media; los ascendidos
    entran con el rating medio de los descendidos."""
    r: dict[str, float] = {}
    pre_l = np.zeros(len(d))
    pre_v = np.zeros(len(d))
    actual = None
    for i, (h, a, gl, gv, s) in enumerate(zip(d["local"], d["visitante"], d["goles_local"], d["goles_visitante"], d["season"])):
        if s != actual:
            if actual is not None:
                prev, nuevos = _EQUIPOS_TEMPORADA[actual], _EQUIPOS_TEMPORADA[s]
                _regresar(r, prev)
                bajan = [r[t] for t in prev - nuevos]
                valor_ascenso = float(np.mean(bajan)) if bajan else float(np.mean([r[t] for t in prev])) - 75
                for t in nuevos - prev:
                    r[t] = valor_ascenso
            actual = s
        rh, ra = r.setdefault(h, 1500.0), r.setdefault(a, 1500.0)
        pre_l[i], pre_v[i] = rh, ra
        esperado = 1 / (1 + 10 ** (-(rh + ELO_VENTAJA_LOCAL - ra) / 400))
        real = 1.0 if gl > gv else (0.5 if gl == gv else 0.0)
        margen = abs(gl - gv)
        delta = ELO_K * (np.log(margen + 1) + 1 if margen else 1.0) * (real - esperado)
        r[h], r[a] = rh + delta, ra - delta
    return pre_l, pre_v, r


_elo_l, _elo_v, _ELO_FINAL = _calcular_elo(df)


# ─────────────────────────────────────────────
#  4. VARIABLES DEL CLASIFICADOR (misma función para entrenar y predecir)
# ─────────────────────────────────────────────
def _vector_features(elo_l, elo_v, l_xgf, l_xga, v_xgf, v_xga) -> pd.DataFrame:
    return pd.DataFrame({
        "elo_diff": np.asarray(elo_l, float) - np.asarray(elo_v, float),
        "D_xg_f": np.asarray(l_xgf, float) - np.asarray(v_xgf, float),
        "D_xg_a": np.asarray(l_xga, float) - np.asarray(v_xga, float),
        "xg_ataque_local": np.asarray(l_xgf, float) + np.asarray(v_xga, float),  # ataque local vs defensa visitante
        "xg_ataque_visit": np.asarray(v_xgf, float) + np.asarray(l_xga, float),
    })[CLF_FEATURES]


_H = _long[_long["home"] == 1].set_index("midx").sort_index()
_A = _long[_long["home"] == 0].set_index("midx").sort_index()
X_all = _vector_features(_elo_l, _elo_v, _H["e_xg_f"], _H["e_xg_a"], _A["e_xg_f"], _A["e_xg_a"])


def _make_clf() -> HistGradientBoostingClassifier:
    # Pocas variables y mucha regularización: con ~1.100 partidos, más variables solo añadían ruido.
    return HistGradientBoostingClassifier(
        max_iter=100, learning_rate=0.03, max_depth=2, min_samples_leaf=60,
        l2_regularization=10.0, early_stopping=False, random_state=42,
    )


def _ajustar_clf(idx: np.ndarray):
    Xt = X_all.iloc[idx]
    ok = Xt.notna().all(axis=1).to_numpy()
    relleno = Xt[ok].median()
    modelo = _make_clf().fit(Xt[ok], y_all[idx][ok])
    if list(modelo.classes_) != [0, 1, 2]:
        raise ValueError(f"Clases inesperadas en el clasificador: {modelo.classes_}")
    return modelo, relleno


# ─────────────────────────────────────────────
#  5. FUERZA DE EQUIPO (Poisson con ataque/defensa por equipo, estilo Dixon-Coles)
# ─────────────────────────────────────────────
_TEAM_IDX = {t: i for i, t in enumerate(lista_equipos)}
_NT = len(lista_equipos)


def _ascendidos(season: int) -> set:
    prev = _EQUIPOS_TEMPORADA.get(season - 1)
    return set() if prev is None else _EQUIPOS_TEMPORADA.get(season, set()) - prev


_ASC = {s: _ascendidos(s) for s in _EQUIPOS_TEMPORADA}
_promo_l = np.array([t in _ASC[s] for t, s in zip(df["local"], df["season"])], dtype=float)
_promo_v = np.array([t in _ASC[s] for t, s in zip(df["visitante"], df["season"])], dtype=float)
_dias = (df["fecha"] - df["fecha"].min()).dt.days.to_numpy(float)
_hi = df["local"].map(_TEAM_IDX).to_numpy()
_ai = df["visitante"].map(_TEAM_IDX).to_numpy()
_gl = df["goles_local"].to_numpy(float)
_gv = df["goles_visitante"].to_numpy(float)
_xgl = df[f"{_XG}_local"].to_numpy(float)
_xgv = df[f"{_XG}_visitante"].to_numpy(float)


def _diseno(hi, ai, pl, pv):
    """Una fila por equipo atacante: [ataque(equipo) | defensa(rival) | es_local | ascendido_ataca | ascendido_defiende]."""
    hi, ai = np.atleast_1d(hi), np.atleast_1d(ai)
    pl, pv = np.atleast_1d(pl).astype(float), np.atleast_1d(pv).astype(float)
    m, r = len(hi), np.arange(len(hi))
    Xh = np.zeros((m, 2 * _NT + 3))
    Xa = np.zeros((m, 2 * _NT + 3))
    Xh[r, hi] = 1; Xh[r, _NT + ai] = 1; Xh[:, 2 * _NT] = 1; Xh[:, 2 * _NT + 1] = pl; Xh[:, 2 * _NT + 2] = pv
    Xa[r, ai] = 1; Xa[r, _NT + hi] = 1; Xa[:, 2 * _NT + 1] = pv; Xa[:, 2 * _NT + 2] = pl
    return Xh, Xa


def _ajustar_ratings(idx: np.ndarray, dia_ref: float) -> PoissonRegressor:
    Xh, Xa = _diseno(_hi[idx], _ai[idx], _promo_l[idx], _promo_v[idx])
    obj_h = RATING_PESO_XG * _xgl[idx] + (1 - RATING_PESO_XG) * _gl[idx]
    obj_a = RATING_PESO_XG * _xgv[idx] + (1 - RATING_PESO_XG) * _gv[idx]
    peso = np.exp(-RATING_DECAIMIENTO * (dia_ref - _dias[idx]))
    with threadpool_limits(limits=1):  # con muchos hilos de BLAS estos ajustes pequeños van ~15 veces más lentos
        return PoissonRegressor(alpha=RATING_ALPHA, max_iter=3000).fit(
            np.vstack([Xh, Xa]), np.r_[obj_h, obj_a], sample_weight=np.r_[peso, peso]
        )


def _lambdas(modelo: PoissonRegressor, hi, ai, pl, pv):
    Xh, Xa = _diseno(hi, ai, pl, pv)
    return (np.clip(modelo.predict(Xh), LAMBDA_MIN, LAMBDA_MAX),
            np.clip(modelo.predict(Xa), LAMBDA_MIN, LAMBDA_MAX))


# Matriz de marcadores (Poisson + corrección Dixon-Coles), vectorizada
_K = np.arange(MAX_GOLES + 1)
_I, _J = np.meshgrid(_K, _K, indexing="ij")  # filas = goles local, columnas = goles visitante


def _goal_probs(lam_l, lam_v, rho: float = DC_RHO) -> dict:
    lam_l = np.atleast_1d(np.asarray(lam_l, float))
    lam_v = np.atleast_1d(np.asarray(lam_v, float))
    M = poisson.pmf(_K[None, :], lam_l[:, None])[:, :, None] * poisson.pmf(_K[None, :], lam_v[:, None])[:, None, :]
    M[:, 0, 0] *= 1 - lam_l * lam_v * rho
    M[:, 0, 1] *= 1 + lam_l * rho
    M[:, 1, 0] *= 1 + lam_v * rho
    M[:, 1, 1] *= 1 - rho
    M = np.clip(M, 0.0, None)
    M /= M.sum(axis=(1, 2), keepdims=True)
    return {
        "M": M,
        "p_local": (M * (_I > _J)).sum(axis=(1, 2)),
        "p_draw": (M * (_I == _J)).sum(axis=(1, 2)),
        "p_visit": (M * (_I < _J)).sum(axis=(1, 2)),
        "btts": (M * ((_I >= 1) & (_J >= 1))).sum(axis=(1, 2)),
        "over25": (M * ((_I + _J) >= 3)).sum(axis=(1, 2)),
    }


def _probs_1x2(gp: dict) -> np.ndarray:
    return np.column_stack([gp["p_visit"], gp["p_local"], gp["p_draw"]])


_REGION = (_I < _J, _I > _J, _I == _J)  # columnas de las probabilidades: visitante, local, empate


def _ajustar_matriz(M: np.ndarray, p3: np.ndarray) -> np.ndarray:
    """Reescala la matriz de marcadores para que su 1X2 coincida con el 1X2 mezclado (M: n x 11 x 11,
    p3: n x 3 en orden visitante, local, empate). Así hándicap, combinados y marcador exacto son
    coherentes con las probabilidades de victoria que se muestran. En la validación no cambia la
    calidad de Over/Under ni de ambos marcan (log loss igual hasta el 3er decimal)."""
    out = M.copy()
    for c, region in enumerate(_REGION):
        actual = (M * region).sum(axis=(1, 2))
        factor = np.asarray(p3)[:, c] / np.maximum(actual, 1e-12)
        out = np.where(region[None], out * factor[:, None, None], out)
    return out


def _mezclar(p_rat: np.ndarray, p_clf: np.ndarray, p_lstm: np.ndarray | None = None) -> np.ndarray:
    p = W_RATINGS * p_rat + (1 - W_RATINGS) * p_clf
    if p_lstm is not None:
        p = (1 - W_LSTM) * p + W_LSTM * p_lstm
    return p / p.sum(axis=1, keepdims=True)


def _ll(y, p) -> float:
    return float(log_loss(y, np.clip(p, 1e-6, 1), labels=[0, 1, 2]))


def _ll_bin(y, p) -> float:
    return float(log_loss(np.asarray(y).astype(int), np.clip(p, 1e-6, 1 - 1e-6), labels=[0, 1]))


def _rps(y, p) -> float:
    """Ranked probability score con el orden visitante < empate < local (menor es mejor)."""
    orden = np.asarray(p)[:, [0, 2, 1]]
    pos = np.select([y == 0, y == 2, y == 1], [0, 1, 2])
    real = np.zeros_like(orden)
    real[np.arange(len(y)), pos] = 1
    return float((((np.cumsum(orden, 1) - np.cumsum(real, 1))[:, :2]) ** 2).sum(1).mean() / 2)


# ─────────────────────────────────────────────
#  6. EVALUACIÓN WALK-FORWARD
#     Se predicen las últimas temporadas por bloques; cada bloque se predice con modelos
#     entrenados SOLO con los partidos anteriores (como en la vida real).
# ─────────────────────────────────────────────
_temporadas = sorted(_EQUIPOS_TEMPORADA)
if len(_temporadas) > TEMPORADAS_EVAL:
    _EVAL_INI = int(df.index[df["season"].isin(_temporadas[-TEMPORADAS_EVAL:])].min())
else:
    _EVAL_INI = int(_N * 0.5)
_EVAL_IDX = np.arange(_EVAL_INI, _N)

print(f"Partidos: {_N} | evaluación partido a partido: {len(_EVAL_IDX)} "
      f"(desde {df['fecha'].iloc[_EVAL_INI].date()}) | datos hasta {_ULTIMA_FECHA.date()}")
print("Evaluando (walk-forward)...")

_p_rat_eval, _p_clf_eval, _lam_eval = [], [], []
for _ini in range(_EVAL_INI, _N, BLOQUE_EVAL):
    _tr = np.arange(_ini)
    _pr = np.arange(_ini, min(_ini + BLOQUE_EVAL, _N))
    _mr = _ajustar_ratings(_tr, _dias[_ini])
    _lh, _la = _lambdas(_mr, _hi[_pr], _ai[_pr], _promo_l[_pr], _promo_v[_pr])
    _p_rat_eval.append(_probs_1x2(_goal_probs(_lh, _la)))
    _lam_eval.append(np.column_stack([_lh, _la]))
    _mc, _fill = _ajustar_clf(_tr)
    _p_clf_eval.append(_mc.predict_proba(X_all.iloc[_pr].fillna(_fill)))

p_rat_eval = np.vstack(_p_rat_eval)
p_clf_eval = np.vstack(_p_clf_eval)
lam_eval = np.vstack(_lam_eval)
y_eval = y_all[_EVAL_IDX]


# ─────────────────────────────────────────────
#  7. LSTM MANY-TO-ONE (opcional)
#     Se entrena con los partidos anteriores a la evaluación y solo entra en la mezcla
#     si mejora el log loss de la mezcla en la evaluación. Si entra, se reentrena con todo.
# ─────────────────────────────────────────────
_LSTM_COLS = [c for c in ["g_f", "g_a", "xg_f", "xg_a", "sot_f", "sot_a", "bc_f", "bc_a", "poss_f", "pts", "home"]
              if c in _long.columns]
_team_hist = {
    t: (g["midx"].to_numpy(), g[_LSTM_COLS].fillna(0.0).to_numpy(np.float32))
    for t, g in _long.groupby("team")
}


def _secuencia_equipo(team: str, antes_de: int) -> np.ndarray:
    n_feat = len(_LSTM_COLS)
    if team not in _team_hist:
        return np.zeros((LSTM_STEPS, n_feat), np.float32)
    midx, feats = _team_hist[team]
    i = int(np.searchsorted(midx, antes_de, side="left"))
    seq = feats[max(0, i - LSTM_STEPS): i]
    if len(seq) < LSTM_STEPS:
        seq = np.vstack([np.zeros((LSTM_STEPS - len(seq), n_feat), np.float32), seq])
    return seq


def _secuencia_partido(local: str, visitante: str, antes_de: int) -> np.ndarray:
    sl, sv = _secuencia_equipo(local, antes_de), _secuencia_equipo(visitante, antes_de)
    return np.concatenate([sl, sv, sl - sv], axis=1)


def _entrenar_lstm(idx: np.ndarray):
    """Devuelve (modelo, escalador) o None si no hay TensorFlow."""
    try:
        import tensorflow as tf
        from tensorflow.keras.callbacks import EarlyStopping
        from tensorflow.keras.layers import LSTM, Dense, Dropout, Input
        from tensorflow.keras.models import Sequential
    except Exception as e:
        print(f"LSTM desactivada: {e}")
        return None
    tf.random.set_seed(42)
    np.random.seed(42)
    Xs = np.stack([_secuencia_partido(df["local"].iat[i], df["visitante"].iat[i], i) for i in idx])
    n_steps, n_feat = Xs.shape[1], Xs.shape[2]
    esc = StandardScaler().fit(Xs.reshape(-1, n_feat))
    Xs = esc.transform(Xs.reshape(-1, n_feat)).reshape(-1, n_steps, n_feat)
    modelo = Sequential([
        Input(shape=(n_steps, n_feat)), LSTM(32), Dropout(0.3),
        Dense(16, activation="relu"), Dropout(0.2), Dense(3, activation="softmax"),
    ])
    modelo.compile(optimizer="adam", loss="sparse_categorical_crossentropy")
    modelo.fit(Xs, y_all[idx], epochs=60, batch_size=32, verbose=0, validation_split=0.15,
               callbacks=[EarlyStopping(monitor="val_loss", patience=6, restore_best_weights=True)])
    return modelo, esc


def _lstm_proba(paquete, secuencias: np.ndarray) -> np.ndarray:
    modelo, esc = paquete
    n, n_steps, n_feat = secuencias.shape
    Xs = esc.transform(secuencias.reshape(-1, n_feat)).reshape(n, n_steps, n_feat)
    p = modelo.predict(Xs, verbose=0).astype(float)
    return p / p.sum(axis=1, keepdims=True)  # la salida float32 de softmax no suma exactamente 1


_lstm = None
_lstm_enabled = False
_lstm_accuracy = _lstm_f1 = _lstm_logloss = None
_paq = _entrenar_lstm(np.arange(_EVAL_INI))
if _paq is not None:
    _seq_eval = np.stack([_secuencia_partido(df["local"].iat[i], df["visitante"].iat[i], i) for i in _EVAL_IDX])
    p_lstm_eval = _lstm_proba(_paq, _seq_eval)
    _lstm_accuracy = float(accuracy_score(y_eval, p_lstm_eval.argmax(1)))
    _lstm_f1 = float(f1_score(y_eval, p_lstm_eval.argmax(1), average="weighted"))
    _lstm_logloss = _ll(y_eval, p_lstm_eval)
    _ll_sin = _ll(y_eval, _mezclar(p_rat_eval, p_clf_eval))
    _ll_con = _ll(y_eval, _mezclar(p_rat_eval, p_clf_eval, p_lstm_eval))
    _lstm_enabled = _ll_con < _ll_sin
    print(f"LSTM {'ACTIVADA' if _lstm_enabled else 'descartada (no mejora la mezcla)'} | "
          f"LogLoss solo LSTM {_lstm_logloss:.3f} | mezcla sin {_ll_sin:.4f} / con {_ll_con:.4f}")
    if _lstm_enabled:
        _lstm = _entrenar_lstm(np.arange(_N))  # reentrenar con todos los partidos para producción
else:
    p_lstm_eval = None


# ─────────────────────────────────────────────
#  8. MÉTRICAS DE LA EVALUACIÓN
# ─────────────────────────────────────────────
p_eval = _mezclar(p_rat_eval, p_clf_eval, p_lstm_eval if _lstm_enabled else None)
_pred = p_eval.argmax(1)
accuracy = float(accuracy_score(y_eval, _pred))
f1 = float(f1_score(y_eval, _pred, average="weighted"))
logloss = _ll(y_eval, p_eval)
rps = _rps(y_eval, p_eval)

# Referencia: frecuencias de todos los partidos anteriores (lo que sabría alguien sin modelo)
_cum = np.cumsum(np.eye(3)[y_all], axis=0)
_prior = np.vstack([_cum[i - 1] / i for i in _EVAL_IDX])
baseline_logloss = _ll(y_eval, _prior)
baseline_acc = float((y_eval == _prior.argmax(1)).mean())

_gp_eval = _goal_probs(lam_eval[:, 0], lam_eval[:, 1])
_gl_e, _gv_e = _gl[_EVAL_IDX], _gv[_EVAL_IDX]
_real_o25 = (_gl_e + _gv_e) > 2.5
_real_btts = (_gl_e > 0) & (_gv_e > 0)
mae_goles_total = float(np.abs(_gl_e + _gv_e - lam_eval.sum(1)).mean())
acc_over25 = float(((_gp_eval["over25"] > 0.5) == _real_o25).mean())
acc_btts = float(((_gp_eval["btts"] > 0.5) == _real_btts).mean())

print(f"Fuerza de equipo | LogLoss {_ll(y_eval, p_rat_eval):.4f}   Clasificador Elo+xG | LogLoss {_ll(y_eval, p_clf_eval):.4f}")
print(f"MEZCLA FINAL     | LogLoss {logloss:.4f} | RPS {rps:.4f} | Accuracy {accuracy:.3f} | F1 {f1:.3f}"
      f"  || Referencia: LogLoss {baseline_logloss:.4f} | Accuracy {baseline_acc:.3f}")
print(f"Goles            | MAE total {mae_goles_total:.3f} | Over2.5 Acc {acc_over25:.3f} LogLoss "
      f"{_ll_bin(_real_o25, _gp_eval['over25']):.3f} | BTTS Acc {acc_btts:.3f} LogLoss {_ll_bin(_real_btts, _gp_eval['btts']):.3f}")


# ─────────────────────────────────────────────
#  8b. MERCADOS DE CONTEO (córners, tarjetas, faltas, tiros...) Y FIABILIDAD DE CADA MERCADO
#      Cada equipo tiene una "tasa" propia de cada estadística (regresión de Poisson con la misma
#      estructura que los goles). El total del partido se modela con una Binomial Negativa, cuya
#      dispersión se mide en los datos (así recoge que ciertas estadísticas varían más que un Poisson).
# ─────────────────────────────────────────────
def _cargar_conteos() -> dict:
    out = {}
    for k, cfg in MERCADOS_CONTEO.items():
        cl, cv = f"{cfg['col']}_local", f"{cfg['col']}_visitante"
        if cl in df.columns and cv in df.columns:
            L, V = df[cl].to_numpy(float), df[cv].to_numpy(float)
            if np.isfinite(L).mean() > 0.9 and np.nansum(L + V) > 0:
                out[k] = (L, V)
    return out


_CONT = _cargar_conteos()


def _dispersion(mu: np.ndarray, x: np.ndarray) -> float:
    """r de la Binomial Negativa (varianza = mu + mu²/r) por el método de los momentos."""
    den = float(np.sum((x - mu) ** 2 - mu))
    r = float(np.sum(mu ** 2)) / den if den > 1e-6 else 1e6
    return float(min(max(r, 1.0), 1e6))


def _ajustar_conteo(stat: str, idx: np.ndarray, dia_ref: float):
    """Devuelve (modelo, r del total, r por equipo)."""
    cfg, (L, V) = MERCADOS_CONTEO[stat], _CONT[stat]
    idx = idx[np.isfinite(L[idx]) & np.isfinite(V[idx])]
    Xh, Xa = _diseno(_hi[idx], _ai[idx], _promo_l[idx], _promo_v[idx])
    peso = np.exp(-cfg["xi"] * (dia_ref - _dias[idx]))
    with threadpool_limits(limits=1):
        modelo = PoissonRegressor(alpha=cfg["alpha"], max_iter=3000).fit(
            np.vstack([Xh, Xa]), np.r_[L[idx], V[idx]], sample_weight=np.r_[peso, peso]
        )
        mh, ma = modelo.predict(Xh), modelo.predict(Xa)
    return modelo, _dispersion(mh + ma, L[idx] + V[idx]), _dispersion(np.r_[mh, ma], np.r_[L[idx], V[idx]])


def _mu_conteo(modelo: PoissonRegressor, hi, ai, pl, pv):
    Xh, Xa = _diseno(hi, ai, pl, pv)
    return np.clip(modelo.predict(Xh), 0.02, 80.0), np.clip(modelo.predict(Xa), 0.02, 80.0)


def _p_mas_de(mu, r, linea: float) -> np.ndarray:
    """P(X > linea) con X ~ Binomial Negativa de media mu y dispersión r (Poisson si r es enorme)."""
    mu, r = np.broadcast_arrays(np.atleast_1d(np.asarray(mu, float)), np.atleast_1d(np.asarray(r, float)))
    k, out = np.floor(linea), np.empty(mu.shape)
    pois = r >= 1e5
    out[pois] = poisson.sf(k, mu[pois])
    out[~pois] = nbinom.sf(k, r[~pois], r[~pois] / (r[~pois] + mu[~pois]))
    return out


def _mejora(y, p, base) -> np.ndarray:
    """Por partido: log loss de la frecuencia histórica menos log loss del modelo (positivo = el modelo aporta)."""
    y = np.asarray(y, float)
    p, base = np.clip(p, 1e-4, 1 - 1e-4), np.clip(base, 1e-3, 1 - 1e-3)
    return -(y * np.log(base) + (1 - y) * np.log(1 - base)) + (y * np.log(p) + (1 - y) * np.log(1 - p))


def _base_expansiva(indicador: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Frecuencia de la jugada en todos los partidos anteriores a cada partido de idx."""
    return np.cumsum(np.nan_to_num(indicador.astype(float)))[idx - 1] / idx


def _resumen(mejora_por_partido: np.ndarray) -> dict:
    x = np.asarray(mejora_por_partido, float)
    x = x[np.isfinite(x)]
    rng = np.random.default_rng(0)
    medias = x[rng.integers(0, len(x), (1000, len(x)))].mean(axis=1)
    media, lo = float(x.mean()), float(np.percentile(medias, 2.5))
    nivel = "alta" if (lo > 0 and media >= NIVEL_ALTA_MEJORA) else (
        "media" if (lo > 0 or media >= NIVEL_MEDIA_MEJORA) else "baja")
    return {"nivel": nivel, "mejora": round(media, 4), "ic_inferior": round(lo, 4), "partidos": int(len(x))}


# Evaluación walk-forward de los mercados de conteo (mismos bloques y mismos partidos que el 1X2)
_FIAB: dict[str, dict] = {}
for _k in _CONT:
    _L, _V = _CONT[_k]
    _mus, _rs = [], []
    for _ini in range(_EVAL_INI, _N, BLOQUE_EVAL):
        _pr = np.arange(_ini, min(_ini + BLOQUE_EVAL, _N))
        _m, _r_tot, _ = _ajustar_conteo(_k, np.arange(_ini), _dias[_ini])
        _mh, _ma = _mu_conteo(_m, _hi[_pr], _ai[_pr], _promo_l[_pr], _promo_v[_pr])
        _mus.append(_mh + _ma)
        _rs.append(np.full(len(_pr), _r_tot))
    _mu_e, _r_e = np.concatenate(_mus), np.concatenate(_rs)
    _tot = _L + _V
    _filas = []
    for _linea in MERCADOS_CONTEO[_k]["lineas"]:
        _ind = _tot > _linea
        _filas.append(_mejora(_ind[_EVAL_IDX], _p_mas_de(_mu_e, _r_e, _linea),
                              _base_expansiva(_ind, _EVAL_IDX))[np.isfinite(_tot[_EVAL_IDX])])
    _FIAB[_k] = _resumen(np.mean(_filas, axis=0))

# Fiabilidad de los mercados de goles (Over/Under y ambos marcan, con la matriz ajustada) y del 1X2
_M_eval = _ajustar_matriz(_gp_eval["M"], p_eval)
_T_goles = _I + _J
_goles_totales = _gl + _gv
_filas = []
for _linea in LINEAS_GOLES_TOTAL:
    _filas.append(_mejora((_goles_totales > _linea)[_EVAL_IDX], (_M_eval * (_T_goles > _linea)).sum(axis=(1, 2)),
                          _base_expansiva(_goles_totales > _linea, _EVAL_IDX)))
_ind_btts = (_gl > 0) & (_gv > 0)
_filas.append(_mejora(_ind_btts[_EVAL_IDX], (_M_eval * ((_I >= 1) & (_J >= 1))).sum(axis=(1, 2)),
                      _base_expansiva(_ind_btts, _EVAL_IDX)))
_FIAB["goles"] = _resumen(np.mean(_filas, axis=0))
_nll = lambda p: -np.log(np.clip(p[np.arange(len(y_eval)), y_eval], 1e-6, 1))  # noqa: E731
_FIAB["resultado"] = _resumen(_nll(_prior) - _nll(p_eval))

print("Mercados         | fiabilidad (mejora de log loss sobre la frecuencia histórica): " + " · ".join(
    f"{k} {v['nivel']} ({v['mejora']:+.3f})" for k, v in _FIAB.items()))


# ─────────────────────────────────────────────
#  9. MODELOS FINALES (todos los partidos) Y ESTADO ACTUAL DE CADA EQUIPO
# ─────────────────────────────────────────────
_TODO = np.arange(_N)
_ratings_final = _ajustar_ratings(_TODO, _dias.max())
_clf_final, _FILL = _ajustar_clf(_TODO)
_conteo_final = {k: _ajustar_conteo(k, _TODO, _dias.max()) for k in _CONT}

# Forma de xG incluyendo el último partido de cada equipo
_XG_ACTUAL = {
    t: g[["xg_f", "xg_a"]].ewm(halflife=EWM_VIDA_MEDIA, min_periods=EWM_MIN).mean().iloc[-1].to_dict()
    for t, g in _long.groupby("team")
}

# Elo actual; si ya empezó una temporada de la que no hay datos, se aplica la regresión de pretemporada
_ELO_ACTUAL = dict(_ELO_FINAL)
_TEMPORADA_HOY = _temporada(date.today())
_TEMPORADA_NUEVA = _TEMPORADA_HOY > _ULTIMA_TEMPORADA
if _TEMPORADA_NUEVA and REGRESAR_ELO_SI_NUEVA_TEMPORADA:
    _regresar(_ELO_ACTUAL, _EQUIPOS_TEMPORADA[_ULTIMA_TEMPORADA])

# Marca de "recién ascendido" tal como aparece en los últimos datos de cada equipo. Se mantiene aunque
# haya empezado otra temporada: el ataque/defensa de esos equipos se estimó junto con esa marca, y
# quitarla inflaría sus goles esperados (p. ej. Oviedo-Levante pasaba de 2.5 a 3.4 goles).
_ASC_ACTUAL = _ASC.get(_ULTIMA_TEMPORADA, set())

if (pd.Timestamp(date.today()) - _ULTIMA_FECHA).days > 60:
    print(f"Aviso: los datos terminan el {_ULTIMA_FECHA.date()}. Actualiza el CSV para tener en cuenta "
          "los partidos y ascendidos más recientes.")
print("Modelos finales listos.")


def _resolver_equipo(nombre: str) -> str | None:
    n = (nombre or "").strip().lower()
    return next((t for t in lista_equipos if t.lower() == n), None)


# ─────────────────────────────────────────────
#  9b. CONSTRUCCIÓN DE LOS MERCADOS DE APUESTA DE UN PARTIDO
# ─────────────────────────────────────────────
_ORDEN_NIVEL = {"baja": 0, "media": 1, "alta": 2}
_NOTA_NIVEL = {
    "alta": "El modelo supera con claridad a la frecuencia histórica en la validación.",
    "media": "El modelo supera a la frecuencia histórica, pero por poco margen.",
    "baja": "El modelo apenas mejora a la frecuencia histórica: úsalo solo como referencia.",
}


def _peor(*niveles: str) -> str:
    return min(niveles, key=_ORDEN_NIVEL.__getitem__)


def _cuota(p: float):
    """Cuota justa = 1 / probabilidad (sin margen de la casa). Sin valor si la probabilidad es < 1 %."""
    return round(1.0 / p, 2) if p >= 0.01 else None


def _item(nombre: str, p: float, nota: str | None = None) -> dict:
    d = {"nombre": nombre, "prob": round(float(p), 4), "cuota": _cuota(float(p))}
    if nota:
        d["nota"] = nota
    return d


def _fila_linea(linea: float, p_over: float) -> dict:
    return {"linea": linea, "over": round(p_over, 4), "under": round(1 - p_over, 4),
            "cuota_over": _cuota(p_over), "cuota_under": _cuota(1 - p_over)}


def _lineas_centradas(mu: float, n: int) -> list[float]:
    """n líneas x.5 consecutivas alrededor del valor esperado (nunca por debajo de 0.5)."""
    ini = max(0.5, float(np.floor(mu)) + 0.5 - n // 2)
    return [ini + i for i in range(n)]


def _grupo(id_: str, titulo: str, nivel: str, tipo: str, validacion: dict | None = None, **resto) -> dict:
    g = {"id": id_, "titulo": titulo, "fiabilidad": nivel, "tipo": tipo, "nota": _NOTA_NIVEL[nivel]}
    if validacion:
        g["validacion"] = {"partidos": validacion["partidos"], "mejora_logloss": validacion["mejora"]}
    g.update(resto)
    return g


def _construir_mercados(local: str, visitante: str, M: np.ndarray, p3: tuple, lam: tuple, conteos: dict) -> dict:
    """M: matriz de marcadores ya ajustada al 1X2 (filas = goles local); p3 = (visitante, local, empate)."""
    pv, pl, pe = p3
    I, J, T = _I, _J, _I + _J

    def P(mascara) -> float:
        return float((M * mascara).sum())

    n1x2 = _FIAB["resultado"]["nivel"]
    ngol = _FIAB["goles"]["nivel"]
    grupos = []

    # ── Resultado ──
    grupos.append(_grupo("resultado", "Resultado", n1x2, "lista", _FIAB["resultado"], items=[
        _item(f"Gana {local}", pl),
        _item("Empate", pe),
        _item(f"Gana {visitante}", pv),
        _item(f"Doble oportunidad: {local} o empate", pl + pe),
        _item("Doble oportunidad: sin empate", pl + pv),
        _item(f"Doble oportunidad: {visitante} o empate", pv + pe),
        _item(f"Empate no válido: {local}", pl / (pl + pv), "Si hay empate se devuelve la apuesta"),
        _item(f"Empate no válido: {visitante}", pv / (pl + pv), "Si hay empate se devuelve la apuesta"),
    ]))

    # ── Hándicap y margen de victoria ──
    grupos.append(_grupo("handicap", "Hándicap y margen de victoria", n1x2, "lista", _FIAB["resultado"], items=[
        _item(f"{local} -1.5 (gana por 2+ goles)", P(I - J >= 2)),
        _item(f"{local} +1.5 (no pierde por 2+)", P(I - J >= -1)),
        _item(f"{visitante} -1.5 (gana por 2+ goles)", P(J - I >= 2)),
        _item(f"{visitante} +1.5 (no pierde por 2+)", P(J - I >= -1)),
        _item(f"{local} gana por 1 gol", P(I - J == 1)),
        _item(f"{local} gana por 2 goles", P(I - J == 2)),
        _item(f"{local} gana por 3 o más", P(I - J >= 3)),
        _item(f"{visitante} gana por 1 gol", P(J - I == 1)),
        _item(f"{visitante} gana por 2 goles", P(J - I == 2)),
        _item(f"{visitante} gana por 3 o más", P(J - I >= 3)),
    ]))

    # ── Goles (líneas) ──
    def serie(ambito, etiqueta, lineas, prob_over):
        return {"ambito": ambito, "etiqueta": etiqueta, "lineas": [_fila_linea(l, prob_over(l)) for l in lineas]}

    grupos.append(_grupo(
        "goles", "Goles", ngol, "lineas", _FIAB["goles"],
        esperado={"local": round(lam[0], 2), "visitante": round(lam[1], 2), "total": round(lam[0] + lam[1], 2)},
        series=[
            serie("total", "Total del partido", LINEAS_GOLES_TOTAL, lambda l: P(T > l)),
            serie("local", local, LINEAS_GOLES_EQUIPO, lambda l: P(I > l)),
            serie("visitante", visitante, LINEAS_GOLES_EQUIPO, lambda l: P(J > l)),
        ]))

    # ── Ambos marcan y portería a cero ──
    grupos.append(_grupo("ambos_marcan", "Ambos marcan y portería a cero", ngol, "lista", _FIAB["goles"], items=[
        _item("Ambos marcan: Sí", P((I >= 1) & (J >= 1))),
        _item("Ambos marcan: No", P((I == 0) | (J == 0))),
        _item(f"{local} marca", P(I >= 1)),
        _item(f"{visitante} marca", P(J >= 1)),
        _item(f"{local} mantiene la portería a cero", P(J == 0)),
        _item(f"{visitante} mantiene la portería a cero", P(I == 0)),
        _item("Ninguno marca (0-0)", P((I == 0) & (J == 0))),
    ]))

    # ── Combinados ──
    ncomb = _peor(n1x2, ngol)
    grupos.append(_grupo("combinados", "Combinados", ncomb, "lista", None, items=[
        _item(f"{local} gana y más de 2.5 goles", P((I > J) & (T > 2.5))),
        _item(f"{local} gana y menos de 2.5 goles", P((I > J) & (T < 2.5))),
        _item(f"{visitante} gana y más de 2.5 goles", P((J > I) & (T > 2.5))),
        _item(f"{visitante} gana y menos de 2.5 goles", P((J > I) & (T < 2.5))),
        _item("Empate y más de 2.5 goles", P((I == J) & (T > 2.5))),
        _item("Empate y menos de 2.5 goles", P((I == J) & (T < 2.5))),
        _item(f"{local} gana y ambos marcan", P((I > J) & (J >= 1))),
        _item(f"{visitante} gana y ambos marcan", P((J > I) & (I >= 1))),
        _item(f"{local} gana sin encajar", P((I > J) & (J == 0))),
        _item(f"{visitante} gana sin encajar", P((J > I) & (I == 0))),
        _item("Ambos marcan y más de 2.5 goles", P((I >= 1) & (J >= 1) & (T > 2.5))),
        _item("Ambos marcan y menos de 2.5 goles", P((I >= 1) & (J >= 1) & (T < 2.5))),
    ]))

    # ── Marcador y goles exactos ──
    top = np.argsort(M, axis=None)[::-1][:8]
    exactos = [_item(f"Marcador {i // M.shape[1]}-{i % M.shape[1]}", M.flat[i]) for i in top]
    exactos += [_item(f"Exactamente {g} gol{'es' if g != 1 else ''} en el partido", P(T == g)) for g in range(5)]
    exactos.append(_item("5 o más goles en el partido", P(T >= 5)))
    grupos.append(_grupo("exactos", "Marcador y goles exactos", "baja", "lista", None, items=exactos,
                         nota="Acertar un marcador exacto es muy difícil: incluso el más probable falla la mayoría de las veces."))

    # ── Mercados de conteo ──
    for k, (mu_l, mu_v, r_tot, r_eq) in conteos.items():
        cfg, f = MERCADOS_CONTEO[k], _FIAB[k]
        n_t, n_e = cfg.get("n_total", 5), cfg.get("n_equipo", 3)
        mu_t = mu_l + mu_v
        grupos.append(_grupo(
            k, cfg["titulo"], f["nivel"], "lineas", f,
            esperado={"local": round(mu_l, 2), "visitante": round(mu_v, 2), "total": round(mu_t, 2)},
            series=[
                serie("total", "Total del partido", _lineas_centradas(mu_t, n_t), lambda l: float(_p_mas_de(mu_t, r_tot, l)[0])),
                serie("local", local, _lineas_centradas(mu_l, n_e), lambda l: float(_p_mas_de(mu_l, r_eq, l)[0])),
                serie("visitante", visitante, _lineas_centradas(mu_v, n_e), lambda l: float(_p_mas_de(mu_v, r_eq, l)[0])),
            ]))

    # ── Destacados: mercados con probabilidad alta pero cuota que aún paga, solo de grupos con fiabilidad suficiente ──
    unidades = {"goles": "goles", **{k: c["unidad"] for k, c in MERCADOS_CONTEO.items()}}
    candidatos = []
    for g in grupos:
        if g["fiabilidad"] == "baja" or g["id"] == "exactos":
            continue
        propios = [(it["nombre"], it["prob"], it["cuota"]) for it in g.get("items", [])]
        for se in g.get("series", []):
            de = "" if se["ambito"] == "total" else f"{se['etiqueta']}: "
            u = unidades.get(g["id"], "")
            for fl in se["lineas"]:
                propios.append((f"{de}más de {fl['linea']} {u}".strip(), fl["over"], fl["cuota_over"]))
                propios.append((f"{de}menos de {fl['linea']} {u}".strip(), fl["under"], fl["cuota_under"]))
        mejores = sorted((c for c in propios if DESTACADO_PROB_MIN <= c[1] <= DESTACADO_PROB_MAX and c[2]),
                         key=lambda c: -c[1])[:DESTACADOS_POR_GRUPO]
        candidatos += [{"grupo": g["id"], "grupo_titulo": g["titulo"], "nombre": n[0].upper() + n[1:], "prob": pr,
                        "cuota": cu, "fiabilidad": g["fiabilidad"]} for n, pr, cu in mejores]
    destacados = sorted(candidatos, key=lambda c: (-_ORDEN_NIVEL[c["fiabilidad"]], -c["prob"]))[:DESTACADOS_MAX]

    return {
        "destacados": destacados,
        "nota": "Cuota justa = 1 / probabilidad del modelo, sin el margen de la casa de apuestas. "
                "Si la cuota real es mayor que la justa, el modelo ve valor; si es menor, no. "
                "La fiabilidad mide cuánto mejora el modelo a la frecuencia histórica en partidos que no vio al entrenar; "
                "no es una garantía de acierto.",
        "validacion_partidos": int(len(_EVAL_IDX)),
        "grupos": grupos,
    }


# ─────────────────────────────────────────────
#  10. API PÚBLICA
# ─────────────────────────────────────────────
def obtener_equipos():
    return lista_equipos


def predecir_partidos(local_in, visitante_in, dia_semana="sabado"):
    # `dia_semana` se conserva por compatibilidad con app.py; el modelo no lo usa.
    try:
        local = _resolver_equipo(local_in)
        visitante = _resolver_equipo(visitante_in)
        if not local:
            return {"error": f"Equipo local '{local_in}' no encontrado"}
        if not visitante:
            return {"error": f"Equipo visitante '{visitante_in}' no encontrado"}
        if local == visitante:
            return {"error": "Los equipos local y visitante deben ser distintos"}

        elo_l, elo_v = _ELO_ACTUAL[local], _ELO_ACTUAL[visitante]
        xl, xv = _XG_ACTUAL[local], _XG_ACTUAL[visitante]

        # 1) Clasificador Elo + xG
        X = _vector_features([elo_l], [elo_v], [xl["xg_f"]], [xl["xg_a"]], [xv["xg_f"]], [xv["xg_a"]]).fillna(_FILL)
        p_clf = _clf_final.predict_proba(X)

        # 2) Fuerza de equipo -> goles esperados y matriz de marcadores
        hi, ai = _TEAM_IDX[local], _TEAM_IDX[visitante]
        pl, pv = float(local in _ASC_ACTUAL), float(visitante in _ASC_ACTUAL)
        lh, la = _lambdas(_ratings_final, [hi], [ai], [pl], [pv])
        lam_l, lam_v = float(lh[0]), float(la[0])
        gp = _goal_probs(lam_l, lam_v)

        # 3) LSTM opcional
        p_lstm = None
        if _lstm_enabled and _lstm is not None:
            p_lstm = _lstm_proba(_lstm, _secuencia_partido(local, visitante, _N)[None, ...])

        p_visit, p_local, p_draw = (float(v) for v in _mezclar(_probs_1x2(gp), p_clf, p_lstm)[0])

        # Matriz de marcadores coherente con el 1X2 mezclado: de aquí salen marcadores, Over/Under y mercados
        M = _ajustar_matriz(gp["M"], np.array([[p_visit, p_local, p_draw]]))[0]
        top = np.argsort(M, axis=None)[::-1][:3]
        top_marcadores = [
            {"marcador": f"{i // M.shape[1]}-{i % M.shape[1]}", "prob": round(float(M.flat[i]), 4)} for i in top
        ]
        p_over25 = float((M * ((_I + _J) >= 3)).sum())
        p_btts = float((M * ((_I >= 1) & (_J >= 1))).sum())

        conteos = {}
        for k, (mod, r_tot, r_eq) in _conteo_final.items():
            mh, ma = _mu_conteo(mod, [hi], [ai], [pl], [pv])
            conteos[k] = (float(mh[0]), float(ma[0]), r_tot, r_eq)
        mercados = _construir_mercados(local, visitante, M, (p_visit, p_local, p_draw), (lam_l, lam_v), conteos)

        return {
            "equipo_local": local,
            "equipo_visitante": visitante,
            "prob_local": round(p_local, 4),
            "prob_empate": round(p_draw, 4),
            "prob_visitante": round(p_visit, 4),
            "goles_esperados": {
                "local": round(lam_l, 2),
                "visitante": round(lam_v, 2),
                "total": round(lam_l + lam_v, 2),
            },
            "marcador_probable": top_marcadores[0]["marcador"],
            "top_marcadores": top_marcadores,
            "recomendacion": "Mas de 2.5 goles" if p_over25 >= 0.5 else "Menos de 2.5 goles",
            "over_2_5": round(p_over25, 4),
            "ambos_marcan": round(p_btts, 4),
            "elo_local": round(elo_l),
            "elo_visitante": round(elo_v),
            # Métricas de la evaluación partido a partido (y la referencia sin modelo)
            "accuracy_modelo": round(accuracy, 3),
            "f1_modelo": round(f1, 3),
            "logloss_modelo": round(logloss, 3),
            "rps_modelo": round(rps, 4),
            "accuracy_referencia": round(baseline_acc, 3),
            "logloss_referencia": round(baseline_logloss, 3),
            "partidos_evaluados": int(len(_EVAL_IDX)),
            "datos_hasta": str(_ULTIMA_FECHA.date()),
            "lstm_integrado": p_lstm is not None,
            "lstm_accuracy": round(_lstm_accuracy, 3) if _lstm_accuracy is not None else None,
            "lstm_f1": round(_lstm_f1, 3) if _lstm_f1 is not None else None,
            "mercados": mercados,
        }

    except Exception as e:
        import traceback

        traceback.print_exc()
        return {"error": str(e)}