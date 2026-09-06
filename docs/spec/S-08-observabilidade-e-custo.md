# S-08 — Trace, tela "Ler atendimento" e teto de custo

**Depende de:** [S-02](S-02-chat-web-e-sessao.md)
**Implementada ANTES de [S-03](S-03-agente-aurora.md)** — inversão deliberada, ver [ADR-006](../adr/ADR-006-observabilidade-e-teto-de-custo.md)
**Decide por:** [ADR-006](../adr/ADR-006-observabilidade-e-teto-de-custo.md), [ADR-008](../adr/ADR-008-openrouter-como-provedor.md)
**Estado:** parcial. §1, §2, §3, §6 e §7 estão no código (`app/observabilidade.py`, tabelas
`trilha` e `incidentes`). **Falta** a tela "Ler atendimento" (§4) e o painel de custo (§5): as
duas exigem sessão autenticada, que ainda não existe no projeto. O que já está pronto é a
**origem** que as duas leem — e é ela que o [ADR-006](../adr/ADR-006-observabilidade-e-teto-de-custo.md)
manda existir antes da [S-03](S-03-agente-aurora.md).

---

## Objetivo

Duas leituras da mesma origem: o Raí abre um atendimento e **lê o que aconteceu**; eu abro o trace
e **depuro**. E o gasto com LLM tem teto que corta em código, não alerta que chega tarde.

## Comportamento

### 1. Estrutura do trace (Langfuse self-hosted)

