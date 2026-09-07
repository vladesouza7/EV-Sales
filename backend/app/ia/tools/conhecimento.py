"""Tool de busca na base de conhecimento (S-03 §2, S-10 §4 e ADR-002).

Consulta respostas oficiais e curadas da Sol & Volt para quebrar objeções
(autonomia, tempo de carga, vida útil da bateria, custo de manutenção), esclarecer
garantias, rotas (como João Pessoa-Recife na BR-101) e carregamento residencial/público.

A invariante 1 continua valendo: a base de conhecimento guarda texto explicativo e fatos
comprovados, nunca preços ou estoque. Preço vem de unidades no Postgres (ADR-003).
"""

import re
import unicodedata
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modelos import ItemConhecimento

STOPWORDS = {
    "que",
    "para",
    "com",
    "uma",
    "umas",
    "dois",
    "duas",
    "este",
    "esse",
    "isso",
    "esta",
    "essa",
    "como",
    "qual",
    "quais",
    "onde",
    "tenho",
    "tem",
    "por",
    "sobre",
    "pela",
    "pelo",
    "mais",
    "muito",
    "carro",
    "eletrico",
    "eletricos",
    "voce",
    "aurora",
    "sol",
    "volt",
    "saber",
    "queria",
    "quero",
    "ser",
    "sera",
}

ITENS_CONHECIMENTO: list[dict[str, str]] = [
    dict(
        topico="estrada_recife_br101",
        categoria="rota",
        titulo="Viagem João Pessoa a Recife pela BR-101 e carregamento",
        conteudo=(
            "A distância entre João Pessoa e Recife pela BR-101 é de aproximadamente 120 km "
            "(cerca de 1h30 a 2h de viagem). Qualquer modelo elétrico do estoque da Sol & Volt "
            "(com autonomia a partir de 280 km) faz a viagem de ida com mais de 50% de sobra, "
            "e modelos como o BYD Seal (372 km) e Blazer EV (481 km) fazem até ida e volta "
            "sem precisar recarregar. Na rota da BR-101 há eletropostos com carregadores "
            "rápidos DC (em Goiana/PE e Alhandra/PB), além de carregadores rápidos nos "
            "shoppings de Recife (Shopping Recife, RioMar, Tacaruna). O app PlugShare mostra "
            "os pontos e status em tempo real."
        ),
        palavras_chave=(
            "recife joao pessoa estrada br 101 br101 viagem viajar distancia goiana "
            "alhandra rota plugshare carregador na estrada recarregar aguentar"
        ),
    ),
    dict(
        topico="vida_util_bateria",
        categoria="objecao",
        titulo="Durabilidade, vida útil e risco de viciar da bateria",
        conteudo=(
            "As baterias de tração dos carros elétricos modernos (como as baterias Blade LFP "
            "da BYD) não têm efeito memória e não viciam. A degradação média é de apenas 1% "
            "a 2% ao ano. Mesmo após 100.000 km rodados, as baterias preservam mais de 90% "
            "da sua capacidade original. Além disso, todas as montadoras fornecem garantia de "
            "fábrica de 8 anos ou 150.000 a 160.000 km específica para a bateria, assegurando "
            "tranquilidade contra defeitos ou perda prematura de carga."
        ),
        palavras_chave=(
            "viciar bateria vida util degradacao desgaste durabilidade dura pouco estragar "
            "trocar bateria 8 anos vida util perda de carga"
        ),
    ),
    dict(
        topico="garantia_baterias",
        categoria="garantia",
        titulo="Garantia de fábrica do conjunto de baterias e trem de força",
        conteudo=(
            "Os veículos elétricos contam com garantia de fábrica de 8 anos ou 150.000 a "
            "160.000 km para a bateria de alta tensão e motor elétrico. Para os veículos "
            "seminovos da Sol & Volt, a garantia de fábrica remanescente é totalmente "
            "transferida ao novo comprador, além da nossa revisão cautelar completa e "
            "procedência garantida."
        ),
        palavras_chave=(
            "garantia cobertura assistencia defeito prazo 8 anos 150 mil km 160 mil km "
            "revisao procedencia fabricacao"
        ),
    ),
    dict(
        topico="tempo_de_recarga",
        categoria="objecao",
        titulo="Tempo de recarga residencial e em carregadores rápidos",
        conteudo=(
            "O tempo de recarga depende do tipo de carregador: em casa (onde são feitas mais de "
            "90% das recargas), um Wallbox de 7,4 kW completa a bateria durante a noite em "
            "6 a 8 horas. Em tomadas comuns 220V com o carregador portátil que acompanha o carro, "
            "recupera-se de 15 a 25 km por hora conectado (cerca de 120 a 150 km por noite). "
            "Já em eletropostos rápidos de rodovia (carregadores DC de 50 a 150 kW), a recarga "
            "rápida de 30% a 80% leva em média 30 a 40 minutos."
        ),
        palavras_chave=(
            "tempo de recarga demora demorado carregar recarga horas minutos wallbox tomada "
            "rapido carregador dc estacao"
        ),
    ),
    dict(
        topico="carregamento_residencial",
        categoria="carregamento",
        titulo="Carregamento em casa, tomadas 220V e instalação de Wallbox",
        conteudo=(
            "Carregar um carro elétrico em casa é simples: em João Pessoa a rede padrão é 220V. "
            "Você pode carregar usando o carregador portátil em qualquer tomada 220V aterrada de "
            "20A, recuperando toda a rodagem do dia a dia enquanto dorme. Quem quiser recarga "
            "ainda mais rápida pode instalar um Wallbox de 7,4 kW na garagem da casa ou na vaga "
            "do condomínio. A rotina é idêntica a carregar um celular: você pluga ao chegar em "
            "casa e acorda com a carga completa."
        ),
        palavras_chave=(
            "carregador residencial em casa tomada 220v wallbox instalacao condominio "
            "apartamento garagem aterramento energia eletrica"
        ),
    ),
    dict(
        topico="carregadores_joao_pessoa",
        categoria="carregamento",
        titulo="Carregadores públicos em João Pessoa e região metropolitana",
        conteudo=(
            "João Pessoa conta com infraestrutura de recarga em plena expansão: há carregadores "
            "no Manaíra Shopping, Mangabeira Shopping, supermercados, concessionárias e postos "
            "nas avenidas Epitácio Pessoa, Rui Carneiro e BR-230, além de carregadores nos hotéis "
            "de Tambaú e Cabo Branco. Pelo aplicativo PlugShare é possível ver todos os pontos "
            "disponíveis, tipo de conector e ocupação em tempo real."
        ),
        palavras_chave=(
            "carregador publico joao pessoa manaira shopping mangabeira epitacio cabo branco "
            "tambau orla posto de recarga eletroposto plugshare"
        ),
    ),
    dict(
        topico="custo_manutencao",
        categoria="objecao",
        titulo="Custo de manutenção e revisões de veículos elétricos",
        conteudo=(
            "A manutenção preventiva de um elétrico é até 70% mais barata que a de um veículo a "
            "combustão similar. O motor elétrico possui apenas uma parte móvel principal e "
            "dispensa troca de óleo, filtro de óleo, filtro de combustível, velas de ignição, "
            "correia dentada ou escapamento. As revisões periódicas checam basicamente o filtro de "
            "cabine do ar-condicionado, fluido de freio e suspensão. Graças à frenagem "
            "regenerativa, as pastilhas e discos de freio duram até o dobro do tempo."
        ),
        palavras_chave=(
            "manutencao revisao custo pecas cara caro oficina troca de oleo filtro "
            "correia pastilha freio regenerativo"
        ),
    ),
    dict(
        topico="custo_por_km_economia",
        categoria="objecao",
        titulo="Economia de combustível e custo por km rodado",
        conteudo=(
            "O custo por km rodado em um carro elétrico é de cerca de 1/4 a 1/5 do custo da "
            "gasolina. Com a tarifa de energia da Energisa na Paraíba (cerca de R$ 0,85 a "
            "R$ 0,95 por kWh), rodar 100 km consome aproximadamente R$ 12 a R$ 15 em energia "
            "(cerca de R$ 0,13 a R$ 0,15 por km), enquanto um carro a combustão médio gasta mais "
            "de R$ 0,60 por km com gasolina. Para quem tem placas solares em casa, o custo é "
            "praticamente zero."
        ),
        palavras_chave=(
            "custo por km economia gasolina combustivel preco energia energisa conta de luz "
            "tarifa solar kwh gastar mais caro que combustao"
        ),
    ),
]


