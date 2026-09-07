#!/usr/bin/env bash
# S-10 §5 — "restauração é testada uma vez por mês. Backup não testado é fé."
#
# Restaura o dump mais recente num banco descartável e confere três coisas:
#
#   1. o `psql` aceitou o arquivo inteiro, sem erro;
#   2. a versão do Alembic no dump é a mesma que o código espera — um backup de um schema
#      antigo restaura, e depois a API não sobe;
#   3. as tabelas do domínio existem e têm linha.
#
# **Nunca no banco `evsales`.** O banco daqui é criado e derrubado a cada execução, pelo
# mesmo motivo do `conferir-migrations.sh`: foi rodando restauração à mão no banco errado
# que eu derrubei um ambiente de desenvolvimento.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }

DESTINO="${EVSALES_BACKUP_DIR:-./backups}"
BANCO="evsales_restauracao"

DUMP="$(ls -1t "$DESTINO"/postgres/evsales-*.sql.gz 2>/dev/null | head -1 || true)"
if [ -z "$DUMP" ]; then
  echo "não achei dump em $DESTINO/postgres. Rode scripts/backup.sh primeiro."
  exit 2
fi
echo "restaurando $(basename "$DUMP") em $BANCO"

docker compose exec -T postgres psql -U evsales -d postgres \
  -c "DROP DATABASE IF EXISTS $BANCO" \
  -c "CREATE DATABASE $BANCO OWNER evsales" >/dev/null

# `ON_ERROR_STOP=1` é o que transforma "restaurou com 40 erros" em falha. Sem ele o psql
# segue até o fim e sai com 0, que é o jeito mais silencioso de um backup parecer bom.
gunzip -c "$DUMP" \
  | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -q -U evsales -d "$BANCO" >/dev/null
echo "  ✓ o dump entrou inteiro"

consultar() {
  docker compose exec -T postgres psql -tAX -U evsales -d "$BANCO" -c "$1"
}

VERSAO_NO_DUMP="$(consultar 'SELECT version_num FROM alembic_version' | tr -d '[:space:]')"
VERSAO_DO_CODIGO="$(cd backend && uv run alembic heads | awk '{print $1}' | tr -d '[:space:]')"
if [ "$VERSAO_NO_DUMP" != "$VERSAO_DO_CODIGO" ]; then
  echo "  ✗ o dump está em $VERSAO_NO_DUMP e o código espera $VERSAO_DO_CODIGO"
  echo "    restaurar isto sobe um schema que a API não reconhece. Backup novo, ou"
  echo "    'alembic upgrade head' depois de restaurar — e testar de novo."
  exit 1
fi
echo "  ✓ schema em $VERSAO_NO_DUMP, igual ao do código"

# Existir é requisito; ter linha é informação. Um dump truncado perde tabela, e é isso que
# reprova aqui. Exigir linha em todas reprovaria uma loja no primeiro dia — e quem lê o
# teste mensal sabe quantos carros tem no pátio, então os números vão para a tela.
FALTOU=""
for tabela in unidades leads conversas mensagens usuarios vendedores \
              reservas espelhos pedidos_de_aprovacao test_drives trilha; do
  if [ "$(consultar "SELECT to_regclass('$tabela') IS NOT NULL" | tr -d '[:space:]')" != "t" ]; then
    FALTOU="$FALTOU $tabela"
    continue
  fi
  echo "     $tabela: $(consultar "SELECT count(*) FROM $tabela" | tr -d '[:space:]')"
done
if [ -n "$FALTOU" ]; then
  echo "  ✗ o dump não trouxe:$FALTOU"
  echo "    tabela do domínio faltando é dump truncado. Não confie neste arquivo."
  exit 1
fi

docker compose exec -T postgres psql -U evsales -d postgres \
  -c "DROP DATABASE $BANCO" >/dev/null
echo "restauração ok · o banco de teste foi derrubado"
echo
echo "A chave de PII não vem no dump. Para ler nome e telefone deste backup você precisa"
echo "da EVSALES_PII_KEY e da EVSALES_PII_PEPPER que estavam em uso quando ele foi feito."
