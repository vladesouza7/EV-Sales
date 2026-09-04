Perfeito. Vamos montar isso como um **projeto real de produção**, não apenas um protótipo.

Vou chamar provisoriamente de **EV Sales AI**: um CRM + agente de vendas especializado em veículos elétricos, operando pelo WhatsApp.

---

# 🚗 EV Sales AI — Arquitetura

A arquitetura que eu adotaria é:

```text
                              INTERNET
                                  │
                                  ▼
                         ┌─────────────────┐
                         │      NGINX      │
                         │ HTTPS / Reverse │
                         │      Proxy      │
                         └────────┬────────┘
                                  │
                 ┌────────────────┴────────────────┐
                 │                                 │
                 ▼                                 ▼
        ┌─────────────────┐               ┌─────────────────┐
        │ React + Vite    │               │    FastAPI      │
        │ TypeScript      │               │     API         │
        │ Tailwind        │               │                 │
        └─────────────────┘               └────────┬────────┘
                                                   │
                     ┌─────────────────────────────┼───────────────┐
                     │                             │               │
                     ▼                             ▼               ▼
               PostgreSQL                       Redis          Qdrant
               ──────────                       ─────          ──────
               CRM                              Cache          RAG
               Leads                            Sessions       Embeddings
               Clientes                         Queue          Conhecimento
               Veículos                         Locks          Catálogos
               Conversas                        Jobs           Manuais
               Vendas
                     │
                     ▼
                   MinIO
                   ─────
                   Fotos
                   PDFs
                   Catálogos
                   Documentos

                                  ▲
                                  │
                           ┌──────┴───────┐
                           │    Worker    │
                           │              │
                           │ Redis Queue  │
                           │ AI / RAG     │
                           │ Follow-ups   │
                           └──────▲───────┘
                                  │
                                  │
                           ┌──────┴───────┐
                           │ Evolution API│
                           │   WhatsApp   │
                           └──────▲───────┘
                                  │
                                  ▼
                              WHATSAPP
```

---

# 1. Estrutura do projeto

Eu começaria desta maneira:

```text
ev-sales-ai/
│
├── docker-compose.yml
├── .env
├── .env.example
├── .gitignore
├── README.md
│
├── nginx/
│   └── nginx.conf
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   │
│   ├── app/
│   │   ├── main.py
│   │   │
│   │   ├── api/
│   │   │   ├── deps.py
│   │   │   │
│   │   │   └── v1/
│   │   │       ├── auth.py
│   │   │       ├── customers.py
│   │   │       ├── conversations.py
│   │   │       ├── leads.py
│   │   │       ├── vehicles.py
│   │   │       ├── sales.py
│   │   │       ├── appointments.py
│   │   │       ├── dashboard.py
│   │   │       └── webhooks.py
│   │   │
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── database.py
│   │   │   ├── redis.py
│   │   │   ├── security.py
│   │   │   └── logging.py
│   │   │
│   │   ├── models/
│   │   │   ├── user.py
│   │   │   ├── customer.py
│   │   │   ├── lead.py
│   │   │   ├── conversation.py
│   │   │   ├── message.py
│   │   │   ├── vehicle.py
│   │   │   ├── proposal.py
│   │   │   └── appointment.py
│   │   │
│   │   ├── schemas/
│   │   │   ├── customer.py
│   │   │   ├── lead.py
│   │   │   ├── message.py
│   │   │   └── vehicle.py
│   │   │
│   │   ├── services/
│   │   │   ├── whatsapp.py
│   │   │   ├── customer.py
│   │   │   ├── lead.py
│   │   │   ├── vehicle.py
│   │   │   └── notification.py
│   │   │
│   │   ├── ai/
│   │   │   ├── agent.py
│   │   │   ├── prompts.py
│   │   │   ├── memory.py
│   │   │   ├── tools.py
│   │   │   ├── rag.py
│   │   │   └── guardrails.py
│   │   │
│   │   ├── integrations/
│   │   │   ├── evolution.py
│   │   │   ├── qdrant.py
│   │   │   ├── minio.py
│   │   │   └── llm.py
│   │   │
│   │   └── workers/
│   │       ├── tasks.py
│   │       ├── message_worker.py
│   │       └── document_worker.py
│   │
│   └── migrations/
│
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.ts
│   │
│   └── src/
│       ├── components/
│       ├── layouts/
│       ├── pages/
│       │   ├── Dashboard/
│       │   ├── Conversations/
│       │   ├── Leads/
│       │   ├── Customers/
│       │   ├── Vehicles/
│       │   ├── Proposals/
│       │   └── Settings/
│       │
│       ├── services/
│       │   └── api.ts
│       │
│       ├── hooks/
│       ├── stores/
│       ├── types/
│       └── App.tsx
│
├── knowledge/
│   ├── vehicles/
│   ├── manuals/
│   ├── catalogs/
│   └── faq/
│
├── storage/
│
└── scripts/
    ├── init-db.sh
    └── seed-vehicles.py
```

