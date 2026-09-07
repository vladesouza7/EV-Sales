#!/usr/bin/env bash
# Sobe o ambiente local para olhar no navegador. Não é a S-10: o compose completo é lá.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  ./scripts/gerar-segredos.sh
  # O volume do postgres já foi criado com evsales/evsales, e o uvicorn roda fora do
  # compose: o host é localhost, não o nome do serviço.
  sed -i 's|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=evsales|' .env
  sed -i 's|^EVSALES_DATABASE_URL=.*|EVSALES_DATABASE_URL=postgresql+psycopg://evsales:evsales@localhost:5432/evsales|' .env
fi

# `.env` de antes da S-12 não tem a chave da Evolution, e sem ela o compose nem interpola.
# Completar o que falta é diferente de rodar o gerar-segredos de novo: aquele ROTACIONARIA a
# EVSALES_PII_KEY, e todo nome e telefone já gravado viraria lixo ilegível.
if ! grep -q '^EVSALES_EVOLUTION_CHAVE=.' .env; then
  sed -i '/^EVSALES_EVOLUTION_CHAVE=/d' .env
  printf 'EVSALES_EVOLUTION_CHAVE=%s
' "$(openssl rand -hex 24)" >> .env
  echo "  EVSALES_EVOLUTION_CHAVE gerada (faltava no .env)"
fi

set -a; . ./.env; set +a

docker compose up -d postgres
until docker compose exec -T postgres pg_isready -U evsales >/dev/null 2>&1; do sleep 1; done

cd backend
uv run alembic upgrade head
cd ..
uv --project backend run python scripts/seed.py

cd backend
exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8010
