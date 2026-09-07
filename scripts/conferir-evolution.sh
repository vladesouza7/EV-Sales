#!/usr/bin/env bash
# S-06 e S-10 §6 — o gateway do WhatsApp está de pé, a chave bate, e a instância existe?
#
# É a primeira coisa a rodar quando "a Aurora parou de responder no WhatsApp". Ele não
# imprime a chave: imprime se ela funciona, que é a pergunta.
set -uo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Crie o .env primeiro: cp .env.example .env"; exit 1; }
set -a; . ./.env; set +a

BASE="${EVSALES_EVOLUTION_URL:-http://localhost:8080}"
INSTANCIA="${EVSALES_EVOLUTION_INSTANCIA:-solevolt}"
CHAVE="${EVSALES_EVOLUTION_CHAVE:-}"

if [ -z "$CHAVE" ]; then
  echo "✗ EVSALES_EVOLUTION_CHAVE está vazia no .env. Rode ./scripts/subir-dev.sh, que completa."
  exit 1
fi

echo "Server URL     : $BASE"
echo "API Key Global : está no .env, em EVSALES_EVOLUTION_CHAVE"
echo "Instância      : $INSTANCIA"
echo

codigo=$(curl -s -o /dev/null -w '%{http_code}' -H "apikey: $CHAVE" "$BASE/instance/fetchInstances")
case "$codigo" in
  200) echo "✓ o gateway responde e a chave bate" ;;
  401) echo "✗ chave recusada — o container subiu com outra. 'docker compose up -d --force-recreate evolution'"; exit 1 ;;
  000) echo "✗ o gateway não respondeu. 'docker compose up -d evolution' e tente de novo"; exit 1 ;;
  *)   echo "✗ resposta inesperada: $codigo"; exit 1 ;;
esac

estado=$(curl -s -H "apikey: $CHAVE" "$BASE/instance/connectionState/$INSTANCIA")
case "$estado" in
  *'"open"'*)      echo "✓ instância '$INSTANCIA' CONECTADA — o WhatsApp da loja está no ar" ;;
  *'"connecting"'*) echo "◐ instância '$INSTANCIA' aguardando leitura do QR em $BASE/manager" ;;
  *'"close"'*)     echo "◐ instância '$INSTANCIA' existe e está desconectada — leia o QR em $BASE/manager" ;;
  *)               echo "◐ instância '$INSTANCIA' ainda não existe — crie no manager, em $BASE/manager" ;;
esac

# O webhook precisa alcançar a API. Em dev ela roda no host, fora do compose.
destino=$(curl -s -H "apikey: $CHAVE" "$BASE/webhook/find/$INSTANCIA" 2>/dev/null)
case "$destino" in
  *'/api/webhooks/evolution'*) echo "✓ webhook apontando para o EV-Sales" ;;
  *) echo "◐ webhook ainda não configurado — veja o passo 4 do README da S-06" ;;
esac