def semear_conhecimento(sessao: Session) -> int:
    """Semeia a base curada de conhecimento de forma idempotente."""
    sessao.execute(
        insert(ItemConhecimento)
        .values(ITENS_CONHECIMENTO)
        .on_conflict_do_nothing(index_elements=["topico"])
    )
    sessao.commit()
    return int(sessao.query(ItemConhecimento).count())


def _normalizar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFD", texto.lower())
    sem_acento = "".join(c for c in decomposto if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", " ", sem_acento)


def _tokens(texto: str) -> list[str]:
    normal = _normalizar(texto)
    return [p for p in normal.split() if len(p) >= 3 and p not in STOPWORDS]


def _pontuar(item: ItemConhecimento, termos_busca: Sequence[str], texto_busca_normal: str) -> int:
    score = 0
    topico_norm = _normalizar(item.topico)
    titulo_norm = _normalizar(item.titulo)
    chaves_norm = _normalizar(item.palavras_chave)
    conteudo_norm = _normalizar(item.conteudo)

    if texto_busca_normal and len(texto_busca_normal) >= 4:
        if texto_busca_normal in chaves_norm:
            score += 25
        if texto_busca_normal in titulo_norm:
            score += 20
        if texto_busca_normal in conteudo_norm:
            score += 10

    for termo in termos_busca:
        if termo in topico_norm:
            score += 15
        if termo in chaves_norm:
            score += 10
        if termo in titulo_norm:
            score += 8
        if termo in conteudo_norm:
            score += 3

    return score


def buscar_conhecimento(sessao: Session, termo: str, limite: int = 3) -> list[dict[str, object]]:
    """Busca na base de conhecimento curada.

    Devolve até `limite` tópicos mais relevantes, ordenados por pontuação.
    Se nada for encontrado ou a busca for vazia, devolve lista vazia.
    """
    texto_limpo = termo.strip()
    if not texto_limpo:
        return []

    tokens_busca = _tokens(texto_limpo)
    texto_norm = _normalizar(texto_limpo)

    itens = list(sessao.scalars(select(ItemConhecimento)))
    if not itens:
        return []

    pontuados: list[tuple[int, ItemConhecimento]] = []
    for item in itens:
        pts = _pontuar(item, tokens_busca, texto_norm)
        if pts > 0:
            pontuados.append((pts, item))

    pontuados.sort(key=lambda par: (par[0], par[1].topico), reverse=True)

    return [
        {
            "topico": item.topico,
            "categoria": item.categoria,
            "titulo": item.titulo,
            "conteudo": item.conteudo,
        }
        for _, item in pontuados[:limite]
    ]
