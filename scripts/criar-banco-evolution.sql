-- A Evolution API guarda instância, contato e mensagem no Postgres. Banco separado, no
-- mesmo servidor: um container de banco só, um `pg_dump` só (S-10 §5), e mesmo assim o
-- schema dela não encosta no banco que é fonte da verdade do estoque (ADR-001).
--
-- Roda só na criação do volume, como todo script de `docker-entrypoint-initdb.d`. Em
-- volume que já existe, criar à mão:
--   docker compose exec -T postgres psql -U evsales -c "CREATE DATABASE evolution OWNER evsales"
CREATE DATABASE evolution OWNER evsales;
