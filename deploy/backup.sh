#!/usr/bin/env bash
set -euo pipefail

# Backup consistente dos dados locais do Eventos Internos (eventos.json + uploads/)
# Uso:
#   ./deploy/backup.sh [DATA_DIR] [BACKUP_DIR]
# Variáveis opcionais:
#   RETENTION_DAYS=14   (remove backups com mais de N dias; 0 desativa a limpeza)

DATA_DIR="${1:-${EVENTOS_DATA_DIR:-/var/lib/eventos-internos}}"
BACKUP_DIR="${2:-${EVENTOS_BACKUP_DIR:-/var/backups/eventos-internos}}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

umask 077

if [[ ! -d "$DATA_DIR" ]]; then
  echo "Erro: diretório de dados não encontrado: $DATA_DIR" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
ARQUIVO_TAR="$BACKUP_DIR/eventos-internos-${TIMESTAMP}.tar.gz"
ARQUIVO_TMP="${ARQUIVO_TAR}.tmp"
ARQUIVO_SHA="${ARQUIVO_TAR}.sha256"
LOCK_FILE="$DATA_DIR/.eventos.lock"

limpar_tmp() {
  rm -f "$ARQUIVO_TMP"
}
trap limpar_tmp EXIT

exec 9>>"$LOCK_FILE"
if command -v flock >/dev/null 2>&1; then
  flock -s 9
fi

ITENS=()
if [[ -f "$DATA_DIR/eventos.json" ]]; then
  ITENS+=("eventos.json")
fi
if [[ -d "$DATA_DIR/uploads" ]]; then
  ITENS+=("uploads")
fi

if [[ ${#ITENS[@]} -eq 0 ]]; then
  echo "Erro: nenhum dado (eventos.json ou uploads/) encontrado em $DATA_DIR" >&2
  exit 1
fi

tar -C "$DATA_DIR" -czf "$ARQUIVO_TMP" "${ITENS[@]}"

# Libera a trava assim que o snapshot terminar
if command -v flock >/dev/null 2>&1; then
  flock -u 9
fi
exec 9>&-

# Verifica integridade do pacote gerado antes de torná-lo definitivo
tar -tzf "$ARQUIVO_TMP" >/dev/null
mv "$ARQUIVO_TMP" "$ARQUIVO_TAR"

(
  cd "$BACKUP_DIR"
  sha256sum "$(basename "$ARQUIVO_TAR")" > "$(basename "$ARQUIVO_SHA")"
)

if [[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] && (( RETENTION_DAYS > 0 )); then
  find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'eventos-internos-*.tar.gz' -o -name 'eventos-internos-*.tar.gz.sha256' \) \
    -mtime "+$RETENTION_DAYS" -delete
fi

echo "Backup concluído: $ARQUIVO_TAR"
