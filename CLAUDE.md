# EV-Sales — contexto para agentes de código

Agente de vendas da **Sol & Volt Veículos Elétricos** (João Pessoa/PB). A consultora se chama
**Aurora**. O dono é o **Raí**; quem aprova é a **Neuza**; os vendedores são **Tarcísio** e
**Jaqueline**. Estes nomes são o sistema, não exemplos — use-os no código, nos testes e nos
commits. Contexto completo: [docs/CASE.md](docs/CASE.md).

**O escopo termina no test drive.** O EV-Sales não fecha venda, não emite nota fiscal, não processa
pagamento e não simula financiamento — isso é presencial, no sistema que a concessionária já tem
([ADR-011](docs/adr/ADR-011-jornada-digital-termina-no-test-drive.md)). O documento que o sistema
emite é o **Espelho de Condição e Reserva** (tabela `espelhos`), que **não é contrato nem documento
fiscal**. Se uma tarefa pedir NF-e, contrato, parcela ou taxa, ela está fora do escopo — pare e
pergunte.

## A regra que vale acima de todas

> **O modelo de linguagem decide o que dizer. O código decide o que pode ser feito.**

Quando uma tarefa puder ser resolvida por instrução no prompt **ou** por restrição no código,
resolva no código. Instrução em prompt some no diff; constraint de banco não.

## As seis invariantes

Se uma mudança violar qualquer uma delas, **pare e pergunte** — não implemente e avise depois.

1. **Nenhum número do domínio sai do modelo.** Preço, autonomia, prazo, potência e disponibilidade
   vêm de tool que leu o Postgres. Nunca coloque catálogo, preço ou autonomia no prompt.
   → [ADR-003](docs/adr/ADR-003-numeros-nunca-saem-do-modelo.md)
2. **Não existe tool de desconto ou de alteração de preço.** Não crie uma. Se uma tarefa parecer
   pedir isso, ela está errada ou é para a tela autenticada da Neuza.
   → [ADR-004](docs/adr/ADR-004-aprovacao-humana-no-irreversivel.md)
3. **Espelho exige `approval_id`; reserva exige aprovação válida.** `espelhos.approval_id` é
   `NOT NULL` com FK. Nunca torne essa coluna nula, nem "temporariamente para testar".
   → [S-04](docs/spec/S-04-fila-de-aprovacao.md)
4. **Reserva é `UPDATE … WHERE status='disponivel'`.** Nunca `SELECT` para verificar e depois
   gravar. Não substitua a constraint por checagem em Python.
   → [ADR-001](docs/adr/ADR-001-postgres-fonte-da-verdade.md)
5. **PII nunca em claro em log, trace, erro ou prompt.** Máscara na origem, não no destino. O
   telefone não entra no prompt em hipótese alguma.
   → [ADR-007](docs/adr/ADR-007-pii-cifrada-e-mascarada.md)
6. **Autonomia WLTP e Inmetro nunca são comparadas como equivalentes.** Todo importado publica WLTP,
   todo chinês publica Inmetro, e o WLTP é sistematicamente mais otimista. `comparar_unidades`
   recusa comparar fontes diferentes; WLTP nunca é citado sem rótulo; `autonomia_km` nulo vira "vou
   confirmar", nunca estimativa nem conversão por regra de três. **Este é o único risco que a
   verificação numérica não pega** — lá os números estão certos, o erro está na comparação.
   → [S-03](docs/spec/S-03-agente-aurora.md)

## Onde as coisas ficam

```
docs/         CASE, PRD, ARQUITETURA, adr/, spec/, pesquisa/
backend/      app/ (api, core, modelos, ia, integracoes, workers), tests/, evals/, migrations/
frontend/     src/ (landing, chat, aprovacao, atendimentos)
scripts/      seed, gerar-segredos, backup
```

Documentação em `docs/`. Testes ao lado do código que testam. Evals em `backend/evals/`.

## Antes de escrever código

**Leia a spec.** Toda tarefa de implementação corresponde a uma spec em `docs/spec/`. Se não
houver spec para o que foi pedido, escreva a spec primeiro e peça revisão — não improvise o
comportamento.

