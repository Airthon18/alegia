"""
Pruebas de marcadores y predicción en vivo (API-Football + modelo en vivo). Sin red ni claves reales.
Ejecutar desde backend/:  python -m unittest discover -s tests -v
Los equipos y marcadores de Seattle vs Real Salt Lake son los del caso de prueba pedido y solo aparecen aquí.
"""
import asyncio
import math
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import api_football  # noqa: E402
import en_vivo  # noqa: E402
import footystats  # noqa: E402
import marcadores  # noqa: E402
import prediccion_fs  # noqa: E402
import pronosticos_previos  # noqa: E402
import reloj  # noqa: E402
import vinculos  # noqa: E402
import vivo_modelo as vm  # noqa: E402
from estados_partido import LIMA  # noqa: E402


def correr(c):
    return asyncio.run(c)


def lima(dia, hora, minuto=0):
    return datetime(2026, 11, dia, hora, minuto, tzinfo=LIMA).timestamp()


SEATTLE_INICIO = lima(19, 22, 0)  # jueves 19 22:00 Lima = viernes 03:00 UTC
MLS = 16504
AF_MLS = 253
PERFIL_UNIFORME = vm.Perfil([float(m) for m in range(1, 91)] * 5)  # 450 goles repartidos de forma uniforme


def fixture_af(goles=(1, 1), corto="2H", elapsed=76, extra=None, af_id=111, local="Seattle Sounders", visitante="Real Salt Lake",
               inicio=SEATTLE_INICIO, liga=AF_MLS, pais="USA", ronda="Play-offs - Round 1", fulltime=None, extratime=None, penalty=None):
    """Partido con la forma de la respuesta de API-Football v3 (fixtures?live=all)."""
    return {
        "fixture": {"id": af_id, "timestamp": int(inicio), "status": {"long": "x", "short": corto, "elapsed": elapsed, "extra": extra}},
        "league": {"id": liga, "name": "Major League Soccer", "country": pais, "season": 2026, "round": ronda},
        "teams": {"home": {"id": 1, "name": local}, "away": {"id": 2, "name": visitante}},
        "goals": {"home": goles[0], "away": goles[1]},
        "score": {
            "halftime": {"home": 0, "away": 0},
            "fulltime": {"home": (fulltime or (None, None))[0], "away": (fulltime or (None, None))[1]},
            "extratime": {"home": (extratime or (None, None))[0], "away": (extratime or (None, None))[1]},
            "penalty": {"home": (penalty or (None, None))[0], "away": (penalty or (None, None))[1]},
        },
    }


# ─────────────────────────────────── Modelo en vivo ───────────────────────────────────
class ModeloEnVivoSeattle(unittest.TestCase):
    """Seattle 1-1 al minuto 76: ambos marcan cumplido, marcadores finales desde 1-1, para +2.5 falta un gol."""

    def setUp(self):
        self.p = vm.posicion("2T", 76)
        self.r = vm.prediccion(1.6, 1.1, 1, 1, self.p, PERFIL_UNIFORME)

    def test_ambos_marcan_ya_cumplido(self):
        self.assertTrue(self.r["ambos_marcan"]["cumplido"])
        self.assertEqual(self.r["ambos_marcan"]["prob"], 1.0)

    def test_los_marcadores_finales_parten_de_1_1(self):
        self.assertEqual(len(self.r["marcadores"]), 3)
        for x in self.r["marcadores"]:
            self.assertGreaterEqual(x["local"], 1)
            self.assertGreaterEqual(x["visitante"], 1)
        self.assertEqual((self.r["marcadores"][0]["local"], self.r["marcadores"][0]["visitante"]), (1, 1))  # sin goles es lo más probable a falta de ~15'

    def test_para_mas_de_2_5_falta_al_menos_un_gol(self):
        m = self.r["mas_2_5"]
        self.assertFalse(m["cumplido"])
        self.assertEqual(m["goles_que_faltan"], 1)
        restantes = self.r["goles_restantes_esperados"]
        self.assertAlmostEqual(m["prob"], 1 - math.exp(-(restantes["local"] + restantes["visitante"])), places=6)

    def test_1x2_suma_uno_y_el_empate_es_lo_mas_probable(self):
        r = self.r["resultado_90"]
        self.assertAlmostEqual(r["local"] + r["empate"] + r["visitante"], 1.0, places=9)
        self.assertGreater(r["empate"], max(r["local"], r["visitante"]))

    def test_si_llega_el_2_1_mas_de_2_5_queda_cumplido(self):
        r = vm.prediccion(1.6, 1.1, 2, 1, vm.posicion("2T", 80), PERFIL_UNIFORME)
        self.assertTrue(r["mas_2_5"]["cumplido"])
        self.assertEqual(r["mas_2_5"]["prob"], 1.0)
        self.assertEqual(r["mas_2_5"]["goles_que_faltan"], 0)
        self.assertTrue(all(x["local"] >= 2 and x["visitante"] >= 1 for x in r["marcadores"]))
        self.assertGreater(r["resultado_90"]["local"], r["resultado_90"]["visitante"])

    def test_cuanto_menos_tiempo_queda_menos_goles_se_esperan(self):
        f = [PERFIL_UNIFORME.restante(vm.posicion("2T", m)) for m in (46, 60, 76, 89)]
        self.assertEqual(f, sorted(f, reverse=True))
        self.assertEqual(PERFIL_UNIFORME.restante(vm.FIN_90), 0.0)


