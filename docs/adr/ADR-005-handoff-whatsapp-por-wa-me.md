# ADR-005 — O cliente inicia o WhatsApp, não a Sol & Volt

**Status:** aceito
**Data:** 2026-09-03

---

## Contexto

A jornada do EV-Sales começa no chat do site e precisa terminar no WhatsApp, porque é lá que a
venda de carro acontece em João Pessoa — é onde o cliente responde, onde ele manda foto para a
esposa e onde ele lembra da conversa três dias depois.

A integração é a **Evolution API**, que opera sobre o WhatsApp Web (via Baileys), não sobre a
Cloud API oficial da Meta. Isso tem uma consequência que muda o desenho:

> Tecnicamente, a Evolution API **consegue** enviar mensagem ativa para qualquer número, sem
> janela de 24h e sem template aprovado. É justamente por conseguir que ela é perigosa.

Disparo ativo a partir de um número novo é o padrão de comportamento que a Meta classifica como
spam. O número da Sol & Volt é o número da loja, impresso no adesivo da vitrine e no Instagram.
Perder esse número não é perder um canal do projeto: é derrubar o comercial inteiro do Raí.

O risco também não é meu para assumir sozinho — é o número dele.

## Decisão

**O primeiro contato no WhatsApp parte sempre do cliente, por link `wa.me` com mensagem
pré-preenchida.** A Evolution API só envia mensagem **dentro de uma conversa que o cliente
iniciou**.

Ao fim da qualificação no chat web, a Aurora oferece a migração, e o botão gera:

```
https://wa.me/5583XXXXXXXXX?text=Oi%2C%20sou%20o%20Tarc%C3%ADsio.%20Vim%20do%20site%20—%20c%C3%B3digo%20SV-7K2M
```

O código `SV-7K2M` é um token opaco de uso único, com validade de 30 minutos, que amarra a
conversa do WhatsApp à sessão do chat web. Quando o webhook da Evolution entrega a primeira
mensagem, o backend reconhece o token, funde as duas conversas e a Aurora continua de onde
parou — com o histórico da qualificação, sem pedir nada de novo.

**Regras de envio, aplicadas no código e não no prompt:**

| Regra | Valor |
|---|---|
| Mensagem ativa para número que nunca escreveu | proibida — sem caminho de código |
| Janela de resposta após a última mensagem do cliente | 24h |
| Teto de mensagens consecutivas sem resposta | 2, depois a Aurora para |
| Follow-up automático | fora do v1 ([PRD §5](../PRD.md#5-o-que-fica-de-fora--e-por-quê)) |
| Instância da Evolution | número dedicado, separado do número pessoal do Raí |

## Alternativas consideradas

### Disparo ativo pela Evolution assim que o lead se cadastra — **descartada**

Era o fluxo mais direto, e o que o desenho original sugeria: capturou telefone na landing,
dispara "oi" no WhatsApp. Descartada pelo risco de banimento acima. Um projeto de portfólio pode
absorver um número banido; a Sol & Volt não.

Há um segundo motivo, menos óbvio e igualmente forte: telefone digitado em formulário erra. O
cliente troca um dígito, e a Aurora vai mandar "Oi, Tarcísio, sobre o BYD Seal" para um
desconhecido — com nome, interesse e faixa de orçamento de outra pessoa dentro. Isso é vazamento
de PII por engano de digitação, e colide de frente com [ADR-007](ADR-007-pii-cifrada-e-mascarada.md).
Com `wa.me`, quem inicia é o dono do aparelho: o número está verificado por construção.

### WhatsApp Cloud API oficial da Meta — **descartada para o v1, recomendada para o v2**

É a escolha correta a prazo: sem risco de ban, com templates aprovados e suporte formal.
Descartada agora porque exige verificação de negócio no Meta Business, número novo em processo
de aprovação e custo por conversa iniciada pela empresa — tudo isso antes da primeira linha de
código funcionar.

O desenho do v1 já protege essa migração: existe uma interface `CanalWhatsApp` com uma
implementação `EvolutionAPI`, e a Aurora não sabe qual está em uso. Trocar é escrever uma segunda
implementação, não reescrever o agente.

### Continuar tudo no chat web, sem WhatsApp — **descartada**

Resolveria o risco eliminando o canal, e mataria o produto junto. O cliente fecha a aba; ele não
fecha o WhatsApp. A continuidade entre sessões é boa parte do valor do EV-Sales.

### QR code em vez de link — **descartada como opção única, mantida como complemento**

Não serve para quem está no celular (não dá para escanear a própria tela), que é a maioria do
tráfego. Fica como alternativa na versão desktop da landing.

## Consequências

**Aceitas:**

- Um toque a mais para o cliente. Medido: se a taxa de aceitação do handoff ficar abaixo de 40%,
  este ADR é reaberto — com Cloud API, não com disparo ativo pela Evolution.
- Lead que se cadastra e nunca abre o WhatsApp fica em silêncio no v1. Sem follow-up automático,
  ele aparece na fila do Tarcísio para contato humano.
- Token de 30 minutos expira, e uma parte dos clientes clica depois. O fallback recupera a
  conversa pelo número, com uma pergunta a mais de confirmação.
- A Evolution API precisa de reconexão de sessão quando o WhatsApp Web cai. Healthcheck e alerta
  em [S-10](../spec/S-10-operacao.md).

**Ganhas:**

- O número comercial da Sol & Volt não corre risco por decisão de arquitetura minha.
- Telefone verificado pela posse do aparelho, não pela digitação — o erro de dígito deixa de ser
  um vazamento de PII.
- O consentimento fica registrado: existe uma mensagem, enviada pelo cliente, que abre a conversa.
