#!/usr/bin/env bash
# S-10 §5 — o backup diário da Sol & Volt.
#
# Quatro coisas, e cada uma tem um motivo diferente para existir:
#
#   postgres/evsales-DATA.sql.gz      os dados da loja. Perder isto é perder a loja.
#   postgres/evolution-DATA.sql.gz    a sessão do WhatsApp mora aqui **e** no volume:
#                                     com DATABASE_ENABLED=true a Evolution guarda a
#                                     instância no Postgres. Backup só do volume não
#                                     dispensa ler o QR de novo.
#   evolution/instancias-DATA.tgz     o volume da sessão.
#   documentos/documentos-DATA.tgz    os Espelhos emitidos (S-04 §5).
#   fotos/fotos-DATA.tgz              as fotos do catálogo, semanal — elas voltam com o
#                                     `subir-fotos.py` se sumirem, então não são diárias.
#
# **O QUE ESTE SCRIPT NÃO GUARDA: `EVSALES_PII_KEY` e `EVSALES_PII_PEPPER`.** E é de
# propósito. Chave junto com o banco cifrado é o mesmo que banco em claro. Elas ficam no
# gerenciador de senhas do Raí, fora do servidor — e **sem elas o backup do banco é
# inútil**: nome e telefone de todo cliente ficam ilegíveis para sempre. É o erro que não
# tem conserto.
#
# Onde escrever: `EVSALES_BACKUP_DIR`, que pode ser um disco externo montado. A "cópia
# externa" da spec é isso — o script não sabe fazer upload, e um `rclone` no cron é uma
# linha de quem opera, não uma dependência daqui.
#
# Diário às 03h (S-10 §5). A linha do cron está no docs/RUNBOOK.md.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }

DESTINO="${EVSALES_BACKUP_DIR:-./backups}"
BUCKET="${EVSALES_MINIO_BUCKET:-evsales}"
HOJE="$(date +%Y-%m-%d)"
# `date +%u`: 7 é domingo. As fotos são semanais (S-10 §5).
DIA_DA_SEMANA="$(date +%u)"

RETENCAO_LOJA=30
RETENCAO_EVOLUTION=7
RETENCAO_FOTOS=28

mkdir -p "$DESTINO"/{postgres,evolution,documentos,fotos}
DESTINO="$(cd "$DESTINO" && pwd)"  # o `docker run` exige caminho absoluto no -v
TEMPORARIO="$(mktemp -d)"
# `|| true`: uma falha na limpeza viraria o código de saída do script inteiro, e cron que
# reclama de um backup que deu certo é cron que alguém aprende a ignorar.
trap 'rm -rf "$TEMPORARIO" 2>/dev/null || true' EXIT

echo "backup de $HOJE em $DESTINO"

# ── Postgres ─────────────────────────────────────────────────────────────────────
# O arquivo só recebe o nome definitivo depois de o dump terminar: com `set -e` e
# `pipefail`, um dump interrompido aborta o script antes do `mv`. Um arquivo pela metade
# com nome de arquivo bom é pior que nenhum arquivo — é o que se restaura na emergência.
despejar() {
  local banco="$1" pasta="$2"
  local parcial="$TEMPORARIO/$banco.sql.gz"
  docker compose exec -T postgres pg_dump -U evsales -d "$banco" --clean --if-exists \
    | gzip >"$parcial"
  mv "$parcial" "$DESTINO/$pasta/$banco-$HOJE.sql.gz"
  echo "  ✓ $banco  ($(du -h "$DESTINO/$pasta/$banco-$HOJE.sql.gz" | cut -f1))"
}

despejar evsales postgres
despejar evolution postgres

# ── volume da sessão do WhatsApp ─────────────────────────────────────────────────
# O nome real do volume é lido do container, não montado à mão: ele leva o prefixo do
# projeto (`ev-sales_...`), e o projeto é o nome da pasta — que quem clonar pode trocar.
volume_de() {
  local servico="$1" destino_no_container="$2"
  docker inspect -f \
    "{{range .Mounts}}{{if eq .Destination \"$destino_no_container\"}}{{.Name}}{{end}}{{end}}" \
    "$(docker compose ps -q "$servico")"
}