class FasesYMinutos(unittest.TestCase):
    def test_posiciones(self):
        self.assertEqual(vm.posicion("1T", 30), 30.0)
        self.assertEqual(vm.posicion("1T", 45, 2), 45.02)
        self.assertEqual(vm.posicion("descanso", None), vm.FIN_1T)
        self.assertEqual(vm.posicion("2T", 90, 3), 90.03)
        self.assertEqual(vm.posicion("prorroga", 105), vm.FIN_90)
        self.assertIsNone(vm.posicion("2T", None))

    def test_en_prorroga_el_resultado_a_90_esta_cerrado(self):
        r = vm.prediccion(1.6, 1.1, 1, 1, vm.posicion("prorroga", 100), PERFIL_UNIFORME)
        self.assertEqual(r["resultado_90"]["empate"], 1.0)
        self.assertEqual(r["fraccion_restante"], 0.0)

    def test_goles_hasta_un_minuto_con_descuentos(self):
        minutos = [12.0, 45.07, 90.03, 105.0]  # el de 105' es de prórroga
        self.assertEqual(vm.goles_a(minutos, 45.0), 1)
        self.assertEqual(vm.goles_a(minutos, vm.FIN_1T), 2)
        self.assertEqual(vm.goles_90(minutos), 3)  # la prórroga no cuenta para el resultado a 90'

    def test_perfil_sin_datos_usa_el_generico(self):
        p = vm.Perfil([])
        self.assertEqual(p.origen, "generico")
        self.assertAlmostEqual(p.restante(0), 1.0)


