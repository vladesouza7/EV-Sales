"""S-06 — handoff para o WhatsApp e continuidade da conversa.

A regra que organiza o arquivo inteiro é do [ADR-005]: **a Sol & Volt nunca manda a
primeira mensagem.** Não existe caminho de código que envie para um número sem mensagem
de entrada registrada — `pode_enviar` é a única porta de saída, e ela consulta o histórico
antes de qualquer coisa. Número comercial banido não volta com pedido de desculpa.

A conversa **não migra de sessão**: `canal` é coluna da mensagem (S-02 §1), então trocar de
canal mantém id, histórico e qualificação. O turno é o mesmo do chat web, com o mesmo
`conversa_id` (ADR-009) — aqui só muda quem entrega a mensagem.
"""

import json
import logging
import re
import secrets
import urllib.parse
import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agenda import confirmar_se_o_cliente_respondeu
from app.configuracao import Chave, valor
from app.conversas import ConversaAberta, _pendente
from app.core.http import Indisponivel, buscar
from app.core.pii import cifrar, decifrar, hash_telefone
from app.core.validacao import normalizar_telefone, primeiro_nome
from app.db import Sessao, agora, obter_sessao
from app.ia.turno import executar_turno
from app.modelos import Conversa, Lead, Mensagem, TokenMigracao, Usuario, Vendedor
from app.observabilidade import registrar, registrar_incidente

logger = logging.getLogger(__name__)
router = APIRouter()
BancoDeDados = Annotated[Session, Depends(obter_sessao)]

# Sem O, 0, I nem 1: o cliente lê o código na tela, e a confusão entre esses quatro é a que
# acontece de verdade.
ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TAMANHO_DO_TOKEN = 6
VALIDADE_DO_TOKEN = timedelta(minutes=30)

JANELA_DE_RESPOSTA = timedelta(hours=24)  # S-06 §6
MAX_CONSECUTIVAS = 2
CONVERSA_ATIVA = timedelta(hours=72)  # S-06 §4.4

LIMITE_DA_BOLHA = 900  # S-06 §7
MAX_BOLHAS = 3

# O código é reconhecido em qualquer posição da mensagem: o cliente pode editar o texto
# pré-preenchido, e edita.
_CODIGO = re.compile(rf"SV-([{ALFABETO}]{{{TAMANHO_DO_TOKEN}}})", re.IGNORECASE)


# ── o convite ────────────────────────────────────────────────────────────────────


def gerar_token(sessao: Session, conversa: Conversa) -> TokenMigracao:
    """S-06 §2. Um por chamada: o cliente que clica duas vezes leva dois links válidos, e
    o uso único do primeiro resolve — recusar o segundo só quebraria quem clicou de novo
    porque a primeira aba fechou."""
    lead = sessao.get(Lead, conversa.lead_id)
    token = TokenMigracao(
        token="".join(secrets.choice(ALFABETO) for _ in range(TAMANHO_DO_TOKEN)),
        conversa_id=conversa.id,
        telefone_hash_esperado=lead.telefone_hash if lead else None,
        expira_em=agora() + VALIDADE_DO_TOKEN,
    )
    sessao.add(token)
    sessao.commit()
    return token


def link_de_migracao(sessao: Session, conversa: Conversa) -> str | None:
    """O `wa.me` da S-06 §3. `None` quando o número da loja ainda não foi configurado.

    O texto vai pré-preenchido com o primeiro nome e o código — **o telefone do cliente não
    entra no link**, porque quem abre o link é ele, do aparelho dele.
    """
    numero = valor(sessao, Chave.whatsapp_numero)
    if not numero:
        return None

    lead = sessao.get(Lead, conversa.lead_id)
    nome = primeiro_nome(decifrar(lead.nome_cifrado)) if lead else "Oi"
    token = gerar_token(sessao, conversa)
    texto = f"Oi, sou o {nome}. Vim do site — código SV-{token.token}"
    return f"https://wa.me/{numero.lstrip('+')}?text={urllib.parse.quote(texto)}"


