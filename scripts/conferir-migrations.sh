#!/usr/bin/env bash
# S-10 §3 — toda migration sobe e desce. Testado nos dois sentidos, **nunca no banco de
# desenvolvimento**.
#
# Existe porque eu já fiz o erro que ele impede: rodar `alembic downgrade base` com o
# EVSALES_DATABASE_URL apontando para `evsales` derruba todas as tabelas do banco em que a
# loja está trabalhando. `downgrade` funcionando é requisito da spec; descobrir isso no
# banco errado é perder o dia de alguém.
#
# O banco daqui é descartável e recriado a cada execução: se ele sumir, não faz falta.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }

SENHA="${POSTGRES_PASSWORD:-evsales}"
BANCO="evsales_migracoes"

docker compose exec -T postgres psql -U evsales -d postgres \
  -c "DROP DATABASE IF EXISTS $BANCO" -c "CREATE DATABASE $BANCO OWNER evsales" >/dev/null

# A variável vale só para os comandos abaixo, e não escapa deste processo.
export EVSALES_DATABASE_URL="postgresql+psycopg://evsales:${SENHA}@localhost:5432/${BANCO}"
echo "banco descartável: $BANCO"

cd backend
uv run alembic upgrade head   >/dev/null && echo "  ✓ sobe"
uv run alembic downgrade base >/dev/null && echo "  ✓ desce"
uv run alembic upgrade head   >/dev/null && echo "  ✓ sobe de novo"
cd ..

docker compose exec -T postgres psql -U evsales -d postgres \
  -c "DROP DATABASE IF EXISTS $BANCO" >/dev/null
echo "migrations ok nos dois sentidos"