# ─────────────────────────────────── API-Football ───────────────────────────────────
class ClienteAPIFootball(unittest.TestCase):
    def setUp(self):
        api_football.reiniciar()
        self.env = os.environ.get("API_FOOTBALL_KEY")
        os.environ["API_FOOTBALL_KEY"] = "clave-de-prueba"
        self.orig_get = api_football._get
        self.orig_client = api_football.httpx.AsyncClient

    def tearDown(self):
        api_football._get = self.orig_get
        api_football.httpx.AsyncClient = self.orig_client
        if self.env is None:
            os.environ.pop("API_FOOTBALL_KEY", None)
        else:
            os.environ["API_FOOTBALL_KEY"] = self.env

    def test_normaliza_un_partido_en_juego(self):
        p = api_football.normalizar(fixture_af())
        self.assertEqual((p["estado"], p["fase"], p["minuto"], p["goles"]), ("en_juego", "2T", 76, {"local": 1, "visitante": 1}))
        self.assertEqual(p["inicio_unix"], int(SEATTLE_INICIO))
        self.assertIsNone(p["resultado_90"])

    def test_playoff_separa_90_minutos_prorroga_y_penales(self):
        p = api_football.normalizar(fixture_af(goles=(2, 2), corto="P", elapsed=120, fulltime=(1, 1), extratime=(1, 1), penalty=(4, 3)))
        self.assertEqual(p["estado"], "penales")
        self.assertEqual(p["resultado_90"], {"local": 1, "visitante": 1})
        self.assertEqual(p["prorroga"], {"local": 1, "visitante": 1})
        self.assertEqual(p["penales"], {"local": 4, "visitante": 3})

    def test_estados_cortos(self):
        for corto, esperado in (("1H", "en_juego"), ("HT", "descanso"), ("ET", "prorroga"), ("P", "penales"), ("FT", "finalizado"), ("AET", "finalizado"), ("PEN", "finalizado"), ("PST", "aplazado"), ("CANC", "cancelado"), ("SUSP", "suspendido"), ("NS", "proximo")):
            self.assertEqual(api_football.normalizar(fixture_af(corto=corto))["estado"], esperado, corto)

    def test_respuesta_incompleta_se_descarta(self):
        f = fixture_af()
        f["teams"]["home"]["name"] = None
        self.assertIsNone(api_football.normalizar(f))
        self.assertIsNone(api_football.normalizar({}))

    def test_errores_del_cuerpo(self):
        self.assertIsNone(api_football._error_de_cuerpo([]))
        self.assertEqual(api_football._error_de_cuerpo({"token": "Error/Missing application key"}).tipo, "credenciales")
        self.assertEqual(api_football._error_de_cuerpo({"requests": "You have reached the request limit for the day"}).tipo, "limite")

    def test_intervalo_ajustado_a_la_cuota(self):
        mediodia = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(api_football.ttl_para_cuota({"dia_restantes": 50}, mediodia), api_football.TTL_MAX_S)  # 43200 s / 45 -> tope 15 min
        self.assertEqual(api_football.ttl_para_cuota({"dia_restantes": 7000}, mediodia), api_football.TTL_MIN_S)
        self.assertAlmostEqual(api_football.ttl_para_cuota({"dia_restantes": 2885}, mediodia), 15.0, delta=0.1)
        self.assertEqual(api_football.ttl_para_cuota({"dia_restantes": 3}, mediodia), 43200)  # agotada: hasta la renovación (00:00 UTC)
        self.assertEqual(api_football.ttl_para_cuota({"dia_restantes": 7000, "minuto_limite": 2}, mediodia), 30)

    def test_la_clave_va_en_la_cabecera_y_nunca_en_la_url(self):
        vistos = []

        def manejador(req):
            vistos.append(req)
            return httpx.Response(200, json={"errors": [], "response": [fixture_af()]}, headers={"x-ratelimit-requests-limit": "100", "x-ratelimit-requests-remaining": "90"})

        api_football.httpx.AsyncClient = lambda **kw: self.orig_client(transport=httpx.MockTransport(manejador), **kw)
        r = correr(api_football.partidos_en_vivo())
        self.assertEqual(vistos[0].headers["x-apisports-key"], "clave-de-prueba")
        self.assertNotIn("clave-de-prueba", str(vistos[0].url))
        self.assertEqual(vistos[0].url.params["live"], "all")
        self.assertEqual(r["cuota"]["dia_restantes"], 90)
        self.assertEqual(len(r["partidos"]), 1)

    def test_una_sola_consulta_compartida_entre_usuarios(self):
        llamadas = []

        async def falso(endpoint, **params):
            llamadas.append(endpoint)
            await asyncio.sleep(0.05)
            return {"response": [fixture_af()]}, {"dia_restantes": 7000}

        api_football._get = falso

        async def usuarios():
            return await asyncio.gather(*(api_football.partidos_en_vivo() for _ in range(30)))

        correr(usuarios())
        self.assertEqual(llamadas, ["fixtures"])

    def test_fallo_con_copia_reciente_se_marca_obsoleto(self):
        async def bien(endpoint, **params):
            return {"response": [fixture_af()]}, {"dia_restantes": 7000}

        async def mal(endpoint, **params):
            raise api_football.APIFootballError("conexion", "x")

        api_football._get = bien
        correr(api_football.partidos_en_vivo())
        api_football._cache["consultado"] -= 60
        api_football._get = mal
        r = correr(api_football.partidos_en_vivo())
        self.assertTrue(r["obsoleto"])
        self.assertEqual(r["error"]["tipo"], "conexion")

    def test_sin_clave(self):
        os.environ.pop("API_FOOTBALL_KEY", None)
        self.assertFalse(api_football.configurada())
        with self.assertRaises(api_football.APIFootballError) as cm:
            correr(self.orig_get("fixtures", live="all"))
        self.assertEqual(cm.exception.tipo, "sin_configurar")


