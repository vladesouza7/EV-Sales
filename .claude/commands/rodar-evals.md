---
description: Roda as suítes de eval da Aurora e compara com a última execução gravada
allowed-tools: Bash(cd:*), Bash(docker compose:*), Bash(uv run:*), Read, Glob, Grep
argument-hint: "[suítes] — vazio roda as seis; ex.: preco autonomia injection"
---

# /rodar-evals — S-03 §8

Roda o eval da Aurora contra o provedor de verdade e diz **qual caso virou** desde a última
execução gravada. Suítes pedidas: `$ARGUMENTS` (vazio = as seis).

## O que fazer

1. Confira que o Postgres está de pé e que o provedor está configurado. Se faltar chave, o
   runner sai com código 2 e diz o que falta — repasse a mensagem e **pare**, não invente
   uma execução.

   ```bash
   docker compose up -d postgres
   ```

2. Rode, sempre com `--gravar`, que é o que produz a comparação:

   ```bash
   cd backend && uv run python -m evals.rodar $ARGUMENTS --gravar
   ```

3. Relate, nesta ordem:
   - o veredito de cada suíte, e **quais são portão de CI** (preço, autonomia, injection —
     esses exigem 100%);
   - os casos que viraram (`↑` voltou a aprovar, `↓` passou a reprovar), um por linha, com o
     motivo que o runner imprimiu;
   - se algum portão reprovou: o caso, a falha e **o número que saiu na fala**. Um portão
     vermelho aqui significa que a Aurora falou um número que não veio de tool, ou comparou
     WLTP com Inmetro como equivalentes. Não é o teste que está errado.

4. `backend/evals/ultima-execucao.json` muda. Comite junto com a mudança que causou a
   diferença — é ele que serve de "antes" na próxima comparação.

## Regras

- **Não corrija caso para fazer suíte passar.** Se um caso está errado, diga qual e por quê,
  e pare. Afrouxar critério de portão é o que o CLAUDE.md proíbe por escrito.
- **Não mexa em `backend/app/ia/prompts/`.** Se a conclusão for que o prompt precisa mudar,
  essa mudança é de revisão humana, e este eval é o "antes" dela.
- Este é o único comando do repositório que **gasta dinheiro**: são até 48 conversas com o
  modelo. Rodar só as suítes que interessam é o padrão educado.
