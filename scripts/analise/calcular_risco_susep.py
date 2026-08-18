"""
Cruza sinistralidade e roubo/furto real (SUSEP: AUTOSEG + IVR) com a
classificacao de categoria de veiculo (popular/intermediario/premium/
eletrico) ja usada em calcular_risco_categoria.py -- pra comparar com o
risco de preco (variacao FIPE) ja calculado la.

NAO mexe no simulador. So calcula e documenta -- decidir como (ou se)
combinar risco de preco + risco de sinistro fica pra depois, depois de
ver os numeros reais. Ver docs/METODOLOGIA_RISCO_SUSEP.md pro
detalhamento completo, incluindo as aproximacoes necessarias no
cruzamento (a SUSEP nao usa o mesmo recorte de "categoria de veiculo"
que este projeto usa).

Investigacao feita antes de escrever este script (Passo 1 da tarefa):
  - susep_autoseg (COD_TARIF, REGIAO, COD_MODELO, ANO_MODELO, SEXO,
    IDADE, EXPOSICAO1/2, PREMIO1/2, IS_MEDIA, FREQ_SIN1/2/3/4/9,
    INDENIZ1/2/3/4/9, ENVIO) NAO tem uma coluna de "categoria de risco
    cadastral" separada -- COD_TARIF e a categoria TARIFARIA (tipo de
    veiculo: 1=Passeio nacional, 2=Passeio importado, 3=Pick-up,
    4=Carga, 5=Moto, 6=Onibus, 7=Utilitarios, 9=Outros -- de-para em
    auto_cat.csv dentro do zip), nao uma classificacao por faixa de
    preco. FREQ_SIN1..9 sao contagens de sinistro por CAUSA (de-para em
    auto_cau.csv): 1=Roubo ou furto, 2=Colisao parcial, 3=Colisao Perda
    Total, 4=Incendio, 9=Outros -- INDENIZ1..9 e o valor pago pra cada
    causa.
  - COD_MODELO em susep_autoseg BATE DIRETO com codigo_fipe em
    fipe_historico_precos (confirmado: 7.963 de 8.375 codigos distintos,
    95,1%, casam por igualdade exata) -- a propria SUSEP documenta isso
    ("Os codigos de modelos sao os da codificacao padronizada da tabela
    FIPE"). Nao precisou de fuzzy match pro AUTOSEG.
  - susep_ivr.modelo (ex: "GM CHEVROLET ONIX") NAO e codigo FIPE, e um
    nome de modelo agrupado. O de-para real esta em auto2_vei.csv
    (tambem dentro dos zips do AUTOSEG, nao ingerido como tabela --
    CODIGO=codigo FIPE, GRUPO=mesmo nome de modelo agrupado que o IVR
    usa): combinando auto2_vei.csv dos 2 semestres, 495 dos 499 modelos
    do IVR (99,2%) batem por igualdade exata de texto com GRUPO. Os 4
    sem match: "RENAULT OUTROS" (bucket generico, sem modelo unico pra
    casar -- exclusao esperada) e GM CHEVROLET SONIC/SPIN, RENAULT
    DUSTER (nao encontrados em nenhum dos 2 semestres do catalogo
    auto2_vei baixado).

Grava em data/processed/risco_susep_categoria.json.

Uso:
    python scripts/analise/calcular_risco_susep.py
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "processed" / "indice_gsa.duckdb"
RAW_DIR = BASE_DIR / "data" / "raw"
OUTPUT_JSON_PATH = BASE_DIR / "data" / "processed" / "risco_susep_categoria.json"
OUTPUT_DOC_PATH = BASE_DIR / "docs" / "METODOLOGIA_RISCO_SUSEP.md"

AUTOSEG_ZIPS = ["autoseg_2019b.zip", "autoseg_2020a.zip"]
CATEGORIAS = ["popular", "intermediario", "premium", "eletrico"]

# Mesma tabela usada em calcular_risco_categoria.py
MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}


def mes_para_chave(mes_referencia: str) -> int:
    nome_mes, ano = mes_referencia.strip().split(" de ")
    return int(ano) * 100 + MESES_PT[nome_mes.strip().lower()]


def classificar_codigo_fipe_por_categoria(con: duckdb.DuckDBPyConnection):
    """Reproduz a mesma classificacao por quartil de preco de
    calcular_risco_categoria.py (preco mais recente de cada veiculo,
    quartis sobre a populacao nao-eletrica), mas agregada por
    codigo_fipe puro -- o AUTOSEG (COD_MODELO) e o auto2_vei.csv nao tem
    o mesmo recorte por ano-modelo/combustivel que fipe_historico_precos
    usa, entao o preco representativo de cada codigo_fipe aqui e a media
    entre suas variantes de ano/combustivel."""
    df = con.execute(
        "SELECT codigo_fipe, ano_modelo, combustivel, mes_referencia, valor FROM fipe_historico_precos"
    ).df()
    df["mes_chave"] = df["mes_referencia"].map(mes_para_chave)
    df["veiculo_id"] = df["codigo_fipe"] + "|" + df["ano_modelo"].astype(str) + "|" + df["combustivel"]

    idx = df.groupby("veiculo_id")["mes_chave"].idxmax()
    precos_atuais = df.loc[idx, ["codigo_fipe", "combustivel", "valor"]].reset_index(drop=True)

    eh_eletrico = precos_atuais["combustivel"].str.strip().str.lower() == "elétrico"
    p25 = precos_atuais.loc[~eh_eletrico, "valor"].quantile(0.25)
    p75 = precos_atuais.loc[~eh_eletrico, "valor"].quantile(0.75)

    preco_medio_por_codigo = precos_atuais.groupby("codigo_fipe")["valor"].mean()
    tem_variante_eletrica = precos_atuais.groupby("codigo_fipe").apply(
        lambda g: bool((g["combustivel"].str.strip().str.lower() == "elétrico").any()),
        include_groups=False,
    )

    def rotular(codigo: str) -> str:
        if tem_variante_eletrica.get(codigo, False):
            return "eletrico"
        preco = preco_medio_por_codigo[codigo]
        if preco <= p25:
            return "popular"
        if preco <= p75:
            return "intermediario"
        return "premium"

    resultado = pd.DataFrame({"codigo_fipe": preco_medio_por_codigo.index})
    resultado["categoria"] = resultado["codigo_fipe"].map(rotular)
    return resultado, float(p25), float(p75)


def carregar_grupo_veiculo() -> pd.DataFrame:
    """auto2_vei.csv (dentro dos zips do AUTOSEG, nao ingerido como
    tabela no banco -- e so um lookup auxiliar pra esta analise) mapeia
    CODIGO (=codigo FIPE) -> GRUPO (nome de modelo agrupado, a mesma
    granularidade que o IVR usa). Combina os 2 semestres pra maximizar
    cobertura."""
    frames = []
    for zip_name in AUTOSEG_ZIPS:
        zip_path = RAW_DIR / zip_name
        if not zip_path.exists():
            continue
        with zipfile.ZipFile(zip_path) as z, z.open("auto2_vei.csv") as f:
            frames.append(pd.read_csv(io.TextIOWrapper(f, encoding="iso-8859-1"), sep=";"))
    if not frames:
        raise SystemExit(
            "[erro] nenhum zip do AUTOSEG encontrado em data/raw/ -- rode "
            "scripts/ingestao/susep.py primeiro."
        )
    df = pd.concat(frames)
    df.columns = [c.strip() for c in df.columns]
    df["CODIGO"] = df["CODIGO"].str.strip()
    df["GRUPO"] = df["GRUPO"].str.strip()
    return df[["CODIGO", "GRUPO"]].drop_duplicates()


def calcular_sinistralidade_autoseg(con: duckdb.DuckDBPyConnection, categoria_por_codigo: pd.DataFrame) -> pd.DataFrame:
    """Sinistralidade real por categoria a partir do AUTOSEG: soma de
    sinistros (todas as causas, e separado por causa=1/Roubo-furto) sobre
    soma de exposicao (veiculos-ano segurados), restrito a COD_TARIF 1/2
    (Passeio nacional/importado -- a mesma populacao de veiculo de
    passeio que a classificacao popular/intermediario/premium cobre)."""
    con.register("categoria_por_codigo", categoria_por_codigo)
    resultado = con.execute(
        """
        SELECT
            c.categoria,
            SUM(a.EXPOSICAO1) AS exposicao_total,
            SUM(a.FREQ_SIN1) AS freq_roubo_furto,
            SUM(a.FREQ_SIN2 + a.FREQ_SIN3) AS freq_colisao,
            SUM(a.FREQ_SIN4) AS freq_incendio,
            SUM(a.FREQ_SIN9) AS freq_outros,
            SUM(a.FREQ_SIN1 + a.FREQ_SIN2 + a.FREQ_SIN3 + a.FREQ_SIN4 + a.FREQ_SIN9) AS freq_sinistro_total,
            COUNT(DISTINCT a.COD_MODELO) AS codigos_fipe_distintos,
            COUNT(*) AS linhas_autoseg
        FROM susep_autoseg a
        JOIN categoria_por_codigo c ON a.COD_MODELO = c.codigo_fipe
        WHERE a.COD_TARIF IN (1, 2) AND c.categoria IS NOT NULL
        GROUP BY c.categoria
        """
    ).df()
    resultado["sinistralidade_geral_pct"] = 100 * resultado["freq_sinistro_total"] / resultado["exposicao_total"]
    resultado["indice_roubo_furto_pct_autoseg"] = 100 * resultado["freq_roubo_furto"] / resultado["exposicao_total"]
    return resultado


def calcular_roubo_ivr(con: duckdb.DuckDBPyConnection, grupo_veiculo: pd.DataFrame, categoria_por_codigo: pd.DataFrame):
    """Indice de roubo/furto do IVR por categoria: junta susep_ivr.modelo
    com auto2_vei.GRUPO (texto identico) pra achar os codigos FIPE de
    cada grupo, e usa a categoria mais frequente entre esses codigos
    (moda) como a categoria do grupo -- e uma aproximacao (um "grupo" do
    IVR pode ter variantes de mais de uma categoria de preco; nao ha como
    saber, so com o IVR, quanto de "veiculos expostos" veio de cada
    variante especifica). A media do indice por categoria e ponderada
    pelos veiculos_expostos de cada grupo (nao e media simples), pra nao
    deixar um modelo de amostra pequena pesar igual a um modelo grande."""
    juncao = grupo_veiculo.merge(categoria_por_codigo, left_on="CODIGO", right_on="codigo_fipe", how="inner")
    categoria_por_grupo = (
        juncao.groupby("GRUPO")["categoria"]
        .agg(lambda s: s.mode().iloc[0])
        .reset_index()
        .rename(columns={"GRUPO": "modelo"})
    )

    ivr = con.execute(
        "SELECT modelo, indice_roubo_furto_pct, veiculos_expostos, numero_sinistros FROM susep_ivr"
    ).df()
    ivr["modelo"] = ivr["modelo"].str.strip()

    juntado = ivr.merge(categoria_por_grupo, on="modelo", how="left")
    sem_match = juntado[juntado["categoria"].isna()]["modelo"].tolist()
    com_match = juntado.dropna(subset=["categoria"]).copy()

    def agregado(g: pd.DataFrame) -> pd.Series:
        return pd.Series(
            {
                "indice_roubo_furto_pct_ivr": (g["indice_roubo_furto_pct"] * g["veiculos_expostos"]).sum()
                / g["veiculos_expostos"].sum(),
                "veiculos_expostos_ivr": g["veiculos_expostos"].sum(),
                "numero_sinistros_ivr": g["numero_sinistros"].sum(),
                "modelos_ivr": len(g),
            }
        )

    resultado = com_match.groupby("categoria").apply(agregado, include_groups=False).reset_index()
    return resultado, sem_match, len(juntado)


def main() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        categoria_por_codigo, p25, p75 = classificar_codigo_fipe_por_categoria(con)
        print(f"[info] {len(categoria_por_codigo)} codigos FIPE classificados (p25=R${p25:,.2f}, p75=R${p75:,.2f})")

        grupo_veiculo = carregar_grupo_veiculo()
        print(f"[info] {len(grupo_veiculo)} pares codigo/grupo carregados de auto2_vei.csv")

        autoseg_resultado = calcular_sinistralidade_autoseg(con, categoria_por_codigo)
        print("\n===== Sinistralidade AUTOSEG por categoria =====")
        print(autoseg_resultado.to_string(index=False))

        ivr_resultado, ivr_sem_match, ivr_total = calcular_roubo_ivr(con, grupo_veiculo, categoria_por_codigo)
        print("\n===== Roubo/furto IVR por categoria =====")
        print(ivr_resultado.to_string(index=False))
        print(f"\n[info] IVR: {ivr_total - len(ivr_sem_match)}/{ivr_total} modelos com categoria resolvida")
        if ivr_sem_match:
            print(f"[info] sem match: {ivr_sem_match}")
    finally:
        con.close()

    saida: dict[str, dict] = {}
    for categoria in CATEGORIAS:
        linha_autoseg = autoseg_resultado[autoseg_resultado["categoria"] == categoria]
        linha_ivr = ivr_resultado[ivr_resultado["categoria"] == categoria]

        entrada: dict[str, float | int | None] = {
            "sinistralidade_geral_pct": None,
            "indice_roubo_furto_pct_autoseg": None,
            "exposicao_total_autoseg": None,
            "codigos_fipe_distintos_autoseg": None,
            "indice_roubo_furto_pct_ivr": None,
            "veiculos_expostos_ivr": None,
            "modelos_ivr": None,
        }
        if not linha_autoseg.empty:
            r = linha_autoseg.iloc[0]
            entrada["sinistralidade_geral_pct"] = round(float(r["sinistralidade_geral_pct"]), 4)
            entrada["indice_roubo_furto_pct_autoseg"] = round(float(r["indice_roubo_furto_pct_autoseg"]), 4)
            entrada["exposicao_total_autoseg"] = round(float(r["exposicao_total"]), 2)
            entrada["codigos_fipe_distintos_autoseg"] = int(r["codigos_fipe_distintos"])
        if not linha_ivr.empty:
            r = linha_ivr.iloc[0]
            entrada["indice_roubo_furto_pct_ivr"] = round(float(r["indice_roubo_furto_pct_ivr"]), 4)
            entrada["veiculos_expostos_ivr"] = round(float(r["veiculos_expostos_ivr"]), 2)
            entrada["modelos_ivr"] = int(r["modelos_ivr"])

        saida[categoria] = entrada

    OUTPUT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n[info] gravado em {OUTPUT_JSON_PATH}")


if __name__ == "__main__":
    main()