---

# 2. Docker Compose

A primeira versão pode ficar assim:

```yaml
services:

  nginx:
    image: nginx:alpine
    container_name: ev-nginx
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./frontend/dist:/usr/share/nginx/html:ro
    depends_on:
      - backend
      - frontend
    networks:
      - ev-network

  frontend:
    build:
      context: ./frontend
    container_name: ev-frontend
    restart: unless-stopped
    environment:
      - VITE_API_URL=${VITE_API_URL}
    networks:
      - ev-network

  backend:
    build:
      context: ./backend
    container_name: ev-backend
    restart: unless-stopped
    env_file:
      - .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      qdrant:
        condition: service_started
      minio:
        condition: service_started
    networks:
      - ev-network

  worker:
    build:
      context: ./backend
    container_name: ev-worker
    restart: unless-stopped
    command: python -m app.workers.message_worker
    env_file:
      - .env
    depends_on:
      - backend
      - redis
      - postgres
      - qdrant
    networks:
      - ev-network

  postgres:
    image: postgres:17
    container_name: ev-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 5s
      retries: 10
    networks:
      - ev-network

  redis:
    image: redis:8-alpine
    container_name: ev-redis
    restart: unless-stopped
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10
    networks:
      - ev-network

  qdrant:
    image: qdrant/qdrant:latest
    container_name: ev-qdrant
    restart: unless-stopped
    volumes:
      - qdrant_data:/qdrant/storage
    networks:
      - ev-network

  minio:
    image: minio/minio:latest
    container_name: ev-minio
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes:
      - minio_data:/data
    networks:
      - ev-network

  evolution:
    image: evoapicloud/evolution-api:latest
    container_name: ev-evolution
    restart: unless-stopped
    environment:
      AUTHENTICATION_API_KEY: ${EVOLUTION_API_KEY}
      DATABASE_PROVIDER: postgresql
      DATABASE_CONNECTION_URI: ${EVOLUTION_DATABASE_URL}
      CACHE_REDIS_ENABLED: "true"
      CACHE_REDIS_URI: redis://redis:6379
    depends_on:
      - postgres
      - redis
    networks:
      - ev-network


volumes:
  postgres_data:
  redis_data:
  qdrant_data:
  minio_data:


networks:
  ev-network:
    driver: bridge
```

**Observação importante:** eu trataria esse `docker-compose.yml` como a base arquitetural; as variáveis e nomes exatos exigidos pela versão da Evolution API que você instalar devem ser conferidos na documentação/release correspondente antes do deploy.

---

# 3. `.env`

Nunca colocaria senhas diretamente no Compose.

```env
APP_ENV=development

POSTGRES_DB=ev_sales
POSTGRES_USER=ev_user
POSTGRES_PASSWORD=change_me

DATABASE_URL=postgresql+asyncpg://ev_user:change_me@postgres:5432/ev_sales

REDIS_URL=redis://redis:6379/0

QDRANT_URL=http://qdrant:6333

MINIO_ENDPOINT=minio:9000
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=change_me
MINIO_BUCKET=ev-sales

EVOLUTION_API_URL=http://evolution:8080
EVOLUTION_API_KEY=change_me

EVOLUTION_DATABASE_URL=postgresql://ev_user:change_me@postgres:5432/evolution

JWT_SECRET=change_me
JWT_ALGORITHM=HS256

LLM_PROVIDER=ollama
OLLAMA_URL=http://ollama:11434
OLLAMA_MODEL=qwen3

VITE_API_URL=http://localhost/api
```