# ─────────────────────────────────── Emparejamiento ───────────────────────────────────
def fs(id_=8749311, local="Seattle Sounders", visitante="Real Salt Lake", inicio=SEATTLE_INICIO, comp=MLS):
    return {"id": id_, "hn": local, "an": visitante, "t": int(inicio), "comp": comp, "s": "incomplete", "hg": 0, "ag": 0, "min": None, "jor": 0}


LIGAS_FS = {MLS: {"pais": "USA", "nombre": "MLS"}, 17199: {"pais": "Spain", "nombre": "La Liga"}, 16708: {"pais": "Uruguay", "nombre": "Primera División"}}


class Emparejamiento(unittest.TestCase):
    def setUp(self):
        vinculos.CON_DISCO = False
        vinculos.reiniciar()

    def vincular(self, f, a):
        return vinculos.vincular(f, [api_football.normalizar(x) for x in a], LIGAS_FS, SEATTLE_INICIO)

    def test_empareja_por_hora_pais_y_equipos(self):
        r = self.vincular([fs()], [fixture_af()])
        self.assertEqual(r["enlaces"][8749311]["af_id"], 111)
        self.assertEqual(vinculos.ligas_vinculadas()[str(MLS)]["af_liga"], AF_MLS)

    def test_tolera_nombres_distintos_del_mismo_equipo(self):
        self.assertGreaterEqual(vinculos.parecido("Bayern München", "Bayern Munich"), 0.6)
        self.assertGreaterEqual(vinculos.parecido("Seattle Sounders", "Seattle Sounders FC"), 0.6)
        self.assertLess(vinculos.parecido("Real Madrid", "Atletico Madrid"), 0.6)
        self.assertLess(vinculos.parecido("Liverpool FC Montevideo", "Nacional"), 0.6)

    def test_local_y_visitante_invertidos_no_se_emparejan(self):
        r = self.vincular([fs()], [fixture_af(local="Real Salt Lake", visitante="Seattle Sounders")])
        self.assertEqual(r["enlaces"], {})

    def test_hora_distinta_no_se_empareja(self):
        r = self.vincular([fs()], [fixture_af(inicio=SEATTLE_INICIO + 3 * 3600)])
        self.assertEqual(r["enlaces"], {})

    def test_pais_distinto_no_se_empareja(self):
        r = self.vincular([fs()], [fixture_af(pais="Spain")])
        self.assertEqual(r["enlaces"], {})

    def test_coincidencia_ambigua_no_se_asigna(self):
        # dos partidos de FootyStats igual de parecidos a la misma hora: no se arriesga el marcador
        f = [fs(1, "Nacional", "Peñarol", comp=16708), fs(2, "Club Nacional", "CA Peñarol", comp=16708)]
        a = [fixture_af(af_id=9, local="Nacional", visitante="Penarol", pais="Uruguay", liga=268)]
        r = vinculos.vincular(f, [api_football.normalizar(x) for x in a], LIGAS_FS, SEATTLE_INICIO)
        self.assertEqual(r["enlaces"], {})
        self.assertEqual(len(r["ambiguos"]), 1)

    def test_el_vinculo_confirmado_se_reutiliza_por_id(self):
        self.vincular([fs()], [fixture_af()])
        # aunque luego el proveedor cambie el nombre, el ID ya confirmado manda
        r = self.vincular([fs()], [fixture_af(local="Seattle", visitante="RSL")])
        self.assertEqual(r["enlaces"][8749311]["af_id"], 111)

    def test_la_competicion_aprendida_exige_la_misma_liga(self):
        self.vincular([fs()], [fixture_af()])
        r = self.vincular([fs(5, "LA Galaxy", "LAFC")], [fixture_af(af_id=7, local="LA Galaxy", visitante="LAFC", liga=999)])
        self.assertEqual(r["enlaces"], {})

    def test_sin_par(self):
        r = self.vincular([fs()], [fixture_af(af_id=50, local="Boca Juniors", visitante="River Plate", pais="Argentina", liga=128)])
        self.assertEqual(len(r["sin_par"]), 1)

    def test_cobertura(self):
        self.vincular([fs()], [fixture_af()])
        ligas = [{"pais": "USA", "nombre": "MLS", "temporadas": [{"id": MLS}]}, {"pais": "Spain", "nombre": "La Liga", "temporadas": [{"id": 17199}]}, {"pais": "Peru", "nombre": "Primera División", "temporadas": [{"id": 16905}]}]
        af = [{"id": 140, "nombre": "La Liga", "pais": "Spain", "eventos": True, "tipo": "League", "temporada": 2026}]
        c = {x["nombre"]: x for x in vinculos.cobertura(ligas, af)}
        self.assertEqual(c["MLS"]["estado"], "confirmada")
        self.assertEqual(c["MLS"]["playoffs"], "confirmados")  # ronda "Play-offs - Round 1" vista en un partido real emparejado
        self.assertEqual(c["La Liga"]["estado"], "probable")
        self.assertEqual(c["Primera División"]["estado"], "sin_equivalente")
        self.assertEqual({x["estado"] for x in vinculos.cobertura(ligas[1:], None)}, {"sin_verificar"})


