from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import uuid

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

EXTENSOES_PERMITIDAS = {
    "imagem": {".jpg", ".jpeg", ".png", ".gif", ".webp"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
}

SEGREDOS_PROIBIDOS = {
    "",
    "troque-esta-chave-secreta",
    "troque-por-uma-chave-segura",
    "changeme",
    "change-me",
    "secret",
    "dev",
    "default",
    "password",
    "123456",
}

TAMANHO_MINIMO_SEGREDO = 32
TAMANHO_MAXIMO_LOCAL = 200
TAMANHO_MAXIMO_NOME_BASE = 80
AMBIENTES_VALIDOS = {"production", "development", "testing"}
NIVEIS_LOG_VALIDOS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

_PADRAO_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PADRAO_HORA = re.compile(r"^\d{2}:\d{2}$")
_MP4_BOXES = {b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot"}

logger = logging.getLogger("eventos_internos")


class ErroArmazenamento(Exception):
    """Erro operacional ao ler ou gravar os dados locais da aplicação."""


def _interpretar_booleano(valor, nome_var):
    if isinstance(valor, bool):
        return valor
    texto = str(valor).strip().lower()
    if texto in {"1", "true", "yes", "on", "sim"}:
        return True
    if texto in {"0", "false", "no", "off", "nao", "não", ""}:
        return False
    raise ValueError(f"Valor inválido para {nome_var}: use 0/1 ou true/false.")


def _interpretar_inteiro(valor, nome_var, minimo, maximo):
    try:
        numero = int(str(valor).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Valor inválido para {nome_var}: deve ser um número inteiro.") from exc
    if numero < minimo or numero > maximo:
        raise ValueError(f"Valor fora do intervalo permitido para {nome_var} ({minimo}..{maximo}).")
    return numero


def _configurar_logging(app, nivel_nome):
    nivel = getattr(logging, nivel_nome)
    app.logger.setLevel(nivel)
    logger.setLevel(nivel)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    logger.propagate = False


def carregar_configuracao(overrides=None):
    """Lê e valida a configuração da aplicação com padrões seguros para produção."""
    fonte = dict(os.environ)
    if overrides:
        for chave, valor in overrides.items():
            if valor is None:
                fonte.pop(chave, None)
            else:
                fonte[chave] = valor

    ambiente = str(fonte.get("EVENTOS_ENV", "production")).strip().lower()
    if ambiente not in AMBIENTES_VALIDOS:
        raise ValueError(
            f"EVENTOS_ENV inválido ({ambiente!r}). Valores aceitos: production, development, testing."
        )

    debug = _interpretar_booleano(fonte.get("EVENTOS_DEBUG", "0"), "EVENTOS_DEBUG")
    if ambiente == "production" and debug:
        raise RuntimeError(
            "Modo debug (EVENTOS_DEBUG=1) não é permitido quando EVENTOS_ENV=production."
        )

    secret_key = str(fonte.get("EVENTOS_SECRET_KEY", "")).strip()
    if secret_key.lower() in SEGREDOS_PROIBIDOS:
        if secret_key != "":
            raise RuntimeError(
                "EVENTOS_SECRET_KEY usa um valor inseguro ou padrão. Gere uma chave forte com: "
                "python3 -c 'import secrets; print(secrets.token_hex(32))'"
            )
        if ambiente == "production":
            raise RuntimeError(
                "EVENTOS_SECRET_KEY é obrigatório em produção. Gere uma chave forte com: "
                "python3 -c 'import secrets; print(secrets.token_hex(32))'"
            )
        secret_key = secrets.token_hex(32)
        segredo_efemero = True
    else:
        if ambiente == "production" and len(secret_key) < TAMANHO_MINIMO_SEGREDO:
            raise RuntimeError(
                f"EVENTOS_SECRET_KEY deve ter ao menos {TAMANHO_MINIMO_SEGREDO} caracteres em produção."
            )
        segredo_efemero = False

    host = str(fonte.get("EVENTOS_HOST", "127.0.0.1")).strip()
    if not host or any(c.isspace() for c in host):
        raise ValueError("EVENTOS_HOST inválido: informe um endereço IP ou hostname válido.")

    port = _interpretar_inteiro(fonte.get("EVENTOS_PORT", "5000"), "EVENTOS_PORT", 1, 65535)
    threads = _interpretar_inteiro(fonte.get("EVENTOS_THREADS", "8"), "EVENTOS_THREADS", 1, 128)
    max_upload_mb = _interpretar_inteiro(
        fonte.get("EVENTOS_MAX_UPLOAD_MB", "100"), "EVENTOS_MAX_UPLOAD_MB", 1, 4096
    )
    max_files = _interpretar_inteiro(
        fonte.get("EVENTOS_MAX_FILES_PER_UPLOAD", "20"),
        "EVENTOS_MAX_FILES_PER_UPLOAD",
        1,
        500,
    )

    cookie_secure = _interpretar_booleano(
        fonte.get("EVENTOS_SESSION_COOKIE_SECURE", "0"),
        "EVENTOS_SESSION_COOKIE_SECURE",
    )

    log_level = str(fonte.get("EVENTOS_LOG_LEVEL", "INFO")).strip().upper()
    if log_level not in NIVEIS_LOG_VALIDOS:
        raise ValueError(
            f"EVENTOS_LOG_LEVEL inválido ({log_level!r}). Valores aceitos: {', '.join(sorted(NIVEIS_LOG_VALIDOS))}."
        )

    data_dir = os.path.abspath(str(fonte.get("EVENTOS_DATA_DIR", BASE_DIR)).strip() or BASE_DIR)
    upload_dir = os.path.abspath(
        str(fonte.get("EVENTOS_UPLOAD_DIR", os.path.join(data_dir, "uploads"))).strip()
    )
    db_file = os.path.abspath(
        str(fonte.get("EVENTOS_DB_FILE", os.path.join(data_dir, "eventos.json"))).strip()
    )
    lock_file = os.path.join(os.path.dirname(db_file), ".eventos.lock")

    return {
        "ENV_MODE": ambiente,
        "DEBUG": debug,
        "TESTING": ambiente == "testing" or bool(fonte.get("TESTING", False)),
        "SECRET_KEY": secret_key,
        "SEGREDO_EFEMERO": segredo_efemero,
        "EVENTOS_HOST": host,
        "EVENTOS_PORT": port,
        "EVENTOS_THREADS": threads,
        "EVENTOS_DATA_DIR": data_dir,
        "UPLOAD_ROOT": upload_dir,
        "DB_FILE": db_file,
        "LOCK_FILE": lock_file,
        "MAX_UPLOAD_MB": max_upload_mb,
        "MAX_CONTENT_LENGTH": max_upload_mb * 1024 * 1024,
        "MAX_FILES_PER_UPLOAD": max_files,
        "LOG_LEVEL": log_level,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": cookie_secure,
    }


def _garantir_diretorio(caminho):
    os.makedirs(caminho, mode=0o750, exist_ok=True)


def _caminho_dentro_de(base, alvo):
    base_real = os.path.realpath(base)
    alvo_real = os.path.realpath(alvo)
    try:
        return os.path.commonpath([base_real, alvo_real]) == base_real
    except ValueError:
        return False


def _definir_permissao_segura(caminho, modo=0o640):
    try:
        os.chmod(caminho, modo)
    except OSError:
        pass


@contextmanager
def _trava_dados(app, exclusivo=False):
    """Coordena acesso concorrente entre threads e processos (incluindo backup)."""
    thread_lock = app.extensions["db_lock"]
    lock_file = app.config["LOCK_FILE"]
    _garantir_diretorio(os.path.dirname(lock_file))

    with thread_lock:
        with open(lock_file, "a+", encoding="utf-8") as fp:
            modo_flock = fcntl.LOCK_EX if exclusivo else fcntl.LOCK_SH
            fcntl.flock(fp.fileno(), modo_flock)
            try:
                yield
            finally:
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)


def carregar_eventos(app=None, usar_trava=True):
    alvo_app = app or current_app
    db_file = alvo_app.config["DB_FILE"]

    def _ler():
        if not os.path.exists(db_file):
            return []
        try:
            with open(db_file, "r", encoding="utf-8") as f:
                dados = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.exception("Falha ao ler arquivo de eventos.")
            raise ErroArmazenamento("Não foi possível ler os registros de eventos.") from exc

        if not isinstance(dados, list):
            logger.error("Estrutura inválida no arquivo de eventos: esperado JSON array.")
            raise ErroArmazenamento("Estrutura inválida no arquivo de eventos.")
        return dados

    if usar_trava:
        with _trava_dados(alvo_app, exclusivo=False):
            return _ler()
    return _ler()


def salvar_eventos(eventos, app=None, usar_trava=False):
    alvo_app = app or current_app
    db_file = alvo_app.config["DB_FILE"]
    diretorio_db = os.path.dirname(db_file)
    _garantir_diretorio(diretorio_db)

    def _escrever_atomico():
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=diretorio_db,
                prefix=".eventos_",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp_path = tmp.name
                json.dump(eventos, tmp, ensure_ascii=False, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
            _definir_permissao_segura(tmp_path, 0o640)
            os.replace(tmp_path, db_file)
        except OSError as exc:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            logger.exception("Falha ao gravar arquivo de eventos.")
            raise ErroArmazenamento("Não foi possível salvar os registros de eventos.") from exc

    if usar_trava:
        with _trava_dados(alvo_app, exclusivo=True):
            _escrever_atomico()
    else:
        _escrever_atomico()


def validar_data(data):
    if not _PADRAO_DATA.match(data):
        return False
    try:
        datetime.strptime(data, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def validar_hora(hora):
    if hora == "":
        return True
    if not _PADRAO_HORA.match(hora):
        return False
    try:
        datetime.strptime(hora, "%H:%M")
        return True
    except ValueError:
        return False


def validar_local(local):
    if not local or len(local) > TAMANHO_MAXIMO_LOCAL:
        return False
    if any(ord(c) < 32 or ord(c) == 127 for c in local):
        return False
    return True


def tipo_arquivo(nome_arquivo):
    ext = os.path.splitext(nome_arquivo)[1].lower()
    for tipo, extensoes in EXTENSOES_PERMITIDAS.items():
        if ext in extensoes:
            return tipo
    return None


def nome_seguro(nome_original):
    basename = os.path.basename(str(nome_original).replace("\\", "/"))
    stem, ext = os.path.splitext(basename)
    ext = ext.lower()

    stem_limpo = "".join(c for c in stem if c.isalnum() or c in "._-")
    stem_limpo = re.sub(r"\.{2,}", ".", stem_limpo).strip("._-")
    if not stem_limpo:
        stem_limpo = "arquivo"
    stem_limpo = stem_limpo[:TAMANHO_MAXIMO_NOME_BASE]

    ext_limpa = "".join(c for c in ext if c.isalnum() or c == ".")
    if not ext_limpa.startswith(".") or ext_limpa == ".":
        ext_limpa = ""
    return f"{stem_limpo}{ext_limpa}"


def validar_assinatura_arquivo(stream, nome_arquivo):
    """Valida cabeçalho binário mínimo para rejeitar arquivos vazios ou renomeados."""
    ext = os.path.splitext(nome_arquivo)[1].lower()
    pos_inicial = stream.tell()
    try:
        cabecalho = stream.read(16)
    finally:
        stream.seek(pos_inicial)

    if not cabecalho:
        return False

    if ext in {".jpg", ".jpeg"}:
        return cabecalho.startswith(b"\xff\xd8\xff")
    if ext == ".png":
        return cabecalho.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == ".gif":
        return cabecalho.startswith((b"GIF87a", b"GIF89a"))
    if ext == ".webp":
        return len(cabecalho) >= 12 and cabecalho[:4] == b"RIFF" and cabecalho[8:12] == b"WEBP"
    if ext in {".mp4", ".mov"}:
        return len(cabecalho) >= 8 and cabecalho[4:8] in _MP4_BOXES
    if ext == ".avi":
        return len(cabecalho) >= 12 and cabecalho[:4] == b"RIFF" and cabecalho[8:12] == b"AVI "
    if ext in {".mkv", ".webm"}:
        return cabecalho.startswith(b"\x1a\x45\xdf\xa3")
    return False


def _nome_arquivo_rota_valido(nome_arquivo):
    if not nome_arquivo or nome_arquivo.startswith("."):
        return False
    if "/" in nome_arquivo or "\\" in nome_arquivo or "\x00" in nome_arquivo or ".." in nome_arquivo:
        return False
    return True


def _limpar_arquivos_parciais(caminhos):
    for caminho in caminhos:
        try:
            if os.path.exists(caminho):
                os.remove(caminho)
        except OSError:
            logger.warning("Não foi possível remover arquivo parcial durante rollback.")


def create_app(config_override=None):
    config = carregar_configuracao(config_override)

    app = Flask(__name__)
    app.config.update(config)
    app.secret_key = config["SECRET_KEY"]
    app.extensions["db_lock"] = threading.Lock()

    _configurar_logging(app, config["LOG_LEVEL"])
    _garantir_diretorio(config["EVENTOS_DATA_DIR"])
    _garantir_diretorio(config["UPLOAD_ROOT"])
    _garantir_diretorio(os.path.dirname(config["DB_FILE"]))

    if config["SEGREDO_EFEMERO"] and config["ENV_MODE"] == "development":
        logger.warning(
            "EVENTOS_SECRET_KEY não definida em modo desenvolvimento; usando chave efêmera."
        )

    logger.info(
        "Aplicação inicializada (env=%s, max_upload_mb=%s, max_files=%s)",
        config["ENV_MODE"],
        config["MAX_UPLOAD_MB"],
        config["MAX_FILES_PER_UPLOAD"],
    )

    @app.after_request
    def aplicar_cabecalhos_seguranca(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            max_upload_mb=app.config["MAX_UPLOAD_MB"],
            max_files=app.config["MAX_FILES_PER_UPLOAD"],
        )

    @app.route("/health")
    def health():
        upload_root = app.config["UPLOAD_ROOT"]
        if not os.path.isdir(upload_root) or not os.access(upload_root, os.W_OK):
            logger.error("Falha na verificação de saúde: diretório de uploads indisponível.")
            return jsonify({"status": "error"}), 503
        return jsonify({"status": "ok"}), 200

    @app.route("/upload", methods=["POST"])
    def upload():
        data = request.form.get("data", "").strip()
        hora = request.form.get("hora", "").strip()
        local = request.form.get("local", "").strip()
        arquivos = request.files.getlist("arquivos")

        if not data or not local:
            flash("Preencha a data e o local do evento.", "erro")
            return redirect(url_for("index"))

        if not validar_data(data):
            flash("Data inválida. Use o formato AAAA-MM-DD.", "erro")
            return redirect(url_for("index"))

        if not validar_hora(hora):
            flash("Horário inválido. Use o formato HH:MM.", "erro")
            return redirect(url_for("index"))

        if not validar_local(local):
            flash(
                f"Local inválido (máximo de {TAMANHO_MAXIMO_LOCAL} caracteres, sem quebras de linha).",
                "erro",
            )
            return redirect(url_for("index"))

        arquivos_enviados = [a for a in arquivos if a and a.filename and a.filename.strip() != ""]
        if not arquivos_enviados:
            flash("Selecione ao menos um arquivo (imagem ou vídeo).", "erro")
            return redirect(url_for("index"))

        max_files = app.config["MAX_FILES_PER_UPLOAD"]
        if len(arquivos_enviados) > max_files:
            flash(f"Envie no máximo {max_files} arquivos por vez.", "erro")
            return redirect(url_for("index"))

        upload_root = app.config["UPLOAD_ROOT"]
        pasta_data = os.path.join(upload_root, data)
        if not _caminho_dentro_de(upload_root, pasta_data):
            logger.warning("Tentativa de acesso fora do diretório de uploads rejeitada (ip=%s).", request.remote_addr)
            flash("Data inválida para armazenamento do evento.", "erro")
            return redirect(url_for("index"))

        arquivos_salvos = []
        caminhos_criados = []
        ignorados = 0
        pasta_criada = False

        try:
            for arquivo in arquivos_enviados:
                tipo = tipo_arquivo(arquivo.filename)
                if tipo is None or not validar_assinatura_arquivo(arquivo.stream, arquivo.filename):
                    ignorados += 1
                    continue

                if not pasta_criada:
                    _garantir_diretorio(pasta_data)
                    pasta_criada = True

                nome_final = f"{uuid.uuid4().hex[:8]}_{nome_seguro(arquivo.filename)}"
                caminho_final = os.path.join(pasta_data, nome_final)
                if not _caminho_dentro_de(pasta_data, caminho_final):
                    ignorados += 1
                    continue

                arquivo.save(caminho_final)
                _definir_permissao_segura(caminho_final, 0o640)
                caminhos_criados.append(caminho_final)
                arquivos_salvos.append({"nome": nome_final, "tipo": tipo})

            if not arquivos_salvos:
                flash("Nenhum arquivo válido foi enviado (use imagens ou vídeos válidos).", "erro")
                return redirect(url_for("index"))

            with _trava_dados(app, exclusivo=True):
                eventos = carregar_eventos(app=app, usar_trava=False)
                eventos.append(
                    {
                        "id": uuid.uuid4().hex,
                        "data": data,
                        "hora": hora,
                        "local": local,
                        "arquivos": arquivos_salvos,
                        "criado_em": datetime.now().isoformat(timespec="seconds"),
                    }
                )
                salvar_eventos(eventos, app=app, usar_trava=False)
        except (OSError, ErroArmazenamento):
            _limpar_arquivos_parciais(caminhos_criados)
            logger.exception("Erro operacional durante upload (ip=%s).", request.remote_addr)
            return (
                render_template(
                    "erro.html",
                    codigo=500,
                    titulo="Erro ao salvar o envio",
                    mensagem="Não foi possível concluir o envio neste momento. Tente novamente ou avise o responsável pelo servidor.",
                ),
                500,
            )

        logger.info(
            "Upload concluído (data=%s, salvos=%d, ignorados=%d, ip=%s)",
            data,
            len(arquivos_salvos),
            ignorados,
            request.remote_addr,
        )
        flash(f"{len(arquivos_salvos)} arquivo(s) enviado(s) com sucesso!", "sucesso")
        if ignorados > 0:
            flash(
                f"{ignorados} arquivo(s) ignorado(s) por formato não permitido, conteúdo inválido ou arquivo vazio.",
                "aviso",
            )
        return redirect(url_for("index"))

    @app.route("/galeria")
    def galeria():
        eventos = carregar_eventos(app=app)
        eventos_por_data = {}
        for evento in eventos:
            if not isinstance(evento, dict) or "data" not in evento:
                continue
            eventos_por_data.setdefault(evento["data"], []).append(evento)
        datas_ordenadas = sorted(eventos_por_data.keys(), reverse=True)
        return render_template(
            "galeria.html",
            eventos_por_data=eventos_por_data,
            datas_ordenadas=datas_ordenadas,
        )

    @app.route("/uploads/<data>/<nome_arquivo>")
    def servir_arquivo(data, nome_arquivo):
        if not validar_data(data) or not _nome_arquivo_rota_valido(nome_arquivo):
            abort(404)

        upload_root = app.config["UPLOAD_ROOT"]
        pasta_data = os.path.join(upload_root, data)
        caminho_arquivo = os.path.join(pasta_data, nome_arquivo)
        if not _caminho_dentro_de(upload_root, pasta_data) or not _caminho_dentro_de(
            pasta_data, caminho_arquivo
        ):
            abort(404)

        return send_from_directory(pasta_data, nome_arquivo)

    @app.errorhandler(RequestEntityTooLarge)
    def erro_arquivo_muito_grande(_exc):
        logger.warning(
            "Upload rejeitado por exceder o tamanho máximo de %s MB (ip=%s).",
            app.config["MAX_UPLOAD_MB"],
            request.remote_addr,
        )
        return (
            render_template(
                "erro.html",
                codigo=413,
                titulo="Envio muito grande",
                mensagem=(
                    f"Os arquivos enviados ultrapassaram o limite máximo permitido de "
                    f"{app.config['MAX_UPLOAD_MB']} MB por envio."
                ),
            ),
            413,
        )

    @app.errorhandler(404)
    def erro_nao_encontrado(_exc):
        return (
            render_template(
                "erro.html",
                codigo=404,
                titulo="Página ou arquivo não encontrado",
                mensagem="O recurso solicitado não existe ou não está disponível.",
            ),
            404,
        )

    @app.errorhandler(ErroArmazenamento)
    @app.errorhandler(500)
    def erro_interno(_exc):
        logger.exception("Erro interno ao processar requisição %s %s.", request.method, request.path)
        return (
            render_template(
                "erro.html",
                codigo=500,
                titulo="Erro interno do servidor",
                mensagem="Ocorreu um erro interno ao processar a solicitação. Consulte os logs do servidor para mais detalhes.",
            ),
            500,
        )

    return app


if __name__ == "__main__":
    # Execução direta pensada apenas para testes locais rápidos (sem debug ativo por padrão).
    # Em produção local, execute `python wsgi.py` via Waitress + systemd.
    aplicacao = create_app()
    aplicacao.run(
        host=aplicacao.config["EVENTOS_HOST"],
        port=aplicacao.config["EVENTOS_PORT"],
        debug=aplicacao.config["DEBUG"],
        threaded=True,
    )
