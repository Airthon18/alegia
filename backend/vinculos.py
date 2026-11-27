"""
Relación entre los partidos de FootyStats (calendario, historial, modelo) y los de API-Football (marcador en vivo).

Un partido de API-Football se asigna a uno de FootyStats solo si coinciden:
  * la hora de inicio en UTC (tolerancia de 20 min),
  * el país de la competición (y, una vez aprendida, la competición y la temporada),
  * el equipo LOCAL con el local y el VISITANTE con el visitante (nombres normalizados y parecidos),
y el emparejamiento es claro en ambos sentidos. Si hay dos candidatos parecidos, no se asigna: vale más mostrar
"Estado por confirmar" que poner el marcador en el partido equivocado.

Los vínculos confirmados (partido y competición) se guardan en disco y se reutilizan por ID.
"""
from __future__ import annotations

import difflib
import json
import re
import threading
import unicodedata

import footystats

TOLERANCIA_HORA_S = 20 * 60
UMBRAL_EQUIPO = 0.6  # parecido mínimo de cada equipo
MARGEN_AMBIGUEDAD = 0.12  # el mejor candidato debe superar al segundo por este margen
_ARCHIVO = "vinculos_api_football.json"
CON_DISCO = True  # las pruebas lo desactivan
_lock = threading.Lock()
_mem: dict | None = None

_RUIDO = {"fc", "cf", "sc", "ac", "afc", "cd", "ud", "sd", "ca", "cs", "fk", "sk", "if", "bk", "club", "de", "del", "la", "el", "the", "sad", "ssc", "as", "sv", "vfb", "vfl", "tsg", "rcd", "rc", "deportivo", "deportes"}
# País de FootyStats (normalizado) -> país de API-Football (normalizado)
_PAISES = {
    "europe": "world",
    "southamerica": "world",
    "africa": "world",
    "international": "world",
    "republicofireland": "ireland",
    "uae": "unitedarabemirates",
    "usa": "usa",
}


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def pais_norm(p: str | None) -> str:
    base = re.sub(r"[^a-z]", "", _ascii(p or ""))
    return _PAISES.get(base, base)


