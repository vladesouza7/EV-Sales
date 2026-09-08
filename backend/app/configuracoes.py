"""S-12 — a tela de configurações. Só o `dono` alcança.

Três coisas que este arquivo existe para garantir, e que valem mais que o formulário:

1. **Nenhuma rota devolve segredo em claro.** Nem para o `dono`, nem com confirmação de
   senha, nem "só desta vez". Ler o segredo de volta por uma tela autenticada
   transformaria o JWT de 20 minutos na chave da conta do provedor.
2. **Recusa e indisponibilidade não são a mesma coisa.** `4xx` não grava; `5xx` e rede fora
   gravam com aviso. Tratar queda como recusa trancaria a tela justamente na hora em que
   alguém precisa trocar de provedor porque o atual caiu.
3. **A sonda busca uma URL que uma pessoa digitou.** Sem seguir redirecionamento, sem
   devolver o corpo, com tempo limite. É a superfície nova mais desconfortável do ADR-014.
"""

import json
import re
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.arquivos import caminho_da_foto, garantir_bucket, guardar
from app.autenticacao import Dono
from app.configuracao import (
    SIGILOSAS,
    VARIAVEL,
    Chave,
    ambiente,
    divergente,
    fonte,
    gravar,
    limpar,
    valor,
)
from app.core.http import Indisponivel, buscar
from app.core.pii import cifrar, decifrar, mascarar_telefone
from app.core.validacao import normalizar_telefone
from app.db import FUSO, obter_sessao
from app.ia.provedor import PRESETS, ProvedorCompativel
from app.modelos import Configuracao, Unidade, Usuario, Vendedor

router = APIRouter()
BancoDeDados = Annotated[Session, Depends(obter_sessao)]

_INSTANCIA = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# S-12 · foto de unidade pela tela, mesma regra do scripts/subir-fotos.py (ADR-013): o
# nome do objeto no MinIO é sempre o chassi, nunca o nome que o navegador mandou.
_TIPOS_DE_FOTO_ACEITOS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
TAMANHO_MAXIMO_DA_FOTO = 5 * 1024 * 1024


class Entrada(BaseModel):
    valores: dict[str, str] = {}
    limpar: list[str] = []


class Telefones(BaseModel):
    usuarios: dict[uuid.UUID, str] = {}
    vendedores: dict[uuid.UUID, str] = {}


@dataclass(frozen=True)
class Veredito:
    grava: bool
    aviso: str = ""
    recusa: str = ""


# ── a sonda ──────────────────────────────────────────────────────────────────────




def _classificar(status: int, o_que: str) -> Veredito:
    if 200 <= status < 300:
        return Veredito(grava=True)
    if 500 <= status < 600:
        return Veredito(grava=True, aviso=f"{o_que} não respondeu agora ({status}). Valor gravado.")
    return Veredito(grava=False, recusa=f"{o_que} recusou ({status}).")


def _sondar_provedor(sessao: Session, novos: dict[Chave, str]) -> Veredito:
    provedor = ProvedorCompativel(_efetivo(sessao, novos))
    if not provedor.configurado():
        # Não é recusa: é alguém no meio do cadastro, colando a chave antes do modelo.
        # Barrar aqui obrigaria a preencher tudo numa tacada só para salvar qualquer coisa.
        return Veredito(
            grava=True, aviso="Ainda falta provedor, modelo ou chave para a Aurora responder."
        )
    corpo = json.dumps(
        {"model": provedor.modelo, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1}
    ).encode()
    try:
        return _classificar(buscar(provedor.url, provedor.cabecalhos(), corpo), "O provedor")
    except Indisponivel:
        return Veredito(grava=True, aviso="Não deu para falar com o provedor agora. Valor gravado.")


def _sondar_evolution(sessao: Session, novos: dict[Chave, str]) -> Veredito:
    efetivo = {chave: novos.get(chave, valor(sessao, chave)) for chave in Chave}
    base = efetivo[Chave.evolution_url].rstrip("/")
    instancia = efetivo[Chave.evolution_instancia]
    chave = efetivo[Chave.evolution_chave]
    if not (base and instancia and chave):
        return Veredito(grava=False, recusa="Faltou URL, instância ou chave da Evolution.")

    # `fetchInstances`, e **não** `connectionState/{instancia}`. Conferido contra a imagem
    # v2.3.7: com a instância ainda inexistente, o `connectionState` devolve 404 tanto para
    # a chave certa quanto para a errada — ele não serve para validar credencial, e usá-lo
    # aqui impediria de salvar a configuração antes de a instância existir, que é
    # exatamente a ordem em que a loja vai fazer isso.
    #
    # `fetchInstances` separa os dois casos: 200 com a chave certa, 401 com a errada.
    url = f"{base}/instance/fetchInstances"
    try:
        return _classificar(buscar(url, {"apikey": chave}), "A Evolution")
    except Indisponivel:
        return Veredito(
            grava=True, aviso="Não deu para falar com a Evolution agora. Valor gravado."
        )