Em produção, obviamente, eu não usaria senhas como `change_me`.

---

# 4. PostgreSQL

Aqui fica o **sistema de registro oficial**.

Um lead poderia ter:

```text
Lead
│
├── id
├── customer_id
├── source
├── status
├── stage
├── score
├── temperature
├── budget_min
├── budget_max
├── preferred_brand
├── preferred_model
├── financing
├── trade_in
├── created_at
└── updated_at
```

### Status

```text
NEW
CONTACTED
QUALIFIED
PROPOSAL
TEST_DRIVE
NEGOTIATION
WON
LOST
```

### Temperatura

```text
COLD
WARM
HOT
```

---

# 5. Conversas

Essa parte será fundamental.

```text
customers
    │
    └── conversations
            │
            └── messages
```

Exemplo:

```text
Customer
   ↓
Conversation
   ↓
Message
   ↓
Message
   ↓
Message
```

Cada mensagem:

```text
id
conversation_id
direction
sender
content
message_type
whatsapp_message_id
ai_generated
created_at
```

Assim você consegue reconstruir **100% da conversa**.

---

# 6. Redis

Redis terá dois papéis diferentes.

### Estado rápido

```text
conversation:{id}:state
```

Exemplo:

```json
{
  "stage": "qualification",
  "budget": 150000,
  "brand": "BYD",
  "model": null
}
```

### Fila

```text
whatsapp_messages
ai_processing
followups
documents
notifications
```

---

# 7. O coração: AI Agent

Aqui está a parte mais interessante.

Eu não faria simplesmente:

```python
response = llm(message)
```

Faria:

```text
                    CLIENTE
                       │
                       ▼
                 Nova mensagem
                       │
                       ▼
                 Context Builder
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
     Redis         PostgreSQL       Qdrant
    memória         histórico         RAG
        │              │              │
        └──────────────┼──────────────┘
                       ▼
                    AGENTE
                       │
               ┌───────┴────────┐
               │                │
               ▼                ▼
             RAG              TOOLS
                                │
                 ┌──────────────┼──────────────┐
                 ▼              ▼              ▼
              veículos       estoque       financiamento
                 │              │              │
                 └──────────────┼──────────────┘
                                ▼
                              LLM
                                │
                                ▼
                             resposta
```

---

# 8. Tools do vendedor

Eu criaria ferramentas como:

```python
buscar_veiculos()

buscar_veiculo()

comparar_veiculos()

consultar_estoque()

consultar_preco()

consultar_condicoes()

calcular_financiamento()

calcular_custo_por_km()

buscar_carregadores()

buscar_concessionarias()

criar_lead()

atualizar_lead()

agendar_test_drive()

criar_proposta()

transferir_para_humano()
```

Isso é fundamental.

A IA **não deve decidir o preço do carro por conta própria**.

Ela consulta o banco.

---

# 9. Exemplo real

Cliente:

> "Quero um elétrico econômico até 150 mil."

O agente pode identificar:

```json
{
  "intent": "vehicle_search",
  "budget_max": 150000,
  "priority": "economy",
  "fuel": "electric"
}
```

Depois:

```text
buscar_veiculos(
    fuel="electric",
    max_price=150000
)
```

PostgreSQL responde.

Depois o agente pode dizer algo como:

> "Tenho algumas opções nessa faixa. Você pretende usar mais na cidade ou também viajar?"

Isso é **venda consultiva**, não simplesmente FAQ.

---

# 10. Qdrant + RAG

Imagine que você carregue:

```text
BYD Dolphin - Manual.pdf
BYD Dolphin - Catálogo.pdf
GWM Ora 03 - Manual.pdf
GWM Ora 03 - Catálogo.pdf
Volvo EX30 - Catálogo.pdf
FAQ carregamento.pdf
FAQ financiamento.pdf
```

Pipeline:

```text
PDF
 │
 ▼
Parser
 │
 ▼
Chunking
 │
 ▼
Embedding
 │
 ▼
Qdrant
```