@router.post("/api/conversas/{conversa_id}/whatsapp")
def oferecer(conversa: ConversaAberta, sessao: BancoDeDados) -> dict[str, object]:
    """O botão da S-06 §1, sempre visível na interface."""
    link = link_de_migracao(sessao, conversa)
    if link is None:
        raise HTTPException(
            503, detail={"mensagem": "O WhatsApp da loja ainda não está configurado."}
        )
    registrar(sessao, conversa.id, "evento", "migracao_oferecida")
    return {"link": link}


# ── formatação de saída (§7) ─────────────────────────────────────────────────────


def formatar(texto: str) -> list[str]:
    """WhatsApp não tem Markdown completo. Converte o que existe e corta o que não.

    Divide em no máximo três bolhas, **sempre em parágrafo**: cortar no meio de uma frase
    entrega ao cliente uma mensagem que termina no meio de um número.
    """
    limpo = re.sub(r"^\s*#{1,6}\s*", "", texto, flags=re.MULTILINE)  # cabeçalho não existe
    limpo = re.sub(r"^\s*\|.*\|\s*$", "", limpo, flags=re.MULTILINE)  # nem tabela
    limpo = re.sub(r"\*\*(.+?)\*\*", r"*\1*", limpo, flags=re.DOTALL)  # negrito é um só
    limpo = re.sub(r"^\s*[-*]\s+", "• ", limpo, flags=re.MULTILINE)  # lista vira bolinha
    limpo = re.sub(r"\n{3,}", "\n\n", limpo).strip()

    if not limpo:
        return []
    if len(limpo) <= LIMITE_DA_BOLHA:
        return [limpo]

    bolhas: list[str] = []
    atual = ""
    for paragrafo in limpo.split("\n\n"):
        junto = f"{atual}\n\n{paragrafo}" if atual else paragrafo
        if atual and len(junto) > LIMITE_DA_BOLHA and len(bolhas) < MAX_BOLHAS - 1:
            bolhas.append(atual)
            atual = paragrafo
        else:
            atual = junto
    bolhas.append(atual)

    # A terceira bolha pode passar de 900, e passa de propósito: cortar perderia o fim da
    # frase, e o fim da frase da Aurora costuma ser a pergunta. Três é o teto de bolhas.
    return [b for b in bolhas if b]


# ── as regras de envio (§6) ──────────────────────────────────────────────────────


def _ultima_do_cliente(sessao: Session, conversa_id: uuid.UUID) -> Mensagem | None:
    return sessao.scalars(
        select(Mensagem)
        .where(
            Mensagem.conversa_id == conversa_id,
            Mensagem.direcao == "entrada",
            Mensagem.canal == "whatsapp",
        )
        .order_by(Mensagem.criada_em.desc(), Mensagem.id)
        .limit(1)
    ).one_or_none()


def pode_enviar(sessao: Session, conversa: Conversa) -> tuple[bool, str]:
    """A **única** porta de saída. Devolve (pode, motivo).

    Três recusas, todas em código e nenhuma no prompt: sem mensagem do cliente, fora da
    janela de 24h, e duas consecutivas sem resposta.
    """
    ultima = _ultima_do_cliente(sessao, conversa.id)
    if ultima is None:
        return False, "sem_mensagem_do_cliente"
    if agora() - ultima.criada_em > JANELA_DE_RESPOSTA:
        return False, "fora_da_janela_de_24h"

    consecutivas = sessao.scalars(
        select(Mensagem).where(
            Mensagem.conversa_id == conversa.id,
            Mensagem.direcao == "saida",
            Mensagem.canal == "whatsapp",
            Mensagem.criada_em > ultima.criada_em,
        )
    ).all()
    if len(consecutivas) >= MAX_CONSECUTIVAS:
        return False, "duas_consecutivas_sem_resposta"
    return True, ""


