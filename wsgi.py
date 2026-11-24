"""Ponto de entrada WSGI de produção com Waitress para o Eventos Internos."""

import logging
import sys

from waitress import serve

from app import create_app

logger = logging.getLogger("eventos_internos.wsgi")

# Margem pequena acima do MAX_CONTENT_LENGTH do Flask para que o Flask
# consiga responder com a página amigável HTTP 413 antes do corte bruto do WSGI.
_MARGEM_WSGI_BYTES = 1024 * 1024


def criar_aplicacao_producao(config_override=None):
    """Cria a aplicação validando a configuração de produção."""
    return create_app(config_override=config_override)


def main():
    try:
        app = criar_aplicacao_producao()
    except (RuntimeError, ValueError) as exc:
        print(f"Erro de configuração ao iniciar o serviço: {exc}", file=sys.stderr)
        sys.exit(1)

    host = app.config["EVENTOS_HOST"]
    port = app.config["EVENTOS_PORT"]
    threads = app.config["EVENTOS_THREADS"]
    max_body = app.config["MAX_CONTENT_LENGTH"] + _MARGEM_WSGI_BYTES

    if host == "0.0.0.0":
        logger.warning(
            "Serviço configurado em 0.0.0.0:%s. Garanta que o firewall restrinja o acesso "
            "exclusivamente à rede interna confiável (LAN).",
            port,
        )

    logger.info(
        "Iniciando Waitress em http://%s:%s (threads=%s, max_upload_mb=%s)",
        host,
        port,
        threads,
        app.config["MAX_UPLOAD_MB"],
    )

    serve(
        app,
        host=host,
        port=port,
        threads=threads,
        ident="eventos-internos",
        max_request_body_size=max_body,
    )


if __name__ == "__main__":
    main()
