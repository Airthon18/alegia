"""
Pruebas de "En vivo" con reloj controlado. Ejecutar desde backend/:  python -m unittest discover -s tests -v
No usan la red ni la clave: el proveedor y el reloj se sustituyen por valores de prueba.
Los equipos y marcadores son los de las capturas del usuario y solo aparecen aquí.
"""
import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import en_vivo  # noqa: E402
import footystats  # noqa: E402
import marcadores  # noqa: E402
import reloj  # noqa: E402
from estados_partido import LIMA, clasificar, dia_lima, evaluar_marcador, fechas_lima  # noqa: E402


def utc(texto: str) -> float:
    return datetime.fromisoformat(texto).replace(tzinfo=timezone.utc).timestamp()


def lima(dia: int, hora: int, minuto: int = 0, mes: int = 11) -> float:
    """Instante UTC de una hora de pared en Lima (UTC-5) de 2026."""
    return datetime(2026, mes, dia, hora, minuto, tzinfo=LIMA).timestamp()


# Capturas del usuario
ESTADO_AHORA = lima(19, 23, 35)  # jueves 19-nov, 23:35 en Lima = viernes 20, 04:35 UTC
SYDNEY_INICIO = lima(20, 3, 40)  # viernes 20, 03:40 en Lima = 08:40 UTC (date_unix 1795164000 en FootyStats)
SEATTLE_INICIO = lima(19, 22, 0)  # jueves 19, 22:00 en Lima = viernes 20, 03:00 UTC
A_LEAGUE, MLS = 17326, 16504

LIGAS = [
    {"pais": "Australia", "nombre": "A-League", "temporadas": [{"id": A_LEAGUE, "etiqueta": "2026/27"}], "temporada_reciente_id": A_LEAGUE},
    {"pais": "USA", "nombre": "MLS", "temporadas": [{"id": MLS, "etiqueta": "2026"}], "temporada_reciente_id": MLS},
    {"pais": "Spain", "nombre": "La Liga", "temporadas": [{"id": 17199, "etiqueta": "2026/27"}], "temporada_reciente_id": 17199},
]


async def ligas():
    return LIGAS


def correr(coro):
    return asyncio.run(coro)


def partido(id_, estado="incomplete", goles=(0, 0), inicio=SEATTLE_INICIO, comp=MLS, local="Seattle Sounders", visita="Real Salt Lake", minuto=None):
    return {"id": id_, "hn": local, "an": visita, "t": int(inicio), "s": estado, "hg": goles[0], "ag": goles[1], "min": minuto, "comp": comp, "jor": 0}


def dia(partidos, consultado, completo=True, obsoleto=False):
    return {"partidos": partidos, "consultado": consultado, "completo": completo, "obsoleto": obsoleto}


def servir(partidos, consultado, fecha_con_partidos=None):
    """Proveedor falso: devuelve todos los partidos bajo la fecha de Lima del primero (o la indicada) y nada bajo las demás."""

    async def obtener(fecha):
        destino = fecha_con_partidos or dia_lima(partidos[0]["t"]) if partidos else None
        return dia(partidos if fecha == destino else [], consultado)

    return obtener


class Base(unittest.TestCase):
    def setUp(self):
        marcadores.CON_DISCO = False
        marcadores.reiniciar()
        reloj._medicion.update({"desfase_s": None, "tomada_local": 0.0, "fuente": None})
        self.hora_local_original = reloj.hora_local

    def tearDown(self):
        reloj.hora_local = self.hora_local_original


