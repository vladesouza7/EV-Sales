"""S-07 §9 — o registro de desfecho, e o que ele move no banco.

O teste que manda no arquivo é `test_o_banco_recusa_desfecho_sem_autor`: `vendeu` é o
único ponto em que o EV-Sales sabe que a venda aconteceu, e um `vendido` sem quem marcou é
um carro que sai do catálogo sem ninguém responsável. A garantia é do CHECK, então ela é
testada contra o banco — não contra o código que hoje faz certo.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agenda import confirmar_se_o_cliente_respondeu
from app.autenticacao import criar_usuario
from app.core.pii import cifrar
from app.db import FUSO, agora
from app.ia.tools.estoque import buscar_unidades
from app.modelos import (
    Conversa,
    Lead,
    Mensagem,
    Reserva,
    TestDrive,
    Trilha,
    Unidade,
    Usuario,
    Vendedor,
)
from app.reserva import reservar_chassi
from app.rotinas import ciclo
from app.testdrive import cobrar_desfechos, enviar_lembretes, quando_lembrar

from .test_reserva import SEAL, _cenario

SENHA = "senha-de-teste-12"


@pytest.fixture
def cenario(sessao: Session) -> TestDrive:
    """Um Seal reservado, um test drive de ontem, e ninguém marcou nada ainda."""
    sessao.add(Unidade(**SEAL))  # type: ignore[arg-type]
    sessao.commit()
    conversa, approval_id = _cenario(sessao)
    reservar_chassi(sessao, conversa, str(SEAL["chassi"]), approval_id)

    tarcisio = Vendedor(nome="Tarcísio", telefone_cifrado=cifrar("+5583988710001"))
    sessao.add(tarcisio)
    sessao.flush()
    inicio = agora() - timedelta(days=1)
    test_drive = TestDrive(
        conversa_id=conversa.id,
        lead_id=conversa.lead_id,
        chassi=str(SEAL["chassi"]),
        vendedor_id=tarcisio.id,
        inicio=inicio,
        fim=inicio + timedelta(minutes=45),
        status="agendado",
    )
    sessao.add(test_drive)
    sessao.commit()
    return test_drive


def _vendedor(sessao: Session, test_drive: TestDrive, email: str = "t@solevolt.com.br") -> Usuario:
    return criar_usuario(sessao, nome="Tarcísio Lima", email=email, senha=SENHA,
                         perfil="vendedor", vendedor_id=test_drive.vendedor_id)  # fmt: skip


def _entrar(cliente: TestClient, usuario: Usuario) -> None:
    entrada = {"email": usuario.email, "senha": SENHA}
    assert cliente.post("/api/entrar", json=entrada).status_code == 200


def _marcar(
    cliente: TestClient, test_drive: TestDrive, desfecho: str, compareceu: bool = True
) -> int:
    resposta = cliente.post(
        f"/api/test-drives/{test_drive.id}/desfecho",
        json={"compareceu": compareceu, "desfecho": desfecho},
    )
    return resposta.status_code


# ── a garantia que é do banco ────────────────────────────────────────────────────


def test_o_banco_recusa_desfecho_sem_autor(sessao: Session, cenario: TestDrive) -> None:
    """`vendido` sem quem marcou é carro fora do catálogo sem responsável. O CHECK recusa,
    e é ele que vale quando o código de aplicação estiver errado."""
    with pytest.raises(IntegrityError):
        sessao.execute(
            text("UPDATE test_drives SET desfecho = 'vendeu' WHERE id = :id"),
            {"id": cenario.id},
        )
        sessao.commit()
    sessao.rollback()


def test_o_banco_recusa_desfecho_desconhecido(sessao: Session, cenario: TestDrive) -> None:
    with pytest.raises(IntegrityError):
        sessao.execute(
            text(
                "UPDATE test_drives SET desfecho = 'quase_vendeu', desfecho_em = now(),"
                " desfecho_por = (SELECT id FROM usuarios LIMIT 1) WHERE id = :id"
            ),
            {"id": cenario.id},
        )
        sessao.commit()
    sessao.rollback()


# ── os quatro desfechos ──────────────────────────────────────────────────────────


def test_vendeu_marca_a_unidade_encerra_a_reserva_e_ganha_o_lead(
    sessao: Session, cliente: TestClient, cenario: TestDrive
) -> None:
    _entrar(cliente, _vendedor(sessao, cenario))

    assert _marcar(cliente, cenario, "vendeu") == 200

    sessao.expire_all()
    assert sessao.get(Unidade, cenario.chassi).status == "vendido"  # type: ignore[union-attr]
    assert sessao.scalars(select(Reserva)).one().status == "concluida"
    assert sessao.get(Lead, cenario.lead_id).situacao == "ganho"  # type: ignore[union-attr]
    assert sessao.get(TestDrive, cenario.id).status == "realizado"  # type: ignore[union-attr]


def test_desistiu_devolve_o_carro_ao_estoque(
    sessao: Session, cliente: TestClient, cenario: TestDrive
) -> None:
    """E o chassi volta a aparecer em `buscar_unidades` — a tool é a mesma do catálogo."""
    _entrar(cliente, _vendedor(sessao, cenario))

    assert _marcar(cliente, cenario, "desistiu") == 200

    sessao.expire_all()
    unidade = sessao.get(Unidade, cenario.chassi)
    assert unidade is not None and unidade.status == "disponivel"
    assert unidade.reservado_para is None
    reserva = sessao.scalars(select(Reserva)).one()
    assert (reserva.status, reserva.motivo_liberacao) == ("liberada", "desistencia")
    assert sessao.get(Lead, cenario.lead_id).situacao == "perdido"  # type: ignore[union-attr]
    assert cenario.chassi in [str(f["chassi"]) for f in buscar_unidades(sessao)]


def test_vai_pensar_mantem_a_reserva_ate_as_72h(
    sessao: Session, cliente: TestClient, cenario: TestDrive
) -> None:
    _entrar(cliente, _vendedor(sessao, cenario))

    assert _marcar(cliente, cenario, "vai_pensar") == 200

    sessao.expire_all()
    assert sessao.get(Unidade, cenario.chassi).status == "reservado"  # type: ignore[union-attr]
    assert sessao.scalars(select(Reserva)).one().status == "ativa"
    assert sessao.get(Lead, cenario.lead_id).situacao == "em_negociacao"  # type: ignore[union-attr]


def test_nao_compareceu_mantem_tudo_e_marca_para_recontatar(
    sessao: Session, cliente: TestClient, cenario: TestDrive
) -> None:
    _entrar(cliente, _vendedor(sessao, cenario))

    assert _marcar(cliente, cenario, "nao_compareceu", compareceu=False) == 200

    sessao.expire_all()
    assert sessao.get(Unidade, cenario.chassi).status == "reservado"  # type: ignore[union-attr]
    assert sessao.scalars(select(Reserva)).one().status == "ativa"
    assert sessao.get(Lead, cenario.lead_id).situacao == "a_recontatar"  # type: ignore[union-attr]
    assert sessao.get(TestDrive, cenario.id).status == "nao_compareceu"  # type: ignore[union-attr]


def test_compareceu_e_nao_compareceu_ao_mesmo_tempo_e_recusado(
    sessao: Session, cliente: TestClient, cenario: TestDrive
) -> None:
    """Contradição não é preferência: a tela manda os dois campos, e um deles mente."""
    _entrar(cliente, _vendedor(sessao, cenario))

    assert _marcar(cliente, cenario, "nao_compareceu", compareceu=True) == 422
    assert _marcar(cliente, cenario, "vendeu", compareceu=False) == 422
    sessao.expire_all()
    assert sessao.get(TestDrive, cenario.id).desfecho is None  # type: ignore[union-attr]


def test_desfecho_nao_se_registra_duas_vezes(
    sessao: Session, cliente: TestClient, cenario: TestDrive
) -> None:
    _entrar(cliente, _vendedor(sessao, cenario))
    assert _marcar(cliente, cenario, "vai_pensar") == 200

    assert _marcar(cliente, cenario, "vendeu") == 409

    sessao.expire_all()
    assert sessao.get(Unidade, cenario.chassi).status == "reservado"  # type: ignore[union-attr]


# ── quem pode, e o rastro ────────────────────────────────────────────────────────


def test_sem_sessao_ninguem_registra_desfecho(cliente: TestClient, cenario: TestDrive) -> None:
    assert _marcar(cliente, cenario, "vendeu") == 401


def test_vendedor_nao_alcanca_test_drive_de_outro(
    sessao: Session, cliente: TestClient, cenario: TestDrive
) -> None:
    """404 e não 403 (S-11 §5): quem não alcança não descobre que o recurso existe."""
    outra = Vendedor(nome="Jaqueline", telefone_cifrado=cifrar("+5583988710002"))
    sessao.add(outra)
    sessao.flush()
    jaqueline = criar_usuario(sessao, nome="Jaqueline S", email="j@solevolt.com.br",
                              senha=SENHA, perfil="vendedor", vendedor_id=outra.id)  # fmt: skip
    _entrar(cliente, jaqueline)

    assert _marcar(cliente, cenario, "vendeu") == 404
    assert cliente.get("/api/desfechos/pendentes").json() == []


def test_a_gerente_ve_o_desfecho_de_todos(
    sessao: Session, cliente: TestClient, cenario: TestDrive
) -> None:
    """§9 — "o item entra na tela da Neuza". É a mesma tela, com o recorte mais largo."""
    neuza = criar_usuario(sessao, nome="Neuza Andrade", email="neuza@solevolt.com.br",
                          senha=SENHA, perfil="gerente")  # fmt: skip
    _entrar(cliente, neuza)

    fila = cliente.get("/api/desfechos/pendentes").json()

    assert len(fila) == 1
    assert fila[0]["test_drive_id"] == str(cenario.id)
    assert "249.990" in fila[0]["carro"]
    # A lista não carrega nome inteiro: sobrenome fica para o dossiê da §8.
    assert "Nóbrega" not in fila[0]["cliente"]


def test_a_auditoria_grava_quem_quando_e_de_qual_ip(
    sessao: Session, cliente: TestClient, cenario: TestDrive
) -> None:
    vendedor = _vendedor(sessao, cenario)
    _entrar(cliente, vendedor)

    _marcar(cliente, cenario, "vendeu")

    sessao.expire_all()
    trilha = sessao.scalars(
        select(Trilha).where(Trilha.nome == "desfecho_registrado")
    ).one()
    assert trilha.dados["por"] == str(vendedor.id)
    assert trilha.dados["perfil"] == "vendedor"
    assert trilha.dados["ip"]
    marcado = sessao.get(TestDrive, cenario.id)
    assert marcado is not None
    assert marcado.desfecho_por == vendedor.id and marcado.desfecho_em is not None


# ── contra o esquecimento, e o limite dela ───────────────────────────────────────


def test_nenhuma_rotina_marca_vendido_sozinho(sessao: Session, cenario: TestDrive) -> None:
    """O cenário que a §9 proíbe por escrito: 30 dias sem desfecho, e a rotina rodando."""
    cenario.inicio = agora() - timedelta(days=30)
    cenario.fim = cenario.inicio + timedelta(minutes=45)
    sessao.commit()

    for _ in range(3):
        ciclo(sessao)

    sessao.expire_all()
    marcado = sessao.get(TestDrive, cenario.id)
    assert marcado is not None and marcado.desfecho is None
    assert sessao.get(Unidade, cenario.chassi).status != "vendido"  # type: ignore[union-attr]
    # Duas cobranças, e não trinta: a rotina lembra duas vezes e para.
    assert marcado.cobrancas == 2


def test_a_primeira_cobranca_espera_duas_horas(sessao: Session, cenario: TestDrive) -> None:
    cenario.inicio = agora() - timedelta(minutes=90)
    cenario.fim = cenario.inicio + timedelta(minutes=45)
    sessao.commit()

    assert cobrar_desfechos(sessao) == 0

    cenario.inicio = agora() - timedelta(hours=3)
    sessao.commit()
    assert cobrar_desfechos(sessao) == 1


def test_desfecho_registrado_nao_e_cobrado(
    sessao: Session, cliente: TestClient, cenario: TestDrive
) -> None:
    _entrar(cliente, _vendedor(sessao, cenario))
    _marcar(cliente, cenario, "vai_pensar")

    assert cobrar_desfechos(sessao) == 0


def test_test_drive_cancelado_nao_pede_desfecho(sessao: Session, cenario: TestDrive) -> None:
    cenario.status = "cancelado"
    sessao.commit()

    assert cobrar_desfechos(sessao) == 0


def test_id_que_nao_existe_da_404(sessao: Session, cliente: TestClient, cenario: TestDrive) -> None:
    _entrar(cliente, _vendedor(sessao, cenario))

    resposta = cliente.post(
        f"/api/test-drives/{uuid.uuid4()}/desfecho",
        json={"compareceu": True, "desfecho": "vendeu"},
    )

    assert resposta.status_code == 404


# ── §6: o lembrete, e a tarefa de ligação quando a janela fecha ──────────────────


def _uma_hora_depois_de_amanha(hora: int) -> datetime:
    """Um horário futuro na hora pedida, longe do almoço e da virada do dia."""
    return (agora() + timedelta(days=1)).replace(hour=hora, minute=0, second=0, microsecond=0)


def _proxima_tarde() -> datetime:
    """O próximo 13h local, sempre entre agora e agora + 24 h.

    Não é preciosismo: `agora() + 20h` cai de manhã em parte do dia, e aí vale a regra dos
    18h da véspera — o lembrete não estaria vencido, e o teste falharia pela hora em que
    alguém rodou a suíte. Uma visita à tarde dentro das próximas 24 h tem lembrete vencido
    sempre.
    """
    momento = agora().astimezone(FUSO)
    tarde = momento.replace(hour=13, minute=0, second=0, microsecond=0)
    return tarde if tarde > momento else tarde + timedelta(days=1)


def _marcado_para(sessao: Session, cenario: TestDrive, inicio: datetime) -> None:
    cenario.inicio = inicio
    cenario.fim = inicio + timedelta(minutes=45)
    sessao.commit()


def _fala_do_cliente(sessao: Session, cenario: TestDrive, quando: datetime) -> None:
    """A janela de 24 h do WhatsApp existe porque o cliente falou. Aqui ela é posta na
    hora que o teste precisa: dentro dos 24 h, ou fora."""
    sessao.add(
        Mensagem(
            conversa_id=cenario.conversa_id,
            direcao="entrada",
            autor="cliente",
            canal="whatsapp",
            conteudo="oi",
            criada_em=quando,
        )
    )
    sessao.commit()


def test_o_lembrete_de_24h_sai_uma_vez_e_uma_so(
    sessao: Session, cenario: TestDrive, configurado: None, enviados: list[dict[str, object]]
) -> None:
    _marcado_para(sessao, cenario, _proxima_tarde())
    _fala_do_cliente(sessao, cenario, agora() - timedelta(hours=1))

    assert enviar_lembretes(sessao) == 1
    assert enviar_lembretes(sessao) == 0, "o lembrete é único (§6)"

    assert len(enviados) == 1
    corpo = str(enviados[0]["corpo"])
    assert "test drive amanhã" in corpo
    assert "Tarcísio" in corpo
    sessao.expire_all()
    assert sessao.get(TestDrive, cenario.id).lembrete_em is not None


def test_antes_da_hora_o_lembrete_nao_sai(
    sessao: Session, cenario: TestDrive, configurado: None, enviados: list[dict[str, object]]
) -> None:
    cenario.inicio = agora() + timedelta(days=3)
    cenario.fim = cenario.inicio + timedelta(minutes=45)
    sessao.commit()
    _fala_do_cliente(sessao, cenario, agora())

    assert enviar_lembretes(sessao) == 0
    assert enviados == []


def test_fora_da_janela_de_24h_o_lembrete_vira_tarefa_de_ligacao(
    sessao: Session, cenario: TestDrive, configurado: None, enviados: list[dict[str, object]]
) -> None:
    """O cenário da spec: test drive amanhã, última mensagem do cliente há 30 horas."""
    _marcado_para(sessao, cenario, _proxima_tarde())
    _fala_do_cliente(sessao, cenario, agora() - timedelta(hours=30))

    assert enviar_lembretes(sessao) == 1

    assert len(enviados) == 1, "uma mensagem, e ela é para o vendedor"
    corpo = str(enviados[0]["corpo"])
    assert "liga para" in corpo
    assert "janela de 24 h" in corpo
    # Nome do cliente mascarado, e nada de telefone (S-09 §3).
    assert "Nóbrega" not in corpo and "988714471" not in corpo
    sessao.expire_all()
    assert sessao.get(TestDrive, cenario.id).lembrete_em is not None


def test_test_drive_de_manha_e_lembrado_as_18h_da_vespera() -> None:
    """§6 — 24 h antes de uma visita às 9h cai às 9h da véspera, no meio do dia de
    trabalho de quem vai dirigir. A spec manda 18h, e é o que a função faz."""
    manha = _uma_hora_depois_de_amanha(9)
    tarde = _uma_hora_depois_de_amanha(15)

    lembrete_da_manha = quando_lembrar(manha)
    assert lembrete_da_manha.astimezone(FUSO).hour == 18
    assert lembrete_da_manha.date() == (manha - timedelta(days=1)).date()
    assert quando_lembrar(tarde) == tarde - timedelta(hours=24)


def test_test_drive_cancelado_nao_recebe_lembrete(
    sessao: Session, cenario: TestDrive, configurado: None, enviados: list[dict[str, object]]
) -> None:
    _marcado_para(sessao, cenario, _proxima_tarde())
    cenario.status = "cancelado"
    sessao.commit()
    _fala_do_cliente(sessao, cenario, agora())

    assert enviar_lembretes(sessao) == 0
    assert enviados == []


def test_test_drive_que_ja_passou_nao_recebe_lembrete(
    sessao: Session, cenario: TestDrive, configurado: None, enviados: list[dict[str, object]]
) -> None:
    """A rotina pode subir depois do horário. Lembrar de ontem é pior que não lembrar."""
    _fala_do_cliente(sessao, cenario, agora())

    assert enviar_lembretes(sessao) == 0
    assert enviados == []


# ── a resposta afirmativa ────────────────────────────────────────────────────────


def test_o_sim_do_cliente_confirma_o_test_drive(
    sessao: Session, cliente: TestClient, cenario: TestDrive
) -> None:
    _marcado_para(sessao, cenario, _proxima_tarde())
    cenario.lembrete_em = agora()
    sessao.commit()

    assert confirmar_se_o_cliente_respondeu(
        sessao, sessao.get(Conversa, cenario.conversa_id), "confirmo sim!"
    )

    sessao.commit()
    sessao.expire_all()
    marcado = sessao.get(TestDrive, cenario.id)
    assert marcado is not None
    assert marcado.status == "confirmado" and marcado.confirmado_em is not None


def test_sem_lembrete_um_ok_qualquer_nao_confirma_nada(
    sessao: Session, cenario: TestDrive
) -> None:
    """Confirmar o que não foi perguntado é inventar resposta do cliente."""
    _marcado_para(sessao, cenario, _proxima_tarde())

    conversa = sessao.get(Conversa, cenario.conversa_id)
    assert not confirmar_se_o_cliente_respondeu(sessao, conversa, "ok")
    sessao.expire_all()
    assert sessao.get(TestDrive, cenario.id).status == "agendado"  # type: ignore[union-attr]


def test_a_negacao_vence_a_afirmativa(sessao: Session, cenario: TestDrive) -> None:
    """"não vou poder" contém "vou". Marcar como confirmada uma visita que o cliente
    acabou de desmarcar é o erro que faz o vendedor esperar por ninguém."""
    _marcado_para(sessao, cenario, _proxima_tarde())
    cenario.lembrete_em = agora()
    sessao.commit()

    conversa = sessao.get(Conversa, cenario.conversa_id)
    for recusa in ("não vou poder", "vou ter que remarcar", "cancela pra mim", "não posso"):
        assert not confirmar_se_o_cliente_respondeu(sessao, conversa, recusa), recusa
    sessao.expire_all()
    assert sessao.get(TestDrive, cenario.id).status == "agendado"  # type: ignore[union-attr]


def test_o_sim_pelo_chat_web_tambem_confirma(
    sessao: Session, cliente: TestClient, cenario: TestDrive
) -> None:
    """O lembrete sai pelo WhatsApp, mas o cliente pode responder na aba que ficou aberta.
    São os dois pontos por onde uma fala entra, e a regra vale nos dois."""
    _marcado_para(sessao, cenario, _proxima_tarde())
    cenario.lembrete_em = agora()
    conversa = sessao.get(Conversa, cenario.conversa_id)
    assert conversa is not None
    conversa.etapa = "encerrada"
    sessao.commit()
    cliente.cookies.set("ev_sessao", conversa.token_sessao)

    resposta = cliente.post(
        f"/api/conversas/{conversa.id}/mensagens", json={"conteudo": "beleza, tô confirmado"}
    )

    assert resposta.status_code == 201
    sessao.expire_all()
    assert sessao.get(TestDrive, cenario.id).status == "confirmado"  # type: ignore[union-attr]
