# Arquitetura em C4 — EV-Sales

Os três níveis do [modelo C4](https://c4model.com): contexto, contêineres e componentes.
Estado em **10 de setembro de 2026**, lido do código — não do desenho-alvo.

Desenhados em `flowchart`, não na sintaxe `C4Context` do Mermaid: aquela é experimental e o
Mermaid embutido no Obsidian nem sempre a suporta. O `flowchart` renderiza em qualquer versão,
e as cores seguem a convenção do C4 — azul-escuro para pessoa, azul para o sistema, cinza para
o que é externo.

O [ARQUITETURA.md](ARQUITETURA.md#onde-o-código-está-hoje) desenha o alvo e diz, ao lado, o que
ainda não existe. Aqui é o contrário: o desenho é o que roda, e as caixas **hachuradas** são as
que a [S-10 §1](spec/S-10-operacao.md) e a [S-08](spec/S-08-observabilidade-e-custo.md) ainda devem.

---

## Nível 1 — Contexto

Quem usa o EV-Sales e com o que ele fala.

```mermaid
flowchart TB
    cliente["<b>Cliente</b><br>Quer um elétrico<br>Chega pela landing ou pelo WhatsApp"]
    neuza["<b>Neuza — gerente</b><br>Aprova condição e reserva<br>Nada irreversível passa sem ela"]
    rai["<b>Raí — dono</b><br>Lê atendimento e custo<br>Configura provedor e WhatsApp"]
    vendedor["<b>Tarcísio e Jaqueline</b><br>Recebem o test drive<br>Registram o desfecho"]

    evsales["<b>EV-Sales</b><br>A Aurora qualifica, recomenda a partir do estoque real,<br>pede aprovação humana e reserva o chassi<br>Termina no test drive marcado"]

    llm["<b>Provedor de LLM</b><br>OpenRouter, Ollama, Gemini, NVIDIA<br>Decide o que dizer — nunca os números"]
    wa["<b>WhatsApp</b><br>O cliente inicia por wa.me<br>O sistema só responde"]
    loja["<b>Sistemas da concessionária</b><br>Financiamento, faturamento, NF-e<br>Fora do escopo — ADR-011"]

    cliente -->|"conversa e agenda test drive<br>HTTPS e SSE"| evsales
    neuza -->|"aprova ou recusa a condição"| evsales
    rai -->|"lê atendimento e custo, configura"| evsales
    vendedor -->|"marca o desfecho"| evsales
    cliente -->|"continua a mesma conversa"| wa
    wa -->|"webhook de mensagem recebida"| evsales
    evsales -->|"responde e avisa a equipe"| wa
    evsales -->|"pede o texto do turno"| llm
    cliente -.->|"fecha a compra presencialmente"| loja

    classDef pessoa fill:#08427b,stroke:#052e56,color:#ffffff
    classDef sistema fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef externo fill:#999999,stroke:#6b6b6b,color:#ffffff
    class cliente,neuza,rai,vendedor pessoa
    class evsales sistema
    class llm,wa,loja externo
```

A seta pontilhada da direita é a fronteira do
[ADR-011](adr/ADR-011-jornada-digital-termina-no-test-drive.md): depois do test drive, a venda
acontece nos sistemas que a loja já opera. Só o **desfecho** volta.

---

## Nível 2 — Contêineres

O que roda, e onde o estado mora. As quatro caixas hachuradas **não existem no código**.

```mermaid
flowchart TB
    cliente["<b>Cliente</b><br>Landing, chat e test drive"]
    equipe["<b>Neuza, Raí e vendedores</b><br>Telas autenticadas"]

    subgraph sistema["EV-Sales"]
        direction TB
        web["<b>Front estático</b><br>HTML, CSS e JS sem build<br>10 telas servidas pelo próprio FastAPI"]
        api["<b>API</b><br>Python 3.12+, FastAPI, uvicorn<br>Rotas, SSE do chat, loop da Aurora,<br>aprovação, reserva, agenda e servidor MCP<br>O turno roda na própria requisição"]
        pg[("<b>Postgres 17 + pgvector</b><br>Fonte da verdade: estoque por chassi,<br>preço em centavos, leads cifrados, conversas,<br>aprovações, reservas, trilha e conhecimento")]
        minio[("<b>MinIO</b><br>Object storage S3<br>Fotos por chassi e o Espelho gerado<br>Bucket privado — ADR-013")]
        evo["<b>Evolution API</b><br>Node<br>Ponte com o WhatsApp. Só responde"]

        nginx["<b>nginx</b><br>S-10 §1 — não construído<br>TLS e roteamento no perfil prod"]
        redis[("<b>Redis</b><br>S-10 §1 — não construído<br>Hoje: coluna no Postgres e<br>memória do processo")]
        worker["<b>Worker</b><br>S-10 §1 — não construído<br>Hoje: um asyncio.Task dentro da API"]
        langfuse["<b>Langfuse</b><br>S-08 — não construído<br>Segunda leitura da trilha"]
    end

    llm["<b>Provedor de LLM</b><br>OpenRouter e compatíveis"]
    wa["<b>WhatsApp</b><br>Rede do cliente"]

    cliente -->|"abre<br>HTTPS"| web
    equipe -->|"abre autenticada<br>cookie de sessão"| web
    web -->|"REST e stream do chat<br>JSON e SSE"| api
    api -->|"reserva é UPDATE ... WHERE status='disponivel'<br>SQLAlchemy e psycopg"| pg
    api -->|"grava e serve por /fotos<br>S3"| minio
    api -->|"turno da Aurora<br>HTTPS"| llm
    api -->|"envia mensagem<br>HTTP com chave"| evo
    evo -->|"webhook"| api
    evo -->|"sessão do número da loja"| wa
    cliente -->|"escreve pelo wa.me"| wa

    classDef pessoa fill:#08427b,stroke:#052e56,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef dados fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef externo fill:#999999,stroke:#6b6b6b,color:#ffffff
    classDef planejado fill:#dddddd,stroke:#999999,color:#555555,stroke-dasharray: 5 5
    class cliente,equipe pessoa
    class web,api,evo container
    class pg,minio dados
    class llm,wa externo
    class nginx,redis,worker,langfuse planejado
```

**A pausa não é um processo suspenso.** O Espelho e a reserva esperam a Neuza, e esse estado é uma
linha no Postgres — reiniciar o container no meio não tem efeito. É por isso que não há caixa de
"orquestrador" aqui: [ADR-009](adr/ADR-009-sem-framework-de-orquestracao.md).

---

## Nível 3 — Componentes da API

Dentro do único contêiner que tem lógica.

```mermaid
flowchart TB
    subgraph api["API — FastAPI"]
        direction TB

        subgraph entrada["Entrada"]
            rotas["<b>Telas e rotas</b><br>main, leads, conversas,<br>autenticacao, atendimentos, configuracoes<br>HTTP, stream SSE e sessão por cookie"]
            mcp["<b>Servidor MCP</b><br>mcp_server<br>Escopo da tela do dono, mais leitura<br>Chave própria — nunca o login"]
        end

        subgraph aurora["A Aurora"]
            turno["<b>Loop do turno</b><br>ia/turno<br>Gera, chama tool, verifica, só então grava"]
            etapas["<b>Tools por etapa</b><br>ia/etapas, ia/registro<br>A lista muda, não o prompt"]
            tools["<b>Tools de domínio</b><br>ia/tools — estoque, conhecimento, qualificacao<br>Todo número sai daqui — ADR-003"]
            verif["<b>Verificação numérica</b><br>ia/verificacao<br>Uma regeneração, senão handoff"]
            provedor["<b>Provedor de LLM</b><br>ia/provedor<br>Trocar de modelo é configuração"]
        end

        subgraph negocio["Domínio"]
            dominio["<b>Aprovação, Espelho, Reserva e Agenda</b><br>aprovacao, espelho, reserva, testdrive, agenda<br>A pausa da Neuza e o UPDATE do chassi"]
            whats["<b>WhatsApp</b><br>whatsapp<br>Token, webhook com dedup e envio"]
        end

        subgraph transversal["Transversal"]
            pii["<b>PII</b><br>core/pii<br>AES-256-GCM, hash indexável,<br>máscara e redação antes do prompt<br>Revisão humana obrigatória"]
            obs["<b>Trilha e custo</b><br>observabilidade, limite<br>Teto de custo que corta"]
            conf["<b>Leitor de configuração</b><br>configuracao<br>banco, depois .env, depois preset<br>Leitor único — ADR-014"]
        end
    end

    pg[("<b>Postgres + pgvector</b><br>Fonte da verdade")]
    evo["<b>Evolution API</b>"]
    llm["<b>Provedor de LLM</b>"]

    rotas --> turno
    rotas --> dominio
    rotas --> pii
    mcp --> conf
    turno --> etapas
    etapas --> tools
    turno --> provedor
    turno --> verif
    turno --> pii
    turno --> obs
    verif -->|"reconfere na fonte"| tools
    tools -->|"SELECT"| pg
    dominio -->|"UPDATE ... WHERE"| pg
    conf -->|"lê cifrado"| pg
    provedor -->|"HTTPS"| llm
    whats --> evo

    classDef componente fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef dados fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef externo fill:#999999,stroke:#6b6b6b,color:#ffffff
    class rotas,mcp,turno,etapas,tools,verif,provedor,dominio,whats,pii,obs,conf componente
    class pg dados
    class evo,llm externo
```

Repare no caminho do número: **`tools` é o único componente que fala com o estoque**, e a
`verificacao` volta à mesma fonte para conferir o que o modelo escreveu. O `provedor` não toca o
Postgres em lugar nenhum — é a [invariante 1](../CLAUDE.md) desenhada.

---

## O mecanismo central — tools por etapa

Não é C4, mas é o que define o que a Aurora **pode fazer**. A restrição é uma lista, não uma frase
no prompt: [ia/etapas.py](../backend/app/ia/etapas.py).

```mermaid
stateDiagram-v2
    direction LR

    [*] --> saudacao
    saudacao --> qualificacao
    qualificacao --> recomendacao
    recomendacao --> objecao
    objecao --> recomendacao
    recomendacao --> condicao
    objecao --> condicao
    condicao --> aguardando_aprovacao
    aguardando_aprovacao --> reserva : Neuza aprova
    aguardando_aprovacao --> humano : Neuza recusa
    reserva --> test_drive
    test_drive --> encerrada
    humano --> encerrada
    encerrada --> [*]

    note right of saudacao
        nenhuma tool de domínio
    end note

    note right of aguardando_aprovacao
        lista VAZIA — nem transferir_para_humano
        o estado é uma linha no Postgres
    end note

    note right of reserva
        reservar_chassi
        UPDATE ... WHERE status='disponivel'
    end note
```

| Etapa | Tools disponíveis |
|---|---|
| `saudacao` | só `transferir_para_humano` |
| `qualificacao` | `registrar_qualificacao`, `buscar_conhecimento` |
| `recomendacao` | `buscar_unidades`, `detalhar_unidade`, `comparar_unidades`, `buscar_conhecimento` |
| `objecao` | as de `recomendacao` mais `calcular_custo_km` — esta ainda não implementada |
| `condicao` | `solicitar_aprovacao` |
| `aguardando_aprovacao` | **nenhuma** |
| `reserva` | `reservar_chassi` |
| `test_drive` | `consultar_agenda`, `agendar_test_drive` — ainda não implementadas |
| `humano`, `encerrada` | **nenhuma** |

`transferir_para_humano` entra em toda etapa que ainda tem turno da Aurora. As tools marcadas como
não implementadas estão no mapa mas o registro só oferece ao modelo o que existe — ampliar essa
lista exige revisão humana ([CLAUDE.md](../CLAUDE.md)).

---

## Como exportar a imagem no Obsidian

1. Abra esta nota em **Modo de leitura** — os quatro diagramas renderizam.
2. Uma imagem por diagrama: botão direito sobre o SVG → **Copiar imagem**. Página inteira:
   `Ctrl+P` → **Export to PDF**.
3. Diagramas largos: `Ctrl+-` reduz o zoom antes da captura, senão o nível 2 corta na margem.