class CapturaWesternSydney(Base):
    """A las 23:35 del jueves 19 en Lima, Western Sydney (programado el viernes 03:40 Lima) debe estar en Próximos partidos."""

    def calendario(self, ahora=None):
        async def falso(season_id):
            return [{"id": 8597530, "home_name": "Western Sydney Wanderers", "away_name": "Adelaide United", "date_unix": int(SYDNEY_INICIO), "status": "incomplete", "game_week": 5}]

        original = footystats._descargar_partidos
        footystats._descargar_partidos = falso
        try:
            return correr(footystats.proximos_partidos(A_LEAGUE, ahora))
        finally:
            footystats._descargar_partidos = original

    def test_las_horas_de_la_captura_son_coherentes(self):
        self.assertEqual(int(SYDNEY_INICIO), 1795164000)  # el timestamp original de FootyStats
        self.assertEqual(datetime.fromtimestamp(SYDNEY_INICIO, LIMA).strftime("%Y-%m-%d %H:%M"), "2026-11-20 03:40")
        self.assertEqual(datetime.fromtimestamp(SYDNEY_INICIO, timezone.utc).strftime("%Y-%m-%d %H:%M"), "2026-11-20 08:40")
        self.assertEqual(datetime.fromtimestamp(ESTADO_AHORA, timezone.utc).strftime("%Y-%m-%d %H:%M"), "2026-11-20 04:35")

    def test_aparece_en_proximos_partidos(self):
        lista = self.calendario(ESTADO_AHORA)
        self.assertEqual([(p["id"], p["estado"]) for p in lista], [(8597530, "proximo")])
        self.assertEqual(lista[0]["fecha_unix"], 1795164000)  # el instante llega sin tocar

    def test_no_aparece_en_vivo_ni_por_confirmar(self):
        r = correr(en_vivo.en_vivo(ESTADO_AHORA, servir([partido(8597530, inicio=SYDNEY_INICIO, comp=A_LEAGUE, local="Western Sydney Wanderers", visita="Adelaide United")], ESTADO_AHORA, "2026-11-20"), ligas))
        self.assertEqual(r["partidos"], [])
        self.assertEqual((r["en_vivo_total"], r["por_confirmar_total"]), (0, 0))

    def test_aunque_el_reloj_del_equipo_marque_5_horas_de_mas_sigue_siendo_proximo(self):
        # Reproduce el error real: el equipo cree que son las 09:35 UTC cuando son las 04:35 UTC
        reloj.hora_local = lambda: ESTADO_AHORA + 5 * 3600
        # FootyStats responde con su cabecera Date (UTC real): el desfase se mide, no se supone
        reloj.registrar("Fri, 20 Nov 2026 04:35:00 GMT", reloj.hora_local() - 0.4, reloj.hora_local())
        self.assertAlmostEqual(reloj.desfase_s(), 18000, delta=2)
        self.assertAlmostEqual(reloj.ahora(), ESTADO_AHORA, delta=2)
        self.assertEqual([p["estado"] for p in self.calendario()], ["proximo"])

    def test_pasada_la_hora_sin_confirmacion_queda_por_confirmar_y_no_en_juego(self):
        ahora = SYDNEY_INICIO + 10 * 60
        r = correr(en_vivo.en_vivo(ahora, servir([partido(8597530, inicio=SYDNEY_INICIO, comp=A_LEAGUE)], ahora, "2026-11-20"), ligas))
        self.assertEqual([p["estado"] for p in r["partidos"]], ["por_confirmar"])
        self.assertEqual(r["en_vivo_total"], 0)


