import os
import json
import uuid
import threading
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash

app = Flask(__name__)
app.secret_key = "troque-esta-chave-secreta"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_ROOT = os.path.join(BASE_DIR, "uploads")
DB_FILE = os.path.join(BASE_DIR, "eventos.json")

# Protege a leitura+escrita do eventos.json quando várias pessoas enviam
# arquivos ao mesmo tempo (evita que um upload sobrescreva o registro de outro)
_db_lock = threading.Lock()

EXTENSOES_PERMITIDAS = {
    "imagem": {".jpg", ".jpeg", ".png", ".gif", ".webp"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
}

os.makedirs(UPLOAD_ROOT, exist_ok=True)


def carregar_eventos():
    if not os.path.exists(DB_FILE):
        return []
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def salvar_eventos(eventos):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(eventos, f, ensure_ascii=False, indent=2)


def tipo_arquivo(nome_arquivo):
    ext = os.path.splitext(nome_arquivo)[1].lower()
    for tipo, extensoes in EXTENSOES_PERMITIDAS.items():
        if ext in extensoes:
            return tipo
    return None


def nome_seguro(nome_original):
    nome = "".join(c for c in nome_original if c.isalnum() or c in "._-").strip()
    return nome or "arquivo"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    data = request.form.get("data", "").strip()
    hora = request.form.get("hora", "").strip()
    local = request.form.get("local", "").strip()
    arquivos = request.files.getlist("arquivos")

    if not data or not local:
        flash("Preencha a data e o local do evento.", "erro")
        return redirect(url_for("index"))

    if not arquivos or arquivos[0].filename == "":
        flash("Selecione ao menos um arquivo (imagem ou vídeo).", "erro")
        return redirect(url_for("index"))

    # Pasta organizada pela data informada no upload (formato AAAA-MM-DD vindo do <input type="date">)
    pasta_data = os.path.join(UPLOAD_ROOT, data)
    os.makedirs(pasta_data, exist_ok=True)

    arquivos_salvos = []
    for arquivo in arquivos:
        if arquivo.filename == "":
            continue
        tipo = tipo_arquivo(arquivo.filename)
        if tipo is None:
            continue
        nome_final = f"{uuid.uuid4().hex[:8]}_{nome_seguro(arquivo.filename)}"
        caminho_final = os.path.join(pasta_data, nome_final)
        arquivo.save(caminho_final)
        arquivos_salvos.append({"nome": nome_final, "tipo": tipo})

    if not arquivos_salvos:
        flash("Nenhum arquivo válido foi enviado (use imagens ou vídeos).", "erro")
        return redirect(url_for("index"))

    with _db_lock:
        eventos = carregar_eventos()
        eventos.append({
            "id": uuid.uuid4().hex,
            "data": data,
            "hora": hora,
            "local": local,
            "arquivos": arquivos_salvos,
            "criado_em": datetime.now().isoformat(timespec="seconds"),
        })
        salvar_eventos(eventos)

    flash(f"{len(arquivos_salvos)} arquivo(s) enviado(s) com sucesso!", "sucesso")
    return redirect(url_for("index"))


@app.route("/galeria")
def galeria():
    eventos = carregar_eventos()
    eventos_por_data = {}
    for evento in eventos:
        eventos_por_data.setdefault(evento["data"], []).append(evento)
    datas_ordenadas = sorted(eventos_por_data.keys(), reverse=True)
    return render_template("galeria.html", eventos_por_data=eventos_por_data, datas_ordenadas=datas_ordenadas)


@app.route("/uploads/<data>/<nome_arquivo>")
def servir_arquivo(data, nome_arquivo):
    pasta_data = os.path.join(UPLOAD_ROOT, data)
    return send_from_directory(pasta_data, nome_arquivo)


if __name__ == "__main__":
    # host 0.0.0.0 permite acesso de outros computadores da rede interna
    # threaded=True permite atender várias pessoas ao mesmo tempo (ex: 5-6 usuários)
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
