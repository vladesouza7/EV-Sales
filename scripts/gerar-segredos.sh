#!/usr/bin/env bash
# S-10 §2 — ninguém precisa saber gerar chave AES, e ninguém usa a do .env.example.
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Crie o .env primeiro: cp .env.example .env"; exit 1; }

trocar() {
  local variavel="$1" valor="$2"
  if grep -q "^${variavel}=" .env; then
    sed -i "s|^${variavel}=.*|${variavel}=${valor}|" .env
  else
    printf '%s=%s\n' "$variavel" "$valor" >> .env
  fi
  echo "  ${variavel} gerada"
}

trocar EVSALES_PII_KEY    "$(openssl rand -base64 32)"
trocar EVSALES_PII_PEPPER "$(openssl rand -base64 32)"
trocar EVSALES_JWT_SECRET "$(openssl rand -base64 32)"
# A chave do gateway do WhatsApp. O container da Evolution a lê no boot, e a tela da S-12
# guarda a cópia com que o EV-Sales fala com ele (S-12 §2, ADR-005).
trocar EVSALES_EVOLUTION_CHAVE "$(openssl rand -hex 24)"

cat <<'AVISO'

  Segredos gravados em .env (que não é versionado).

  ATENÇÃO: guarde EVSALES_PII_KEY e EVSALES_PII_PEPPER FORA DO SERVIDOR, agora.
  Backup do banco sem a chave é inútil — é o erro que não tem conserto.
AVISO
