"""S-06 — critérios de aceite do handoff e da continuidade.

O cenário que manda no arquivo é o primeiro: **a loja nunca envia primeiro**. Ele não testa
uma função, testa a ausência de um caminho — e por isso vale mais do que os outros juntos.
"""

import asyncio
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import whatsapp
from app.configuracao import Chave, gravar
from app.core.pii import cifrar, hash_telefone
from app.db import agora
from app.modelos import Conversa, Lead, Mensagem, TokenMigracao, Trilha, Unidade, Usuario
from app.whatsapp import JANELA_DE_RESPOSTA, formatar, pode_enviar, receber

from .test_conversas import LEAD, SEAL

TELEFONE = "+5583991575299"
JID = "5583991575299@s.whatsapp.net"


@pytest.fixture
def configurado(sessao: Session) -> None:
    """A loja com WhatsApp cadastrado. Sem isto não há link nem envio — de propósito."""
    from app.autenticacao import criar_usuario

    rai = criar_usuario(sessao, nome="Raí Sol", email="rai@solevolt.com.br",
                        senha="senha-de-teste-12", perfil="dono")  # fmt: skip
    gravar(sessao, Chave.whatsapp_numero, TELEFONE, rai)
    gravar(sessao, Chave.evolution_url, "http://evolution:8080", rai)
    gravar(sessao, Chave.evolution_instancia, "solevolt", rai)
    gravar(sessao, Chave.evolution_chave, "chave-da-instancia", rai)


