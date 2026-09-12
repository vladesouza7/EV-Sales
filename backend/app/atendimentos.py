"""S-08 §4 e §5 — as duas telas do Raí.

> "Se der ruim eu quero abrir a conversa e ler. Do começo ao fim. Não quero explicação de
> engenheiro, quero ler o que foi dito."

A mesma origem que eu leio como trace, ele lê como transcrição (ADR-006). A diferença
inteira está neste arquivo: aqui a linha da `trilha` vira frase em português.

**Sem jargão.** Não aparece "span", "token", "trace_id" nem "latência" — isso está no meu
lado da ferramenta, não no dele. E as linhas ⚙ e ⏸ são o que separa transcrição de
auditoria: elas mostram **onde o número veio de fora do modelo** e **onde um humano
decidiu**. Sem elas o Raí lê a conversa e ainda precisa acreditar em mim.
"""

import re
import uuid
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.autenticacao import exige_perfil
from app.core.dinheiro import formatar
from app.core.pii import hash_telefone
from app.core.validacao import normalizar_telefone
from app.db import FUSO, agora, obter_sessao
from app.modelos import Conversa, Lead, Mensagem, Trilha, Usuario
from app.observabilidade import (
    MICRO_REAIS_POR_REAL,
    TETO_MICRO_REAIS,
    gasto_do_mes,
)

router = APIRouter()
BancoDeDados = Annotated[Session, Depends(obter_sessao)]
DonoOuGerente = Annotated[Usuario, exige_perfil("dono", "gerente")]

LIMITE_DA_BUSCA = 50


def _reais(micro: int) -> str:
    return formatar(round(micro / MICRO_REAIS_POR_REAL * 100))


def _final(chassi: object) -> str:
    return f"…{str(chassi)[-4:]}"


def _da_tool(nome: str, dados: dict[str, object]) -> str | None:
    """A linha ⚙ — onde o número veio de fora do modelo."""
    retorno = dados.get("retorno")
    if nome == "buscar_unidades":
        # Lista crua ou `{"unidades": [...], "aviso": ...}`: o retorno ganhou o aviso de
        # fontes misturadas, e a trilha do Raí não pode passar a dizer "0 carros" por isso.
        if isinstance(retorno, dict):
            retorno = retorno.get("unidades")
        quantidade = len(retorno) if isinstance(retorno, list) else 0
        return f"consultou o estoque → {quantidade} carro(s) disponível(is)"
    if nome == "detalhar_unidade":
        chassi = retorno.get("chassi") if isinstance(retorno, dict) else None
        return f"consultou a ficha do chassi {_final(chassi)}" if chassi else "consultou uma ficha"
    if nome == "comparar_unidades":
        unidades = retorno.get("unidades") if isinstance(retorno, dict) else None
        quantidade = len(unidades) if isinstance(unidades, list) else 0
        return f"comparou {quantidade} carros lado a lado"
    if nome == "registrar_qualificacao":
        return "anotou o que o cliente contou sobre o uso"
    if nome == "transferir_para_humano":
        return "chamou um vendedor"
    return None


def _do_evento(nome: str, dados: dict[str, object]) -> tuple[str, str] | None:
    """A linha ⏸ — onde um humano decidiu. Devolve (símbolo, frase)."""
    if nome == "aprovacao_solicitada":
        preco = dados.get("preco_centavos")
        valor = formatar(int(preco)) if isinstance(preco, int) else "—"
        return "⏸", f"pediu aprovação — {valor} · chassi {_final(dados.get('chassi'))}"
    if nome == "aprovado":
        return "⏸", f"APROVADO por {dados.get('por', '—')} · espelho {dados.get('espelho', '—')}"
    if nome == "recusado":
        return "⏸", f"recusado por {dados.get('por', '—')} — motivo: {dados.get('motivo', '—')}"
    if nome == "reservado":
        return "⚙", f"reservou o chassi {_final(dados.get('chassi'))}, por 72 horas"
    if nome == "reserva_perdida":
        return "⚠", "o carro já tinha sido reservado por outro cliente"
    if nome in ("teto_atingido", "provedor_nao_configurado", "provedor_indisponivel"):
        return "⚠", "o atendimento automático estava indisponível; a conversa foi para a equipe"
    if nome == "transferido_para_humano":
        return "⏸", "a conversa passou para um vendedor"
    if nome == "reserva_renovada":
        return "⚙", f"reserva do chassi {_final(dados.get('chassi'))} renovada por mais 72 horas"
    return None


