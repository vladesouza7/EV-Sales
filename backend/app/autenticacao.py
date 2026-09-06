"""S-11 — login, sessão em JWT e o que cada perfil alcança.

A sessão é um JWT de 20 minutos em cookie `HttpOnly`. Não há tabela de sessões: com esse
prazo, **a expiração já é a revogação**. O que sobra — "o Raí precisa derrubar agora" — é
uma coluna, `usuarios.sessoes_validas_apos`, comparada com o `iat` do token.

Duas coisas que a assinatura do token **não** garante e que este arquivo garante:

1. **Toda requisição autenticada lê a linha do usuário.** É o que faz `ativo = false`,
   mudança de perfil e o carimbo de revogação valerem na hora. Sem essa leitura o desenho
   fica stateless de verdade, ao preço de o Raí não conseguir desligar ninguém.
2. **A claim `inicio` é copiada pela renovação.** Sem isso, renovar reiniciaria o relógio
   e o teto de 12 horas nunca chegaria — renovação sem teto é sessão eterna.
"""

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import agora, obter_sessao
from app.limite import dentro_do_limite
from app.modelos import Usuario

logger = logging.getLogger(__name__)

COOKIE = "ev_staff"
ALGORITMO = "HS256"

VALIDADE = timedelta(minutes=20)
RENOVAR_FALTANDO = timedelta(minutes=10)
LIMITE_ABSOLUTO = timedelta(hours=12)

TENTATIVAS_MAXIMAS = 5
BLOQUEIO = timedelta(minutes=15)
LIMITE_POR_IP = 20
SENHA_MINIMA = 12

# Resposta única para todas as falhas de login. Distinguir "e-mail não existe" de "senha
# errada" de "conta bloqueada" conta a quem está adivinhando qual das três acertar.
RECUSA = {"mensagem": "E-mail ou senha incorretos."}

_hasher = PasswordHasher()
# Hash descartável, conferido quando o e-mail não existe: sem isso o tempo da resposta
# revela quais contas existem, e a mensagem única não teria valor nenhum.
_HASH_FANTASMA = _hasher.hash("nao-existe-mas-custa-o-mesmo-tempo")

router = APIRouter()
BancoDeDados = Annotated[Session, Depends(obter_sessao)]


def _segredo() -> str:
    segredo = os.environ.get("EVSALES_JWT_SECRET", "")
    if not segredo:
        raise RuntimeError("EVSALES_JWT_SECRET não está no ambiente.")
    return segredo


def cifrar_senha(senha: str) -> str:
    return _hasher.hash(senha)


def conferir_senha(hash_guardado: str, senha: str) -> bool:
    try:
        return _hasher.verify(hash_guardado, senha)
    except VerifyMismatchError:
        return False
    except Exception:
        # Hash corrompido é recusa, não erro 500 na tela de login.
        return False


def emitir(
    usuario: Usuario, emitido_em: datetime | None = None, inicio: datetime | None = None
) -> str:
    agora_ = emitido_em or agora()
    comeco = inicio or agora_
    return jwt.encode(
        {
            "sub": str(usuario.id),
            "perfil": usuario.perfil,
            "iat": int(agora_.timestamp()),
            "exp": int((agora_ + VALIDADE).timestamp()),
            "inicio": int(comeco.timestamp()),
        },
        _segredo(),
        algorithm=ALGORITMO,
    )


def _gravar_cookie(resposta: Response, token: str) -> None:
    resposta.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        # TLS é do nginx do perfil prod; em dev o cookie precisa viajar em http.
        secure=os.environ.get("EVSALES_COOKIE_SEGURO", "").lower() == "true",
        max_age=int(VALIDADE.total_seconds()),
    )