def _efetivo(sessao: Session, novos: dict[Chave, str]) -> dict[str, str]:
    """O ambiente que valeria se estes valores fossem gravados."""
    return {**ambiente(sessao), **{VARIAVEL[chave]: texto for chave, texto in novos.items()}}


def _sondar(sessao: Session, novos: dict[Chave, str]) -> Veredito:
    """Só sonda o grupo que a tela mandou: salvar o WhatsApp não depende da chave da LLM."""
    vereditos = []
    if any(chave.startswith("llm_") for chave in novos):
        vereditos.append(_sondar_provedor(sessao, novos))
    if any(chave.startswith("evolution_") for chave in novos):
        vereditos.append(_sondar_evolution(sessao, novos))

    recusado = next((v for v in vereditos if not v.grava), None)
    if recusado is not None:
        return recusado
    aviso = " ".join(v.aviso for v in vereditos if v.aviso)
    return Veredito(grava=True, aviso=aviso)


# ── validação ────────────────────────────────────────────────────────────────────


def _validar(chave: Chave, texto: str) -> str:
    if chave is Chave.whatsapp_numero:
        # A mesma regra do cliente (S-01 §3): a instância recebe a primeira mensagem dele.
        return normalizar_telefone(texto)
    if chave in (Chave.evolution_url, Chave.llm_url):
        if not texto.startswith(("http://", "https://")):
            raise ValueError("A URL precisa começar com http:// ou https://.")
    if chave is Chave.evolution_instancia and not _INSTANCIA.match(texto):
        raise ValueError("Nome de instância: minúsculas, números e hífen.")
    if chave is Chave.llm_provedor and texto not in PRESETS:
        raise ValueError(f"Provedor desconhecido. Os que existem: {', '.join(PRESETS)}.")
    if chave is Chave.llm_fallbacks and len([m for m in texto.split(",") if m.strip()]) > 2:
        raise ValueError("No máximo dois fallbacks, na ordem de tentativa.")
    return texto.strip()


def _lidas(entrada: Entrada) -> tuple[dict[Chave, str], list[Chave]]:
    """Campo ausente ou vazio significa **não mudou**. Apagar é explícito."""
    desconhecidas = (set(entrada.valores) | set(entrada.limpar)) - set(Chave.__members__)
    if desconhecidas:
        primeira = sorted(desconhecidas)[0]
        raise HTTPException(422, detail={"mensagem": f"Chave inexistente: {primeira}"})

    novos: dict[Chave, str] = {}
    for nome, texto in entrada.valores.items():
        if not texto.strip():
            continue
        try:
            novos[Chave(nome)] = _validar(Chave(nome), texto.strip())
        except ValueError as erro:
            raise HTTPException(422, detail={"mensagem": str(erro)}) from None
    return novos, [Chave(nome) for nome in entrada.limpar]


# ── rotas ────────────────────────────────────────────────────────────────────────


def _resumo(sessao: Session, chave: Chave) -> dict[str, object]:
    texto = valor(sessao, chave)
    linha = sessao.get(Configuracao, chave.value)
    sigilosa = chave in SIGILOSAS
    return {
        "chave": chave.value,
        "resumo": (f"••••{texto[-4:]}" if texto else "") if sigilosa else texto,
        "sigilosa": sigilosa,
        "fonte": fonte(sessao, chave),
        "divergente": divergente(sessao, chave),
        "atualizado_em": linha.atualizado_em.astimezone(FUSO).isoformat() if linha else None,
    }


def _telefone_mascarado(cifrado: bytes | None) -> str:
    """Mascarado sempre. Esta tela nunca mostra telefone completo, nem para o `dono`."""
    if not cifrado:
        return ""
    try:
        return mascarar_telefone(decifrar(cifrado))
    except Exception:
        return "?"


@router.get("/api/configuracoes")
def ler_tudo(sessao: BancoDeDados, _: Dono) -> dict[str, object]:
    usuarios = sessao.scalars(
        select(Usuario).where(Usuario.perfil.in_(("dono", "gerente")), Usuario.ativo)
    ).all()
    vendedores = sessao.scalars(select(Vendedor).where(Vendedor.ativo)).all()
    return {
        "chaves": [_resumo(sessao, chave) for chave in Chave],
        "provedores": sorted(PRESETS),
        "telefones": [
            {"id": str(u.id), "nome": u.nome, "papel": u.perfil,
             "telefone": _telefone_mascarado(u.telefone_cifrado)}
            for u in usuarios
        ] + [
            {"id": str(v.id), "nome": v.nome, "papel": "vendedor",
             "telefone": _telefone_mascarado(v.telefone_cifrado)}
            for v in vendedores
        ],
    }  # fmt: skip


@router.post("/api/configuracoes/testar")
def testar(entrada: Entrada, sessao: BancoDeDados, _: Dono) -> dict[str, object]:
    novos, _apagar = _lidas(entrada)
    veredito = _sondar(sessao, novos)
    return {"ok": veredito.grava and not veredito.aviso, "aviso": veredito.aviso,
            "recusa": veredito.recusa}  # fmt: skip