Os critérios de aceite em Gherkin viram testes, e os testes vêm antes da implementação.

## Convenções

- **Português** em domínio, nomes de tabela, coluna, função de negócio, commit e comentário. A
  Sol & Volt é brasileira e a equipe dela lê o código.
- **Dinheiro é `bigint` em centavos.** Nunca `float`, nunca `Decimal` em coluna.
- **Fuso `America/Fortaleza`**, sempre explícito. A Paraíba não tem horário de verão.
- **Telefone em E.164** (`+5583988714471`), cifrado.
- Migrations Alembic com `downgrade` que funciona.
- Type hints obrigatórios no backend; `mypy` e `ruff` bloqueiam o CI.

## O que o CI bloqueia

Cinco portões reprovam o merge, e nenhum tem exceção manual:

| Portão | Spec |
|---|---|
| Eval de preço e estoque — 100% | [S-03](docs/spec/S-03-agente-aurora.md) |
| Eval de autonomia e fonte — 100% | [S-03](docs/spec/S-03-agente-aurora.md) |
| Eval de injection — 100% | [S-03](docs/spec/S-03-agente-aurora.md) |
| Teste de concorrência de reserva — 1 vencedor em 50 | [S-05](docs/spec/S-05-reserva-de-chassi.md) |
| Varredura de PII nos logs — 0 ocorrências | [S-09](docs/spec/S-09-protecao-de-pii.md) |

**Nunca desabilite, marque como `skip` ou afrouxe um desses testes para fazer o build passar.** Se
um deles falhar, o código está errado — não o teste. Se você acredita que o teste está errado,
pare e diga isso, com o caso concreto.

## O que NÃO fica na mão do agente

Deliberadamente fora do que um agente altera sozinho. Toque nestes arquivos e o PR exige revisão
humana explícita:

| O quê | Por quê |
|---|---|
| Migrations que mexem em `unidades`, `reservas`, `pedidos_de_aprovacao`, `espelhos` | É o schema que garante as invariantes 3 e 4 |
| A lista de tools por etapa (`tools_da_etapa`) | Adicionar uma tool a uma etapa é ampliar o que a Aurora pode fazer |
| `backend/app/ia/prompts/` | Mudança de prompt exige eval comparativo antes e depois |
| `backend/app/core/pii.py` | Cifragem e mascaramento |
| `docker-compose.yml` e `.env.example` | Superfície de operação e de segredo |
| Qualquer arquivo em `docs/adr/` | ADR é registro histórico. Decisão nova é ADR novo, nunca edição do antigo |

## Comandos

| Comando | O que faz |
|---|---|
| `/nova-spec` | Cria spec a partir do template, com o case no contexto |
| `/verificar-spec` | **Sessão limpa** lê a spec e o código e emite veredito |
| `/rodar-evals` | Roda as suítes e compara com a última execução gravada |
| `/revisar-risco` | Confere um diff contra as seis invariantes |

`/verificar-spec` roda numa sessão que **nunca viu a implementação** e **não tem permissão de
corrigir o que encontra**. Revisor que conserta virou autor, e autor não revisa o próprio trabalho.

## Como o trabalho do agente é revisado

1. Spec escrita e revisada por mim antes de qualquer código.
2. Testes dos cenários Gherkin primeiro, falhando.
3. Implementação com a spec no contexto.
4. `/verificar-spec` em sessão limpa; veredito anexado ao PR.
5. `/revisar-risco` sobre o diff.
6. CI verde, com os cinco portões.
7. Revisão humana minha no PR — obrigatória nos arquivos da tabela acima.

`main` é protegida: sem push direto, PR obrigatório.

## Erros que já aconteceram aqui

- Colocar preço no prompt "só para o agente ter contexto". Não faça. Ele parafraseia, e "R$ 149.990"
  vira "cerca de 150 mil" numa proposta.
- Trocar `UPDATE … WHERE` por `SELECT` + `UPDATE` para "melhorar a mensagem de erro". Isso reabre a
  janela de reserva dupla.
- Logar o objeto `Lead` inteiro ao depurar. Use o `__repr__`, que já vem mascarado.
- Marcar um eval como `skip` para destravar o build.
