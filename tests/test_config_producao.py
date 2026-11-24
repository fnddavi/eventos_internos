import secrets
import socket
import tempfile
import threading
import unittest
import urllib.request

from waitress import create_server

from app import create_app
from wsgi import criar_aplicacao_producao

SEGREDO_FORTE = secrets.token_hex(32)


class TestConfiguracaoProducao(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _base_env(self, **extras):
        env = {
            "EVENTOS_ENV": "production",
            "EVENTOS_DEBUG": "0",
            "EVENTOS_SECRET_KEY": SEGREDO_FORTE,
            "EVENTOS_DATA_DIR": self.tmpdir.name,
            "EVENTOS_HOST": None,
            "EVENTOS_PORT": None,
            "EVENTOS_THREADS": None,
            "EVENTOS_MAX_UPLOAD_MB": None,
            "EVENTOS_MAX_FILES_PER_UPLOAD": None,
            "EVENTOS_LOG_LEVEL": None,
            "EVENTOS_SESSION_COOKIE_SECURE": None,
        }
        env.update(extras)
        return env

    def test_producao_recusa_sem_secret_key(self):
        with self.assertRaises(RuntimeError):
            create_app(self._base_env(EVENTOS_SECRET_KEY=None))

    def test_producao_recusa_secret_key_antigo_ou_curto(self):
        for invalido in ["troque-esta-chave-secreta", "changeme", "curto-demais-123"]:
            with self.subTest(secret=invalido):
                with self.assertRaises(RuntimeError):
                    create_app(self._base_env(EVENTOS_SECRET_KEY=invalido))

    def test_producao_recusa_debug_ativo(self):
        with self.assertRaises(RuntimeError):
            create_app(self._base_env(EVENTOS_DEBUG="1"))

    def test_producao_padroes_seguros(self):
        app = create_app(self._base_env())
        self.assertEqual(app.config["ENV_MODE"], "production")
        self.assertFalse(app.config["DEBUG"])
        self.assertEqual(app.config["EVENTOS_HOST"], "127.0.0.1")
        self.assertEqual(app.config["EVENTOS_PORT"], 5000)
        self.assertEqual(app.config["EVENTOS_THREADS"], 8)
        self.assertEqual(app.config["MAX_UPLOAD_MB"], 100)
        self.assertEqual(app.config["MAX_CONTENT_LENGTH"], 100 * 1024 * 1024)
        self.assertEqual(app.config["MAX_FILES_PER_UPLOAD"], 20)
        self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertFalse(app.config["SESSION_COOKIE_SECURE"])

    def test_desenvolvimento_gera_segredo_efemero_e_recusa_placeholder(self):
        app1 = create_app(
            self._base_env(EVENTOS_ENV="development", EVENTOS_SECRET_KEY=None)
        )
        app2 = create_app(
            self._base_env(EVENTOS_ENV="development", EVENTOS_SECRET_KEY=None)
        )
        self.assertNotEqual(app1.secret_key, app2.secret_key)
        self.assertGreaterEqual(len(app1.secret_key), 32)

        with self.assertRaises(RuntimeError):
            create_app(
                self._base_env(
                    EVENTOS_ENV="development",
                    EVENTOS_SECRET_KEY="troque-esta-chave-secreta",
                )
            )

    def test_parametros_configuraveis(self):
        app = create_app(
            self._base_env(
                EVENTOS_HOST="192.168.10.25",
                EVENTOS_PORT="8080",
                EVENTOS_THREADS="12",
                EVENTOS_MAX_UPLOAD_MB="250",
                EVENTOS_MAX_FILES_PER_UPLOAD="50",
                EVENTOS_SESSION_COOKIE_SECURE="1",
                EVENTOS_LOG_LEVEL="WARNING",
            )
        )
        self.assertEqual(app.config["EVENTOS_HOST"], "192.168.10.25")
        self.assertEqual(app.config["EVENTOS_PORT"], 8080)
        self.assertEqual(app.config["EVENTOS_THREADS"], 12)
        self.assertEqual(app.config["MAX_CONTENT_LENGTH"], 250 * 1024 * 1024)
        self.assertEqual(app.config["MAX_FILES_PER_UPLOAD"], 50)
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])

    def test_parametros_invalidos_disparam_erro(self):
        casos = [
            {"EVENTOS_ENV": "staging"},
            {"EVENTOS_PORT": "0"},
            {"EVENTOS_PORT": "70000"},
            {"EVENTOS_PORT": "abc"},
            {"EVENTOS_THREADS": "0"},
            {"EVENTOS_MAX_UPLOAD_MB": "0"},
            {"EVENTOS_MAX_FILES_PER_UPLOAD": "-1"},
            {"EVENTOS_LOG_LEVEL": "TRACE"},
            {"EVENTOS_HOST": "127.0.0.1 8080"},
        ]
        for caso in casos:
            with self.subTest(caso=caso):
                with self.assertRaises(ValueError):
                    create_app(self._base_env(**caso))

    def test_waitress_wsgi_serve_health_e_index(self):
        app = criar_aplicacao_producao(self._base_env())

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            porta_livre = s.getsockname()[1]

        servidor = create_server(
            app,
            host="127.0.0.1",
            port=porta_livre,
            threads=2,
            ident="eventos-internos",
        )
        thread = threading.Thread(target=servidor.run, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{porta_livre}/health", timeout=5
            ) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn(b'"status":"ok"', resp.read().replace(b" ", b""))
                self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")

            with urllib.request.urlopen(
                f"http://127.0.0.1:{porta_livre}/", timeout=5
            ) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn(b"Enviar fotos", resp.read())
        finally:
            servidor.task_dispatcher.shutdown()
            servidor.close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
