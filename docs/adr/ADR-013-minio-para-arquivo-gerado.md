# ADR-013 — MinIO volta, para arquivo gerado e para foto que alguém sobe

**Status:** aceito
**Data:** 2026-09-06
**Reverte:** o corte registrado em [ARQUITETURA.md](../ARQUITETURA.md#o-que-foi-cortado-do-desenho-original-e-por-quê) e [S-10 §1](../spec/S-10-operacao.md)
**Toca em:** [ADR-007](ADR-007-pii-cifrada-e-mascarada.md), [ADR-011](ADR-011-jornada-digital-termina-no-test-drive.md)

---

## Contexto

O MinIO estava no rascunho inicial, foi cortado, e a justificativa do corte está escrita na
[S-10 §1](../spec/S-10-operacao.md):

> "Fotos de veículo ficam em volume servido pelo nginx. São ~20 arquivos; object store é
> overkill declarado."

**A justificativa é válida para o problema que ela descreve, e o problema mudou.** Ela supõe
arquivo **estático, pré-carregado no volume junto com o deploy**. O que o projeto precisa é de
duas coisas que não são isso:

1. **Foto que alguém sobe.** O Tarcísio fotografa o seminovo que entrou no pátio hoje e precisa
   que aquilo apareça no catálogo. Escrever no filesystem do container faz o arquivo sumir no
   próximo `docker compose up --build`, e "não perca o container" não é instrução operacional
   que se dê a uma concessionária.
2. **Arquivo gerado em runtime.** O **Espelho de Condição e Reserva** ([S-04](../spec/S-04-fila-de-aprovacao.md))
   tem `pdf_url` no esquema. É o documento que a Neuza aprova e que o cliente recebe. Ele nasce
   depois do deploy, por definição.

E há a evidência de que o desenho atual não fecha: o seed grava sete `foto_url` apontando para
`/static/fotos/`, **e esse diretório não existe no repositório**. Todo o catálogo cai no
`sem-foto.svg`. Não existe hoje nenhum caminho pelo qual uma foto entre no sistema.

## Decisão

**MinIO volta como o único serviço de arquivo do EV-Sales. Um bucket, privado, com dois prefixos
e duas formas de acesso — porque os dois conteúdos têm risco diferente.**

| Prefixo | Conteúdo | Tem PII | Como é servido |
|---|---|---|---|
| `fotos/` | Foto de unidade, por chassi | não | Proxy do próprio app, em `GET /fotos/{objeto}` |
| `documentos/` | Espelho de Condição e Reserva (PDF) | **sim** | URL assinada, com expiração, emitida só por fluxo autenticado |

### O bucket é privado, e é o PDF que decide isso

Foto de carro é informação pública — está no catálogo, que é público. O **Espelho não é**: ele
carrega o nome do cliente e o chassi reservado para ele. Uma URL pública de PDF entrega PII a
quem tiver o link, **por fora de todas as proteções que já existem** — a cifragem do
[ADR-007](ADR-007-pii-cifrada-e-mascarada.md) não alcança um arquivo servido por HTTP aberto, e o
mascaramento também não.

Por isso o bucket inteiro é privado e o acesso é decidido por prefixo, não por objeto. Política
por objeto é política que alguém esquece de aplicar no objeto seguinte.

### O proxy do app é o que mantém o MinIO fora da internet

`GET /fotos/{objeto}` lê do MinIO e devolve os bytes. Custa uma passagem a mais para ~20 arquivos
numa loja, e em troca não há política de bucket público para configurar errado, nem URL assinada
expirando dentro de uma página em cache.

Em desenvolvimento a porta do MinIO é publicada, pelo mesmo motivo que a do Postgres já é: o
`subir-dev.sh` roda o uvicorn fora do compose. No perfil `prod` os dois ficam atrás do nginx.
**Não é a porta fechada que protege o Espelho** — é o bucket privado e a validação de prefixo
abaixo. Segurança que depende de topologia de rede é segurança que some quando alguém muda a
topologia.

**A rota serve exclusivamente o prefixo `fotos/`, e o nome do objeto é validado antes de virar
caminho.** Sem isso, `GET /fotos/../documentos/espelho-0042.pdf` seria uma rota pública para o
documento com o nome do cliente. É a única linha realmente perigosa deste ADR, e ela tem teste.

### Retenção

O Espelho passa a ser um lugar onde PII mora. Apagar o lead pela retenção da
[S-09 §6](../spec/S-09-protecao-de-pii.md) tem de apagar o objeto também — PII que sobrevive à
exclusão no lugar que ninguém olha é o pior resultado possível de uma rotina de retenção.

O Espelho **continua não sendo contrato nem documento fiscal** ([ADR-011](ADR-011-jornada-digital-termina-no-test-drive.md)),
e o PDF diz isso na própria face.

## Alternativas consideradas

### Manter o volume Docker e só criar o diretório que falta — **descartada**

É a opção de menor diff, e resolve metade: a foto pré-carregada funciona. Não resolve upload — o
arquivo escrito pelo processo morre no rebuild — e não resolve a expiração de link assinado para
o PDF, porque volume não tem esse conceito. Ficaríamos com o Espelho servido por rota estática,
que é exatamente o vazamento descrito acima.

### Guardar os arquivos no Postgres, como `bytea` — **descartada**

Tem um argumento forte e honesto: um backup só, uma transação só, e a exclusão do lead apagaria o
PDF junto **por foreign key**, de graça. Perdeu porque coloca o binário no caminho de todo dump e
de toda restauração do banco que é a fonte da verdade do estoque ([ADR-001](ADR-001-postgres-fonte-da-verdade.md)):
restaurar preço de carro passaria a depender de mover fotos. Fica registrada como a alternativa
mais difícil de recusar.

### Um bucket público para foto e um privado para documento — **descartada**

Mais próximo do "certo por livro". Recusada porque cria **duas** políticas para manter em vez de
uma, e a única diferença prática seria evitar o proxy de ~20 imagens. Dois lugares com regras
diferentes de acesso é onde alguém, um dia, sobe o Espelho no bucket errado.

## Consequências

**Aceitas:**

- Mais um container com estado, e mais uma linha no backup da [S-10 §5](../spec/S-10-operacao.md).
  É o segundo lugar do sistema que guarda dado que não pode ser recriado.
- Uma dependência nova (`minio`). Assinar requisição S3 na mão seria criptografia escrita por
  mim no caminho de um documento com PII — o tipo de código que não se escreve para economizar
  uma dependência.
- Toda foto passa pelo app. Para ~20 arquivos é irrelevante; se um dia deixar de ser, a saída é
  `nginx` na frente do bucket, não bucket público.
- **Volta uma caixa que eu tinha cortado.** O corte estava certo para o problema descrito e
  errado para o problema real, e essa distinção fica registrada aqui em vez de virar uma edição
  silenciosa na S-10.

**Ganhas:**

- Existe, pela primeira vez, um caminho para uma foto entrar no sistema.
- A [S-04](../spec/S-04-fila-de-aprovacao.md) encontra o armazenamento do Espelho pronto quando
  chegar, em vez de inventar um no meio da entrega mais sensível do projeto.
- O PDF com nome de cliente nunca fica atrás de uma URL adivinhável.