@pytest.fixture
def enviados(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Nada sai para a rede. O que a suíte confere é **se** saiu e com o quê."""
    saidas: list[dict[str, object]] = []

    def falso(url: str, cabecalhos: dict[str, str], corpo: bytes | None = None) -> int:
        saidas.append({"url": url, "corpo": (corpo or b"").decode()})
        return 200

    monkeypatch.setattr(whatsapp, "buscar", falso)
    return saidas


def _payload(texto: str, message_id: str = "MSG1", jid: str = JID) -> dict[str, object]:
    """O formato que a Evolution v2 entrega em `messages.upsert`."""
    return {
        "event": "messages.upsert",
        "instance": "solevolt",
        "data": {
            "key": {"remoteJid": jid, "fromMe": False, "id": message_id},
            "message": {"conversation": texto},
            "pushName": "Tarcísio",
        },
    }


def _entregar(cliente: TestClient, payload: dict[str, object]) -> int:
    return cliente.post(
        "/api/webhooks/evolution", json=payload, headers={"apikey": "chave-da-instancia"}
    ).status_code


# ── a regra que organiza tudo ────────────────────────────────────────────────────


def test_a_loja_nunca_envia_primeiro(
    sessao: Session, cliente: TestClient, configurado: None
) -> None:
    """Lead cadastrado, telefone conhecido, nenhuma mensagem recebida: não sai nada."""
    cliente.post("/api/leads", json=LEAD)
    conversa = sessao.scalars(select(Conversa)).one()

    liberado, motivo = pode_enviar(sessao, conversa)
    assert not liberado
    assert motivo == "sem_mensagem_do_cliente"
    assert whatsapp.enviar(sessao, conversa, "oi, tudo bem?") is False


def test_a_janela_de_24h(sessao: Session, cliente: TestClient, configurado: None) -> None:
    cliente.post("/api/leads", json=LEAD)
    conversa = sessao.scalars(select(Conversa)).one()
    sessao.add(
        Mensagem(
            conversa_id=conversa.id, direcao="entrada", autor="cliente", canal="whatsapp",
            conteudo="oi", criada_em=agora() - JANELA_DE_RESPOSTA - timedelta(minutes=1),
            processada_em=agora(),
        )  # fmt: skip
    )
    sessao.commit()

    liberado, motivo = pode_enviar(sessao, conversa)
    assert not liberado
    assert motivo == "fora_da_janela_de_24h"


def test_duas_consecutivas_e_a_aurora_para(
    sessao: Session, cliente: TestClient, configurado: None
) -> None:
    cliente.post("/api/leads", json=LEAD)
    conversa = sessao.scalars(select(Conversa)).one()
    sessao.add(
        Mensagem(conversa_id=conversa.id, direcao="entrada", autor="cliente",
                 canal="whatsapp", conteudo="oi", processada_em=agora())  # fmt: skip
    )
    sessao.commit()
    assert pode_enviar(sessao, conversa)[0]

    for _ in range(2):
        sessao.add(
            Mensagem(conversa_id=conversa.id, direcao="saida", autor="aurora",
                     canal="whatsapp", conteudo="oi", processada_em=agora())  # fmt: skip
        )
    sessao.commit()

    liberado, motivo = pode_enviar(sessao, conversa)
    assert not liberado
    assert motivo == "duas_consecutivas_sem_resposta"


# ── §2 e §3 o convite ────────────────────────────────────────────────────────────


def test_o_link_leva_o_codigo_e_nao_o_telefone_do_cliente(
    sessao: Session, cliente: TestClient, configurado: None
) -> None:
    conversa_id = cliente.post("/api/leads", json=LEAD).json()["conversa_id"]

    link = cliente.post(f"/api/conversas/{conversa_id}/whatsapp").json()["link"]

    token = sessao.scalars(select(TokenMigracao)).one()
    assert link.startswith("https://wa.me/5583991575299?text=")
    assert f"SV-{token.token}" in link.replace("%20", " ").replace("%2C", ",")
    assert "98871" not in link  # o telefone do LEAD não entra no link
    assert len(token.token) == 6
    assert not set(token.token) & set("O0I1")


def test_sem_numero_configurado_nao_ha_convite(cliente: TestClient) -> None:
    conversa_id = cliente.post("/api/leads", json=LEAD).json()["conversa_id"]
    assert cliente.post(f"/api/conversas/{conversa_id}/whatsapp").status_code == 503


# ── §4 recepção ──────────────────────────────────────────────────────────────────


def test_webhook_sem_chave_e_recusado(cliente: TestClient, configurado: None) -> None:
    assert cliente.post("/api/webhooks/evolution", json=_payload("oi")).status_code == 401
    assert (
        cliente.post(
            "/api/webhooks/evolution", json=_payload("oi"), headers={"apikey": "errada"}
        ).status_code
        == 401
    )


def test_o_codigo_migra_a_conversa_sem_repetir_nada(
    sessao: Session, cliente: TestClient, configurado: None, enviados: list[dict[str, object]]
) -> None:
    conversa_id = uuid.UUID(cliente.post("/api/leads", json=LEAD).json()["conversa_id"])
    cliente.post(f"/api/conversas/{conversa_id}/whatsapp")
    token = sessao.scalars(select(TokenMigracao)).one()

    assert _entregar(cliente, _payload(f"Oi, sou a Jaqueline. código SV-{token.token}")) == 200

    sessao.expire_all()
    assert sessao.scalars(select(Conversa)).one().id == conversa_id  # a MESMA conversa
    assert sessao.get(Conversa, conversa_id).canal_atual == "whatsapp"  # type: ignore[union-attr]
    assert sessao.get(TokenMigracao, token.token).usado_em is not None  # type: ignore[union-attr]


def test_token_de_uso_unico_nao_migra_duas_vezes(
    sessao: Session, cliente: TestClient, configurado: None, enviados: list[dict[str, object]]
) -> None:
    conversa_id = uuid.UUID(cliente.post("/api/leads", json=LEAD).json()["conversa_id"])
    cliente.post(f"/api/conversas/{conversa_id}/whatsapp")
    token = sessao.scalars(select(TokenMigracao)).one()

    _entregar(cliente, _payload(f"SV-{token.token}", message_id="M1"))
    # Outro número, mesmo código.
    _entregar(
        cliente,
        _payload(f"SV-{token.token}", message_id="M2", jid="5511999998888@s.whatsapp.net"),
    )

    sessao.expire_all()
    conversas = sessao.scalars(select(Conversa)).all()
    assert len(conversas) == 2, "o segundo virou conversa própria, pelo telefone_hash"
    assert len(sessao.scalars(select(Lead)).all()) == 2


def test_token_expirado_cai_no_telefone(
    sessao: Session, cliente: TestClient, configurado: None, enviados: list[dict[str, object]]
) -> None:
    conversa_id = uuid.UUID(cliente.post("/api/leads", json=LEAD).json()["conversa_id"])
    cliente.post(f"/api/conversas/{conversa_id}/whatsapp")
    token = sessao.scalars(select(TokenMigracao)).one()
    token.expira_em = agora() - timedelta(minutes=1)
    sessao.commit()

    _entregar(cliente, _payload(f"SV-{token.token}"))

    sessao.expire_all()
    assert sessao.get(TokenMigracao, token.token).usado_em is None  # type: ignore[union-attr]


def test_webhook_duplicado_nao_gera_resposta_dupla(
    sessao: Session, cliente: TestClient, configurado: None, enviados: list[dict[str, object]]
) -> None:
    """A Evolution entrega *at-least-once*. Quem recusa a segunda é o índice único."""
    assert _entregar(cliente, _payload("vocês têm elétrico?", message_id="IGUAL")) == 200
    assert _entregar(cliente, _payload("vocês têm elétrico?", message_id="IGUAL")) == 200

    entradas = sessao.scalars(
        select(Mensagem).where(Mensagem.whatsapp_message_id == "IGUAL")
    ).all()
    assert len(entradas) == 1


def test_cliente_que_chega_direto_e_atendido(
    sessao: Session, cliente: TestClient, configurado: None, enviados: list[dict[str, object]]
) -> None:
    assert _entregar(cliente, _payload("vocês têm elétrico até 150 mil?")) == 200

    lead = sessao.scalars(select(Lead)).one()
    assert lead.origem == "whatsapp_direto"
    assert lead.telefone_hash == hash_telefone(TELEFONE)
    conversa = sessao.scalars(select(Conversa)).one()
    assert conversa.canal_atual == "whatsapp"
    # A conversa nasce na saudação e o turno já rodou: a Aurora respondeu, que é o que
    # "é atendido normalmente" quer dizer.
    assert sessao.scalars(select(Mensagem).where(Mensagem.direcao == "saida")).all()


def test_numero_antigo_sem_o_nono_digito_encontra_o_lead(
    sessao: Session, cliente: TestClient, configurado: None, enviados: list[dict[str, object]]
) -> None:
    """Contas antigas chegam sem o nono dígito. Sem a correção, todo cliente que migrasse
    viraria lead novo — o oposto de "sem repetir nada"."""
    sessao.add(
        Lead(nome_cifrado=cifrar("Tarcísio"), telefone_cifrado=cifrar(TELEFONE),
             telefone_hash=hash_telefone(TELEFONE), origem="landing")  # fmt: skip
    )
    sessao.commit()

    _entregar(cliente, _payload("oi", jid="558391575299@s.whatsapp.net"))

    assert len(sessao.scalars(select(Lead)).all()) == 1


def test_audio_e_imagem_nao_derrubam_o_webhook(cliente: TestClient, configurado: None) -> None:
    """Fora do escopo do v1: entram e são ignorados, sem 500."""
    sem_texto = _payload("oi")
    sem_texto["data"]["message"] = {"audioMessage": {"url": "..."}}  # type: ignore[index]
    assert _entregar(cliente, sem_texto) == 200


def test_eco_do_proprio_envio_e_ignorado(
    sessao: Session, cliente: TestClient, configurado: None
) -> None:
    meu = _payload("resposta da Aurora")
    meu["data"]["key"]["fromMe"] = True  # type: ignore[index]
    assert _entregar(cliente, meu) == 200
    assert sessao.scalars(select(Mensagem)).all() == []


def test_a_mensagem_do_cliente_e_gravada_antes_de_processar(
    sessao: Session, cliente: TestClient, configurado: None
) -> None:
    """§8 — mensagem do cliente nunca é perdida. `receber` grava e devolve; o turno é
    de quem chama."""
    conversa_id = receber(sessao, _payload("me manda o preço"))

    assert conversa_id is not None
    entrada = sessao.scalars(select(Mensagem)).one()
    assert entrada.conteudo == "me manda o preço"
    assert entrada.processada_em is None
    assert entrada.payload_bruto is not None


# ── §5 continuidade ──────────────────────────────────────────────────────────────


def test_web_fecha_quando_o_whatsapp_assume(
    sessao: Session, cliente: TestClient, configurado: None, enviados: list[dict[str, object]]
) -> None:
    conversa_id = cliente.post("/api/leads", json=LEAD).json()["conversa_id"]
    cliente.post(f"/api/conversas/{conversa_id}/whatsapp")
    token = sessao.scalars(select(TokenMigracao)).one()
    _entregar(cliente, _payload(f"SV-{token.token}"))

    recusa = cliente.post(
        f"/api/conversas/{conversa_id}/mensagens", json={"conteudo": "e o preço?"}
    )
    assert recusa.status_code == 409
    assert cliente.get(f"/api/conversas/{conversa_id}").json()["canal_atual"] == "whatsapp"


# ── §7 formatação ────────────────────────────────────────────────────────────────


def test_formatacao_do_whatsapp() -> None:
    assert formatar("**Seal** custa isso") == ["*Seal* custa isso"]
    # O marcador some, o texto do cabeçalho fica: ele costuma ser a informação. A tabela
    # não fica — os números dela a Aurora repete em prosa, e tabela no WhatsApp é ilegível.
    assert formatar("## Carros disponíveis\ntexto") == ["Carros disponíveis\ntexto"]
    assert formatar("- um\n- dois") == ["• um\n• dois"]
    assert formatar("| a | b |\n\ntexto") == ["texto"]
    assert formatar("   ") == []


def test_mensagem_longa_vira_no_maximo_tres_bolhas() -> None:
    paragrafo = "palavra " * 60  # ~480 caracteres
    bolhas = formatar("\n\n".join([paragrafo] * 8))
    assert 1 < len(bolhas) <= 3
    assert all(bolha.strip() for bolha in bolhas)


def test_envio_manda_uma_requisicao_por_bolha(
    sessao: Session, cliente: TestClient, configurado: None, enviados: list[dict[str, object]]
) -> None:
    cliente.post("/api/leads", json=LEAD)
    conversa = sessao.scalars(select(Conversa)).one()
    sessao.add(
        Mensagem(conversa_id=conversa.id, direcao="entrada", autor="cliente",
                 canal="whatsapp", conteudo="oi", processada_em=agora())  # fmt: skip
    )
    sessao.commit()

    assert whatsapp.enviar(sessao, conversa, "**oi**") is True
    assert len(enviados) == 1
    assert enviados[0]["url"] == "http://evolution:8080/message/sendText/solevolt"
    assert "*oi*" in str(enviados[0]["corpo"])


def test_envio_bloqueado_deixa_rastro(
    sessao: Session, cliente: TestClient, configurado: None
) -> None:
    cliente.post("/api/leads", json=LEAD)
    conversa = sessao.scalars(select(Conversa)).one()

    whatsapp.enviar(sessao, conversa, "oi")

    linha = sessao.scalars(select(Trilha).where(Trilha.nome == "envio_bloqueado")).one()
    assert linha.dados["motivo"] == "sem_mensagem_do_cliente"


# ── §4.6 o turno é o mesmo ───────────────────────────────────────────────────────


def test_o_turno_do_whatsapp_e_o_mesmo_do_chat(
    sessao: Session, cliente: TestClient, configurado: None, enviados: list[dict[str, object]]
) -> None:
    sessao.add(Unidade(**SEAL))  # type: ignore[arg-type]
    sessao.commit()

    conversa_id = receber(sessao, _payload("oi, quero um elétrico"))
    assert conversa_id is not None
    asyncio.run(whatsapp.responder(conversa_id))

    sessao.expire_all()
    saidas = sessao.scalars(
        select(Mensagem).where(Mensagem.direcao == "saida", Mensagem.canal == "whatsapp")
    ).all()
    assert len(saidas) == 1
    assert len(enviados) == 1


# ── aviso para a equipe: a segunda porta de saída ────────────────────────────────


def _neuza_com_telefone(sessao: Session) -> Usuario:
    from app.autenticacao import criar_usuario

    neuza = criar_usuario(sessao, nome="Neuza Andrade", email="neuza@solevolt.com.br",
                          senha="senha-de-teste-12", perfil="gerente")  # fmt: skip
    neuza.telefone_cifrado = cifrar(TELEFONE)
    sessao.commit()
    return neuza


def test_aviso_para_a_equipe_sai_sem_mensagem_de_entrada(
    sessao: Session, configurado: None, enviados: list[dict[str, object]]
) -> None:
    """A regra de nunca enviar primeiro protege o número contra ban por mensagem a
    **cliente**. Funcionário cadastrou o telefone na tela do dono — é o consentimento."""
    neuza = _neuza_com_telefone(sessao)

    assert whatsapp.avisar_equipe(sessao, [neuza], "reserva para aprovar") == 1
    assert len(enviados) == 1


def test_aviso_para_a_equipe_nao_alcanca_cliente(sessao: Session, configurado: None) -> None:
    """O tipo é a garantia: um `Lead` não entra por aqui, e alargar exige mexer na
    assinatura — que é uma linha de diff difícil de não ver."""
    lead = Lead(nome_cifrado=cifrar("Tarcísio"), telefone_cifrado=cifrar(TELEFONE),
                telefone_hash=hash_telefone(TELEFONE), origem="landing")  # fmt: skip
    sessao.add(lead)
    sessao.commit()

    with pytest.raises(TypeError):
        whatsapp.avisar_equipe(sessao, [lead], "oi")  # type: ignore[list-item]


def test_quem_nao_cadastrou_telefone_nao_recebe(
    sessao: Session, configurado: None, enviados: list[dict[str, object]]
) -> None:
    from app.autenticacao import criar_usuario

    sem_telefone = criar_usuario(sessao, nome="Jaqueline S", email="jaq@solevolt.com.br",
                                 senha="senha-de-teste-12", perfil="gerente")  # fmt: skip

    assert whatsapp.avisar_equipe(sessao, [sem_telefone], "oi") == 0
    assert enviados == []


def test_a_notificacao_da_neuza_nao_leva_telefone_nem_nome_inteiro(
    sessao: Session, cliente: TestClient, configurado: None, enviados: list[dict[str, object]]
) -> None:
    """S-04 §3 com ADR-007: nome mascarado, telefone ausente, preço lido do banco."""
    from app.aprovacao import solicitar_aprovacao

    _neuza_com_telefone(sessao)
    sessao.add(Unidade(**SEAL))  # type: ignore[arg-type]
    sessao.commit()
    cliente.post("/api/leads", json={"nome": "Tarcísio Nóbrega", "telefone": "+5583988714471"})
    conversa = sessao.scalars(select(Conversa)).one()

    solicitar_aprovacao(sessao, conversa, str(SEAL["chassi"]))

    assert len(enviados) == 1
    corpo = str(enviados[0]["corpo"])
    assert "Tarcísio N." in corpo
    assert "Nóbrega" not in corpo
    assert "988714471" not in corpo
    assert "249.990" in corpo


# ── os dois avisos que a S-06 destravou ──────────────────────────────────────────


def _rai_com_telefone(sessao: Session) -> Usuario:
    """O  cria o Raí sem telefone: quem não cadastrou não recebe."""
    rai = sessao.scalars(select(Usuario).where(Usuario.perfil == "dono")).one()
    rai.telefone_cifrado = cifrar(TELEFONE)
    sessao.commit()
    return rai


def test_incidente_critico_alerta_o_dono_no_whatsapp(
    sessao: Session, configurado: None, enviados: list[dict[str, object]]
) -> None:
    """S-08 §6 — "alerta imediato" é a gravidade crítica, e o canal do Raí é o WhatsApp."""
    from app.observabilidade import registrar_incidente

    _rai_com_telefone(sessao)

    registrar_incidente(sessao, None, "teto_atingido", {"gasto_micro_reais": 901_000_000})

    assert len(enviados) == 1
    corpo = str(enviados[0]["corpo"])
    assert "teto de custo" in corpo
    assert "R$ 901,00" in corpo and "R$ 900,00" in corpo


def test_incidente_de_gravidade_alta_nao_acorda_ninguem(
    sessao: Session, configurado: None, enviados: list[dict[str, object]]
) -> None:
    """ é alta e vira revisão da semana, não mensagem de madrugada."""
    from app.observabilidade import registrar_incidente

    _rai_com_telefone(sessao)

    registrar_incidente(sessao, None, "numero_divergente", {"divergentes": ["400 km"]})

    assert enviados == []


def test_o_alerta_de_oitenta_por_cento_sai_mesmo_sendo_alta(
    sessao: Session, configurado: None, enviados: list[dict[str, object]]
) -> None:
    """S-08 §3 — a exceção nomeada: aviso de 80% só serve **antes** de o teto cortar."""
    from app.observabilidade import registrar_incidente

    _rai_com_telefone(sessao)

    registrar_incidente(sessao, None, "custo_perto_do_teto", {"gasto_micro_reais": 720_000_000})

    assert len(enviados) == 1
    assert "80%" in str(enviados[0]["corpo"])


def test_whatsapp_caido_nao_se_avisa_pelo_whatsapp(
    sessao: Session, configurado: None, enviados: list[dict[str, object]]
) -> None:
    """ é crítica e **não** alerta: o aviso falharia, o fracasso
    registraria outro incidente igual, e a pilha acabaria. Ele se lê no log e na saúde."""
    from app.observabilidade import registrar_incidente

    _rai_com_telefone(sessao)

    registrar_incidente(sessao, None, "evolution_desconectada", {"etapa": "envio"})

    assert enviados == []


def test_reserva_vencida_avisa_a_equipe_de_vendas_uma_vez(
    sessao: Session, configurado: None, enviados: list[dict[str, object]]
) -> None:
    """S-05 §4 — ao liberar, notifica. Três chassis numa rodada não são três mensagens."""
    from app.modelos import Vendedor
    from app.reserva import _avisar_a_equipe_de_vendas

    sessao.add(Vendedor(nome="Tarcísio", telefone_cifrado=cifrar(TELEFONE), ativo=True))
    sessao.add(Vendedor(nome="Fora de férias", telefone_cifrado=cifrar(TELEFONE), ativo=False))
    sessao.commit()

    assert _avisar_a_equipe_de_vendas(sessao, ["CHASSI-1", "CHASSI-2"]) == 1
    assert len(enviados) == 1
    corpo = str(enviados[0]["corpo"])
    assert "CHASSI-1" in corpo and "CHASSI-2" in corpo


def test_rodada_sem_reserva_vencida_nao_manda_nada(
    sessao: Session, configurado: None, enviados: list[dict[str, object]]
) -> None:
    """A rotina roda a cada 5 minutos. Silêncio é a resposta certa 287 vezes por dia."""
    from app.reserva import _avisar_a_equipe_de_vendas

    assert _avisar_a_equipe_de_vendas(sessao, []) == 0
    assert enviados == []