def criar_usuario(
    sessao: Session,
    *,
    nome: str,
    email: str,
    senha: str,
    perfil: str,
    vendedor_id: uuid.UUID | None = None,
) -> Usuario:
    usuario = Usuario(
        nome=nome,
        email=email.strip().lower(),
        senha_hash=cifrar_senha(senha),
        perfil=perfil,
        vendedor_id=vendedor_id,
    )
    sessao.add(usuario)
    sessao.commit()
    return usuario


def _ler_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, _segredo(), algorithms=[ALGORITMO])
    except jwt.PyJWTError:
        # Assinatura inválida, token vencido ou payload adulterado saem por aqui, e
        # nenhum deles chega a consultar o banco.
        return None


def usuario_da_sessao(
    requisicao: Request, resposta: Response, sessao: BancoDeDados
) -> Usuario:
    """A dependência que protege toda rota interna. Recusa é 401, sempre igual."""
    token = requisicao.cookies.get(COOKIE)
    payload = _ler_token(token) if token else None
    if payload is None:
        raise HTTPException(401, detail={"mensagem": "Faça login para continuar."})

    inicio = datetime.fromtimestamp(payload["inicio"], tz=agora().tzinfo)
    if agora() - inicio > LIMITE_ABSOLUTO:
        # Renovação sem teto é sessão eterna: um aparelho usado a cada dezenove minutos
        # receberia token novo para sempre.
        raise HTTPException(401, detail={"mensagem": "Sua sessão expirou."})

    usuario = sessao.get(Usuario, uuid.UUID(payload["sub"]))
    if usuario is None or not usuario.ativo:
        raise HTTPException(401, detail={"mensagem": "Faça login para continuar."})

    emitido_em = datetime.fromtimestamp(payload["iat"], tz=agora().tzinfo)
    if usuario.sessoes_validas_apos and emitido_em < usuario.sessoes_validas_apos:
        raise HTTPException(401, detail={"mensagem": "Sua sessão expirou."})

    expira_em = datetime.fromtimestamp(payload["exp"], tz=agora().tzinfo)
    if expira_em - agora() < RENOVAR_FALTANDO:
        # `inicio` vai adiante sem ser recalculado — é o que sustenta as 12 horas.
        _gravar_cookie(resposta, emitir(usuario, inicio=inicio))
    return usuario


Autenticado = Annotated[Usuario, Depends(usuario_da_sessao)]


def exige_perfil(*perfis: str) -> Any:
    """404 e não 403: responder "existe, mas não é seu" já é dizer que existe.

    É a mesma regra que a S-02 usa para conversa de outro cliente.
    """

    def conferir(usuario: Autenticado) -> Usuario:
        if usuario.perfil not in perfis:
            raise HTTPException(404, detail={"mensagem": "Não encontrado."})
        return usuario

    return Depends(conferir)


# Os perfis viram tipo, como `BancoDeDados` e `Autenticado`: a rota declara quem alcança
# na assinatura, e não numa checagem no corpo que alguém esquece de escrever.
Dono = Annotated[Usuario, exige_perfil("dono")]


class Credenciais(BaseModel):
    email: str
    senha: str


class TrocaDeSenha(BaseModel):
    atual: str
    nova: str


@router.post("/api/entrar")
def entrar(
    credenciais: Credenciais, requisicao: Request, resposta: Response, sessao: BancoDeDados
) -> dict[str, str]:
    ip = requisicao.client.host if requisicao.client else "desconhecido"
    if not dentro_do_limite(f"entrar:{ip}", maximo=LIMITE_POR_IP, janela_segundos=60):
        raise HTTPException(429, detail={"mensagem": "Muitas tentativas. Espere um minuto."})

    usuario = sessao.scalars(
        select(Usuario).where(Usuario.email == credenciais.email.strip().lower())
    ).one_or_none()

    # Confere um hash mesmo sem usuário: o tempo da resposta não pode contar quem existe.
    hash_guardado = usuario.senha_hash if usuario else _HASH_FANTASMA
    senha_confere = conferir_senha(hash_guardado, credenciais.senha)

    bloqueado = bool(usuario and usuario.bloqueado_ate and usuario.bloqueado_ate > agora())
    if usuario is None or not senha_confere or bloqueado or not usuario.ativo:
        if usuario is not None and not bloqueado:
            usuario.tentativas_falhas += 1
            if usuario.tentativas_falhas >= TENTATIVAS_MAXIMAS:
                usuario.bloqueado_ate = agora() + BLOQUEIO
            sessao.commit()
        logger.warning("login recusado de %s", ip)
        raise HTTPException(401, detail=RECUSA)

    usuario.tentativas_falhas = 0
    usuario.bloqueado_ate = None
    sessao.commit()
    _gravar_cookie(resposta, emitir(usuario))
    return {"nome": usuario.nome, "perfil": usuario.perfil}


