"""
Ingestao do custo de manutencao de veiculos por regiao metropolitana, a
partir do IPCA (IBGE), via API do SIDRA.

Investigacao feita antes de escrever este script (ver docs/FONTES.md):
o IPCA nao tem um subitem unico literalmente chamado "Manutencao e
Acessorios para Veiculos". Dentro do grupo "5.Transportes" > item
"5102.Veiculo proprio", o que existe de fato sao dois subitens
separados que juntos cobrem manutencao de veiculo:
    - 5102009.Acessorios e pecas  (codigo SIDRA 7645)
    - 5102011.Conserto de automovel (codigo SIDRA 7647)
Este script ingere os dois, nomeados exatamente como o IBGE nomeia (sem
inventar um rotulo "manutencao e acessorios" unificado).

Fonte: tabela SIDRA 7060 — "IPCA - Variacao mensal, acumulada no ano,
acumulada em 12 meses e peso mensal, para o indice geral, grupos,
subgrupos, itens e subitens de produtos e servicos (a partir de
janeiro/2020)". Nivel territorial N7 = "Regiao Metropolitana ate 2020"
(10 regioes: Belem, Fortaleza, Recife, Salvador, Belo Horizonte, Grande
Vitoria, Rio de Janeiro, Sao Paulo, Curitiba, Porto Alegre — as unicas
com abertura de "regiao metropolitana" nessa tabela; algumas capitais
adicionais so tem abertura por municipio isolado (N6), fora do escopo
deste script).

API usada: https://apisidra.ibge.gov.br/values/... (formato classico do
SIDRA, mais direto que a API v3 de metadados pra puxar valores).

Variaveis gravadas (as que a tabela realmente expoe — nao ha "indice"
absoluto nessa tabela, so variacoes percentuais):
    - IPCA - Variacao mensal (%)              -> variacao_mensal_percentual
    - IPCA - Variacao acumulada em 12 meses (%) -> variacao_acumulada_12m_percentual

Grava em data/processed/indice_gsa.duckdb, tabela
ibge_manutencao_veiculos.

Por padrao carrega so os ultimos 3 anos (--anos-recentes), suficiente
pra tabela 7060 (que so cobre a partir de jan/2020). Pra ir antes de
2020 seria necessario combinar com a tabela 1419 (2012-2019) — fora do
escopo desta primeira rodada.

Uso:
    python scripts/ingestao/ibge_manutencao.py                    # ultimos 3 anos
    python scripts/ingestao/ibge_manutencao.py --anos-recentes 5
    python scripts/ingestao/ibge_manutencao.py --tudo              # desde jan/2020
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import requests

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "processed" / "indice_gsa.duckdb"

API_BASE_URL = "https://apisidra.ibge.gov.br/values"
TABELA = 7060
NIVEL_TERRITORIAL = "n7"  # Regiao Metropolitana ate 2020
VARIAVEIS = "63,2265"  # IPCA - Variacao mensal ; IPCA - Variacao acumulada em 12 meses
CLASSIFICACAO = "c315"  # Geral, grupo, subgrupo, item e subitem
SUBITENS = "7645,7647"  # 5102009.Acessorios e pecas ; 5102011.Conserto de automovel
REQUEST_TIMEOUT = 60

VARIAVEL_COLUNA = {
    "63": "variacao_mensal_percentual",
    "2265": "variacao_acumulada_12m_percentual",
}


def numero_ou_none(valor: str):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def montar_url(periodo: str) -> str:
    return (
        f"{API_BASE_URL}/t/{TABELA}/{NIVEL_TERRITORIAL}/all/v/{VARIAVEIS}"
        f"/p/{periodo}/{CLASSIFICACAO}/{SUBITENS}"
    )


def get_connection() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ibge_manutencao_veiculos (
            codigo_regiao VARCHAR NOT NULL,
            regiao_metropolitana VARCHAR NOT NULL,
            codigo_subitem VARCHAR NOT NULL,
            subitem VARCHAR NOT NULL,
            mes_referencia DATE NOT NULL,
            variacao_mensal_percentual DOUBLE,
            variacao_acumulada_12m_percentual DOUBLE,
            atualizado_em TIMESTAMP NOT NULL,
            PRIMARY KEY (codigo_regiao, codigo_subitem, mes_referencia)
        )
        """
    )
    return con


def mes_referencia_para_data(codigo_mes: str):
    ano = int(codigo_mes[:4])
    mes = int(codigo_mes[4:6])
    return f"{ano:04d}-{mes:02d}-01"


