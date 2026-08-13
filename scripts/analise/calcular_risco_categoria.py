"""
Calcula o risco/faixa de depreciacao real por categoria de veiculo a
partir do historico de precos ja importado (fipe_historico_precos), pra
substituir os valores heuristicos inventados (5%/10%/18%/15%) que hoje
existem no simulador.

Classificacao em categoria (sem usar lista de marca, pra evitar viés
subjetivo):
  - eletrico: qualquer linha cujo campo combustivel seja "Eletrico"
  - popular / intermediario / premium: quartis do preco mais recente de
    cada veiculo (codigo_fipe, ano_modelo, combustivel), calculados
    sobre a populacao NAO-eletrica (o eletrico ja sai como categoria
    propria, entao nao faz sentido ele influenciar os quartis de preco
    dos veiculos a combustao/hibridos)
      popular:       25% mais baratos (valor <= P25)
      intermediario: 50% do meio       (P25 < valor <= P75)
      premium:       25% mais caros    (valor > P75)

Risco (variacao real) por categoria:
  Para cada veiculo com 2+ meses de historico de preco, calcula o
  coeficiente de variacao (desvio padrao / media, ao longo dos meses
  disponiveis) e a amplitude percentual (max-min)/media. O risco
  exportado e a media do coeficiente de variacao dos veiculos da
  categoria; a amplitude e calculada em paralelo so como cross-check
  (fica registrada em docs/METODOLOGIA_RISCO.md).

So entram nesse calculo de variacao os veiculos que tem historico real
de 2+ meses — a maior parte do catalogo (importado via CSV unico de
agosto/2026) tem so 1 mes e por isso nao entra nessa parte da conta
(entra na definicao dos quartis de preco, mas nao no calculo de risco).

Uso:
    python scripts/analise/calcular_risco_categoria.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "processed" / "indice_gsa.duckdb"
OUTPUT_JSON_PATH = BASE_DIR / "data" / "processed" / "risco_categoria.json"
OUTPUT_DOC_PATH = BASE_DIR / "docs" / "METODOLOGIA_RISCO.md"

MESES_PT = {
    "janeiro": 1,
    "fevereiro": 2,
    "março": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

CATEGORIAS = ["popular", "intermediario", "premium", "eletrico"]

# Limites de sanidade pro criterio de aceite: se algum risco calculado
# cair fora disso, o script para em vez de exportar um numero absurdo.
RISCO_MIN_PLAUSIVEL = 0.001
RISCO_MAX_PLAUSIVEL = 0.40


def mes_para_chave(mes_referencia: str) -> int:
    """Converte 'agosto de 2026' em 202608 (ordenavel cronologicamente)."""
    nome_mes, ano = mes_referencia.strip().split(" de ")
    return int(ano) * 100 + MESES_PT[nome_mes.strip().lower()]


def carregar_historico(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = con.execute(
        """
        SELECT codigo_fipe, ano_modelo, combustivel, mes_referencia, valor
        FROM fipe_historico_precos
        """
    ).df()
    df["mes_chave"] = df["mes_referencia"].map(mes_para_chave)
    df["veiculo_id"] = (
        df["codigo_fipe"] + "|" + df["ano_modelo"].astype(str) + "|" + df["combustivel"]
    )
    return df


def preco_mais_recente_por_veiculo(df: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por veiculo, com o valor do mes_chave mais alto."""
    idx = df.groupby("veiculo_id")["mes_chave"].idxmax()
    return df.loc[idx, ["veiculo_id", "combustivel", "valor"]].reset_index(drop=True)


def classificar_categoria(precos: pd.DataFrame) -> pd.DataFrame:
    precos = precos.copy()
    eh_eletrico = precos["combustivel"].str.strip().str.lower() == "elétrico"

    nao_eletrico = precos.loc[~eh_eletrico, "valor"]
    p25 = nao_eletrico.quantile(0.25)
    p75 = nao_eletrico.quantile(0.75)

    def rotular(row):
        if eh_eletrico.loc[row.name]:
            return "eletrico"
        if row["valor"] <= p25:
            return "popular"
        if row["valor"] <= p75:
            return "intermediario"
        return "premium"

    precos["categoria"] = precos.apply(rotular, axis=1)
    return precos, p25, p75


def calcular_variacao_por_veiculo(df: pd.DataFrame) -> pd.DataFrame:
    """CV (std/media) e amplitude ((max-min)/media) por veiculo, so pra
    quem tem 2+ meses distintos de historico."""
    agrupado = df.groupby("veiculo_id")["valor"]
    contagem = agrupado.transform("count")
    historico_real = df[contagem >= 2].copy()

    stats = historico_real.groupby("veiculo_id")["valor"].agg(
        media="mean", desvio="std", minimo="min", maximo="max", n_meses="count"
    )
    stats["cv"] = stats["desvio"] / stats["media"]
    stats["amplitude_pct"] = (stats["maximo"] - stats["minimo"]) / stats["media"]
    return stats.reset_index()


