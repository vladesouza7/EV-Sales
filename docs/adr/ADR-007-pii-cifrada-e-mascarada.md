# ADR-007 — PII cifrada em repouso, mascarada em tudo que se lê

**Status:** aceito
**Data:** 2026-09-03
**Decide sobre:** [CASE.md — fala 5 do Raí](../CASE.md#o-que-ele-disse-com-as-palavras-dele)

---

## Contexto

> "Telefone de cliente é o meu ativo. Foi o que sobrou da loja do meu pai. Se vazar, meu
> concorrente liga pra todo mundo na segunda-feira."

A frase revela que, para o Raí, a base de telefones **é** o patrimônio — 35 anos de Falcão
Automóveis viraram uma lista de contatos. E o desenho do EV-Sales espalha esse dado por muitos
lugares ao mesmo tempo:

```
formulário da landing → Postgres → Redis (sessão) → prompt do LLM → OpenRouter → provedor do modelo
                                 ↘ trace do Langfuse
                                 ↘ log da aplicação
                                 ↘ webhook da Evolution API
                                 ↘ PDF do espelho de condição
```

São oito lugares. Cifrar a coluna do banco e parar por aí resolve um deles e dá uma sensação
falsa de que o problema foi tratado — o vazamento realista não é alguém roubando o dump do
Postgres, é um `logger.info(f"lead: {lead}")` que alguém (eu, ou um agente de código numa
madrugada) escreveu sem pensar.

E há um agravante específico deste projeto: a conversa **vai para fora**. O texto que o cliente
digita segue para o OpenRouter e daí para o provedor do modelo. Se o Tarcísio escrever "meu CPF
é 000.000.000-00, pode reservar", isso sai da infraestrutura da Sol & Volt.

## Decisão

**Três camadas, aplicadas em ordem de proximidade com o dado.**

### 1. Minimização — o dado que não é coletado não vaza

O v1 coleta **nome e telefone**, e mais nada. Sem CPF, sem e-mail, sem endereço, sem CNH — nem
para o test drive, que hoje é conferido presencialmente no balcão e continua assim. Cada campo
novo precisa passar por este ADR.

O nome é guardado como o cliente digitou; se ele der só o primeiro nome, é o que fica.

### 2. Cifragem em repouso, com a chave fora do banco

`telefone` e `nome` são cifrados na aplicação (AES-GCM, chave em variável de ambiente, nunca no
Compose versionado). O banco guarda ciphertext.

Junto vai um `telefone_hash` (HMAC-SHA256 com pepper) — determinístico, indexável — que resolve
os dois casos reais de busca sem decifrar nada: reconhecer o cliente que volta, e amarrar o
webhook da Evolution ao lead existente.

Decifrar é uma chamada explícita, e só três lugares a fazem: montar o link `wa.me`, enviar pela
Evolution API, e a tela do vendedor autenticado. Nenhum desses três é código que um agente vai
escrever sem eu revisar.

### 3. Mascaramento na origem — e "na origem" é a parte que importa

O mascaramento acontece **na camada que constrói o registro**, não numa configuração do destino:

```
(83) 98871-4471  →  (83) *****-4471
Tarcísio Nóbrega →  Tarcísio N.
000.000.000-00   →  [CPF-REMOVIDO]
```

Isso vale para log, trace do Langfuse, mensagem de erro e stack trace. Configurar mascaramento no
Langfuse protegeria o Langfuse; mascarar antes protege qualquer destino, inclusive o que eu
adicionar daqui a seis meses. É a mesma lógica do [ADR-006](ADR-006-observabilidade-e-teto-de-custo.md).

**Para o modelo**, um passo a mais: um redator determinístico varre a mensagem do cliente antes
de montar o prompt e substitui CPF, e-mail, placa e cartão por marcadores. O telefone nunca entra
no prompt — a Aurora não precisa dele para conversar, e o `lead_id` basta para as tools.

No OpenRouter, a rota é restrita a provedores com política de retenção zero e sem treinamento
sobre os dados ([ADR-008](ADR-008-openrouter-como-provedor.md)).

### Teste que bloqueia

Um teste do CI roda um atendimento completo, captura stdout, o banco de traces e os logs, e
**falha se encontrar qualquer telefone ou nome completo em claro**. É a única forma de essa
decisão sobreviver a seis meses de commits.

## Alternativas consideradas

### Cifrar o banco inteiro e não mascarar o resto — **descartada**

Cifragem em repouso protege contra roubo de disco ou dump. O vazamento provável é o log, e log é
justamente o que mais gente lê e o que mais fácil se copia para um chamado de suporte. Cifrar
tudo e logar em claro é cofre com a senha escrita na porta.

### Não cifrar, só controlar acesso ao banco — **descartada**

Suficiente para a operação atual e frágil no cenário que ele descreveu. Uma credencial de
`readonly` vazada devolve a base inteira. Com cifragem na aplicação, ela devolve ciphertext.

### Tokenizar o telefone e nunca guardar o número — **descartada, com pena**

Arquitetonicamente a mais limpa: guardar só o hash e nunca o valor. Descartada porque o produto
precisa **enviar mensagem** pela Evolution API, e para isso o número precisa existir em algum
lugar recuperável. Hash é de mão única.

Fica o que dá para preservar: o hash é o identificador em todo o sistema, e o ciphertext só é
tocado nos três pontos de saída.

### Mascarar só no Langfuse, via configuração dele — **descartada**

Uma linha de config, protege um destino. A decisão inteira dependeria de ninguém desmarcar uma
caixa numa interface — e um agente de código adicionando um `logger.debug` continuaria vazando.

### Não mandar nada do cliente para provedor externo (LLM local) — **descartada neste ADR**

Elimina a superfície de uma vez, e é o argumento mais forte a favor de Ollama. Perdeu para
qualidade em tool calling e para o requisito de custo mensurável, em [ADR-008](ADR-008-openrouter-como-provedor.md).
O redator determinístico e a restrição de provedores no OpenRouter são a mitigação assumida.

## Consequências

**Aceitas:**

- Busca por telefone só funciona com o número exato (hash determinístico). Sem busca parcial por
  "termina em 4471". A Neuza busca por nome.
- Perder a chave é perder o acesso aos nomes e telefones. Procedimento de backup da chave, fora
  do repositório, documentado em [S-10](../spec/S-10-operacao.md).
- Hash determinístico é vulnerável a ataque de dicionário se o pepper vazar junto — o espaço de
  celulares brasileiros é pequeno. Aceito: pepper mora em variável de ambiente separada da chave
  de cifragem.
- Debugar fica mais chato: o log diz `(83) *****-4471`. É o objetivo.
- O redator vai ocasionalmente mascarar um número inofensivo. Prefiro esse erro ao contrário.

**Ganhas:**

- O ativo que o Raí herdou do pai não está em texto claro em nenhum lugar que se lê por acidente.
- A proteção viaja junto com o dado, então ela sobrevive a trocar Langfuse, trocar provedor de
  LLM ou adicionar um destino novo.
- O teste de CI transforma "a gente tomou cuidado" em algo que quebra o build.
