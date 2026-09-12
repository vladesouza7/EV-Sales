# ADR-015 — Os evals da Aurora informam o merge, não o bloqueiam

**Status:** proposto
**Data:** 2026-09-12
**Muda:** [S-03 §8](../spec/S-03-agente-aurora.md) e [S-10 §7](../spec/S-10-operacao.md) — os três
evals continuam existindo, rodando e reprovando; deixam de segurar o botão de merge
**Toca em:** [ADR-006](ADR-006-observabilidade-e-teto-de-custo.md), [ADR-012](ADR-012-provedor-configuravel.md), [docs/spec/README.md](../spec/README.md), [CLAUDE.md](../../CLAUDE.md)

---

## Contexto

O [docs/spec/README.md](../spec/README.md) diz, e continua certo no espírito:

> risco sem verificação automatizada é desejo, não requisito

Foi com essa frase que os cinco portões nasceram, e eles se pagaram. Numa única sequência de
depuração, os três evals da [S-03 §8](../spec/S-03-agente-aurora.md) pegaram:

| O que acharam | Como apareceria sem eles |
|---|---|
| O prompt inteiro vazando pela regeneração (`inj-03`) | Cliente com o manual da Aurora na mão |
| `condicao="novo"` escondendo metade do pátio | "Não temos Porsche" com o Taycan no pátio |
| Token de controle do provedor na fala (`auto-05`) | `<｜tool▁calls▁begin｜>` na tela do cliente |
| `require_parameters` faltando no OpenRouter | Tool call silenciosamente ignorada |

Nenhum teste unitário pegaria nenhum desses. **O valor dos evals não está em questão.**

O que está em questão é outra coisa: **do que cada portão depende para dar veredito.**

| Portão | Depende de |
|---|---|
| ruff, mypy, 423 testes, migrations | só deste repositório |
| concorrência de reserva (S-05) | só deste repositório |
| varredura de PII (S-09 §7) | só deste repositório |
| **os três evals (S-03 §8)** | **um provedor de LLM terceiro** |

Os três primeiros grupos dão a mesma resposta para a mesma entrada, hoje e daqui a um ano. O
quarto não. Mesmo com `EVSALES_TEMPERATURA=0`, duas execuções do mesmo commit deram resultados
diferentes: `preco-03` e `inj-06` passaram numa e reprovaram na seguinte, sem uma linha ter
mudado. Inferência hospedada em lote não é determinística mesmo com decodificação gulosa, e o
roteamento do OpenRouter pode escolher backends diferentes para o mesmo id de modelo.

Somam-se a isso duas coisas práticas: **o eval custa dinheiro por execução** — é a única coisa no
CI que custa —, e o projeto é, hoje, um projeto de estudo sem cliente atendido.

O efeito combinado é que um provedor terceiro decide quando o autor consegue integrar trabalho. E
o efeito colateral era pior: enquanto o eval morava no mesmo job, a suíte de 423 testes aparecia
como `skipped` sempre que ele reprovava — uma falha de conduta do modelo escondia o veredito de
tudo que não depende de modelo nenhum.

## Decisão

O `ci.yml` passa a ter **dois jobs**.

### 1. `portoes` — determinístico, e é este que deve bloquear

ruff, mypy, migrations em ambos os sentidos, o portão de concorrência da
[S-05](../spec/S-05-reserva-de-chassi.md), a varredura de PII da
[S-09 §7](../spec/S-09-protecao-de-pii.md) e a suíte inteira. Tudo que depende só deste
repositório. **É este o job a marcar como obrigatório** em *Settings → Branches → Require status
checks*.

### 2. `evals` — depende do provedor, e informa

Os três da [S-03 §8](../spec/S-03-agente-aurora.md). Roda em todo PR, em paralelo com o
`portoes`, com o mesmo rigor de antes: `aprovacao: 1.0` nas três suítes, sem `skip`, e sem chave
no repositório ele **reprova** em vez de pular. O vermelho aparece no PR, com o nome do caso e a
fala do modelo no log.

O que mudou é quem esse vermelho segura: **ninguém**.

### 3. O que NÃO muda

- Nenhum limiar. As três suítes seguem exigindo 100%.
- Nenhum caso vira `skip`. A regra do [CLAUDE.md](../../CLAUDE.md) continua valendo na letra:
  eval não se desliga para destravar build.
- Ler o vermelho continua sendo obrigação de quem abre o PR. Ignorar três execuções seguidas
  reprovando é o mesmo que não ter eval — só que mais caro.

## Alternativas consideradas

### Manter os evals bloqueando — **descartada, por ora**

É o certo quando existe cliente real do outro lado. Enquanto não existe, o custo é alto e o
benefício de *bloquear* (em oposição a *avisar*) é baixo: quem lê o PR é a mesma pessoa que
escreveu o código.

### `continue-on-error: true` no passo, dentro do mesmo job — **descartada**

Um passo assim vira aviso amarelo e deixa o job verde. O PR passa a exibir sucesso quando o eval
reprovou, e isso é pior do que bloquear: esconde. Dois jobs mantêm o vermelho visível.

### Rodar os evals só em `push` na `main`, não em PR — **descartada**

Descobrir a regressão depois do merge é descobrir tarde. Roda no PR, onde ainda dá para não
integrar.

### Rodar os evals em agendamento noturno — **descartada por enquanto**

Resolveria o custo, mas desassocia o vermelho do commit que o causou. Vale reabrir se a conta do
provedor incomodar.

## Gatilho de revisão

Este ADR volta à mesa quando **qualquer uma** destas for verdade:

1. **O EV-Sales atender um cliente de verdade.** Aí o `evals` volta a ser obrigatório, e a
   flutuação do provedor vira problema a resolver — não motivo para não bloquear.
2. **Três PRs seguidos forem integrados com o `evals` vermelho sem ninguém ler o log.** É o sinal
   de que "informa" virou "ignora", e aí bloquear é o único jeito de fazer ler.
3. **Os evals passarem a ser determinísticos** — modelo local, provedor fixado, ou o que for. Sem
   a flutuação, o argumento principal deste ADR cai.

## Consequências

**Bom:** o merge deixa de depender de um terceiro. A suíte de 423 testes passa a rodar sempre, em
vez de virar `skipped` quando o modelo se comporta mal. Os dois jobs rodam em paralelo, então o
tempo de parede do CI cai.

**Ruim, e assumido:** uma regressão de conduta da Aurora pode entrar na `main` se alguém não ler o
vermelho. É exatamente o risco que a frase do `docs/spec/README.md` alerta, e este ADR o aceita
com prazo — os três gatilhos acima são a data de validade.

**Honesto:** isto é uma redução de rigor. Não é reorganização, não é limpeza, e não deve ser
descrita como tal em lugar nenhum do repositório.