class CapturaSeattle(Base):
    AHORA = SEATTLE_INICIO + 60 * 60  # minuto ~60 del partido

    def correr(self, p, consultado=None):
        consultado = self.AHORA - 10 if consultado is None else consultado
        return correr(en_vivo.en_vivo(self.AHORA, servir([p], consultado), ligas))

    def test_con_respuesta_que_confirma_1_0_al_minuto_60_esta_en_vivo(self):
        r = self.correr(partido(8749311, "inprogress", (1, 0), minuto=60))
        p = r["partidos"][0]
        self.assertEqual((p["estado"], p["minuto"]), ("en_juego", 60))
        self.assertEqual((p["marcador"]["estado"], p["marcador"]["local"], p["marcador"]["visitante"]), ("confirmado", 1, 0))
        self.assertEqual(r["en_vivo_total"], 1)
        self.assertEqual(p["liga"], {"pais": "USA", "nombre": "MLS"})
        self.assertEqual(p["dia_peru"], "2026-11-19")  # jueves en Perú aunque en UTC ya sea viernes

    def test_sin_estado_ni_goles_confirmados_esta_por_confirmar_y_sin_marcador(self):
        r = self.correr(partido(8749311, "incomplete", (0, 0)))
        p = r["partidos"][0]
        self.assertEqual((p["estado"], p["etiqueta"]), ("por_confirmar", "Estado por confirmar"))
        self.assertEqual(p["marcador"]["estado"], "no_disponible")
        self.assertIsNone(p["marcador"]["local"])  # nunca un 0-0 inventado
        self.assertIsNone(p["minuto"])
        self.assertEqual((r["en_vivo_total"], r["por_confirmar_total"]), (0, 1))

    def test_los_goles_de_un_incomplete_no_lo_convierten_en_vivo(self):
        p = self.correr(partido(8749311, "incomplete", (1, 0)))["partidos"][0]
        self.assertEqual(p["estado"], "por_confirmar")
        self.assertEqual(p["marcador"]["estado"], "provisional")

    def test_el_estado_en_vivo_vencido_ya_no_es_evidencia_vigente(self):
        p = self.correr(partido(8749311, "inprogress", (1, 0), minuto=60), consultado=self.AHORA - 11 * 60)["partidos"][0]
        self.assertEqual(p["estado"], "por_confirmar")

    def test_sin_minuto_del_proveedor_no_se_inventa(self):
        p = self.correr(partido(8749311, "inprogress", (1, 0)))["partidos"][0]
        self.assertEqual(p["estado"], "en_juego")
        self.assertIsNone(p["minuto"])


class TodosLosEstadosYLigas(Base):
    AHORA = SEATTLE_INICIO + 60 * 60

    def test_en_vivo_0_0_aplazado_y_finalizado_en_distintas_ligas(self):
        ps = [
            partido(1, "inprogress", (0, 0), comp=MLS, local="Seattle Sounders", visita="Real Salt Lake", minuto=60),  # realmente en vivo, 0-0
            partido(2, "postponed", (0, 0), comp=A_LEAGUE, local="Western Sydney Wanderers", visita="Adelaide United"),  # aplazado
            partido(3, "complete", (2, 1), comp=17199, local="Real Madrid", visita="Celta de Vigo"),  # finalizado
            partido(4, "halftime", (1, 1), comp=17199, local="FC Barcelona", visita="Villarreal"),  # descanso explícito
            partido(5, "incomplete", (0, 0), comp=17199, local="Sevilla FC", visita="Real Betis"),  # sin confirmar
        ]
        r = correr(en_vivo.en_vivo(self.AHORA, servir(ps, self.AHORA - 5), ligas))
        por_id = {p["id"]: p for p in r["partidos"]}
        self.assertEqual(por_id[1]["estado"], "en_juego")
        self.assertEqual((por_id[1]["marcador"]["estado"], por_id[1]["marcador"]["local"], por_id[1]["marcador"]["visitante"]), ("confirmado", 0, 0))  # 0-0 real
        self.assertEqual(por_id[2]["estado"], "aplazado")
        self.assertEqual(por_id[2]["marcador"]["estado"], "no_disponible")
        self.assertEqual((por_id[3]["estado"], por_id[3]["marcador"]["estado"], por_id[3]["marcador"]["local"]), ("finalizado", "confirmado", 2))
        self.assertEqual(por_id[4]["estado"], "descanso")
        self.assertEqual(por_id[5]["estado"], "por_confirmar")
        # el contador incluye solo los que tienen evidencia explícita y vigente
        self.assertEqual(r["en_vivo_total"], 2)
        self.assertEqual(r["por_confirmar_total"], 1)

    def test_misma_logica_en_todas_las_ligas(self):
        for liga in LIGAS:
            comp = liga["temporada_reciente_id"]
            r = correr(en_vivo.en_vivo(self.AHORA, servir([partido(10 + comp % 7, "incomplete", (0, 0), comp=comp)], self.AHORA - 5), ligas))
            self.assertEqual([p["estado"] for p in r["partidos"]], ["por_confirmar"], liga["nombre"])

    def test_sin_deduccion_por_tiempo_transcurrido(self):
        # El 'incomplete' 45 minutos, 2 horas o 10 horas después del inicio NUNCA es "en juego"
        for minutos in (1, 20, 45, 55, 80, 100, 140, 600):
            self.assertEqual(clasificar("incomplete", SEATTLE_INICIO, SEATTLE_INICIO + minutos * 60)["estado"], "por_confirmar", minutos)
        self.assertEqual(clasificar("incomplete", SEATTLE_INICIO, SEATTLE_INICIO - 1)["estado"], "proximo")

    def test_estados_documentados_de_footystats(self):
        for s, esperado in (("complete", "finalizado"), ("suspended", "suspendido"), ("canceled", "cancelado")):
            self.assertEqual(clasificar(s, SEATTLE_INICIO, SEATTLE_INICIO + 3600)["estado"], esperado)

    def test_evaluar_marcador(self):
        self.assertEqual(evaluar_marcador("por_confirmar", 0, 0)["estado"], "no_disponible")
        self.assertEqual(evaluar_marcador("finalizado", 0, 0)["estado"], "confirmado")  # un 0-0 final sí es real
        self.assertEqual(evaluar_marcador("en_juego", 0, 0)["estado"], "confirmado")
        self.assertEqual(evaluar_marcador("por_confirmar", 1, 0)["estado"], "provisional")
        self.assertEqual(evaluar_marcador("en_juego", None, None)["estado"], "no_disponible")  # valor vacío
        self.assertEqual(evaluar_marcador("proximo", 0, 0)["estado"], "no_disponible")


