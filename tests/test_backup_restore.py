import json
import os
import stat
import subprocess
import tarfile
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_SCRIPT = os.path.join(BASE_DIR, "deploy", "backup.sh")
RESTORE_SCRIPT = os.path.join(BASE_DIR, "deploy", "restore.sh")


class TestBackupERestauracao(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmpdir.name, "dados")
        self.backup_dir = os.path.join(self.tmpdir.name, "backups")
        self.restore_dir = os.path.join(self.tmpdir.name, "restaurado")
        os.makedirs(os.path.join(self.data_dir, "uploads", "2026-10-22"), exist_ok=True)

        self.eventos_originais = [
            {
                "id": "abc123",
                "data": "2026-10-22",
                "hora": "14:00",
                "local": "Auditório",
                "arquivos": [{"nome": "12345678_foto.jpg", "tipo": "imagem"}],
                "criado_em": "2026-10-22T14:01:00",
            }
        ]
        with open(os.path.join(self.data_dir, "eventos.json"), "w", encoding="utf-8") as f:
            json.dump(self.eventos_originais, f)
        self.conteudo_foto = b"\xff\xd8\xff\xe0conteudo-foto-teste"
        with open(
            os.path.join(self.data_dir, "uploads", "2026-10-22", "12345678_foto.jpg"), "wb"
        ) as f:
            f.write(self.conteudo_foto)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_backup_e_restauracao_completos(self):
        res_backup = subprocess.run(
            ["bash", BACKUP_SCRIPT, self.data_dir, self.backup_dir],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_backup.returncode, 0, msg=res_backup.stderr)

        arquivos_tar = [f for f in os.listdir(self.backup_dir) if f.endswith(".tar.gz")]
        self.assertEqual(len(arquivos_tar), 1)
        tar_path = os.path.join(self.backup_dir, arquivos_tar[0])
        sha_path = tar_path + ".sha256"
        self.assertTrue(os.path.exists(sha_path))

        # Permissões restritas (umask 077 -> sem acesso para grupo/outros)
        modo = stat.S_IMODE(os.stat(tar_path).st_mode)
        self.assertEqual(modo & 0o077, 0)

        # Prepara um destino com dados antigos para validar a cópia pré-restauração
        os.makedirs(self.restore_dir, exist_ok=True)
        with open(os.path.join(self.restore_dir, "eventos.json"), "w", encoding="utf-8") as f:
            f.write("[]")

        res_restore = subprocess.run(
            ["bash", RESTORE_SCRIPT, tar_path, self.restore_dir],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_restore.returncode, 0, msg=res_restore.stderr)

        with open(os.path.join(self.restore_dir, "eventos.json"), "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), self.eventos_originais)

        foto_restaurada = os.path.join(
            self.restore_dir, "uploads", "2026-10-22", "12345678_foto.jpg"
        )
        with open(foto_restaurada, "rb") as f:
            self.assertEqual(f.read(), self.conteudo_foto)

        # Cópia de segurança pré-restauração deve existir
        pre_restores = [
            d for d in os.listdir(self.restore_dir) if d.startswith(".pre-restore-")
        ]
        self.assertEqual(len(pre_restores), 1)

    def test_restore_rejeita_checksum_adulterado(self):
        subprocess.run(
            ["bash", BACKUP_SCRIPT, self.data_dir, self.backup_dir],
            check=True,
            capture_output=True,
        )
        tar_path = os.path.join(
            self.backup_dir,
            [f for f in os.listdir(self.backup_dir) if f.endswith(".tar.gz")][0],
        )
        with open(tar_path + ".sha256", "w", encoding="utf-8") as f:
            f.write("0" * 64 + "  " + os.path.basename(tar_path) + "\n")

        res = subprocess.run(
            ["bash", RESTORE_SCRIPT, tar_path, self.restore_dir],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(res.returncode, 0)

    def test_restore_rejeita_tar_com_path_traversal(self):
        tar_malicioso = os.path.join(self.tmpdir.name, "malicioso.tar.gz")
        arquivo_tmp = os.path.join(self.tmpdir.name, "x.txt")
        with open(arquivo_tmp, "w", encoding="utf-8") as f:
            f.write("oi")
        with tarfile.open(tar_malicioso, "w:gz") as tar:
            tar.add(arquivo_tmp, arcname="../fora.txt")

        res = subprocess.run(
            ["bash", RESTORE_SCRIPT, tar_malicioso, self.restore_dir],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("insegura", res.stderr)


if __name__ == "__main__":
    unittest.main()