def buscar_dados(periodo: str) -> list[dict]:
    url = montar_url(periodo)
    print(f"[info] consultando SIDRA: {url}")
    resposta = requests.get(url, timeout=REQUEST_TIMEOUT)
    resposta.raise_for_status()
    linhas = resposta.json()

    if not linhas or "D1C" not in linhas[0]:
        raise ValueError(
            "Resposta do SIDRA sem cabecalho esperado — layout pode ter mudado. "
            f"Primeira linha recebida: {linhas[0] if linhas else '(vazia)'}"
        )

    return linhas[1:]  # primeira linha e o cabecalho de descricao das colunas


def transformar(linhas_brutas: list[dict]) -> dict[tuple, dict]:
    """Agrupa as linhas (uma por variavel) em uma linha por
    (regiao, subitem, mes), com as duas variaveis como colunas."""
    agrupado: dict[tuple, dict] = {}

    for linha in linhas_brutas:
        chave = (linha["D1C"], linha["D4C"], linha["D3C"])
        registro = agrupado.setdefault(
            chave,
            {
                "codigo_regiao": linha["D1C"],
                "regiao_metropolitana": linha["D1N"],
                "codigo_subitem": linha["D4C"],
                "subitem": linha["D4N"],
                "mes_referencia": mes_referencia_para_data(linha["D3C"]),
                "variacao_mensal_percentual": None,
                "variacao_acumulada_12m_percentual": None,
            },
        )
        coluna = VARIAVEL_COLUNA.get(linha["D2C"])
        if coluna:
            registro[coluna] = numero_ou_none(linha["V"])

    return agrupado


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument(
        "--anos-recentes",
        type=int,
        default=3,
        help="Carrega so os ultimos N anos (em meses: N*12). Default: 3.",
    )
    grupo.add_argument(
        "--tudo",
        action="store_true",
        help="Carrega a serie completa disponivel na tabela 7060 (desde jan/2020).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    periodo = "all" if args.tudo else f"last%20{args.anos_recentes * 12}"

    linhas_brutas = buscar_dados(periodo)
    if not linhas_brutas:
        print("[erro] nenhuma linha retornada pelo SIDRA — abortando sem gravar.")
        sys.exit(1)

    registros = transformar(linhas_brutas)
    now = datetime.now(timezone.utc)

    con = get_connection()
    try:
        antes = con.execute("SELECT COUNT(*) FROM ibge_manutencao_veiculos").fetchone()[0]

        con.executemany(
            """
            INSERT OR REPLACE INTO ibge_manutencao_veiculos VALUES
            (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r["codigo_regiao"],
                    r["regiao_metropolitana"],
                    r["codigo_subitem"],
                    r["subitem"],
                    r["mes_referencia"],
                    r["variacao_mensal_percentual"],
                    r["variacao_acumulada_12m_percentual"],
                    now,
                )
                for r in registros.values()
            ],
        )

        depois = con.execute("SELECT COUNT(*) FROM ibge_manutencao_veiculos").fetchone()[0]
        regioes_distintas = con.execute(
            "SELECT COUNT(DISTINCT codigo_regiao) FROM ibge_manutencao_veiculos"
        ).fetchone()[0]
        subitens_distintos = con.execute(
            "SELECT DISTINCT subitem FROM ibge_manutencao_veiculos ORDER BY 1"
        ).fetchall()
        mes_min, mes_max = con.execute(
            "SELECT MIN(mes_referencia), MAX(mes_referencia) FROM ibge_manutencao_veiculos"
        ).fetchone()

        print("\n===== Resumo da ingestao IBGE/SIDRA (manutencao de veiculos) =====")
        print(f"Escopo desta rodada: {'serie completa (desde jan/2020)' if args.tudo else f'ultimos {args.anos_recentes} ano(s)'}")
        print(f"Linhas brutas recebidas do SIDRA: {len(linhas_brutas)}")
        print(f"Registros (regiao/subitem/mes) processados: {len(registros)}")
        print(f"ibge_manutencao_veiculos antes:  {antes}")
        print(f"ibge_manutencao_veiculos depois: {depois}")
        print(f"Regioes metropolitanas distintas: {regioes_distintas}")
        print(f"Subitens: {[s[0] for s in subitens_distintos]}")
        print(f"Periodo no banco: {mes_min} a {mes_max}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