class MarcadorConservado(Base):
    AHORA = SEATTLE_INICIO + 60 * 60

    def consulta(self, ahora, p):
        return correr(en_vivo.en_vivo(ahora, servir([p], ahora - 1), ligas))["partidos"][0]

    def test_conserva_el_ultimo_confirmado_ante_una_respuesta_vacia(self):
        t0 = self.AHORA
        self.consulta(t0, partido(8749311, "inprogress", (1, 0), minuto=60))
        p = self.consulta(t0 + 120, partido(8749311, "incomplete", (0, 0)))  # el proveedor ahora responde sin confirmar
        self.assertEqual((p["marcador"]["local"], p["marcador"]["visitante"]), (1, 0))
        self.assertTrue(p["marcador"]["desactualizado"])
        self.assertEqual(p["marcador"]["recibido_unix"], int(t0 - 1))  # cuándo se recibió ese dato
        self.assertEqual(p["consultado_unix"], int(t0 + 120 - 1))  # cuándo se consultó por última vez: otra hora distinta

    def test_ignora_una_respuesta_mas_antigua(self):
        t0 = self.AHORA
        self.consulta(t0, partido(8749311, "inprogress", (2, 1)))
        antigua = correr(en_vivo.en_vivo(t0 + 30, servir([partido(8749311, "inprogress", (0, 0))], t0 - 600), ligas))["partidos"][0]
        self.assertEqual((antigua["marcador"]["local"], antigua["marcador"]["visitante"]), (2, 1))

    def test_el_dato_conserva_su_hora_si_el_marcador_no_cambia(self):
        t0 = self.AHORA
        a = self.consulta(t0, partido(8749311, "inprogress", (1, 0)))
        b = self.consulta(t0 + 180, partido(8749311, "inprogress", (1, 0)))
        self.assertEqual(a["marcador"]["recibido_unix"], b["marcador"]["recibido_unix"])
        self.assertGreater(b["consultado_unix"], a["consultado_unix"])

    def test_un_gol_nuevo_actualiza_la_hora_del_dato(self):
        t0 = self.AHORA
        a = self.consulta(t0, partido(8749311, "inprogress", (1, 0)))
        b = self.consulta(t0 + 180, partido(8749311, "inprogress", (1, 1)))
        self.assertGreater(b["marcador"]["recibido_unix"], a["marcador"]["recibido_unix"])
        self.assertFalse(b["marcador"]["desactualizado"])

    def test_transicion_por_confirmar_en_vivo_finalizado_sin_duplicarse(self):
        pasos = [
            (SEATTLE_INICIO - 600, "incomplete", None, "proximo"),
            (SEATTLE_INICIO + 600, "incomplete", None, "por_confirmar"),
            (SEATTLE_INICIO + 3600, "inprogress", (1, 0), "en_juego"),
            (SEATTLE_INICIO + 3 * 3600, "complete", (2, 1), "finalizado"),
        ]
        for ahora, estado, goles, esperado in pasos:
            p = partido(8749311, estado, goles or (0, 0))
            r = correr(en_vivo.en_vivo(ahora, servir([p], ahora - 1, dia_lima(SEATTLE_INICIO)), ligas))
            ids = [x["id"] for x in r["partidos"]]
            if esperado == "proximo":
                self.assertEqual(ids, [])  # los futuros están en "Próximos partidos", no aquí
            else:
                self.assertEqual(ids, [8749311])
                self.assertEqual(r["partidos"][0]["estado"], esperado)