def enviar(sessao: Session, conversa: Conversa, texto: str) -> bool:
    """Entrega pela Evolution. Passa por `pode_enviar` **antes** de montar qualquer coisa.

    Decifrar o telefone aqui é um dos três pontos autorizados da S-09 §2 — e é por isso que
    ele acontece nesta função e não em quem a chama.
    """
    liberado, motivo = pode_enviar(sessao, conversa)
    if not liberado:
        logger.warning("envio bloqueado: %s conversa=%s", motivo, conversa.id)
        registrar(sessao, conversa.id, "evento", "envio_bloqueado", dados={"motivo": motivo})
        return False

    base = valor(sessao, Chave.evolution_url).rstrip("/")
    instancia = valor(sessao, Chave.evolution_instancia)
    chave = valor(sessao, Chave.evolution_chave)
    lead = sessao.get(Lead, conversa.lead_id)
    if not (base and instancia and chave and lead):
        registrar_incidente(sessao, conversa.id, "evolution_desconectada", {"motivo": "sem_config"})
        return False

    numero = decifrar(lead.telefone_cifrado).lstrip("+")
    for bolha in formatar(texto):
        corpo = json.dumps({"number": numero, "text": bolha}, ensure_ascii=False).encode()
        try:
            status = buscar(
                f"{base}/message/sendText/{instancia}",
                {"apikey": chave, "Content-Type": "application/json"},
                corpo,
            )
        except Indisponivel:
            # ponytail: sem retentativa com backoff (S-06 §8) — ela precisa da fila do
            # worker (S-10 §1). A mensagem fica gravada, e o Raí a lê em /atendimentos.
            registrar_incidente(sessao, conversa.id, "evolution_desconectada", {"etapa": "envio"})
            return False
        if not 200 <= status < 300:
            registrar_incidente(
                sessao, conversa.id, "evolution_desconectada", {"status": status}
            )
            return False
    return True


# ── aviso para a equipe ──────────────────────────────────────────────────────────


def avisar_equipe(sessao: Session, pessoas: Sequence[Usuario | Vendedor], texto: str) -> int:
    """A **segunda** porta de saída, e a única que envia sem mensagem de entrada.

    A regra de nunca enviar primeiro (ADR-005) existe para proteger o número comercial de
    ban por mensagem não solicitada a **cliente**. Funcionário é outra coisa: o telefone da
    Neuza e o do Tarcísio só existem no banco porque o Raí os cadastrou na tela da S-12, o
    que é o consentimento — e sem esta porta a notificação da S-04 §3 não existe.

    O que a mantém honesta é o tipo: ela recebe `Usuario` ou `Vendedor`, nunca `Lead`. Um
    número de cliente não tem como chegar aqui, e há teste disso. Se um dia alguém quiser
    alargar, vai ter que alargar a assinatura — que é uma linha de diff difícil de não ver.
    """
    base = valor(sessao, Chave.evolution_url).rstrip("/")
    instancia = valor(sessao, Chave.evolution_instancia)
    chave = valor(sessao, Chave.evolution_chave)
    if not (base and instancia and chave):
        logger.warning("aviso para a equipe não saiu: WhatsApp da loja não configurado")
        return 0

    enviados = 0
    for pessoa in pessoas:
        if not isinstance(pessoa, Usuario | Vendedor):  # pragma: no cover - guarda de tipo
            raise TypeError("avisar_equipe só envia para funcionário cadastrado")
        if not pessoa.telefone_cifrado:
            continue
        numero = decifrar(pessoa.telefone_cifrado).lstrip("+")
        for bolha in formatar(texto):
            corpo = json.dumps({"number": numero, "text": bolha}, ensure_ascii=False).encode()
            try:
                status = buscar(
                    f"{base}/message/sendText/{instancia}",
                    {"apikey": chave, "Content-Type": "application/json"},
                    corpo,
                )
            except Indisponivel:
                registrar_incidente(sessao, None, "evolution_desconectada", {"etapa": "aviso"})
                return enviados
            if not 200 <= status < 300:
                registrar_incidente(sessao, None, "evolution_desconectada", {"status": status})
                return enviados
        enviados += 1
    return enviados