def main() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        df = carregar_historico(con)
    finally:
        con.close()

    total_linhas = len(df)
    total_veiculos = df["veiculo_id"].nunique()
    meses_distintos = sorted(df["mes_chave"].unique())
    n_meses = len(meses_distintos)

    precos_atuais = preco_mais_recente_por_veiculo(df)
    precos_atuais, p25, p75 = classificar_categoria(precos_atuais)

    variacao_veiculo = calcular_variacao_por_veiculo(df)

    categoria_por_veiculo = precos_atuais.set_index("veiculo_id")["categoria"]
    variacao_veiculo["categoria"] = variacao_veiculo["veiculo_id"].map(categoria_por_veiculo)

    resumo_categoria = precos_atuais.groupby("categoria")["veiculo_id"].count().to_dict()
    resumo_variacao = variacao_veiculo.groupby("categoria").agg(
        n_veiculos_com_historico=("veiculo_id", "count"),
        cv_medio=("cv", "mean"),
        amplitude_media=("amplitude_pct", "mean"),
    )

    risco = {}
    for categoria in CATEGORIAS:
        if categoria not in resumo_variacao.index:
            raise RuntimeError(
                f"Categoria '{categoria}' nao tem nenhum veiculo com 2+ meses de "
                "historico — nao da pra calcular risco real pra ela. Rode de novo "
                "quando houver mais meses de historico acumulados."
            )
        valor = float(resumo_variacao.loc[categoria, "cv_medio"])
        if not (RISCO_MIN_PLAUSIVEL <= valor <= RISCO_MAX_PLAUSIVEL):
            raise RuntimeError(
                f"Risco calculado para '{categoria}' = {valor:.4f} esta fora da faixa "
                f"plausivel [{RISCO_MIN_PLAUSIVEL}, {RISCO_MAX_PLAUSIVEL}] — "
                "investigar antes de exportar (nao vou gravar um numero absurdo)."
            )
        risco[categoria] = round(valor, 4)

    OUTPUT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(risco, f, ensure_ascii=False, indent=2)
        f.write("\n")

    agora = datetime.now(timezone.utc)
    escrever_metodologia(
        agora=agora,
        total_linhas=total_linhas,
        total_veiculos=total_veiculos,
        n_meses=n_meses,
        meses_distintos=meses_distintos,
        p25=p25,
        p75=p75,
        resumo_categoria=resumo_categoria,
        resumo_variacao=resumo_variacao,
        risco=risco,
    )

    print("===== Risco por categoria calculado =====")
    for categoria in CATEGORIAS:
        n_total = resumo_categoria.get(categoria, 0)
        n_hist = int(resumo_variacao.loc[categoria, "n_veiculos_com_historico"])
        print(
            f"{categoria:14s} risco={risco[categoria]:.4f}  "
            f"(veiculos na categoria: {n_total}, com historico real: {n_hist})"
        )
    print(f"\nGravado em: {OUTPUT_JSON_PATH}")
    print(f"Metodologia em: {OUTPUT_DOC_PATH}")


def chave_para_mes_legivel(chave: int) -> str:
    ano = chave // 100
    mes_num = chave % 100
    nome = {v: k for k, v in MESES_PT.items()}[mes_num]
    return f"{nome} de {ano}"


def formatar_brl(valor: float) -> str:
    """Formata no padrao brasileiro: milhar com ponto, decimal com virgula."""
    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {texto}"