def equipo_norm(nombre: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", _ascii(nombre)) if t not in _RUIDO]


def _misma_palabra(x: str, y: str) -> bool:
    """"munchen"/"munich", "atletico"/"atletico": iguales o casi; nunca palabras cortas distintas."""
    return x == y or (min(len(x), len(y)) >= 4 and difflib.SequenceMatcher(None, x, y).ratio() >= 0.75)


def parecido(a: str, b: str) -> float:
    """
    0..1: palabras significativas en común sobre las del nombre más largo. Tolera "Bayern München" / "Bayern Munich" y
    siglas como "FC", pero no confunde equipos que solo comparten ciudad ("Real Madrid" / "Atlético Madrid" = 0,5).
    """
    ta, tb = equipo_norm(a), equipo_norm(b)
    if not ta or not tb:
        return 0.0
    libres = list(tb)
    comunes = 0
    for x in ta:
        par = next((y for y in libres if _misma_palabra(x, y)), None)
        if par is not None:
            libres.remove(par)
            comunes += 1
    return comunes / max(len(ta), len(tb))


def _ruta():
    return footystats.CACHE_DIR / _ARCHIVO


def _cargar() -> dict:
    global _mem
    if _mem is None:
        _mem = {"partidos": {}, "ligas": {}}
        if CON_DISCO:
            try:
                datos = json.loads(_ruta().read_text(encoding="utf-8"))
                _mem = {"partidos": datos.get("partidos", {}), "ligas": datos.get("ligas", {})}
            except (OSError, ValueError):
                pass
    return _mem


def reiniciar() -> None:
    global _mem
    _mem = {"partidos": {}, "ligas": {}}


def _guardar() -> None:
    if not CON_DISCO:
        return
    footystats.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _ruta().with_suffix(".tmp")
    tmp.write_text(json.dumps(_cargar(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(_ruta())


def ligas_vinculadas() -> dict:
    """id de temporada de FootyStats -> {af_liga, af_temporada, nombre, pais, rondas_vistas}."""
    return dict(_cargar()["ligas"])


def vincular(fs: list[dict], af: list[dict], ligas_fs: dict[int, dict], ahora: float) -> dict:
    """
    fs: partidos de FootyStats ({id, hn, an, t, comp}); af: partidos normalizados de API-Football;
    ligas_fs: id de temporada -> {pais, nombre}.
    Devuelve {"enlaces": {fs_id: partido_af}, "ambiguos": [...], "sin_par": [...]} (sin_par: en juego en API-Football sin
    partido de FootyStats con el que emparejar, normalmente de competiciones que no están en tu lista).
    """
    with _lock:
        mem = _cargar()
        por_af = {p["af_id"]: p for p in af}
        fs_por_id = {m["id"]: m for m in fs}
        enlaces: dict[int, dict] = {}
        usados_af: set = set()
        cambio = False

        # 1. Vínculos ya confirmados: por ID, sin volver a comparar nombres
        for fs_id_txt, v in mem["partidos"].items():
            fs_id = int(fs_id_txt)
            if fs_id in fs_por_id and v["af"] in por_af:
                enlaces[fs_id] = por_af[v["af"]]
                usados_af.add(v["af"])

        # 2. Candidatos nuevos
        ligas_mem = mem["ligas"]
        af_a_comp = {v["af_liga"]: int(k) for k, v in ligas_mem.items()}

        def puntuar(m, p):
            if abs(m["t"] - p["inicio_unix"]) > TOLERANCIA_HORA_S:
                return None
            liga = ligas_fs.get(m.get("comp"))
            conocida = ligas_mem.get(str(m.get("comp")))
            if conocida and conocida["af_liga"] != p["liga"]["id"]:
                return None  # la competición ya está vinculada a otra liga de API-Football
            otra = af_a_comp.get(p["liga"]["id"])
            if otra is not None and otra != m.get("comp"):
                return None  # esa liga de API-Football ya corresponde a otra competición
            if not conocida and liga and pais_norm(liga["pais"]) != pais_norm(p["liga"]["pais"]):
                return None
            sl, sv = parecido(m["hn"], p["local"]), parecido(m["an"], p["visitante"])
            if min(sl, sv) < UMBRAL_EQUIPO:
                return None
            # local con local y visitante con visitante: si al revés encaja mejor, no es el mismo partido
            if parecido(m["hn"], p["visitante"]) + parecido(m["an"], p["local"]) > sl + sv:
                return None
            bonus = 0.05 if conocida else 0.0
            return (sl + sv) / 2 + bonus

        libres_fs = [m for m in fs if m["id"] not in enlaces]
        libres_af = [p for p in af if p["af_id"] not in usados_af]
        puntos: dict[tuple, float] = {}
        for p in libres_af:
            for m in libres_fs:
                s = puntuar(m, p)
                if s is not None:
                    puntos[(m["id"], p["af_id"])] = s
        ambiguos, sin_par = [], []
        for p in libres_af:
            cands = sorted(((s, fid) for (fid, aid), s in puntos.items() if aid == p["af_id"]), reverse=True)
            if not cands:
                sin_par.append(p)
                continue
            mejor, fid = cands[0]
            # también debe ser el mejor candidato visto desde el partido de FootyStats (emparejamiento mutuo)
            rivales_fs = sorted((s for (f2, a2), s in puntos.items() if f2 == fid and a2 != p["af_id"]), reverse=True)
            segundo = max(cands[1][0] if len(cands) > 1 else 0.0, rivales_fs[0] if rivales_fs else 0.0)
            if mejor - segundo < MARGEN_AMBIGUEDAD:
                ambiguos.append({"af_id": p["af_id"], "partido": f'{p["local"]} - {p["visitante"]}', "candidatos": [fs_por_id[c[1]]["hn"] + " - " + fs_por_id[c[1]]["an"] for c in cands[:3]]})
                continue
            m = fs_por_id[fid]
            enlaces[fid] = p
            mem["partidos"][str(fid)] = {"af": p["af_id"], "confirmado_unix": int(ahora)}
            if str(m.get("comp")) not in ligas_mem and m.get("comp") is not None:
                liga = ligas_fs.get(m["comp"]) or {}
                ligas_mem[str(m["comp"])] = {"af_liga": p["liga"]["id"], "af_temporada": p["liga"]["temporada"], "af_nombre": p["liga"]["nombre"], "pais": liga.get("pais"), "nombre": liga.get("nombre"), "rondas_vistas": []}
                af_a_comp[p["liga"]["id"]] = m["comp"]
            cambio = True

        # Rondas observadas (para saber si los playoffs vienen en la misma liga de API-Football)
        for fid, p in enlaces.items():
            comp = str(fs_por_id[fid].get("comp"))
            ronda = p["liga"].get("ronda")
            if comp in ligas_mem and ronda and ronda not in ligas_mem[comp]["rondas_vistas"]:
                ligas_mem[comp]["rondas_vistas"] = (ligas_mem[comp]["rondas_vistas"] + [ronda])[-30:]
                cambio = True
        if cambio:
            _guardar()
        return {"enlaces": enlaces, "ambiguos": ambiguos, "sin_par": sin_par}


def cobertura(ligas_fs: list[dict], ligas_af: list[dict] | None) -> list[dict]:
    """
    Para cada competición de FootyStats: si API-Football tiene una liga equivalente (mismo país y nombre parecido),
    si ofrece eventos en vivo, y si ya se confirmó con un partido real emparejado (incluidos los playoffs).
    """
    vinc = ligas_vinculadas()
    salida = []
    for l in ligas_fs:
        confirmado = next((v for k, v in vinc.items() if any(int(k) == t["id"] for t in l["temporadas"])), None)
        candidato = None
        if ligas_af:
            opciones = [(parecido(l["nombre"], a["nombre"]), a) for a in ligas_af if pais_norm(a["pais"]) == pais_norm(l["pais"])]
            opciones = sorted([o for o in opciones if o[0] >= 0.5], key=lambda o: -o[0])
            if opciones and (len(opciones) == 1 or opciones[0][0] - opciones[1][0] >= MARGEN_AMBIGUEDAD):
                candidato = opciones[0][1]
        rondas = (confirmado or {}).get("rondas_vistas") or []
        playoffs = [r for r in rondas if re.search(r"play|final|semi|quarter|knockout|round of", r, re.I)]
        salida.append({
            "pais": l["pais"],
            "nombre": l["nombre"],
            "estado": "confirmada" if confirmado else ("probable" if candidato else ("sin_verificar" if ligas_af is None else "sin_equivalente")),
            "api_football": {"id": (confirmado or {}).get("af_liga") or (candidato or {}).get("id"), "nombre": (confirmado or {}).get("af_nombre") or (candidato or {}).get("nombre"), "eventos_en_vivo": (candidato or {}).get("eventos")},
            "playoffs": "confirmados" if playoffs else "sin_verificar",
            "rondas_vistas": rondas[-5:],
        })
    return salida