@router.post("/api/sair")
def sair(resposta: Response) -> dict[str, str]:
    resposta.delete_cookie(COOKIE)
    return {"mensagem": "Até logo!"}


@router.get("/api/eu")
def eu(usuario: Autenticado) -> dict[str, str]:
    return {"nome": usuario.nome, "perfil": usuario.perfil}


@router.post("/api/minha-senha")
def trocar_a_propria_senha(
    troca: TrocaDeSenha, usuario: Autenticado, resposta: Response, sessao: BancoDeDados
) -> dict[str, str]:
    if not conferir_senha(usuario.senha_hash, troca.atual):
        raise HTTPException(401, detail=RECUSA)
    if len(troca.nova) < SENHA_MINIMA:
        raise HTTPException(
            400, detail={"mensagem": f"A senha precisa de pelo menos {SENHA_MINIMA} caracteres."}
        )

    agora_ = agora()
    usuario.senha_hash = cifrar_senha(troca.nova)
    usuario.senha_trocada_em = agora_
    # Derruba tudo, inclusive a sessão que fez a troca: é o que torna "perdi o celular"
    # resolvível pelo próprio dono da conta, sem esperar o Raí.
    usuario.sessoes_validas_apos = agora_
    sessao.commit()
    resposta.delete_cookie(COOKIE)
    return {"mensagem": "Senha trocada. Entre de novo."}


# ── S-11 §8 — a tela do Raí, e só dele ───────────────────────────────────────────


@router.get("/api/usuarios")
def listar_usuarios(sessao: BancoDeDados, _: Dono) -> list[dict[str, object]]:
    usuarios = sessao.scalars(select(Usuario).order_by(Usuario.nome))
    return [
        {
            "id": str(u.id),
            "nome": u.nome,
            "perfil": u.perfil,
            "ativo": u.ativo,
            "bloqueado": bool(u.bloqueado_ate and u.bloqueado_ate > agora()),
        }
        for u in usuarios
    ]


@router.post("/api/usuarios/{usuario_id}/revogar")
def revogar_sessoes(
    usuario_id: uuid.UUID, sessao: BancoDeDados, _: Dono
) -> dict[str, str]:
    """O celular que ficou no balcão do café. Derruba tudo daquela pessoa, na hora.

    Por usuário e não por dispositivo: separar os dois exigiria guardar cada `jti`, que é
    ter a tabela de sessões de volta (S-11 §2, consequência aceita).
    """
    alvo = sessao.get(Usuario, usuario_id)
    if alvo is None:
        raise HTTPException(404, detail={"mensagem": "Não encontrado."})
    alvo.sessoes_validas_apos = agora()
    sessao.commit()
    return {"mensagem": f"Sessões de {alvo.nome} encerradas."}


@router.post("/api/usuarios/{usuario_id}/desativar")
def desativar(
    usuario_id: uuid.UUID, sessao: BancoDeDados, _: Dono
) -> dict[str, str]:
    alvo = sessao.get(Usuario, usuario_id)
    if alvo is None:
        raise HTTPException(404, detail={"mensagem": "Não encontrado."})
    alvo.ativo = False
    sessao.commit()
    return {"mensagem": f"{alvo.nome} desativado."}