Quando o cliente perguntar:

> "Quanto tempo demora para carregar?"

o agente busca o contexto relevante.

---

# 11. MinIO

Eu faria um bucket:

```text
ev-sales
```

Estrutura:

```text
vehicles/
    byd/
        dolphin/
        seal/
        song-plus/

    gwm/
        ora-03/
        haval-h6/

    volvo/
        ex30/

documents/
    manuals/
    catalogs/
    warranties/

customers/
    documents/

proposals/
```

E o PostgreSQL mantém apenas os metadados.

---

# 12. WhatsApp

O fluxo principal será:

```text
                    WhatsApp
                       │
                       ▼
                 Evolution API
                       │
                    webhook
                       │
                       ▼
                    FastAPI
                       │
                       ▼
                 Salva mensagem
                       │
                       ▼
                     Redis
                       │
                       ▼
                    Worker
                       │
                       ▼
                  AI Agent
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
       Redis       PostgreSQL     Qdrant
          │            │            │
          └────────────┼────────────┘
                       ▼
                     LLM
                       │
                       ▼
                   resposta
                       │
                       ▼
                 Evolution API
                       │
                       ▼
                    WhatsApp
```

---

# 13. React Dashboard

O frontend teria inicialmente:

```text
Dashboard
│
├── 📊 Visão geral
│
├── 💬 Conversas
│
├── 👤 Clientes
│
├── 🎯 Leads
│
├── 🚗 Veículos
│
├── 💰 Propostas
│
├── 📅 Test Drives
│
├── 👨‍💼 Vendedores
│
├── 📈 Relatórios
│
└── ⚙️ Configurações
```

---

# 14. Tela de Conversas

Eu faria algo semelhante a:

```text
┌─────────────────────────────────────────────────────────┐
│ EV SALES AI                                             │
├───────────────┬─────────────────────────┬───────────────┤
│ CONVERSAS     │ CHAT                    │ LEAD          │
│               │                         │               │
│ João 🔥       │ João:                   │ João Silva    │
│ Maria 🟡      │ Quero um elétrico...    │               │
│ Carlos 🔵     │                         │ Score: 87     │
│ Ana 🔥        │ AI:                     │ 🔥 HOT        │
│               │ Tenho algumas opções... │               │
│               │                         │ Budget        │
│               │ João:                   │ R$150.000     │
│               │ Quanto custa?           │               │
│               │                         │ Veículo       │
│               │ AI:                     │ BYD Dolphin   │
│               │                         │               │
└───────────────┴─────────────────────────┴───────────────┘
```

---

# 15. Lead Scoring

Aqui dá para fazer algo muito bom.

Por exemplo:

```text
+20 orçamento informado
+15 modelo definido
+15 perguntou preço
+10 perguntou financiamento
+10 perguntou test drive
+10 informou cidade
+10 perguntou disponibilidade
+10 informou prazo de compra
```

Resultado:

```text
0 ─────────────── 40 ─────────────── 70 ─────────── 100
      FRIO                 MORNO                  QUENTE
```

Um lead com:

```text
87
🔥 HOT
```

pode gerar uma notificação:

> 🔥 Novo lead quente aguardando atendimento.

---

# 16. Handoff para humano

Isso também é essencial.

O cliente pode falar:

> "Quero falar com um vendedor."

Então:

```text
AI
 │
 ▼
transferir_para_humano()
 │
 ▼
Redis
 │
 ▼
conversation.mode = HUMAN
```

A IA para de responder.

O vendedor assume.

Depois:

```text
vendedor → "Devolver para IA"
```

e:

```text
conversation.mode = AI
```

---

# 17. Follow-up automático

Uma das funções que mais pode gerar valor comercial.

Exemplo:

Cliente recebeu proposta e desapareceu.

Depois de 24h:

```text
Redis Scheduler
       │
       ▼
Follow-up Worker
       │
       ▼
AI
       │
       ▼
Evolution API
       │
       ▼
WhatsApp
```

Mensagem:

> "Olá, João! Tudo bem? Vi que você estava avaliando o Dolphin. Posso te ajudar a comparar a autonomia dele com outra opção na mesma faixa?"

