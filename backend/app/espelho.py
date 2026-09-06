"""S-04 §5 — o Espelho de Condição e Reserva em PDF.

**Não é contrato nem documento fiscal**, e não precisa de aviso dizendo isso: um espelho
de condição e reserva não é documento fiscal por natureza, e é exatamente o artefato que a
Neuza produz hoje à mão (ADR-011).

O que ele **não** contém é tão parte da spec quanto o que contém: nada de financiamento,
parcela, taxa, prazo de crédito, campo fiscal, assinatura ou promessa de entrega. Um teste
abre o PDF gerado e falha se qualquer uma dessas palavras aparecer.
"""

from datetime import datetime, timedelta

from fpdf import FPDF

from app.core.dinheiro import formatar, por_extenso

VALIDADE_DA_CONDICAO = timedelta(days=7)
PRAZO_DA_RESERVA = timedelta(hours=72)

LOJA = (
    "Sol & Volt Veículos Elétricos",
    "Av. Cabo Branco, 1200 - Tambaú, João Pessoa/PB",
    "CNPJ 41.220.336/0001-08  ·  (83) 3244-1010",
)

RODAPE = (
    "A negociação final, o financiamento e a documentação são tratados "
    "presencialmente na loja."
)


# A fonte base do PDF cobre latin-1, que basta para o português — menos pela pontuação
# tipográfica. O travessão chega aqui de dentro do banco, no `autonomia_texto`, então
# sanitizar não é preciosismo: sem isto, aprovar um carro com autonomia WLTP quebraria a
# emissão do documento na frente da Neuza.
_SUBSTITUICOES = {
    "—": "-", "–": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", " ": " ",
}  # fmt: skip


def _texto(valor: str) -> str:
    for original, trocado in _SUBSTITUICOES.items():
        valor = valor.replace(original, trocado)
    # O que sobrar fora de latin-1 vira "?" em vez de derrubar a emissão.
    return valor.encode("latin-1", "replace").decode("latin-1")


def _linha(pdf: FPDF, rotulo: str, valor: str, tamanho: int = 11) -> None:
    """Rótulo em negrito à esquerda, valor ao lado, e o cursor volta para a margem.

    O `set_x` no início não é decoração: sem ele o `multi_cell` herda o cursor de onde a
    linha anterior parou e fica sem largura para render um caractere sequer.
    """
    pdf.set_x(pdf.l_margin)
    pdf.set_font("helvetica", "B", tamanho)
    pdf.cell(38, 7, _texto(rotulo), new_x="RIGHT", new_y="TOP")
    pdf.set_font("helvetica", "", tamanho)
    pdf.multi_cell(0, 7, _texto(valor), new_x="LMARGIN", new_y="NEXT")


def gerar(
    *,
    numero: str,
    nome_do_cliente: str,
    unidade: dict[str, object],
    preco_centavos: int,
    aprovado_por: str,
    emitido_em: datetime,
    test_drive: str | None = None,
) -> bytes:
    """Devolve os bytes do PDF. Quem grava é o chamador — aqui não há efeito colateral."""
    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("helvetica", "B", 15)
    pdf.cell(0, 9, _texto(LOJA[0]), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "", 9)
    for linha in LOJA[1:]:
        pdf.cell(0, 5, _texto(linha), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)
    pdf.set_font("helvetica", "B", 13)
    pdf.cell(0, 8, _texto("ESPELHO DE CONDIÇÃO E RESERVA"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "", 10)
    pdf.cell(
        0,
        6,
        _texto(f"Nº {numero} · emitido em {emitido_em.strftime('%d/%m/%Y às %H:%M')}"),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(5)

    _linha(pdf, "Cliente", nome_do_cliente)
    _linha(
        pdf,
        "Veículo",
        f"{unidade['marca']} {unidade['modelo']} {unidade['versao']} "
        f"{unidade['ano']} · {unidade['cor']} · {unidade['condicao']}",
    )
    _linha(pdf, "Chassi", str(unidade["chassi"]))
    if unidade.get("autonomia_texto"):
        _linha(pdf, "Autonomia", str(unidade["autonomia_texto"]))

    pdf.ln(3)
    pdf.set_font("helvetica", "B", 14)
    preco = _texto(f"Preço à vista: {formatar(preco_centavos)}")
    pdf.cell(0, 9, preco, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "I", 10)
    # Por extenso junto com o algarismo: é o que impede um dígito alterado de passar.
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, 6, _texto(f"({por_extenso(preco_centavos)})"), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    _linha(pdf, "Validade", f"{emitido_em.date().strftime('%d/%m/%Y')} + 7 dias corridos")
    _linha(pdf, "Reserva", "o chassi acima sai do estoque por 72 horas")
    if test_drive:
        _linha(pdf, "Test drive", test_drive)
    _linha(pdf, "Aprovado por", aprovado_por)

    pdf.ln(8)
    pdf.set_font("helvetica", "", 9)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, 5, _texto(RODAPE), new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())