class RelojDeReferencia(Base):
    def test_mide_el_desfase_exacto_de_5_horas(self):
        local = utc("2026-11-20T09:37:58")
        reloj.registrar("Fri, 20 Nov 2026 04:37:58 GMT", local - 0.5, local + 0.5)
        self.assertAlmostEqual(reloj.desfase_s(), 18000, delta=1.5)
        self.assertEqual(reloj.texto_desfase(reloj.desfase_s()), "5 h 0 min adelantado")
        self.assertIn("5 h 0 min adelantado", reloj.aviso())

    def test_un_desfase_pequeno_no_se_corrige(self):
        reloj.hora_local = lambda: 1_000_000_030.0
        reloj.registrar("Sun, 09 Sep 2001 01:46:40 GMT", 1_000_000_030.0, 1_000_000_030.0)  # referencia 1_000_000_000
        self.assertLess(abs(reloj.desfase_s()), 60)
        self.assertEqual(reloj.ahora(), 1_000_000_030.0)
        self.assertIsNone(reloj.aviso())

    def test_reloj_atrasado(self):
        reloj.registrar("Fri, 20 Nov 2026 04:00:00 GMT", utc("2026-11-20T02:00:00"), utc("2026-11-20T02:00:00"))
        self.assertIn("atrasado", reloj.aviso())

    def test_cabecera_invalida_no_rompe(self):
        self.assertFalse(reloj.registrar(None, 0, 0))
        self.assertFalse(reloj.registrar("no es una fecha", 0, 0))
        self.assertFalse(reloj.hay_referencia())

    def test_en_vivo_avisa_del_reloj_desviado_y_clasifica_con_la_hora_de_referencia(self):
        reloj.hora_local = lambda: ESTADO_AHORA + 5 * 3600
        reloj.registrar("Fri, 20 Nov 2026 04:35:00 GMT", reloj.hora_local(), reloj.hora_local())
        ahora = reloj.ahora()
        p = partido(8597530, inicio=SYDNEY_INICIO, comp=A_LEAGUE)
        r = correr(en_vivo.en_vivo(ahora, servir([p], ahora, "2026-11-20"), ligas))
        self.assertEqual(r["partidos"], [])
        self.assertEqual(r["estado_consulta"], "ok")  # el reloj desviado no convierte la consulta en parcial
        self.assertIn("5 h 0 min adelantado", r["aviso_reloj"])
        self.assertTrue(r["reloj"]["corregido"])

    def test_el_timestamp_esta_en_segundos_no_en_milisegundos(self):
        c = footystats._compactar_dia({"id": 1, "date_unix": 1795164000, "status": "incomplete", "homeGoalCount": 0, "awayGoalCount": 0, "competition_id": A_LEAGUE})
        self.assertEqual(c["t"], 1795164000)
        self.assertLess(c["t"], 10**11)

    def test_el_dia_de_peru_no_es_el_dia_utc(self):
        self.assertEqual(dia_lima(SEATTLE_INICIO), "2026-11-19")
        self.assertEqual(datetime.fromtimestamp(SEATTLE_INICIO, timezone.utc).date().isoformat(), "2026-11-20")

    def test_antes_de_medianoche_peru_se_consulta_ayer_hoy_y_manana(self):
        self.assertEqual(fechas_lima(ESTADO_AHORA), ["2026-11-18", "2026-11-19", "2026-11-20"])  # 23:35 Lima

    def test_pasada_la_medianoche_peru_sigue_incluyendo_el_dia_anterior(self):
        self.assertEqual(fechas_lima(lima(20, 0, 10)), ["2026-11-19", "2026-11-20"])

    def test_a_media_tarde_solo_ayer_y_hoy(self):
        self.assertEqual(fechas_lima(lima(20, 15, 0)), ["2026-11-19", "2026-11-20"])