def escrever_metodologia(
    *,
    agora: datetime,
    total_linhas: int,
    total_veiculos: int,
    n_meses: int,
    meses_distintos: list[int],
    p25: float,
    p75: float,
    resumo_categoria: dict,
    resumo_variacao: pd.DataFrame,
    risco: dict,
) -> None:
    data_calculo = agora.strftime("%Y-%m-%d %H:%M UTC")
    primeiro_mes = chave_para_mes_legivel(meses_distintos[0])
    ultimo_mes = chave_para_mes_legivel(meses_distintos[-1])

    blocos = []
    blocos.append("# Metodologia do Risco de Depreciação por Categoria")
    blocos.append(
        f"Calculado em: **{data_calculo}**, pelo script "
        "`scripts/analise/calcular_risco_categoria.py`."
    )
    blocos.append(
        "Esses números substituem os valores heurísticos inventados que o "
        "simulador usava antes (5%/10%/18%/15%) por uma variação real de preço, "
        "calculada a partir do histórico de preços FIPE já importado neste "
        "projeto (tabela `fipe_historico_precos`)."
    )

    blocos.append("## Como cada veículo foi classificado em categoria")
    blocos.append(
        "A classificação **não usa lista de marca** — isso evitaria viés subjetivo "
        "sobre o que é \"popular\" ou \"premium\". Em vez disso:"
    )
    blocos.append(
        "1. **Elétrico**: qualquer combinação código FIPE/ano/combustível cujo "
        "campo `combustivel` seja `Elétrico` vira, sozinha, a categoria "
        "`eletrico`.\n"
        "2. Para todos os outros (Gasolina, Diesel, Flex, Álcool, Híbrido, Gás "
        "Natural), pegamos o **preço mais recente de cada veículo** e calculamos "
        "os quartis dessa distribuição de preço:\n"
        f"   - **popular**: preço ≤ {formatar_brl(p25)} (25% mais baratos)\n"
        f"   - **intermediario**: {formatar_brl(p25)} < preço ≤ {formatar_brl(p75)} "
        "(50% do meio)\n"
        f"   - **premium**: preço > {formatar_brl(p75)} (25% mais caros)"
    )
    blocos.append(
        "Os quartis são calculados só sobre os veículos não-elétricos — como o "
        "elétrico já vira categoria própria, não faz sentido deixá-lo puxar os "
        "limites de preço dos veículos a combustão/híbridos."
    )

    tabela_qtd = [
        "## Quantos veículos em cada categoria",
        "",
        "| Categoria | Veículos na categoria (preço mais recente) | Veículos com histórico real (2+ meses) usados no cálculo de risco |",
        "|---|---|---|",
    ]
    for categoria in CATEGORIAS:
        n_total = resumo_categoria.get(categoria, 0)
        n_hist = int(resumo_variacao.loc[categoria, "n_veiculos_com_historico"])
        tabela_qtd.append(f"| {categoria} | {n_total} | {n_hist} |")
    blocos.append("\n".join(tabela_qtd))

    blocos.append("## Como o risco (variação real de preço) foi calculado")
    blocos.append(
        "Para cada veículo que tem **2 ou mais meses distintos** de preço no "
        "histórico, calculamos:\n\n"
        "- **Coeficiente de variação (CV)**: desvio padrão do preço ao longo dos "
        "meses disponíveis, dividido pela média do preço desse veículo no "
        "período. É o número usado no `risco_categoria.json`.\n"
        "- **Amplitude percentual**: (preço máximo − preço mínimo) / preço médio, "
        "calculada em paralelo só como conferência cruzada do CV."
    )
    blocos.append(
        "O risco de cada categoria é a **média do CV dos veículos daquela "
        "categoria** que têm histórico real. Veículos com um único mês de dado "
        "(a maior parte do catálogo, importada de uma vez via CSV de agosto/2026) "
        "entram na definição da categoria (pelo preço), mas não entram nessa "
        "conta — não têm variação pra medir."
    )

    tabela_resultado = [
        "## Resultado",
        "",
        "| Categoria | Risco (CV médio) | Amplitude média (cross-check) |",
        "|---|---|---|",
    ]
    for categoria in CATEGORIAS:
        cv = risco[categoria]
        amp = float(resumo_variacao.loc[categoria, "amplitude_media"])
        tabela_resultado.append(f"| {categoria} | {cv * 100:.2f}% | {amp * 100:.2f}% |")
    blocos.append("\n".join(tabela_resultado))

    blocos.append(
        f"JSON exportado: `data/processed/risco_categoria.json`\n\n"
        "```json\n" + json.dumps(risco, ensure_ascii=False, indent=2) + "\n```"
    )

    blocos.append(
        "## Período de histórico usado\n\n"
        f"- {n_meses} meses de referência distintos disponíveis no banco, de "
        f"**{primeiro_mes}** a **{ultimo_mes}**.\n"
        f"- {total_linhas} linhas de preço no total, para {total_veiculos} "
        "combinações distintas de código FIPE/ano-modelo/combustível."
    )

    blocos.append(
        "## Limitações — seja honesto sobre isso ao usar o número\n\n"
        "- **O histórico real ainda é curto.** A maior parte do catálogo "
        "(~48 mil veículos) foi importada de uma vez só via CSV completo da FIPE "
        "referente a um único mês (agosto/2026) — para esses veículos não existe "
        "ainda variação mês a mês pra medir. Só um subconjunto de cerca de 2.200 "
        "veículos foi acompanhado mês a mês (a maioria com os 12 meses de "
        "setembro/2025 a agosto/2026, via ingestão pela API do plano pago), e é "
        "só esse subconjunto que entra no cálculo do risco.\n"
        "- **A categoria elétrico tem a menor amostra com histórico real** "
        f"({int(resumo_variacao.loc['eletrico', 'n_veiculos_com_historico'])} "
        "veículos) — o número tende a ser mais instável que o das outras "
        "categorias e deve ser revisado quando houver mais meses acumulados.\n"
        "- Os quartis de preço usam o preço **mais recente disponível** de cada "
        "veículo, que na prática é o mês do CSV mais recente pra quase todo mundo "
        "— não há problema de mistura de meses diferentes nessa parte da conta.\n"
        "- Conforme a ingestão mensal (`scripts/ingestao/fipe_historico.py` / "
        "`fipe_importar_csv.py`) for acumulando mais meses, este script deve ser "
        "rodado de novo para refinar os números — o risco calculado aqui tende a "
        "ficar mais confiável com mais meses de dado real."
    )

    OUTPUT_DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_DOC_PATH, "w", encoding="utf-8") as f:
        f.write("\n\n".join(blocos) + "\n")


if __name__ == "__main__":
    main()
