"""
Gera o primeiro relatorio publicavel do Indice GSA, cruzando FIPE + ANP +
IBGE — os tres dados reais ja ingeridos neste projeto.

Nao e um dump de tabela: calcula tres achados concretos e escreve um
relatorio curto em Markdown com narrativa em cima dos numeros reais.

  1. Estabilidade de preco FIPE — reaproveita a logica de
     scripts/analise/calcular_risco_categoria.py (mesmo calculo de
     coeficiente de variacao por veiculo), agregada num numero geral em
     vez de por categoria.
  2. Combustivel por regiao — ranking de UFs por preco mais recente de
     gasolina e etanol (ANP), comparando as 5 mais caras com as 5 mais
     baratas de cada combustivel.
  3. Manutencao por regiao metropolitana — variacao acumulada em 12
     meses do subitem "Conserto de automovel" do IPCA (IBGE/SIDRA), mes
     mais recente disponivel, comparando regioes acima e abaixo da
     media.

Grava em docs/RELATORIO_INDICE_GSA_{AAAA-MM}.md (mes de geracao).

Uso:
    python scripts/relatorio/gerar_indice_gsa.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "processed" / "indice_gsa.duckdb"
DOCS_DIR = BASE_DIR / "docs"

sys.path.insert(0, str(BASE_DIR / "scripts" / "analise"))
from calcular_risco_categoria import (  # noqa: E402
    carregar_historico,
    calcular_variacao_por_veiculo,
    formatar_brl,
)

CODIGO_SUBITEM_CONSERTO = "7647"  # 5102011.Conserto de automovel


def formatar_pct(valor: float, casas: int = 2) -> str:
    texto = f"{valor:,.{casas}f}"
    texto = texto.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{texto}%"


def formatar_inteiro(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")


def calcular_estabilidade_fipe(con: duckdb.DuckDBPyConnection) -> dict:
    df = carregar_historico(con)
    variacao = calcular_variacao_por_veiculo(df)

    return {
        "cv_medio": float(variacao["cv"].mean()),
        "amplitude_media": float(variacao["amplitude_pct"].mean()),
        "n_veiculos_com_historico": len(variacao),
        "n_veiculos_total": df["veiculo_id"].nunique(),
        "n_meses": df["mes_chave"].nunique(),
    }


def calcular_combustivel_regional(con: duckdb.DuckDBPyConnection) -> dict:
    resultado = {}
    for chave, tipo_combustivel in [("gasolina", "GASOLINA COMUM"), ("etanol", "ETANOL HIDRATADO")]:
        df = con.execute(
            """
            SELECT uf, preco_medio, semana_referencia
            FROM anp_precos_combustivel
            WHERE tipo_combustivel = ?
            QUALIFY semana_referencia = MAX(semana_referencia) OVER (PARTITION BY uf)
            ORDER BY preco_medio DESC
            """,
            [tipo_combustivel],
        ).fetchdf()

        mais_caras = df.head(5).reset_index(drop=True)
        mais_baratas = df.tail(5).sort_values("preco_medio").reset_index(drop=True)
        preco_max = float(df["preco_medio"].max())
        preco_min = float(df["preco_medio"].min())

        resultado[chave] = {
            "mais_caras": mais_caras,
            "mais_baratas": mais_baratas,
            "preco_max": preco_max,
            "preco_min": preco_min,
            "gap_percentual": (preco_max - preco_min) / preco_min,
            "uf_max": mais_caras.loc[0, "uf"],
            "uf_min": mais_baratas.loc[0, "uf"],
            "semana_mais_recente": pd.Timestamp(df["semana_referencia"].max()).strftime("%Y-%m-%d"),
        }
    return resultado


def calcular_manutencao_regional(con: duckdb.DuckDBPyConnection) -> dict:
    mes_mais_recente = con.execute(
        "SELECT MAX(mes_referencia) FROM ibge_manutencao_veiculos WHERE codigo_subitem = ?",
        [CODIGO_SUBITEM_CONSERTO],
    ).fetchone()[0]

    df = con.execute(
        """
        SELECT regiao_metropolitana, variacao_mensal_percentual, variacao_acumulada_12m_percentual
        FROM ibge_manutencao_veiculos
        WHERE codigo_subitem = ? AND mes_referencia = ?
        ORDER BY variacao_acumulada_12m_percentual DESC
        """,
        [CODIGO_SUBITEM_CONSERTO, mes_mais_recente],
    ).fetchdf()

    media_acumulada = float(df["variacao_acumulada_12m_percentual"].mean())

    return {
        "mes_referencia": mes_mais_recente,
        "df": df,
        "media_acumulada_12m": media_acumulada,
        "acima_da_media": df[df["variacao_acumulada_12m_percentual"] > media_acumulada],
        "abaixo_da_media": df[df["variacao_acumulada_12m_percentual"] <= media_acumulada],
        "regiao_maior_alta": df.iloc[0],
        "regiao_menor_alta": df.iloc[-1],
        "n_regioes": len(df),
    }


def tabela_markdown(linhas: list[str], cabecalho: list[str]) -> str:
    saida = ["| " + " | ".join(cabecalho) + " |", "|" + "|".join(["---"] * len(cabecalho)) + "|"]
    saida.extend(linhas)
    return "\n".join(saida)


def escrever_relatorio(
    *,
    caminho_saida: Path,
    agora: datetime,
    fipe: dict,
    combustivel: dict,
    manutencao: dict,
) -> None:
    data_geracao = agora.strftime("%Y-%m-%d")
    gap_gas = combustivel["gasolina"]["gap_percentual"]
    gap_eta = combustivel["etanol"]["gap_percentual"]

    blocos = []

    blocos.append("# Índice GSA — Relatório de Custo Real de Posse de Veículos")
    blocos.append(f"*Gerado em {data_geracao}, a partir de dado real já ingerido no projeto (FIPE + ANP + IBGE/SIDRA).*")

    blocos.append(
        "## Resumo\n\n"
        f"O preço da tabela FIPE se mostrou historicamente estável mês a mês — variação "
        f"média de {formatar_pct(fipe['cv_medio'] * 100)} entre os veículos com histórico "
        f"real acompanhado, o que reforça a FIPE como referência confiável de curto prazo. "
        f"O combustível já não é assim: a gasolina custa {formatar_pct(gap_gas * 100)} a mais "
        f"em {combustivel['gasolina']['uf_max']} do que em {combustivel['gasolina']['uf_min']}, e o etanol "
        f"varia ainda mais entre estados ({formatar_pct(gap_eta * 100)} do mais caro ao mais "
        f"barato) — onde o carro roda pesa tanto quanto qual carro é. Na manutenção, o "
        f"conserto de automóvel subiu em 12 meses de {formatar_pct(manutencao['regiao_menor_alta']['variacao_acumulada_12m_percentual'])} "
        f"em {manutencao['regiao_menor_alta']['regiao_metropolitana']} a "
        f"{formatar_pct(manutencao['regiao_maior_alta']['variacao_acumulada_12m_percentual'])} em "
        f"{manutencao['regiao_maior_alta']['regiao_metropolitana']} — uma diferença regional grande "
        "o suficiente pra merecer entrar na conta do custo total de posse."
    )

    # ----- FIPE -----
    blocos.append("## FIPE — estabilidade de preço")
    blocos.append(
        f"Entre os {formatar_inteiro(fipe['n_veiculos_com_historico'])} veículos (de um catálogo de "
        f"{formatar_inteiro(fipe['n_veiculos_total'])} combinações código FIPE/ano/combustível) que têm "
        f"histórico real de 2 ou mais meses ao longo de {fipe['n_meses']} meses de dado "
        f"disponível, a variação de preço mês a mês foi de **{formatar_pct(fipe['cv_medio'] * 100)}** "
        f"em média (coeficiente de variação — desvio padrão sobre a média do preço de cada "
        f"veículo), com amplitude média (máximo menos mínimo sobre a média) de "
        f"{formatar_pct(fipe['amplitude_media'] * 100)}."
    )
    blocos.append(
        "Isso confirma o achado que já tínhamos ao calcular o risco por categoria "
        "(`docs/METODOLOGIA_RISCO.md`): a tabela FIPE se move pouco de um mês pro outro. "
        "O risco real de depreciação de um veículo está muito mais em revenda/mercado no "
        "horizonte de anos do que em oscilação mês a mês da própria tabela."
    )

    # ----- ANP -----
    blocos.append("## ANP — combustível por região")
    for chave, titulo in [("gasolina", "Gasolina comum"), ("etanol", "Etanol hidratado")]:
        dado = combustivel[chave]
        blocos.append(f"### {titulo}")
        linhas_caras = [
            f"| {row.uf} | {formatar_brl(row.preco_medio)}/l |"
            for row in dado["mais_caras"].itertuples()
        ]
        linhas_baratas = [
            f"| {row.uf} | {formatar_brl(row.preco_medio)}/l |"
            for row in dado["mais_baratas"].itertuples()
        ]
        blocos.append(
            "**5 UFs mais caras**\n\n"
            + tabela_markdown(linhas_caras, ["UF", "Preço médio"])
            + "\n\n**5 UFs mais baratas**\n\n"
            + tabela_markdown(linhas_baratas, ["UF", "Preço médio"])
        )
        blocos.append(
            f"{titulo} custa **{formatar_pct(dado['gap_percentual'] * 100)}** a mais em "
            f"{dado['uf_max']} ({formatar_brl(dado['preco_max'])}/l) do que em {dado['uf_min']} "
            f"({formatar_brl(dado['preco_min'])}/l), na semana de referência mais recente "
            f"({dado['semana_mais_recente']})."
        )
    blocos.append(
        "O padrão geográfico se repete nos dois combustíveis: Norte e Nordeste concentram os "
        "preços mais altos (custo logístico de distribuição), Sudeste e Sul os mais baixos. "
        "Pra quem está simulando o custo de posse, a UF de uso do carro muda o gasto com "
        "combustível tanto quanto — às vezes mais do que — a escolha entre gasolina e etanol "
        "dentro do mesmo posto."
    )

    # ----- IBGE -----
    blocos.append("## IBGE/SIDRA — manutenção por região metropolitana")
    linhas_manutencao = [
        f"| {row.regiao_metropolitana} | {formatar_pct(row.variacao_acumulada_12m_percentual)} | {formatar_pct(row.variacao_mensal_percentual)} |"
        for row in manutencao["df"].itertuples()
    ]
    blocos.append(
        f"Variação acumulada em 12 meses do subitem `5102011.Conserto de automóvel` do IPCA, "
        f"mês de referência {manutencao['mes_referencia']}, para as "
        f"{manutencao['n_regioes']} regiões metropolitanas com essa abertura disponível "
        f"(média das regiões: {formatar_pct(manutencao['media_acumulada_12m'])}):\n\n"
        + tabela_markdown(linhas_manutencao, ["Região metropolitana", "Acum. 12 meses", "Variação do mês"])
    )
    acima = ", ".join(manutencao["acima_da_media"]["regiao_metropolitana"].tolist())
    abaixo = ", ".join(manutencao["abaixo_da_media"]["regiao_metropolitana"].tolist())
    blocos.append(
        f"**{manutencao['regiao_maior_alta']['regiao_metropolitana']}** teve a maior alta acumulada "
        f"({formatar_pct(manutencao['regiao_maior_alta']['variacao_acumulada_12m_percentual'])}) e "
        f"**{manutencao['regiao_menor_alta']['regiao_metropolitana']}** a menor "
        f"({formatar_pct(manutencao['regiao_menor_alta']['variacao_acumulada_12m_percentual'])}). "
        f"Acima da média das regiões: {acima}. Abaixo: {abaixo}. Manter e consertar carro num "
        f"desses dois extremos representa uma diferença de mais de "
        f"{formatar_pct(manutencao['regiao_maior_alta']['variacao_acumulada_12m_percentual'] - manutencao['regiao_menor_alta']['variacao_acumulada_12m_percentual'])} "
        "em 12 meses só nessa linha de custo."
    )

    # ----- Metodologia e limitações -----
    blocos.append("## Metodologia e limitações")
    blocos.append(
        "- **FIPE**: o cálculo de variação usa só veículos com 2+ meses de histórico real "
        f"({formatar_inteiro(fipe['n_veiculos_com_historico'])} de {formatar_inteiro(fipe['n_veiculos_total'])} combinações "
        "no catálogo) — a maior parte do catálogo veio de um único CSV mensal e ainda não "
        "tem série temporal própria pra medir variação. Ver `docs/METODOLOGIA_RISCO.md` para "
        "o detalhamento por categoria de veículo (popular/intermediário/premium/elétrico)."
    )
    blocos.append(
        "- **ANP**: preço médio de revenda por semana e UF, direto da série histórica oficial "
        "da ANP (não passa por posto individual, é a média semanal já agregada pela própria "
        "agência). A semana de referência pode variar ligeiramente entre UFs quando alguma "
        "não teve pesquisa de preço concluída na semana mais recente."
    )
    blocos.append(
        "- **IBGE/SIDRA**: cobertura territorial restrita às 10 regiões metropolitanas que o "
        "IPCA abre nessa tabela (Belém, Fortaleza, Recife, Salvador, Belo Horizonte, Grande "
        "Vitória, Rio de Janeiro, São Paulo, Curitiba, Porto Alegre) — não é o Brasil inteiro, "
        "e nem todo estado tem uma região metropolitana coberta. Não existe um subitem "
        "\"Manutenção e Acessórios\" único no IPCA: usamos `Conserto de automóvel`, que é o "
        "subitem que melhor representa mão de obra de manutenção; `Acessórios e peças` "
        "também está ingerido na mesma tabela (`ibge_manutencao_veiculos`) e pode entrar em "
        "uma próxima versão deste relatório."
    )
    blocos.append(
        "- **Todos os três** ainda têm histórico curto (2-3 anos de série real, a maior parte "
        "concentrada em 2023-2026) — números tendem a ficar mais estáveis e mais confiáveis "
        "conforme a ingestão mensal for acumulando mais meses. Este relatório reflete o dado "
        f"disponível em {data_geracao} e deve ser regenerado periodicamente, não tratado como "
        "estático."
    )

    blocos.append("---\n\n**Gustavo Santos Analytics — Cultura Data-Driven**")

    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho_saida, "w", encoding="utf-8") as f:
        f.write("\n\n".join(blocos) + "\n")


def main() -> None:
    agora = datetime.now(timezone.utc)
    caminho_saida = DOCS_DIR / f"RELATORIO_INDICE_GSA_{agora.strftime('%Y-%m')}.md"

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        fipe = calcular_estabilidade_fipe(con)
        combustivel = calcular_combustivel_regional(con)
        manutencao = calcular_manutencao_regional(con)
    finally:
        con.close()

    escrever_relatorio(
        caminho_saida=caminho_saida,
        agora=agora,
        fipe=fipe,
        combustivel=combustivel,
        manutencao=manutencao,
    )

    print("===== Relatório gerado =====")
    print(f"FIPE: variação média {fipe['cv_medio'] * 100:.2f}% ({fipe['n_veiculos_com_historico']} veículos com histórico)")
    print(
        f"ANP gasolina: {combustivel['gasolina']['uf_max']} "
        f"(R$ {combustivel['gasolina']['preco_max']:.2f}) vs {combustivel['gasolina']['uf_min']} "
        f"(R$ {combustivel['gasolina']['preco_min']:.2f})"
    )
    print(
        f"IBGE manutenção: {manutencao['regiao_maior_alta']['regiao_metropolitana']} "
        f"({manutencao['regiao_maior_alta']['variacao_acumulada_12m_percentual']:.2f}%) vs "
        f"{manutencao['regiao_menor_alta']['regiao_metropolitana']} "
        f"({manutencao['regiao_menor_alta']['variacao_acumulada_12m_percentual']:.2f}%)"
    )
    print(f"\nGravado em: {caminho_saida}")


if __name__ == "__main__":
    main()