Isso é muito mais interessante que simplesmente:

> "Você ainda tem interesse?"

---

# 18. Segurança

Eu já colocaria desde o começo:

```text
JWT
RBAC
CORS
Rate limiting
Webhook authentication
Secrets via .env
SQL injection protection
Input validation
Audit logs
HTTPS
```

Perfis:

```text
ADMIN
MANAGER
SELLER
AI_OPERATOR
VIEWER
```

Um vendedor não deveria conseguir apagar todo o estoque, por exemplo.

---

# 19. Observabilidade

Como você já trabalha com Docker e observabilidade, eu colocaria uma camada própria:

```text
Prometheus
     │
     ▼
Grafana
     │
     ├── API
     ├── Redis
     ├── PostgreSQL
     ├── Worker
     └── Evolution
```

E logs:

```text
Loki
  │
  ▼
Grafana
```

Métricas interessantes:

```text
messages_received
messages_sent
ai_response_time
llm_tokens
rag_latency
lead_conversion
leads_created
hot_leads
test_drives
sales
```

---

# 20. Uma decisão importante sobre Ollama

Como você já trabalha com **Ollama**, podemos deixar o projeto preparado para:

```text
LLM_PROVIDER=ollama
```

mas também:

```text
LLM_PROVIDER=openai
```

ou outro provedor.

Então:

```text
                AI Service
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
       Ollama      API      outro LLM
```

O restante do sistema não precisa saber qual modelo está sendo utilizado.

Isso é uma **ótima proteção arquitetural contra lock-in**.

---

# 21. MVP que eu faria primeiro

Não tentaria construir tudo de uma vez.

### Sprint 1

```text
Docker
PostgreSQL
Redis
FastAPI
React
Vite
Tailwind
Nginx
```

### Sprint 2

```text
Evolution API
Webhook
WhatsApp
Conversas
Mensagens
```

### Sprint 3

```text
LLM
AI Agent
Memory
Tools
```

### Sprint 4

```text
Qdrant
Embeddings
RAG
MinIO
Documentos
```

### Sprint 5

```text
Leads
Lead scoring
CRM
Dashboard
```

### Sprint 6

```text
Follow-up
Test drive
Propostas
Handoff humano
Analytics
```

---

# ⭐ Stack final

Minha recomendação fica:

```text
┌──────────────────────────────────────────┐
│                 FRONTEND                 │
│       React + Vite + TypeScript          │
│              Tailwind CSS                │
└────────────────────┬─────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────┐
│                  NGINX                   │
│              HTTPS / Proxy               │
└────────────────────┬─────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────┐
│                 FASTAPI                  │
│              REST + WebSocket            │
└─────────┬─────────────┬──────────────────┘
          │             │
          ▼             ▼
     PostgreSQL       Redis
          │             │
          │             ▼
          │           Worker
          │             │
          │             ▼
          │          AI Agent
          │             │
          ├─────────────┼─────────────┐
          │             │             │
          ▼             ▼             ▼
       Qdrant         MinIO         LLM
        RAG           Files       Ollama/API
          │
          │
          ▼
    Conhecimento EV

                     ▲
                     │
              Evolution API
                     ▲
                     │
                  WhatsApp
```

## 🚀 Próximo passo

Eu sugiro que **não paremos na arquitetura**. O próximo passo é criar a **versão 0.1 executável**.

Eu montaria primeiro:

```text
ev-sales-ai/
├── docker-compose.yml
├── .env.example
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py
│       ├── core/
│       ├── models/
│       ├── api/
│       └── integrations/
├── frontend/
│   ├── Dockerfile
│   └── src/
└── nginx/
    └── nginx.conf
```

e faria o primeiro fluxo funcionar de ponta a ponta:

**WhatsApp → Evolution API → FastAPI → Redis → Worker → PostgreSQL → resposta no WhatsApp.**

Depois colocamos **Qdrant + MinIO + RAG + agente especialista em vendas**.

Esse caminho é particularmente bom para o seu caso porque você já está trabalhando com **Docker, Ollama, FastAPI e ambientes locais**, então podemos construir o projeto de forma modular e depois levar o mesmo Compose para um servidor.