# ─────────────────────────────────── Integración En vivo ───────────────────────────────────
class IntegracionEnVivo(unittest.TestCase):
    AHORA = SEATTLE_INICIO + 80 * 60

    def setUp(self):
        marcadores.CON_DISCO = False
        marcadores.reiniciar()
        vinculos.CON_DISCO = False
        vinculos.reiniciar()
        en_vivo.ULTIMO.clear()
        self.env = os.environ.pop("API_FOOTBALL_KEY", None)

    def tearDown(self):
        if self.env is not None:
            os.environ["API_FOOTBALL_KEY"] = self.env

    async def _ligas(self):
        return [{"pais": "USA", "nombre": "MLS", "temporadas": [{"id": MLS}], "temporada_reciente_id": MLS}]

    def dia(self, partidos):
        async def obtener(fecha):
            return {"partidos": partidos if fecha == "2026-11-19" else [], "consultado": self.AHORA - 30, "completo": True, "obsoleto": False}
        return obtener

    def vivo(self, fixtures, consultado=None, obsoleto=False, error=None):
        async def obtener():
            return {"partidos": [api_football.normalizar(f) for f in fixtures], "consultado": consultado or self.AHORA - 10, "ttl": 60, "proxima": self.AHORA + 50, "cuota": {"dia_restantes": 7000}, "obsoleto": obsoleto, "error": error}
        return obtener

    def correr(self, obtener_vivo=None, partidos=None):
        return correr(en_vivo.en_vivo(self.AHORA, self.dia(partidos or [fs()]), self._ligas, None, obtener_vivo))

    def test_sin_clave_fuente_pendiente_de_configurar(self):
        r = self.correr()
        self.assertEqual(r["fuente_vivo"]["estado"], "pendiente_configurar")
        self.assertEqual(r["partidos"][0]["estado"], "por_confirmar")  # sin fuente en vivo, sigue el criterio anterior
        self.assertEqual(r["en_vivo_total"], 0)

    def test_seattle_1_1_al_76_confirmado(self):
        r = self.correr(self.vivo([fixture_af((1, 1), "2H", 76)]))
        p = r["partidos"][0]
        self.assertEqual((p["estado"], p["fase"], p["minuto"], p["fuente"]), ("en_juego", "2T", 76, "API-Football"))
        self.assertEqual((p["marcador"]["estado"], p["marcador"]["local"], p["marcador"]["visitante"]), ("confirmado", 1, 1))
        self.assertEqual(r["en_vivo_total"], 1)
        self.assertEqual(r["fuente_vivo"]["estado"], "conectada")
        self.assertEqual(r["fuente_vivo"]["vinculados"], 1)
        self.assertEqual(en_vivo.ULTIMO[8749311]["comp"], MLS)

    def test_llega_el_gol_y_se_actualiza(self):
        a = self.correr(self.vivo([fixture_af((1, 1), "2H", 76)], consultado=self.AHORA - 200))["partidos"][0]
        b = self.correr(self.vivo([fixture_af((2, 1), "2H", 79)], consultado=self.AHORA - 10))["partidos"][0]
        self.assertEqual((b["marcador"]["local"], b["marcador"]["visitante"], b["minuto"]), (2, 1, 79))
        self.assertGreater(b["marcador"]["recibido_unix"], a["marcador"]["recibido_unix"])

    def test_correccion_del_proveedor(self):
        self.correr(self.vivo([fixture_af((2, 1), "2H", 79)], consultado=self.AHORA - 100))
        b = self.correr(self.vivo([fixture_af((1, 1), "2H", 80)], consultado=self.AHORA - 10))["partidos"][0]  # gol anulado
        self.assertEqual((b["marcador"]["local"], b["marcador"]["visitante"]), (1, 1))

    def test_playoff_en_prorroga(self):
        p = self.correr(self.vivo([fixture_af((2, 1), "ET", 105, fulltime=(1, 1), extratime=(1, 0))]))["partidos"][0]
        self.assertEqual((p["estado"], p["resultado_90"], p["prorroga"]), ("prorroga", {"local": 1, "visitante": 1}, {"local": 1, "visitante": 0}))

    def test_fuente_desconectada_sin_datos(self):
        async def falla():
            raise api_football.APIFootballError("credenciales", "API-Football rechazó la clave")
        r = self.correr(falla)
        self.assertEqual((r["fuente_vivo"]["estado"], r["fuente_vivo"]["tipo"]), ("desconectada", "credenciales"))
        self.assertEqual(r["partidos"][0]["estado"], "por_confirmar")

    def test_fuente_desconectada_con_copia_avisa_y_usa_la_copia(self):
        r = self.correr(self.vivo([fixture_af()], obsoleto=True, error={"tipo": "conexion", "mensaje": "x"}))
        self.assertEqual(r["fuente_vivo"]["estado"], "desconectada")
        self.assertEqual(r["estado_consulta"], "parcial")
        self.assertEqual(r["partidos"][0]["estado"], "en_juego")

    def test_senal_atrasada_y_senal_perdida(self):
        a = self.correr(self.vivo([fixture_af()], consultado=self.AHORA - 10 * 60))["partidos"][0]
        self.assertTrue(a["senal_atrasada"])
        self.assertEqual(a["estado"], "en_juego")
        b = self.correr(self.vivo([fixture_af()], consultado=self.AHORA - 40 * 60))["partidos"][0]
        self.assertEqual(b["estado"], "por_confirmar")

    def test_ambiguo_cobertura_parcial(self):
        partidos = [fs(1, "Nacional", "Peñarol", comp=MLS), fs(2, "Club Nacional", "CA Peñarol", comp=MLS)]
        r = self.correr(self.vivo([fixture_af(af_id=9, local="Nacional", visitante="Penarol")]), partidos)
        self.assertEqual(r["estado_consulta"], "parcial")
        self.assertEqual(r["fuente_vivo"]["ambiguos"], 1)
        self.assertEqual(r["en_vivo_total"], 0)

    def test_ningun_partido_en_juego(self):
        r = self.correr(self.vivo([]), partidos=[fs(inicio=self.AHORA + 3600)])
        self.assertEqual((r["fuente_vivo"]["estado"], r["en_vivo_total"], r["estado_consulta"]), ("conectada", 0, "ok"))


