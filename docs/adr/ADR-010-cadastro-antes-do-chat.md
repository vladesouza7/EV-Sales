# ADR-010 — Nome e telefone antes do chat, com saída pelo lado

**Status:** aceito
**Data:** 2026-09-03
**Decisão de produto do Raí, registrada com o custo assumido**

---

## Contexto

A landing da Sol & Volt pede **nome e telefone** antes de abrir o chat com a Aurora. Foi decisão
do Raí, e o argumento dele é operacional, não teórico:

> "Conversa sem telefone não vira nada. Eu já vivi isso no Instagram: o cara pergunta tudo, some,
> e eu não tenho como chamar de volta. Prefiro perder o curioso e ficar com quem quer comprar."

Levantei a objeção padrão, e ela é real: formulário antes do valor derruba conversão. A literatura
de captação e a minha própria experiência dizem que pedir contato antes de entregar qualquer coisa
custa entre 20% e 60% dos visitantes. E há um segundo custo, este do
[ADR-007](ADR-007-pii-cifrada-e-mascarada.md): coletar telefone de quem só queria saber o preço do
Dolphin é guardar PII de alguém que nunca virou lead — dado que precisa ser protegido, retido e
eventualmente apagado, sem ter gerado valor nenhum.

O Raí ouviu, considerou, e manteve. É negócio dele, e o argumento do "não tenho como chamar de
volta" pesa mais na realidade dele do que a estatística geral pesa na minha.

## Decisão

**O formulário fica.** Nome e telefone antes do chat, como o Raí pediu — com três mitigações que
custam pouco e recuperam a maior parte do que a objeção previa perder.

### 1. O formulário entrega valor antes de pedir

Ele não é uma barreira anônima ("cadastre-se para continuar"). Ele é a primeira pergunta da
Aurora, e diz o que vai acontecer:

> **Oi! Sou a Aurora, consultora da Sol & Volt.**
> Me diz seu nome e WhatsApp que eu já te mostro o que temos na loja hoje — com preço e
> disponibilidade real, não tabela desatualizada.
>
> *Seu contato fica só aqui com a gente. Se pedir, eu apago.*

A promessa de "estoque de hoje, preço real" é verdadeira ([ADR-003](ADR-003-numeros-nunca-saem-do-modelo.md))
e é exatamente o que o cliente não consegue em nenhum outro site de concessionária.

### 2. Existe uma saída pelo lado, discreta e sem culpa

Um link secundário — *"prefiro só olhar os carros"* — abre o catálogo somente-leitura, com preço
e estoque reais e **sem chat**. Quem chegou para pesquisar pesquisa; quem quer conversar se
cadastra. O Raí não perde o curioso para o concorrente, e o curioso não vira PII guardada à toa.

Do catálogo, um botão "falar com a Aurora sobre este" volta para o formulário — agora depois do
valor entregue, que é a ordem que converte.

### 3. Telefone só é validado de verdade quando ele importa

O formulário valida formato de celular brasileiro e nada além disso. **A verificação real acontece
no handoff:** o cliente é quem inicia o WhatsApp pelo `wa.me`
([ADR-005](ADR-005-handoff-whatsapp-por-wa-me.md)), então a posse do aparelho comprova o número.
Sem SMS, sem código, sem atrito — e o dígito errado vira um lead sem WhatsApp, não uma mensagem
com o nome e o orçamento do Tarcísio no celular de um estranho.

### 4. Retenção com prazo, porque coletar cedo obriga a apagar

Lead que não trocou nenhuma mensagem com a Aurora tem nome e telefone **apagados em 90 dias**, por
rotina automática. A conversa em si é anonimizada e mantida para o Raí auditar
([ADR-006](ADR-006-observabilidade-e-teto-de-custo.md)). Cadastro que não virou conversa não é
ativo — é passivo de LGPD.

## Alternativas consideradas

### Chat aberto, telefone pedido no meio da conversa — **descartada pelo cliente**

Era a minha recomendação. O padrão é conhecido e funciona: o chat abre livre, a Aurora qualifica,
e por volta do quarto ou quinto turno — quando o cliente já recebeu recomendação e está engajado —
ela pede o WhatsApp **com contrapartida**: *"te mando o comparativo dos três com preço, me passa
seu WhatsApp?"* A taxa de entrega de contato nesse ponto é substancialmente maior, e o dado só é
coletado de quem demonstrou interesse.

Perdeu para uma preocupação legítima do Raí: **conversa boa que termina sem contato é prejuízo
visível para ele**, e ele já viveu isso no Instagram. A minha alternativa troca certeza por
volume, e ele não quer fazer essa troca agora.

Fica como a primeira hipótese a testar quando houver dado: um teste A/B, com a métrica de
`leads com contato E ao menos 4 turnos de conversa` — não `cadastros`, que é a métrica que engana.
Se o chat aberto ganhar, este ADR é reaberto com número na mão em vez de opinião.

### Só nome, telefone depois — **descartada**

Metade do atrito, metade do benefício, e ainda pede algo antes de entregar. Não resolve a objeção
nem atende plenamente o Raí.

### Login social (Google) — **descartada**

Traz e-mail verificado sem digitação, mas o canal do negócio é WhatsApp, e e-mail não serve para
nada aqui. Adiciona OAuth, dependência externa e mais um dado pessoal coletado
([ADR-007](ADR-007-pii-cifrada-e-mascarada.md) manda o contrário).

## Consequências

**Aceitas:**

- Perda de topo de funil, de tamanho ainda desconhecido. A saída pelo catálogo recupera parte, e o
  A/B mede o resto.
- PII coletada antes de haver relação. Compensado pela retenção de 90 dias e pela cifragem.
- Duas portas de entrada para manter (chat e catálogo somente-leitura). O catálogo lê as mesmas
  tools do agente, então o custo é uma tela, não um segundo backend.

**Ganhas:**

- Todo lead que entra no chat é chamável de volta — que é o que o Raí pediu, e o que ele mede.
- O handoff pelo `wa.me` já era necessário, e agora ele acumula a função de verificar o telefone
  sem nenhum atrito adicional.
- A hipótese contrária ficou registrada com a métrica que a decide. Quando houver dado, a discussão
  já está montada.