# Serviço no chão não gera arquivo vazio com nome de arquivo bom: ele avisa e não escreve.
# É a diferença entre "não tenho backup da sessão" e "tenho, e ele está vazio".
de_pe() { [ -n "$(docker compose ps -q "$1")" ]; }

if de_pe evolution; then
  VOLUME_EVOLUTION="$(volume_de evolution /evolution/instances)"
  docker run --rm \
    -v "$VOLUME_EVOLUTION:/dados:ro" \
    -v "$DESTINO/evolution:/saida" \
    busybox tar czf "/saida/instancias-$HOJE.tgz" -C /dados .
  echo "  ✓ sessão da Evolution"
else
  echo "  ! Evolution não está de pé: o volume da sessão NÃO entrou neste backup"
fi

# ── MinIO ────────────────────────────────────────────────────────────────────────
# `mc` dentro de um container na mesma rede do compose, e a credencial entra por variável
# de ambiente: montá-la na URL (`http://usuario:senha@minio`) quebra com senha que tem
# `+` ou `/`, e a do `gerar-segredos.sh` é base64.
espelhar() {
  local prefixo="$1"
  # `--user` do dono do backup, e não root: sem isso o `mc` deixa os arquivos copiados
  # como root, a limpeza do `trap` falha e o script termina com erro depois de um backup
  # que deu certo. `MC_CONFIG_DIR` na pasta montada é o que permite o usuário sem home.
  docker run --rm --network "$REDE" --user "$(id -u):$(id -g)" \
    -e "MINIO_USUARIO=${EVSALES_MINIO_ACCESS_KEY:-evsales}" \
    -e "MINIO_SENHA=${EVSALES_MINIO_SECRET_KEY:-evsales-local}" \
    -e MC_CONFIG_DIR=/copia/.mc \
    -v "$TEMPORARIO:/copia" \
    --entrypoint sh minio/mc -c "
      mc alias set loja http://minio:9000 \"\$MINIO_USUARIO\" \"\$MINIO_SENHA\" >/dev/null
      mc mirror --overwrite --quiet loja/$BUCKET/$prefixo /copia/$prefixo || true
    "
  # Cópia datada, e não espelho vivo: um espelho com `--remove` apagaria do backup o que
  # alguém apagou do MinIO por acidente, e um espelho sem `--remove` guardaria para sempre
  # o Espelho de um lead que a retenção da S-09 §6 mandou apagar. A cópia datada expira.
  mkdir -p "$TEMPORARIO/$prefixo"
  tar czf "$DESTINO/$prefixo/$prefixo-$HOJE.tgz" -C "$TEMPORARIO" "$prefixo"
  echo "  ✓ $prefixo"
}

if de_pe minio; then
  REDE="$(docker inspect -f \
    '{{range $rede, $_ := .NetworkSettings.Networks}}{{$rede}}{{end}}' \
    "$(docker compose ps -q minio)")"
  espelhar documentos
  if [ "$DIA_DA_SEMANA" = "7" ]; then
    espelhar fotos
  fi
else
  echo "  ! MinIO não está de pé: os Espelhos NÃO entraram neste backup"
fi

# ── retenção ─────────────────────────────────────────────────────────────────────
find "$DESTINO/postgres" -name 'evsales-*.sql.gz' -mtime "+$RETENCAO_LOJA" -delete
find "$DESTINO/documentos" -name 'documentos-*.tgz' -mtime "+$RETENCAO_LOJA" -delete
find "$DESTINO/postgres" -name 'evolution-*.sql.gz' -mtime "+$RETENCAO_EVOLUTION" -delete
find "$DESTINO/evolution" -name 'instancias-*.tgz' -mtime "+$RETENCAO_EVOLUTION" -delete
find "$DESTINO/fotos" -name 'fotos-*.tgz' -mtime "+$RETENCAO_FOTOS" -delete

echo "backup ok · a chave de PII NÃO está aqui, e sem ela este backup não se lê"
echo "restauração se testa com scripts/conferir-backup.sh — backup não testado é fé"