class RespuestasIncompletas(Base):
    AHORA = SEATTLE_INICIO + 600

    def test_una_fecha_falla_cobertura_parcial_pero_se_muestran_los_otros_datos(self):
        async def obtener(fecha):
            if fecha == "2026-11-18":
                raise footystats.FootyStatsError("conexion", "No se pudo conectar con FootyStats")
            return dia([partido(8749311)] if fecha == "2026-11-19" else [], self.AHORA)

        r = correr(en_vivo.en_vivo(self.AHORA, obtener, ligas))
        self.assertEqual(r["estado_consulta"], "parcial")
        self.assertEqual(len(r["partidos"]), 1)
        self.assertTrue(any("2026-11-18" in a for a in r["avisos"]))

    def test_si_todas_las_consultas_fallan_es_un_error_no_una_lista_vacia(self):
        async def obtener(fecha):
            raise footystats.FootyStatsError("limite", "límite")

        with self.assertRaises(footystats.FootyStatsError) as cm:
            correr(en_vivo.en_vivo(self.AHORA, obtener, ligas))
        self.assertEqual(cm.exception.tipo, "limite")

    def test_respuesta_correcta_sin_partidos_es_ok_y_vacia(self):
        async def obtener(fecha):
            return dia([], self.AHORA)

        r = correr(en_vivo.en_vivo(self.AHORA, obtener, ligas))
        self.assertEqual((r["estado_consulta"], r["partidos"], r["en_vivo_total"]), ("ok", [], 0))

    def test_pagina_perdida_marca_cobertura_parcial(self):
        async def obtener(fecha):
            return dia([partido(8749311)], self.AHORA, completo=False)

        self.assertEqual(correr(en_vivo.en_vivo(self.AHORA, obtener, ligas))["estado_consulta"], "parcial")

    def test_datos_antiguos_se_avisan(self):
        async def obtener(fecha):
            return dia([partido(8749311)], self.AHORA - 300, obsoleto=True)

        self.assertEqual(correr(en_vivo.en_vivo(self.AHORA, obtener, ligas))["estado_consulta"], "parcial")

    def test_competicion_fuera_de_mi_lista_queda_sin_liga_y_se_avisa(self):
        async def obtener(fecha):
            return dia([partido(8749311, comp=999)], self.AHORA)

        r = correr(en_vivo.en_vivo(self.AHORA, obtener, ligas))
        self.assertIsNone(r["partidos"][0]["liga"])
        self.assertTrue(any("Otras competiciones" in a for a in r["avisos"]))

    def test_campos_ausentes_en_el_proveedor_no_rompen(self):
        self.assertIsNone(footystats._compactar_dia({"id": 1}))  # sin fecha
        c = footystats._compactar_dia({"id": 1, "date_unix": 5, "status": "incomplete"})
        self.assertEqual((c["hg"], c["ag"], c["hn"], c["min"]), (None, None, None, None))

    def test_goles_vacios_o_negativos_no_son_un_marcador(self):
        c = footystats._compactar_dia({"id": 1, "date_unix": 5, "status": "incomplete", "homeGoalCount": -1, "awayGoalCount": -1})
        self.assertEqual(evaluar_marcador("por_confirmar", c["hg"], c["ag"])["estado"], "no_disponible")

    def test_mismo_id_en_dos_fechas_no_se_duplica(self):
        async def obtener(fecha):
            return dia([partido(8749311)], self.AHORA)

        self.assertEqual(len(correr(en_vivo.en_vivo(self.AHORA, obtener, ligas))["partidos"]), 1)

    def test_pronostico_previo_marcado(self):
        async def obtener(fecha):
            return dia([partido(8749311)] if fecha == "2026-11-19" else [], self.AHORA)

        r = correr(en_vivo.en_vivo(self.AHORA, obtener, ligas, lambda: {8749311}))
        self.assertTrue(r["partidos"][0]["pronostico_previo"])


