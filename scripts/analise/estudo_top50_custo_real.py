"""
Custo Real de Posse (12 meses) dos 50 modelos mais vendidos do Brasil,
cruzando fontes ja integradas: FIPE (deprecicao real), ANP (combustivel),
IBGE (manutencao). SUSEP nao entra na soma (a tarefa pediu "somar os
tres": deprecicao + combustivel + manutencao) -- fica de fora tambem da
interpretacao, pra nao forcar um vinculo modelo-a-categoria que nao
existe de forma objetiva em nenhuma fonte integrada (ver docs/
ESTUDO_TOP50_CUSTO_REAL.md, secao de metodologia).

Le data/processed/proxy_frota_locadora.json (os 50 modelos +
codigos_fipe, ja calculado em scripts/analise/proxy_frota_locadora.py)
e cruza com fipe_historico_precos, anp_precos_combustivel e
ibge_manutencao_veiculos.

===== DEPRECIACAO (dado real, sem premissa) =====
Bug real encontrado e corrigido antes de aceitar os numeros: os
codigos_fipe de cada modelo (vindos de proxy_frota_locadora.json) cobrem
TODO o historico do nome comercial no catalogo FIPE, nao so a geracao
atual -- o Strada, por exemplo, tem trims catalogados de 1999 a 2027
(mais um sentinela `ano_modelo=32000`, convencao da FIPE pra "0km/ano
corrente"). Calcular a mediana de preco sobre essas ~29 safras dava um
preco de Strada "tipico" de uma unidade de ~2012 (R$ 50 mil), nao o
Strada que se compra hoje -- silenciosamente errado, pego so ao
inspecionar o resultado antes de escrever o estudo. Por isso o calculo
abaixo restringe cada modelo aos 2 anos-modelo mais recentes que tem no
catalogo (mais o sentinela 32000, tambem carro nao-usado) antes de
qualquer outra coisa.

Pra cada modelo, olha todas as combinacoes (codigo_fipe, ano_modelo,
combustivel) que aparecem nos codigos_fipe daquele modelo, ja
restritas ao ano-modelo recente acima. Restrito tambem aos
12 MESES CANONICOS da ingestao mensal regular (setembro/2025 a
agosto/2026) -- a tabela tambem tem 5 meses esparsos de testes do robo
de backfill (abril/2022, abril/2023, abril/2024, abril/2025, agosto/2025)
que NAO cobrem o catalogo inteiro e foram excluidos explicitamente pra
nao enviesar o calculo.
Pra cada combinacao: pega o primeiro e o ultimo mes com preco DENTRO
desses 12 meses canonicos (pode ser menos de 12 meses pra lancamentos
recentes -- Tera, EX2, Kait, Jaecoo 7, Omoda 5 sao exemplos que nao
existiam em setembro/2025), calcula a variacao percentual observada
nesse periodo, e registra quantos meses foram realmente observados. O
numero final do modelo e a MEDIANA dessa variacao entre todas as suas
combinacoes (robusto a versao/trim isolado com comportamento atipico).
`meses_observados` no resultado avisa quando o periodo e mais curto que
12 meses -- nesses casos o numero NAO e anualizado (evita extrapolar
uma tendencia de poucos meses como se fosse um ano inteiro).

===== COMBUSTIVEL (preco real da ANP + consumo assumido, ver limitacoes) =====
O tipo de combustivel predominante de cada modelo (moda entre as
combinacoes acima) decide qual preco da ANP usar (media nacional mais
recente disponivel): Gasolina/Flex -> GASOLINA COMUM; Alcool -> ETANOL
HIDRATADO; Diesel -> OLEO DIESEL S10. Eletricos ficam com custo de
combustivel NAO CALCULADO (a ANP nao cobre eletricidade -- exigiria
tarifa da ANEEL, que esta integrada no projeto mas NAO foi listada como
fonte pra esta tarefa, entao nao foi usada aqui pra nao extrapolar o
escopo pedido).
O consumo em km/l NAO existe cruzado por modelo nas fontes listadas
pra esta tarefa (existe INMETRO/PBEV integrado no projeto, mas o
cruzamento com FIPE ali e por nome de texto, nao por codigo_fipe direto
-- usa-lo aqui empataria dois cruzamentos fuzzy em cadeia, o que reduz a
rastreabilidade do numero final; nao foi usado por isso). Em vez disso,
CONSUMO_KM_L abaixo e uma premissa de mercado por TIPO de combustivel
(nao por modelo especifico) -- mesmo tratamento que a tarefa ja da a
"12.000 km/ano": um numero-padrao assumido e citado como tal, nao uma
medicao.

===== MANUTENCAO (unica premissa que nao vem de fonte integrada) =====
ibge_manutencao_veiculos so tem VARIACAO PERCENTUAL (inflacao de pecas/
acessorios e conserto), nao um custo absoluto -- nao existe, em nenhuma
fonte integrada, um R$/ano de manutencao por veiculo. Pra chegar num
numero em R$, e preciso de uma base, que a IBGE nao fornece. Usamos
BASELINE_MANUTENCAO_PCT_ANO (3% do valor do veiculo ao ano -- citado
com frequencia como regra pratica de mercado pra TCO automotivo no
Brasil) como base, ajustada pela variacao acumulada em 12 meses do IBGE
(media nacional entre os dois subitens e todas as regioes metropolitanas
pesquisadas). Essa base e a UNICA premissa deste estudo que nao vem de
nenhuma fonte ja integrada -- todas as outras (km/ano, consumo por tipo
de combustivel) sao convencoes de mercado, nao afirmacoes de fato.

Grava em data/processed/estudo_top50_custo_real.json.

Uso:
    python scripts/analise/estudo_top50_custo_real.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import duckdb

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "processed" / "indice_gsa.duckdb"
INPUT_PATH = BASE_DIR / "data" / "processed" / "proxy_frota_locadora.json"
OUTPUT_PATH = BASE_DIR / "data" / "processed" / "estudo_top50_custo_real.json"

KM_ANO = 12_000  # padrao de mercado, conforme pedido na tarefa

MESES_CANONICOS_12 = [
    "setembro de 2025", "outubro de 2025", "novembro de 2025", "dezembro de 2025",
    "janeiro de 2026", "fevereiro de 2026", "março de 2026", "abril de 2026",
    "maio de 2026", "junho de 2026", "julho de 2026", "agosto de 2026",
]
ORDEM_MES = {m: i for i, m in enumerate(MESES_CANONICOS_12)}

# combustivel FIPE -> (tipo_combustivel ANP, consumo assumido km/l)
# "padrao de mercado" citado, nao medicao -- ver docstring do modulo.
CONSUMO_POR_COMBUSTIVEL = {
    "Gasolina": ("GASOLINA COMUM", 12.0),
    "Flex": ("GASOLINA COMUM", 12.0),
    "Álcool": ("ETANOL HIDRATADO", 8.0),
    "Diesel": ("OLEO DIESEL S10", 9.0),
    "Híbrido": ("GASOLINA COMUM", 18.0),
    "Elétrico": (None, None),
}

BASELINE_MANUTENCAO_PCT_ANO = 0.03  # unica premissa fora das fontes integradas


def carregar_precos_nacionais_anp(con: duckdb.DuckDBPyConnection) -> dict[str, float]:
    semana_mais_recente = con.execute("SELECT MAX(semana_referencia) FROM anp_precos_combustivel").fetchone()[0]
    rows = con.execute(
        """
        SELECT tipo_combustivel, AVG(preco_medio)
        FROM anp_precos_combustivel
        WHERE semana_referencia = ?
        GROUP BY tipo_combustivel
        """,
        [semana_mais_recente],
    ).fetchall()
    print(f"[info] ANP: preco nacional medio da semana de {semana_mais_recente}")
    return {tipo: preco for tipo, preco in rows}


def carregar_variacao_ibge_nacional(con: duckdb.DuckDBPyConnection) -> float:
    mes_mais_recente = con.execute("SELECT MAX(mes_referencia) FROM ibge_manutencao_veiculos").fetchone()[0]
    media = con.execute(
        "SELECT AVG(variacao_acumulada_12m_percentual) FROM ibge_manutencao_veiculos WHERE mes_referencia = ?",
        [mes_mais_recente],
    ).fetchone()[0]
    print(f"[info] IBGE: variacao acumulada 12m nacional de {mes_mais_recente} = {media:.2f}%")
    return media


def calcular_depreciacao_modelo(con: duckdb.DuckDBPyConnection, codigos_fipe: list[str]):
    placeholders = ",".join(["?"] * len(codigos_fipe))
    rows = con.execute(
        f"""
        SELECT codigo_fipe, ano_modelo, combustivel, mes_referencia, valor
        FROM fipe_historico_precos
        WHERE codigo_fipe IN ({placeholders})
          AND mes_referencia IN ({",".join(["?"] * len(MESES_CANONICOS_12))})
        """,
        [*codigos_fipe, *MESES_CANONICOS_12],
    ).fetchall()

    por_combo: dict[tuple, dict[str, float]] = {}
    for codigo_fipe, ano_modelo, combustivel, mes_ref, valor in rows:
        chave = (codigo_fipe, ano_modelo, combustivel)
        por_combo.setdefault(chave, {})[mes_ref] = valor

    # Os codigos_fipe de cada modelo cobrem TODA a historia do nome
    # comercial no catalogo FIPE (ex.: Strada tem trims de 1999 a 2027 +
    # o sentinela 32000 = "0km/ano corrente"), nao so a geracao atual.
    # Sem filtrar por ano_modelo recente, a mediana de preco cai no meio
    # dessa faixa de decadas (um Strada "tipico" de ~2012, nao o Strada
    # que se compra hoje) -- catch real encontrado inspecionando o
    # resultado antes de aceitar os numeros. Restringe aos 2 anos-modelo
    # mais recentes de cada modelo (+ o sentinela 32000, tambem "carro
    # novo"), representando o que esta a venda como zero/seminovo agora.
    anos_reais = [ano for (_, ano, _) in por_combo if ano < 3000]
    if not anos_reais:
        return None
    ano_mais_recente = max(anos_reais)
    por_combo = {
        chave: precos
        for chave, precos in por_combo.items()
        if chave[1] >= ano_mais_recente - 1 or chave[1] >= 3000
    }

    variacoes_pct = []
    precos_atuais = []
    meses_observados_lista = []
    combustiveis_vistos = []

    for (codigo_fipe, ano_modelo, combustivel), precos_por_mes in por_combo.items():
        meses_disponiveis = sorted(precos_por_mes.keys(), key=lambda m: ORDEM_MES[m])
        if len(meses_disponiveis) < 2:
            continue
        primeiro_mes, ultimo_mes = meses_disponiveis[0], meses_disponiveis[-1]
        preco_inicial = precos_por_mes[primeiro_mes]
        preco_final = precos_por_mes[ultimo_mes]
        if preco_inicial <= 0:
            continue

        variacao_pct = (preco_final - preco_inicial) / preco_inicial * 100
        variacoes_pct.append(variacao_pct)
        precos_atuais.append(preco_final)
        meses_observados_lista.append(ORDEM_MES[ultimo_mes] - ORDEM_MES[primeiro_mes] + 1)
        combustiveis_vistos.append(combustivel)

    if not variacoes_pct:
        return None

    combustivel_predominante = statistics.mode(combustiveis_vistos)

    return {
        # variacao_preco_pct_observada e a variacao BRUTA de preco (negativo =
        # preco caiu = perda real de valor = CUSTO pro dono). Convertido pra
        # "depreciacao" (convencao intuitiva: numero positivo = valor perdido)
        # so na hora de montar o resultado final, em main() -- ver comentario
        # la sobre o bug de sinal que isso corrigiu.
        "variacao_preco_pct_observada": statistics.median(variacoes_pct),
        "preco_atual_rs": statistics.median(precos_atuais),
        "meses_observados": min(meses_observados_lista),
        "qtd_combinacoes_usadas": len(variacoes_pct),
        "combustivel_predominante": combustivel_predominante,
    }


def main() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    dados_proxy = json.load(open(INPUT_PATH, encoding="utf-8"))

    precos_anp = carregar_precos_nacionais_anp(con)
    variacao_ibge_nacional = carregar_variacao_ibge_nacional(con)

    resultados = []
    sem_dado_suficiente = []

    for modelo_info in dados_proxy["modelos"]:
        nome_modelo = modelo_info["modelo"]
        marca = modelo_info["marca"]
        codigos_fipe = modelo_info["codigos_fipe"]

        dep = calcular_depreciacao_modelo(con, codigos_fipe)
        if dep is None:
            sem_dado_suficiente.append((nome_modelo, marca))
            continue

        preco_atual = dep["preco_atual_rs"]
        combustivel = dep["combustivel_predominante"]
        tipo_anp, consumo_km_l = CONSUMO_POR_COMBUSTIVEL.get(combustivel, (None, None))

        if tipo_anp is not None and consumo_km_l is not None:
            preco_combustivel_l = precos_anp.get(tipo_anp)
            litros_ano = KM_ANO / consumo_km_l
            custo_combustivel_anual = litros_ano * preco_combustivel_l
        else:
            preco_combustivel_l = None
            custo_combustivel_anual = None

        custo_manutencao_anual = preco_atual * BASELINE_MANUTENCAO_PCT_ANO * (1 + variacao_ibge_nacional / 100)
        # Sinal invertido de proposito: preco caindo (variacao negativa) e
        # PERDA DE VALOR REAL pro dono do carro -- um CUSTO, nao uma reducao
        # de custo. Bug real encontrado nesta rodada (Compass e EX2
        # apareciam com "custo total negativo" porque foram os que MAIS
        # depreciaram, nao os que menos -- o sinal original tratava queda de
        # preco como se fosse dinheiro entrando). depreciacao_pct aqui e
        # POSITIVO quando o carro perde valor (convencao intuitiva de
        # "depreciacao"), negativo so no caso raro de valorizacao real.
        depreciacao_pct = -dep["variacao_preco_pct_observada"]
        custo_depreciacao_anual_rs = preco_atual * depreciacao_pct / 100

        componentes_rs = [custo_depreciacao_anual_rs, custo_manutencao_anual]
        if custo_combustivel_anual is not None:
            componentes_rs.append(custo_combustivel_anual)
        custo_total_anual_rs = sum(componentes_rs)
        custo_total_pct_preco = custo_total_anual_rs / preco_atual * 100

        resultados.append(
            {
                "rank_fenabrave": modelo_info["rank_fenabrave"],
                "modelo": nome_modelo,
                "marca": marca,
                "categoria": modelo_info["categoria"],
                "unidades_emplacadas": modelo_info["unidades_emplacadas"],
                "preco_atual_rs": round(preco_atual, 2),
                "meses_observados_depreciacao": dep["meses_observados"],
                "qtd_combinacoes_fipe_usadas": dep["qtd_combinacoes_usadas"],
                "depreciacao_pct_observada": round(depreciacao_pct, 2),
                "custo_depreciacao_anual_rs": round(custo_depreciacao_anual_rs, 2),
                "combustivel_predominante": combustivel,
                "consumo_assumido_km_l": consumo_km_l,
                "preco_combustivel_anp_rs_l": round(preco_combustivel_l, 3) if preco_combustivel_l else None,
                "custo_combustivel_anual_rs": round(custo_combustivel_anual, 2) if custo_combustivel_anual else None,
                "custo_manutencao_anual_rs": round(custo_manutencao_anual, 2),
                "custo_total_anual_rs": round(custo_total_anual_rs, 2),
                "custo_total_pct_preco": round(custo_total_pct_preco, 2),
            }
        )

    con.close()

    resultados.sort(key=lambda r: r["custo_total_pct_preco"])

    if sem_dado_suficiente:
        print(f"[aviso] {len(sem_dado_suficiente)} modelo(s) sem dado suficiente de preco (menos de 2 meses):")
        for nome, marca in sem_dado_suficiente:
            print(f"   - {nome} ({marca})")

    saida = {
        "premissas": {
            "km_ano": KM_ANO,
            "consumo_por_combustivel_km_l": CONSUMO_POR_COMBUSTIVEL,
            "baseline_manutencao_pct_ano": BASELINE_MANUTENCAO_PCT_ANO,
            "ibge_variacao_acumulada_12m_nacional_pct": round(variacao_ibge_nacional, 2),
            "precos_anp_nacionais_rs_l": {k: round(v, 3) for k, v in precos_anp.items()},
        },
        "modelos_sem_dado_suficiente": [{"modelo": n, "marca": m} for n, m in sem_dado_suficiente],
        "ranking": resultados,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)

    print(f"\n[info] {len(resultados)} modelos calculados, {len(sem_dado_suficiente)} sem dado suficiente")
    print(f"[info] gravado em {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