@router.get("/api/atendimentos")
def listar(sessao: BancoDeDados, _: DonoOuGerente, busca: str = "") -> list[dict[str, object]]:
    """S-08 §4 — busca por nome, telefone (número exato) ou data.

    Telefone é procurado pelo **hash**, sem decifrar nada: é exatamente para isso que o
    `telefone_hash` do ADR-007 existe. Nome exige decifrar, porque nome cifrado não se
    procura — e por isso o nome só é conferido dentro da página que já foi lida, nunca
    varrendo a base inteira.
    """
    consulta = select(Conversa).order_by(Conversa.criada_em.desc()).limit(LIMITE_DA_BUSCA)
    termo = busca.strip()

    if termo:
        telefone = _telefone(termo)
        if telefone:
            consulta = (
                select(Conversa)
                .join(Lead, Lead.id == Conversa.lead_id)
                .where(Lead.telefone_hash == hash_telefone(telefone))
                .order_by(Conversa.criada_em.desc())
            )
        else:
            data = _como_data(termo)
            if data is not None:
                consulta = (
                    select(Conversa)
                    .where(
                        Conversa.criada_em >= data,
                        Conversa.criada_em < data + timedelta(days=1),
                    )
                    .order_by(Conversa.criada_em.desc())
                )

    linhas: list[dict[str, object]] = []
    for conversa in sessao.scalars(consulta):
        lead = sessao.get(Lead, conversa.lead_id)
        nome = lead.nome_mascarado() if lead else "?"
        if termo and not _e_telefone_ou_data(termo) and termo.lower() not in nome.lower():
            continue
        custo = sessao.scalar(
            select(func.coalesce(func.sum(Trilha.custo_micro_reais), 0)).where(
                Trilha.conversa_id == conversa.id
            )
        )
        linhas.append(
            {
                "id": str(conversa.id),
                "cliente": nome,
                "quando": conversa.criada_em.astimezone(FUSO).strftime("%d/%m/%Y %H:%M"),
                "etapa": conversa.etapa,
                "desfecho": conversa.desfecho or "em andamento",
                "custo": _reais(int(custo or 0)),
            }
        )
    return linhas


def _telefone(termo: str) -> str:
    """A validação da S-01 recusa o que não é celular levantando; aqui isso é normal.

    Quem digita "Tarcísio" na busca não cometeu um erro — está buscando por nome.
    """
    try:
        return normalizar_telefone(termo)
    except ValueError:
        return ""


def _como_data(termo: str) -> datetime | None:
    # "05/09" ganha o ano corrente aqui, e não no `strptime`: sem ano, o Python assume
    # 1900 e avisa que vai mudar de comportamento.
    completo = f"{termo}/{agora().year}" if re.fullmatch(r"\d{1,2}/\d{1,2}", termo) else termo
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(completo, formato).replace(tzinfo=FUSO)
        except ValueError:
            continue
    return None


def _e_telefone_ou_data(termo: str) -> bool:
    return bool(_telefone(termo)) or _como_data(termo) is not None