class ConsultaCompartida(Base):
    def setUp(self):
        super().setUp()
        footystats._cache_dia.clear()
        footystats._candados_dia.clear()
        self.original = footystats._get

    def tearDown(self):
        footystats._get = self.original
        super().tearDown()

    def test_muchos_usuarios_a_la_vez_hacen_una_sola_consulta(self):
        llamadas = []

        async def falso(endpoint, **params):
            llamadas.append((endpoint, params["date"], params.get("timezone")))
            await asyncio.sleep(0.05)
            return {"success": True, "data": [{"id": 1, "date_unix": 5, "status": "incomplete"}], "pager": {"max_page": 1}}

        footystats._get = falso

        async def usuarios():
            return await asyncio.gather(*(footystats.partidos_del_dia("2026-11-19") for _ in range(25)))

        self.assertEqual(len(correr(usuarios())), 25)
        self.assertEqual(llamadas, [("todays-matches", "2026-11-19", "America/Lima")])  # una sola, y en hora de Perú

    def test_pagina_2_fallida_deja_cobertura_parcial(self):
        async def falso(endpoint, **params):
            if params["page"] == 2:
                raise footystats.FootyStatsError("conexion", "x")
            return {"success": True, "data": [{"id": 1, "date_unix": 5, "status": "incomplete"}], "pager": {"max_page": 2}}

        footystats._get = falso
        r = correr(footystats.partidos_del_dia("2026-11-19"))
        self.assertFalse(r["completo"])
        self.assertEqual(len(r["partidos"]), 1)

    def test_si_falla_se_sirve_la_ultima_copia_marcada_como_antigua(self):
        async def bien(endpoint, **params):
            return {"success": True, "data": [{"id": 1, "date_unix": 5, "status": "incomplete"}], "pager": {"max_page": 1}}

        async def mal(endpoint, **params):
            raise footystats.FootyStatsError("conexion", "x")

        footystats._get = bien
        correr(footystats.partidos_del_dia("2026-11-19"))
        footystats._cache_dia["2026-11-19"]["consultado"] -= footystats.TTL_DIA_S + 1
        footystats._get = mal
        self.assertTrue(correr(footystats.partidos_del_dia("2026-11-19"))["obsoleto"])

    def test_sin_copia_el_error_sube(self):
        async def mal(endpoint, **params):
            raise footystats.FootyStatsError("credenciales", "x")

        footystats._get = mal
        with self.assertRaises(footystats.FootyStatsError):
            correr(footystats.partidos_del_dia("2026-11-19"))


if __name__ == "__main__":
    unittest.main()