def quem_recebe(sessao: Session, perfil: str) -> Sequence[Usuario]:
    """Quem tem telefone cadastrado e o perfil pedido. Sem lista de destinatários à parte:
    uma segunda lista dizendo quem é a gerente divergiria de `usuarios` no primeiro mês."""
    return sessao.scalars(
        select(Usuario).where(
            Usuario.perfil == perfil,
            Usuario.ativo,
            Usuario.telefone_cifrado.is_not(None),
        )
    ).all()


# ── recepção (§4) ────────────────────────────────────────────────────────────────


def _e164(jid: str) -> str | None:
    """`5583991575299@s.whatsapp.net` → `+5583991575299`.

    O WhatsApp devolve contas antigas sem o nono dígito. Sem esta correção o
    `telefone_hash` nunca bate com o do cadastro, e todo cliente que migrasse viraria lead
    novo — o oposto de "sem repetir nada".
    """
    digitos = re.sub(r"\D", "", jid.split("@")[0])
    try:
        return normalizar_telefone(digitos)
    except ValueError:
        pass
    if len(digitos) == 12 and digitos.startswith("55"):
        return _e164(f"{digitos[:4]}9{digitos[4:]}")
    return None


def _texto(dados: dict[str, object]) -> str:
    """Payload de terceiro é fronteira de confiança: nada aqui assume formato."""
    mensagem = dados.get("message")
    mensagem = mensagem if isinstance(mensagem, dict) else {}
    direto = mensagem.get("conversation")
    if isinstance(direto, str) and direto.strip():
        return direto.strip()
    estendida = mensagem.get("extendedTextMessage")
    if isinstance(estendida, dict) and isinstance(estendida.get("text"), str):
        return str(estendida["text"]).strip()
    return ""


def _conversa_do_codigo(sessao: Session, texto: str, hash_de_quem_escreveu: str) -> Conversa | None:
    """§4.3 — o código migra a conversa. Uso único, 30 minutos, `UPDATE … WHERE`.

    A marcação usa a mesma forma da reserva (invariante 4): quem garante que o token não é
    resgatado duas vezes é a linha que o `UPDATE` não encontra, não um `SELECT` anterior.
    """
    achado = _CODIGO.search(texto)
    if achado is None:
        return None

    marcado = sessao.execute(
        update(TokenMigracao)
        .where(
            TokenMigracao.token == achado.group(1).upper(),
            TokenMigracao.usado_em.is_(None),
            TokenMigracao.expira_em > agora(),
        )
        .values(usado_em=agora())
        .returning(TokenMigracao.conversa_id)
    ).one_or_none()
    sessao.commit()
    if marcado is None:
        registrar(sessao, None, "evento", "codigo_de_migracao_invalido")
        return None

    conversa = sessao.get(Conversa, marcado[0])
    if conversa is None:
        return None
    conversa.canal_atual = "whatsapp"
    lead = sessao.get(Lead, conversa.lead_id)
    if lead is not None and lead.telefone_hash != hash_de_quem_escreveu:
        # Resgatado de outro número. A conversa migra do mesmo jeito — o cliente pode ter
        # cadastrado um número e escrito de outro —, mas fica registrado.
        registrar(sessao, conversa.id, "evento", "token_resgatado_de_outro_numero")
    sessao.commit()
    return conversa


def _conversa_do_numero(sessao: Session, e164: str, telefone_hash: str) -> Conversa:
    """§4.4 — sem código válido, quem identifica é o `telefone_hash`."""
    lead = sessao.scalars(select(Lead).where(Lead.telefone_hash == telefone_hash)).one_or_none()
    if lead is None:
        # Alguém que achou o número no Instagram. A Aurora atende pela saudação.
        lead = Lead(
            nome_cifrado=cifrar("Cliente"),
            telefone_cifrado=cifrar(e164),
            telefone_hash=telefone_hash,
            origem="whatsapp_direto",
        )
        sessao.add(lead)
        sessao.flush()

    recente = sessao.scalars(
        select(Conversa)
        .where(Conversa.lead_id == lead.id, Conversa.criada_em > agora() - CONVERSA_ATIVA)
        .order_by(Conversa.criada_em.desc())
        .limit(1)
    ).one_or_none()
    if recente is not None:
        recente.canal_atual = "whatsapp"
        sessao.commit()
        return recente

    conversa = Conversa(
        lead_id=lead.id,
        etapa="saudacao",
        canal_atual="whatsapp",
        token_sessao=secrets.token_urlsafe(32),
        token_expira_em=agora() + timedelta(hours=24),
    )
    sessao.add(conversa)
    sessao.commit()
    return conversa


