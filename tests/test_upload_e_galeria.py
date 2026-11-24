import io
import json
import os
import secrets
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import create_app

SEGREDO_FORTE = secrets.token_hex(32)

JPEG_VALIDO = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
PNG_VALIDO = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
GIF_VALIDO = b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"
WEBP_VALIDO = b"RIFF\x14\x00\x00\x00WEBPVP8 \x08\x00\x00\x00"
MP4_VALIDO = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
WEBM_VALIDO = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01"


class TestUploadEGaleria(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.app = self._criar_app()
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _criar_app(self, **extras):
        env = {
            "EVENTOS_ENV": "production",
            "EVENTOS_DEBUG": "0",
            "EVENTOS_SECRET_KEY": SEGREDO_FORTE,
            "EVENTOS_DATA_DIR": self.tmpdir.name,
            "EVENTOS_MAX_UPLOAD_MB": "10",
            "EVENTOS_MAX_FILES_PER_UPLOAD": "5",
        }
        env.update(extras)
        return create_app(env)

    def test_index_e_cabecalhos_seguranca(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(resp.headers.get("Referrer-Policy"), "same-origin")
        self.assertIn("Limite de 10 MB".encode("utf-8"), resp.data)

    def test_health_ok(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"status": "ok"})

    def test_fluxo_completo_upload_galeria_e_download(self):
        resp = self.client.post(
            "/upload",
            data={
                "data": "2026-10-22",
                "hora": "14:30",
                "local": "Auditório Principal",
                "arquivos": [
                    (io.BytesIO(JPEG_VALIDO), "foto evento.jpg"),
                    (io.BytesIO(MP4_VALIDO), "video_abertura.mp4"),
                ],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("2 arquivo(s) enviado(s) com sucesso!".encode("utf-8"), resp.data)

        db_file = self.app.config["DB_FILE"]
        with open(db_file, "r", encoding="utf-8") as f:
            eventos = json.load(f)
        self.assertEqual(len(eventos), 1)
        evento = eventos[0]
        self.assertEqual(evento["data"], "2026-10-22")
        self.assertEqual(evento["hora"], "14:30")
        self.assertEqual(evento["local"], "Auditório Principal")
        self.assertEqual(len(evento["arquivos"]), 2)
        self.assertEqual(evento["arquivos"][0]["tipo"], "imagem")
        self.assertEqual(evento["arquivos"][1]["tipo"], "video")

        galeria = self.client.get("/galeria")
        self.assertEqual(galeria.status_code, 200)
        self.assertIn("Auditório Principal".encode("utf-8"), galeria.data)
        self.assertIn("2026-10-22".encode("utf-8"), galeria.data)

        nome_jpg = evento["arquivos"][0]["nome"]
        download = self.client.get(f"/uploads/2026-10-22/{nome_jpg}")
        try:
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.data, JPEG_VALIDO)
            self.assertEqual(download.headers.get("X-Content-Type-Options"), "nosniff")
        finally:
            download.close()

    def test_preserva_eventos_json_existente_e_ordena_datas_decrescente(self):
        pasta_antiga = os.path.join(self.app.config["UPLOAD_ROOT"], "2025-05-10")
        os.makedirs(pasta_antiga, exist_ok=True)
        with open(os.path.join(pasta_antiga, "legado.png"), "wb") as f:
            f.write(PNG_VALIDO)

        with open(self.app.config["DB_FILE"], "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "id": "evento-legado",
                        "data": "2025-05-10",
                        "hora": "09:00",
                        "local": "Sala Antiga",
                        "arquivos": [{"nome": "legado.png", "tipo": "imagem"}],
                        "criado_em": "2025-05-10T09:05:00",
                    }
                ],
                f,
            )

        self.client.post(
            "/upload",
            data={
                "data": "2026-11-24",
                "hora": "",
                "local": "Pátio Novo",
                "arquivos": [(io.BytesIO(GIF_VALIDO), "animacao.gif")],
            },
            content_type="multipart/form-data",
        )

        galeria = self.client.get("/galeria")
        self.assertEqual(galeria.status_code, 200)
        texto = galeria.data.decode("utf-8")
        self.assertIn("Sala Antiga", texto)
        self.assertIn("Pátio Novo", texto)
        self.assertLess(texto.index("2026-11-24"), texto.index("2025-05-10"))

        resp_legado = self.client.get("/uploads/2025-05-10/legado.png")
        try:
            self.assertEqual(resp_legado.status_code, 200)
            self.assertEqual(resp_legado.data, PNG_VALIDO)
        finally:
            resp_legado.close()

    def test_validacao_campos_e_path_traversal_no_upload(self):
        casos_invalidos = [
            {"data": "", "hora": "", "local": "Sala"},
            {"data": "2026-10-22", "hora": "", "local": ""},
            {"data": "2026-02-30", "hora": "", "local": "Sala"},
            {"data": "../../etc", "hora": "", "local": "Sala"},
            {"data": "2026-10-22", "hora": "25:99", "local": "Sala"},
            {"data": "2026-10-22", "hora": "10:00", "local": "A" * 201},
            {"data": "2026-10-22", "hora": "10:00", "local": "Linha1\nLinha2"},
        ]
        for campos in casos_invalidos:
            with self.subTest(campos=campos):
                resp = self.client.post(
                    "/upload",
                    data={**campos, "arquivos": [(io.BytesIO(JPEG_VALIDO), "foto.jpg")]},
                    content_type="multipart/form-data",
                    follow_redirects=True,
                )
                self.assertEqual(resp.status_code, 200)
                self.assertIn('class="msg erro"'.encode("utf-8"), resp.data)
        self.assertFalse(os.path.exists(self.app.config["DB_FILE"]))

    def test_rejeita_extensao_invalida_conteudo_falso_e_arquivo_vazio(self):
        resp = self.client.post(
            "/upload",
            data={
                "data": "2026-10-22",
                "hora": "10:00",
                "local": "Sala B",
                "arquivos": [
                    (io.BytesIO(b"<script>alert(1)</script>"), "falso.jpg"),
                    (io.BytesIO(b""), "vazio.png"),
                    (io.BytesIO(b"#!/bin/sh\necho oi"), "script.sh"),
                ],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Nenhum arquivo válido".encode("utf-8"), resp.data)
        self.assertFalse(os.path.exists(self.app.config["DB_FILE"]))

    def test_mistura_arquivo_valido_e_invalido_salva_valido_e_avisa(self):
        resp = self.client.post(
            "/upload",
            data={
                "data": "2026-10-22",
                "hora": "11:00",
                "local": "Auditório",
                "arquivos": [
                    (io.BytesIO(WEBP_VALIDO), "banner.webp"),
                    (io.BytesIO(b"texto puro"), "nao_e_imagem.jpg"),
                    (io.BytesIO(WEBM_VALIDO), "clipe.webm"),
                ],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("2 arquivo(s) enviado(s) com sucesso!".encode("utf-8"), resp.data)
        self.assertIn("1 arquivo(s) ignorado(s)".encode("utf-8"), resp.data)

    def test_limite_quantidade_arquivos(self):
        arquivos = [(io.BytesIO(JPEG_VALIDO), f"foto_{i}.jpg") for i in range(6)]
        resp = self.client.post(
            "/upload",
            data={"data": "2026-10-22", "hora": "", "local": "Sala", "arquivos": arquivos},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("máximo 5 arquivos".encode("utf-8"), resp.data)

    def test_limite_tamanho_upload_retorna_413_amigavel(self):
        app_pequeno = self._criar_app(EVENTOS_MAX_UPLOAD_MB="1")
        client = app_pequeno.test_client()
        payload_grande = JPEG_VALIDO + (b"\x00" * (1024 * 1024 + 4096))

        resp = client.post(
            "/upload",
            data={
                "data": "2026-10-22",
                "hora": "",
                "local": "Sala",
                "arquivos": [(io.BytesIO(payload_grande), "grande.jpg")],
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 413)
        self.assertIn("1 MB".encode("utf-8"), resp.data)
        self.assertNotIn(b"Traceback", resp.data)
        self.assertNotIn(self.tmpdir.name.encode("utf-8"), resp.data)

    def test_path_traversal_na_rota_uploads_retorna_404(self):
        with open(os.path.join(self.tmpdir.name, "segredo.txt"), "w", encoding="utf-8") as f:
            f.write("confidencial")

        rotas = [
            "/uploads/2026-10-22/../../segredo.txt",
            "/uploads/..%2F../segredo.txt",
            "/uploads/invalida/foto.jpg",
            "/uploads/2026-10-22/.oculto.jpg",
            "/uploads/2026-10-22/inexistente.jpg",
        ]
        for rota in rotas:
            with self.subTest(rota=rota):
                resp = self.client.get(rota)
                self.assertEqual(resp.status_code, 404)
                self.assertNotIn(b"confidencial", resp.data)

    def test_eventos_json_corrompido_retorna_500_sem_vazar_detalhes_e_sem_sobrescrever(self):
        conteudo_corrompido = "{json invalido"
        with open(self.app.config["DB_FILE"], "w", encoding="utf-8") as f:
            f.write(conteudo_corrompido)

        resp = self.client.get("/galeria")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("Erro interno".encode("utf-8"), resp.data)
        self.assertNotIn(b"Traceback", resp.data)
        self.assertNotIn(self.tmpdir.name.encode("utf-8"), resp.data)

        with open(self.app.config["DB_FILE"], "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), conteudo_corrompido)

    def test_rollback_remove_arquivos_quando_falha_gravacao_json(self):
        with patch("app.salvar_eventos", side_effect=OSError("disco cheio")):
            resp = self.client.post(
                "/upload",
                data={
                    "data": "2026-10-22",
                    "hora": "15:00",
                    "local": "Sala C",
                    "arquivos": [(io.BytesIO(JPEG_VALIDO), "foto.jpg")],
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(resp.status_code, 500)
        self.assertNotIn(b"disco cheio", resp.data)
        pasta_data = os.path.join(self.app.config["UPLOAD_ROOT"], "2026-10-22")
        arquivos_restantes = os.listdir(pasta_data) if os.path.isdir(pasta_data) else []
        self.assertEqual(arquivos_restantes, [])

    def test_uploads_concorrentes_preservam_todos_registros(self):
        erros = []
        total_threads = 8

        def enviar(idx):
            try:
                client = self.app.test_client()
                resp = client.post(
                    "/upload",
                    data={
                        "data": "2026-11-24",
                        "hora": f"10:0{idx}",
                        "local": f"Sala {idx}",
                        "arquivos": [(io.BytesIO(PNG_VALIDO), f"img_{idx}.png")],
                    },
                    content_type="multipart/form-data",
                )
                if resp.status_code != 302:
                    erros.append(f"Thread {idx} retornou {resp.status_code}")
            except Exception as exc:  # pragma: no cover
                erros.append(str(exc))

        threads = [threading.Thread(target=enviar, args=(i,)) for i in range(total_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(erros, [])
        with open(self.app.config["DB_FILE"], "r", encoding="utf-8") as f:
            eventos = json.load(f)
        self.assertEqual(len(eventos), total_threads)
        locais = {e["local"] for e in eventos}
        self.assertEqual(locais, {f"Sala {i}" for i in range(total_threads)})


if __name__ == "__main__":
    unittest.main()