| Nível | Corresponde a | Guarda |
|---|---|---|
| Trace | uma conversa | `conversa_id`, `lead_id` mascarado, canal, etapa final, custo total |
| Span de turno | uma mensagem do cliente → resposta | versão do prompt, modelo, tokens, custo, latência |
| Span de tool | uma chamada | nome, argumentos, retorno, duração |
| Span de verificação | [S-03 §4](S-03-agente-aurora.md#4-verificação-numérica-na-saída--o-mecanismo-central) | números extraídos, números permitidos, veredito |
| Evento | aprovação, reserva, incidente | quem, quando, o quê |

**Toda PII é mascarada na camada que monta o span** — nunca por configuração do Langfuse
([S-09](S-09-protecao-de-pii.md)).

### 2. Custo, faturado e não estimado

O custo por turno vem do campo `usage` da resposta do OpenRouter
([ADR-008](../adr/ADR-008-openrouter-como-provedor.md)). Não há tokenizer local, não há tabela de
preço copiada à mão — o número gravado é o número que o Raí paga.

```
custo_turno  = usage devolvido pelo provedor
custo_conversa = soma dos turnos
custo_mes    = contador incremental em Redis, chave "custo:2026-09", com backup diário no Postgres
```

**Emenda do [ADR-012](../adr/ADR-012-provedor-configuravel.md).** Com o provedor virando
configuração, só o OpenRouter devolve custo faturado. `trilha.custo_faturado` guarda a diferença
entre **"custou zero"** e **"não sei quanto custou"**, e o mês que tiver qualquer turno não faturado
registra o incidente `custo_nao_faturado` — porque um teto que deixa de contar sem avisar é pior que
teto nenhum.

### 3. Teto — verificado antes de cada chamada

```python
def pode_chamar_llm() -> Decisao:
    gasto = redis.get(f"custo:{mes_atual()}")
    if gasto >= TETO_MENSAL:  # R$ 900
        return Decisao.CORTAR
    if gasto >= TETO_MENSAL * 0.8:  # R$ 720
        alertar_uma_vez_por_dia()
    return Decisao.SEGUIR
```

| Nível | Valor | O que acontece |
|---|---|---|
| Conversa cara | R$ 0,45 | Marca `custo_alto`, entra na revisão semanal. **Não corta** — cliente no meio da compra não é lugar de economizar |
| Alerta | R$ 720 (80%) | WhatsApp para o Raí e para mim, uma vez por dia |
| Teto | **R$ 900** | Aurora para. Conversa nova recebe mensagem honesta e vai para a fila humana |

Mensagem no teto:

> "Oi! No momento nosso atendimento automático está indisponível. Já avisei o Tarcísio e ele te
> responde em seguida — pode deixar sua pergunta aqui."

Créditos pré-pagos no OpenRouter são a segunda barreira, física: se o corte falhar, a conta para
sozinha ([ADR-008](../adr/ADR-008-openrouter-como-provedor.md)).

### 4. A tela "Ler atendimento" — feita para o Raí

`/atendimentos`, autenticada, perfil `dono` ou `gerente`.

Busca por nome, telefone (número exato) ou data. Resultado: lista com nome mascarado, data,
desfecho, custo. Abrir mostra a conversa em ordem, com três tipos de linha:

```
14:22  Tarcísio   Boa tarde, tenho uns 150 mil, queria um elétrico pra cidade
14:22  Aurora     Oi, Tarcísio! Prazer, sou a Aurora…
       ⚙ consultou o estoque → 4 unidades até R$ 150.000        (14:22:07 · 180ms)
14:23  Aurora     Com 150 mil você tem três opções boas aqui…
14:29  Tarcísio   Gostei do Dolphin. Consegue fazer um preço?
       ⏸ pediu aprovação — R$ 149.990 · chassi …4471            (14:29:41)
       ⏸ APROVADO por Neuza Andrade                             (14:31:02 · 1min21s)
14:31  Aurora     A Neuza confirmou! Fica em R$ 149.990…
       ⚙ reservou o chassi …4471                                (14:33:15)
       ✅ test drive agendado — quinta, 14h, com Tarcísio Lima
```

As linhas `⚙` e `⏸` são o que separa transcrição de auditoria: elas mostram **onde o número veio de
fora do modelo** e **onde um humano decidiu**. Sem elas, o Raí lê e ainda precisa acreditar em mim.

Rodapé do atendimento: custo total, duração, turnos, modelo usado, versão do prompt.

**Sem jargão na tela.** Não aparece "span", "token", "trace_id" nem "latência". Isso está no
Langfuse, que é a minha ferramenta, não a dele.

### 5. Painel de custo — `/custo`

Uma página, quatro números e um gráfico:

```
Setembro/2026

  Gasto do mês       R$ 118,40   ▇▇▇░░░░░░░░░░  13% do teto
  Teto               R$ 900,00
  Conversas          264
  Custo por conversa R$ 0,45 (média)   ·   maior: R$ 1,12

  [gráfico: gasto acumulado por dia, com a linha do teto]
```

Cor: verde até 60%, âmbar até 80%, vermelho acima. Sem tabela de tokens.

### 6. Incidentes

Registrados no trace **e** numa tabela consultável, porque incidente que só existe em trace é
incidente que ninguém revisa:

| Tipo | Gravidade | Ação |
|---|---|---|
| `numero_divergente` | alta | Handoff; revisão obrigatória na semana |
| `chassi_divergente` | crítica | Alerta imediato para mim |
| `reserva_perdida` | informativa | Contagem semanal |
| `injection_suspeita` | alta | Trecho guardado para o eval |
| `evolution_desconectada` | crítica | Alerta imediato |
| `teto_atingido` | crítica | Alerta para o Raí |
| `custo_alto` | baixa | Revisão semanal |

### 7. Saúde

`GET /health` (liveness) e `GET /health/ready` — Postgres, Redis, OpenRouter e a instância da
Evolution. A checagem da Evolution é a que mais importa: sessão do WhatsApp Web cai sozinha
([S-06 §8](S-06-handoff-whatsapp.md#8-falha-da-evolution-api)).

---

## Critérios de aceite

```gherkin
Cenário: toda conversa tem trace completo
  Quando um atendimento termina
  Então existe uma trace com todos os turnos
  E cada tool call tem span com argumentos e retorno
  E o custo total está preenchido

Cenário: o custo é o faturado, não estimado
  Quando um turno é processado
  Então o custo gravado vem do campo usage da resposta do provedor

Cenário: o teto corta de verdade
  Dado que o gasto do mês é R$ 900,00
  Quando uma conversa nova começa
  Então nenhuma chamada ao LLM é feita
  E o cliente recebe a mensagem de indisponibilidade
  E a conversa entra na fila humana

Cenário: alerta em 80%
  Dado que o gasto passou de R$ 720
  Então o Raí recebe alerta no WhatsApp
  E o alerta não se repete no mesmo dia

Cenário: conversa cara não é cortada no meio
  Dado uma conversa que já custou R$ 0,60
  Quando o cliente manda outra mensagem
  Então a Aurora responde normalmente
  E a conversa é marcada com "custo_alto"

Cenário: o Raí lê sem jargão
  Quando o Raí abre um atendimento
  Então vê as mensagens em ordem cronológica
  E vê as linhas de consulta ao estoque e de aprovação
  E não vê as palavras "span", "token" ou "trace_id"

Cenário: a tela mostra quem aprovou
  Dado um atendimento com condição aprovada
  Então a linha de aprovação mostra "Neuza Andrade" e o horário

Cenário: nenhuma PII no trace
  Quando um atendimento completo é gravado
  Então nenhum span contém telefone ou nome completo em claro
```

## Fora do escopo

- Prometheus, Grafana e Loki ([ADR-006](../adr/ADR-006-observabilidade-e-teto-de-custo.md)).
- Dashboard de funil e analytics de conversão ([PRD §5](../PRD.md#5-o-que-fica-de-fora--e-por-quê)).
- Exportar conversa para CSV ou PDF.
- Alerta por e-mail ou SMS — o canal do Raí é o WhatsApp.