# ─────────────────────────────── Predicción en vivo completa ───────────────────────────────
class PrediccionEnVivo(unittest.TestCase):
    """De punta a punta con un historial sintético: fuerza previa + estado confirmado + pronóstico previo por separado."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_original = footystats.CACHE_DIR
        footystats.CACHE_DIR = Path(self.tmp.name)
        self.orig = (prediccion_fs.predecir_partido, prediccion_fs._contexto)
        hist_partidos = []
        for i in range(60):  # historial pequeño: no alcanza para validar -> "Estimación en vivo experimental"
            hist_partidos.append({"id": i, "h": i % 6, "a": (i + 1) % 6, "hn": f"E{i % 6}", "an": f"E{(i + 1) % 6}", "t": 1_700_000_000 + i * 86400, "s": "complete", "hg": 1, "ag": 1,
                                  "hx": None, "ax": None, "ml": [30.0], "mv": [70.0], "sid": 1})
        objetivo = {"id": 8749311, "h": 100, "a": 101, "hn": "Seattle Sounders", "an": "Real Salt Lake", "t": int(SEATTLE_INICIO), "s": "incomplete", "hg": 0, "ag": 0, "hx": None, "ax": None}
        hist = prediccion_fs.Historial(hist_partidos)

        async def previa(season_id, match_id, detalle=False):
            return {"estado": "ok", "partido": {"id": match_id, "local": "Seattle Sounders", "visitante": "Real Salt Lake"}, "goles_esperados": {"local": 1.6, "visitante": 1.1},
                    "resultado_1x2": {"local": 0.48, "empate": 0.27, "visitante": 0.25}}

        async def contexto(season_id, match_id):
            return {"pais": "USA", "nombre": "MLS"}, [1], hist, objetivo

        prediccion_fs.predecir_partido, prediccion_fs._contexto = previa, contexto

    def tearDown(self):
        prediccion_fs.predecir_partido, prediccion_fs._contexto = self.orig
        footystats.CACHE_DIR = self.cache_original
        self.tmp.cleanup()

    def vivo(self, fase="2T", minuto=76, goles=(1, 1), resultado_90=None, prorroga=None, penales=None):
        return {"fase": fase, "minuto": minuto, "minuto_extra": None, "marcador": {"local": goles[0], "visitante": goles[1]}, "resultado_90": resultado_90, "prorroga": prorroga, "penales": penales, "dato_unix": 1, "consultado_unix": 2}

    def test_seattle_1_1_al_76(self):
        r = correr(prediccion_fs.prediccion_en_vivo(MLS, 8749311, self.vivo()))
        self.assertEqual(r["estado"], "ok")
        self.assertEqual((r["calculado_con"]["minuto"], r["calculado_con"]["marcador"]), (76, {"local": 1, "visitante": 1}))
        self.assertTrue(r["ambos_marcan"]["cumplido"])
        self.assertEqual(r["mas_2_5"]["goles_que_faltan"], 1)
        self.assertTrue(all(x["local"] >= 1 and x["visitante"] >= 1 for x in r["marcadores_finales"]))
        self.assertEqual(r["etiqueta"], "Estimación en vivo experimental")
        # el pronóstico previo se conserva por separado y no cambia con el marcador
        self.assertEqual(r["pronostico_previo"]["resultado_1x2"], {"local": 0.48, "empate": 0.27, "visitante": 0.25})
        self.assertIn("estimado_unix", r)

    def test_con_2_1_mas_de_2_5_cumplido(self):
        r = correr(prediccion_fs.prediccion_en_vivo(MLS, 8749311, self.vivo(minuto=80, goles=(2, 1))))
        self.assertTrue(r["mas_2_5"]["cumplido"])

    def test_playoff_prorroga_y_penales_separados_del_resultado_a_90(self):
        r = correr(prediccion_fs.prediccion_en_vivo(MLS, 8749311, self.vivo("penales", 120, (2, 2), {"local": 1, "visitante": 1}, {"local": 1, "visitante": 1}, {"local": 4, "visitante": 3})))
        self.assertEqual(r["resultado_90"]["empate"], 1.0)  # a los 90' terminó 1-1
        self.assertEqual(r["calculado_con"]["marcador"], {"local": 1, "visitante": 1})
        self.assertEqual(r["tiempo_extra"]["penales"], {"local": 4, "visitante": 3})

    def test_en_prorroga_sin_resultado_a_90_no_se_inventa(self):
        r = correr(prediccion_fs.prediccion_en_vivo(MLS, 8749311, self.vivo("prorroga", 100, (2, 1))))
        self.assertEqual(r["estado"], "sin_resultado_90")


if __name__ == "__main__":
    unittest.main()