@router.put("/api/configuracoes")
def escrever(entrada: Entrada, sessao: BancoDeDados, usuario: Dono) -> dict[str, object]:
    novos, apagar = _lidas(entrada)

    veredito = _sondar(sessao, novos)
    if not veredito.grava:
        raise HTTPException(422, detail={"mensagem": veredito.recusa})

    for chave, texto in novos.items():
        gravar(sessao, chave, texto, usuario)
    for chave in apagar:
        limpar(sessao, chave, usuario)
    return {"aviso": veredito.aviso}


@router.put("/api/configuracoes/telefones")
def escrever_telefones(entrada: Telefones, sessao: BancoDeDados, usuario: Dono) -> dict[str, str]:
    """S-12 §3 — telefone de pessoa continua morando com a pessoa.

    A validação é a mesma do cliente, e a gravação é cifrada: aqui não há caminho que
    guarde número em claro, nem para funcionário.
    """
    alvos: list[tuple[Usuario | Vendedor, str]] = []
    for usuario_id, bruto in entrada.usuarios.items():
        pessoa = sessao.get(Usuario, usuario_id)
        if pessoa is None:
            raise HTTPException(404, detail={"mensagem": "Não encontrado."})
        alvos.append((pessoa, bruto))
    for vendedor_id, bruto in entrada.vendedores.items():
        vendedor = sessao.get(Vendedor, vendedor_id)
        if vendedor is None:
            raise HTTPException(404, detail={"mensagem": "Não encontrado."})
        alvos.append((vendedor, bruto))

    # Valida tudo antes de gravar qualquer coisa: metade dos telefones trocados é pior do
    # que nenhum, porque ninguém sabe qual metade.
    prontos: list[tuple[Usuario | Vendedor, str]] = []
    for quem, bruto in alvos:
        try:
            prontos.append((quem, normalizar_telefone(bruto)))
        except ValueError as erro:
            raise HTTPException(422, detail={"mensagem": str(erro)}) from None

    for quem, numero in prontos:
        quem.telefone_cifrado = cifrar(numero)
    sessao.commit()

    from app.observabilidade import registrar

    registrar(
        sessao,
        None,
        "evento",
        "telefone_de_aviso_alterado",
        dados={"usuario_id": str(usuario.id), "quantos": len(prontos)},
    )
    return {"resultado": "gravado"}


@router.get("/api/configuracoes/unidades")
def listar_unidades_para_foto(sessao: BancoDeDados, _: Dono) -> list[dict[str, object]]:
    """Todas as unidades, não só as `disponivel` do catálogo público: foto de um carro
    reservado ou vendido continua precisando de manutenção."""
    unidades = sessao.scalars(select(Unidade).order_by(Unidade.marca, Unidade.modelo)).all()
    return [
        {
            "chassi": u.chassi,
            "marca": u.marca,
            "modelo": u.modelo,
            "cor": u.cor,
            "condicao": u.condicao,
            "foto_url": u.foto_url,
        }
        for u in unidades
    ]


@router.post("/api/configuracoes/unidades/{chassi}/foto")
async def subir_foto_da_unidade(
    chassi: str, requisicao: Request, sessao: BancoDeDados, usuario: Dono
) -> dict[str, str]:
    """A tela do `scripts/subir-fotos.py` (ADR-013). Corpo é o arquivo puro, sem
    `multipart/form-data` — evita depender de mais uma lib para um POST de bytes."""
    unidade = sessao.get(Unidade, chassi)
    if unidade is None:
        raise HTTPException(404, detail={"mensagem": "Chassi não encontrado."})

    tipo = requisicao.headers.get("content-type", "")
    extensao = _TIPOS_DE_FOTO_ACEITOS.get(tipo)
    if extensao is None:
        raise HTTPException(422, detail={"mensagem": "Envie jpg, png ou webp."})

    declarado = requisicao.headers.get("content-length")
    if declarado and int(declarado) > TAMANHO_MAXIMO_DA_FOTO:
        raise HTTPException(422, detail={"mensagem": "Foto maior que 5 MB."})
    conteudo = await requisicao.body()
    if not conteudo:
        raise HTTPException(422, detail={"mensagem": "Arquivo vazio."})
    if len(conteudo) > TAMANHO_MAXIMO_DA_FOTO:
        raise HTTPException(422, detail={"mensagem": "Foto maior que 5 MB."})

    garantir_bucket()
    nome = f"{chassi}{extensao}"
    try:
        guardar(caminho_da_foto(nome), conteudo, tipo)
    except RuntimeError:
        raise HTTPException(503, detail={"mensagem": "Armazenamento fora do ar."}) from None

    unidade.foto_url = f"/fotos/{nome}"
    sessao.commit()

    from app.observabilidade import registrar

    registrar(
        sessao,
        None,
        "evento",
        "foto_de_unidade_trocada",
        dados={"usuario_id": str(usuario.id), "chassi": chassi},
    )
    return {"foto_url": unidade.foto_url}
