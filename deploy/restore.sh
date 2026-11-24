#!/usr/bin/env bash
set -euo pipefail

# Restauração segura dos dados locais do Eventos Internos (eventos.json + uploads/)
# IMPORTANTE: pare o serviço systemd antes de restaurar:
#   sudo systemctl stop eventos-internos
#
# Uso:
#   ./deploy/restore.sh <CAMINHO_DO_BACKUP.tar.gz> [DATA_DIR]

if [[ $# -lt 1 ]]; then
  echo "Uso: $0 <arquivo-backup.tar.gz> [DATA_DIR]" >&2
  exit 1
fi

ARQUIVO_TAR="$1"
DATA_DIR="${2:-${EVENTOS_DATA_DIR:-/var/lib/eventos-internos}}"
ARQUIVO_SHA="${ARQUIVO_TAR}.sha256"

umask 027

if [[ ! -f "$ARQUIVO_TAR" ]]; then
  echo "Erro: arquivo de backup não encontrado: $ARQUIVO_TAR" >&2
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet eventos-internos 2>/dev/null; then
    echo "Erro: o serviço eventos-internos está em execução. Pare-o antes com: sudo systemctl stop eventos-internos" >&2
    exit 1
  fi
fi

if [[ -f "$ARQUIVO_SHA" ]]; then
  echo "Verificando integridade SHA-256..."
  (
    cd "$(dirname "$ARQUIVO_TAR")"
    sha256sum -c "$(basename "$ARQUIVO_SHA")"
  )
fi

# Valida o conteúdo do pacote (impede caminhos absolutos ou traversal com ..)
while IFS= read -r entrada; do
  if [[ "$entrada" == /* ]] || [[ "$entrada" == *".."* ]]; then
    echo "Erro: entrada insegura detectada no backup: $entrada" >&2
    exit 1
  fi
done < <(tar -tzf "$ARQUIVO_TAR")

mkdir -p "$DATA_DIR"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
DIR_TMP="$(mktemp -d "${DATA_DIR%/}/.restore_${TIMESTAMP}_XXXXXX")"
limpar_tmp() {
  rm -rf "$DIR_TMP"
}
trap limpar_tmp EXIT

tar -xzf "$ARQUIVO_TAR" -C "$DIR_TMP"

if [[ -f "$DIR_TMP/eventos.json" ]]; then
  python3 -m json.tool "$DIR_TMP/eventos.json" >/dev/null
fi

# Guarda cópia de segurança dos dados atuais antes de substituir
COPIA_PRE_RESTORE=""
if [[ -f "$DATA_DIR/eventos.json" || -d "$DATA_DIR/uploads" ]]; then
  COPIA_PRE_RESTORE="${DATA_DIR%/}/.pre-restore-${TIMESTAMP}"
  mkdir -p "$COPIA_PRE_RESTORE"
  [[ -f "$DATA_DIR/eventos.json" ]] && cp -a "$DATA_DIR/eventos.json" "$COPIA_PRE_RESTORE/"
  [[ -d "$DATA_DIR/uploads" ]] && cp -a "$DATA_DIR/uploads" "$COPIA_PRE_RESTORE/"
  echo "Cópia de segurança pré-restauração salva em: $COPIA_PRE_RESTORE"
fi

if [[ -f "$DIR_TMP/eventos.json" ]]; then
  mv "$DIR_TMP/eventos.json" "$DATA_DIR/eventos.json"
  chmod 640 "$DATA_DIR/eventos.json"
fi

if [[ -d "$DIR_TMP/uploads" ]]; then
  rm -rf "$DATA_DIR/uploads"
  mv "$DIR_TMP/uploads" "$DATA_DIR/uploads"
  find "$DATA_DIR/uploads" -type d -exec chmod 750 {} +
  find "$DATA_DIR/uploads" -type f -exec chmod 640 {} +
fi

echo "Restauração concluída em $DATA_DIR."
echo "Se executou como root, ajuste a propriedade antes de religar o serviço:"
echo "  sudo chown -R eventos:eventos $DATA_DIR"
echo "  sudo systemctl start eventos-internos"