def receber(sessao: Session, payload: dict[str, object]) -> uuid.UUID | None:
    """Grava a mensagem e devolve a conversa a processar. **Não** processa o turno.

    A mensagem do cliente é gravada antes de qualquer tentativa de processar (§8): o que
    não pode acontecer é a Sol & Volt perder o que o cliente escreveu.
    """
    dados = payload.get("data")
    dados = dados if isinstance(dados, dict) else {}
    chave = dados.get("key")
    chave = chave if isinstance(chave, dict) else {}
    if chave.get("fromMe"):
        return None  # eco do que nós mesmos mandamos

    message_id = str(chave.get("id") or "")
    e164 = _e164(str(chave.get("remoteJid") or ""))
    texto = _texto(dados)
    if not (message_id and e164 and texto):
        # Áudio, imagem e documento estão fora do escopo do v1 (S-06, fora do escopo).
        return None

    telefone_hash = hash_telefone(e164)
    conversa = _conversa_do_codigo(sessao, texto, telefone_hash) or _conversa_do_numero(
        sessao, e164, telefone_hash
    )

    entrada = Mensagem(
        conversa_id=conversa.id,
        direcao="entrada",
        autor="cliente",
        canal="whatsapp",
        conteudo=texto,
        whatsapp_message_id=message_id,
        payload_bruto=payload,
    )
    sessao.add(entrada)
    conversa.ultima_mensagem_em = agora()
    # S-07 §6 — o lembrete sai por aqui, e é por aqui que o "sim" volta.
    confirmar_se_o_cliente_respondeu(sessao, conversa, texto)
    try:
        sessao.commit()
    except IntegrityError:
        # §4.1 — a Evolution reentrega. O índice único recusou a segunda, e é só isso que
        # precisa acontecer: um turno, uma resposta.
        sessao.rollback()
        return None
    return conversa.id


async def responder(conversa_id: uuid.UUID) -> None:
    """O "worker" da §4.6 — a mesma função de turno do chat web.

    ponytail: roda em `BackgroundTasks`, depois do 200. Vira consumidor de fila quando o
    `worker` da S-10 §1 existir; o que a spec exige é que o webhook não processe, e não
    processa.
    """
    with Sessao() as sessao:
        conversa = sessao.get(Conversa, conversa_id)
        pendente = _pendente(sessao, conversa_id)
        if conversa is None or pendente is None or conversa.modo != "aurora":
            return
        saidas: list[uuid.UUID] = []
        async for nome, dados in executar_turno(sessao, conversa, pendente):
            if nome == "mensagem_fim":
                saidas.append(uuid.UUID(str(dados["mensagem_id"])))
        for saida_id in saidas:
            saida = sessao.get(Mensagem, saida_id)
            # O turno já grava a saída com `canal = conversa.canal_atual`, que aqui é
            # `whatsapp` — não há segundo lugar decidindo canal.
            if saida is not None:
                enviar(sessao, conversa, saida.conteudo)


@router.post("/api/webhooks/evolution")
async def webhook(
    payload: dict[str, object],
    tarefas: BackgroundTasks,
    sessao: BancoDeDados,
    apikey: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """§4 — autentica, grava e responde. O turno roda depois da resposta.

    Webhook que processa é webhook que estoura o timeout e faz a Evolution reenviar — e
    reenvio é turno duplicado se a deduplicação falhar.
    """
    esperada = valor(sessao, Chave.evolution_chave)
    if not esperada or not secrets.compare_digest(apikey or "", esperada):
        raise HTTPException(401, detail={"mensagem": "Não autorizado."})

    conversa_id = receber(sessao, payload)
    if conversa_id is not None:
        tarefas.add_task(responder, conversa_id)
    return {"resultado": "recebido"}