@router.get("/api/atendimentos/{conversa_id}")
def ler(conversa_id: uuid.UUID, sessao: BancoDeDados, _: DonoOuGerente) -> dict[str, object]:
    """A conversa em ordem, com as linhas ⚙ e ⏸ intercaladas no horário em que aconteceram."""
    conversa = sessao.get(Conversa, conversa_id)
    if conversa is None:
        raise HTTPException(404, detail={"mensagem": "Não encontrado."})
    lead = sessao.get(Lead, conversa.lead_id)
    nome = lead.nome_mascarado() if lead else "?"

    linhas: list[dict[str, object]] = []
    for mensagem in sessao.scalars(
        select(Mensagem)
        .where(Mensagem.conversa_id == conversa_id)
        .order_by(Mensagem.criada_em, Mensagem.id)
    ):
        linhas.append(
            {
                "tipo": "fala",
                "hora": mensagem.criada_em.astimezone(FUSO).strftime("%H:%M"),
                "quando": mensagem.criada_em.isoformat(),
                "quem": nome if mensagem.autor == "cliente" else "Aurora",
                "texto": mensagem.conteudo,
            }
        )

    custo = 0
    turnos = 0
    modelo = versao = None
    for passo in sessao.scalars(
        select(Trilha).where(Trilha.conversa_id == conversa_id).order_by(Trilha.criado_em)
    ):
        custo += passo.custo_micro_reais
        if passo.tipo == "turno":
            turnos += 1
            modelo = passo.dados.get("modelo") or modelo
            versao = passo.dados.get("versao_do_prompt") or versao
            continue
        frase = None
        if passo.tipo == "tool":
            texto = _da_tool(passo.nome, passo.dados)
            frase = ("⚙", texto) if texto else None
        elif passo.tipo == "evento":
            frase = _do_evento(passo.nome, passo.dados)
        if frase is None:
            continue
        linhas.append(
            {
                "tipo": "acao",
                "hora": passo.criado_em.astimezone(FUSO).strftime("%H:%M:%S"),
                "quando": passo.criado_em.isoformat(),
                "simbolo": frase[0],
                "texto": frase[1],
            }
        )

    linhas.sort(key=lambda linha: str(linha["quando"]))
    primeira = conversa.criada_em
    ultima = conversa.ultima_mensagem_em or primeira
    return {
        "cliente": nome,
        "etapa": conversa.etapa,
        "desfecho": conversa.desfecho or "em andamento",
        "linhas": linhas,
        # O rodapé da §4. "modelo" e "versão do prompt" ficam porque são o que responde
        # "por que ela respondeu assim naquele dia" — e isso o Raí entende.
        "rodape": {
            "custo": _reais(custo),
            "duracao_min": max(int((ultima - primeira).total_seconds() // 60), 0),
            "turnos": turnos,
            "modelo": modelo or "—",
            "versao_do_prompt": versao or "—",
        },
    }


@router.get("/api/custo")
def custo_do_mes(sessao: BancoDeDados, _: DonoOuGerente) -> dict[str, object]:
    """S-08 §5 — quatro números e o acumulado por dia. **Sem tabela de tokens.**

    Responde direto à fala 6 do Raí: "não quero descobrir dia 30 que gastei quatro mil
    real". Por isso a tela mostra o gasto, o teto e a distância entre os dois — e não a
    contagem de tokens, que não responde pergunta nenhuma dele.
    """
    referencia = agora()
    inicio = referencia.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    gasto = gasto_do_mes(sessao, referencia)

    por_conversa = list(
        sessao.execute(
            select(
                Trilha.conversa_id,
                func.sum(Trilha.custo_micro_reais),
                func.bool_and(Trilha.custo_faturado),
            )
            .where(Trilha.criado_em >= inicio, Trilha.conversa_id.isnot(None))
            .group_by(Trilha.conversa_id)
        )
    )
    custos = [int(total or 0) for _id, total, _faturado in por_conversa]
    conversas = len(custos)

    # Agrupado em Python, no fuso de Fortaleza, e não por `date_trunc` no banco: o
    # Postgres agruparia em UTC, e tudo o que acontece depois das 21h na Paraíba cairia
    # no dia seguinte do gráfico. Dia errado num painel de custo é dia que ninguém
    # reconhece.
    por_dia: dict[str, int] = {}
    for quando, valor in sessao.execute(
        select(Trilha.criado_em, Trilha.custo_micro_reais)
        .where(Trilha.criado_em >= inicio)
        .order_by(Trilha.criado_em)
    ):
        dia = quando.astimezone(FUSO).strftime("%d/%m")
        por_dia[dia] = por_dia.get(dia, 0) + int(valor or 0)

    acumulado = 0
    dias = []
    for dia, total in por_dia.items():
        acumulado += total
        dias.append({"dia": dia, "acumulado": acumulado})

    fracao = gasto / TETO_MICRO_REAIS if TETO_MICRO_REAIS else 0
    # ADR-012: se algum turno do mês não veio faturado, o painel diz isso. Mostrar
    # R$ 0,00 como se fosse verdade é o modo silencioso de o teto parar de proteger.
    nao_faturados = sum(1 for _id, _total, faturado in por_conversa if faturado is False)
    return {
        "mes": referencia.strftime("%m/%Y"),
        "gasto": _reais(gasto),
        "teto": _reais(TETO_MICRO_REAIS),
        "fracao_do_teto": round(fracao, 4),
        "faixa": "verde" if fracao < 0.6 else "ambar" if fracao < 0.8 else "vermelho",
        "conversas": conversas,
        "media_por_conversa": _reais(sum(custos) // conversas) if conversas else _reais(0),
        "maior_conversa": _reais(max(custos)) if custos else _reais(0),
        "acumulado_por_dia": dias,
        "conversas_sem_custo_faturado": nao_faturados,
    }
