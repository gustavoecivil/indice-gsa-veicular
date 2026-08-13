"""
Importa o CSV completo da tabela FIPE (baixado manualmente do plano pago
em fipe.online) direto pro DuckDB, na mesma tabela fipe_historico_precos
que a ingestao via API (fipe_historico.py) preenche.

Muito mais rapido que catar preco combinacao por combinacao via API: o
CSV ja vem com o catalogo inteiro de um mes em um arquivo so.

Colunas reais do CSV (verificadas manualmente antes de escrever este
script — ver data/raw/fipe_tabela_completa_2026-08.csv):
    Type, Brand Code, Brand Value, Model Code, Model Value, Year Code,
    Year Value, Fipe Code, Fuel Letter, Fuel Type, Price, Month

De-para explicito pra fipe_historico_precos:
    Fipe Code   -> codigo_fipe
    Brand Value -> marca
    Model Value -> modelo
    Year Code   -> ano_modelo   (parte antes do "-", ex: "1991-1" -> 1991)
    Fuel Type   -> combustivel
    Month       -> mes_referencia
    Price       -> valor        ("R$ 10.063,00" -> 10063.0)

Idempotente: mesma chave primaria da tabela (codigo_fipe, ano_modelo,
combustivel, mes_referencia) — rodar de novo com o CSV de outro mes so
adiciona os meses novos, sem duplicar.

Uso:
    python scripts/ingestao/fipe_importar_csv.py
    python scripts/ingestao/fipe_importar_csv.py --csv data/raw/outro_arquivo.csv
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "processed" / "indice_gsa.duckdb"
DEFAULT_CSV_PATH = BASE_DIR / "data" / "raw" / "fipe_tabela_completa_2026-08.csv"

PRICE_RE = re.compile(r"[^\d,]")


def parse_preco(preco_texto: str) -> float:
    """Converte 'R$ 10.063,00' para 10063.0."""
    limpo = PRICE_RE.sub("", preco_texto)
    limpo = limpo.replace(".", "").replace(",", ".")
    return float(limpo)


def get_connection() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS fipe_historico_precos (
            codigo_fipe VARCHAR NOT NULL,
            marca VARCHAR NOT NULL,
            modelo VARCHAR NOT NULL,
            ano_modelo INTEGER NOT NULL,
            combustivel VARCHAR NOT NULL,
            mes_referencia VARCHAR NOT NULL,
            valor DOUBLE NOT NULL,
            atualizado_em TIMESTAMP NOT NULL,
            PRIMARY KEY (codigo_fipe, ano_modelo, combustivel, mes_referencia)
        )
        """
    )
    return con


def carregar_csv(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, encoding="utf-8")

    colunas_esperadas = {
        "Fipe Code",
        "Brand Value",
        "Model Value",
        "Year Code",
        "Fuel Type",
        "Price",
        "Month",
    }
    faltando = colunas_esperadas - set(df.columns)
    if faltando:
        raise ValueError(
            f"CSV nao tem as colunas esperadas: {faltando}. "
            f"Colunas encontradas: {list(df.columns)}"
        )

    now = datetime.now(timezone.utc)

    saida = pd.DataFrame(
        {
            "codigo_fipe": df["Fipe Code"].astype(str),
            "marca": df["Brand Value"].astype(str),
            "modelo": df["Model Value"].astype(str),
            "ano_modelo": df["Year Code"].astype(str).str.split("-").str[0].astype(int),
            "combustivel": df["Fuel Type"].astype(str),
            "mes_referencia": df["Month"].astype(str),
            "valor": df["Price"].astype(str).map(parse_preco),
            "atualizado_em": now,
        }
    )
    return saida


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help="Caminho do CSV da tabela FIPE a importar (default: %(default)s)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_path = args.csv

    if not csv_path.exists():
        print(f"[erro] arquivo nao encontrado: {csv_path}")
        sys.exit(1)

    print(f"[info] lendo {csv_path} ...")
    df = carregar_csv(csv_path)
    print(f"[info] {len(df)} linhas lidas do CSV")
    print(f"[info] meses de referencia no CSV: {sorted(df['mes_referencia'].unique())}")

    con = get_connection()
    try:
        antes = con.execute("SELECT COUNT(*) FROM fipe_historico_precos").fetchone()[0]

        con.register("csv_importado", df)
        con.execute(
            """
            INSERT OR REPLACE INTO fipe_historico_precos
            SELECT codigo_fipe, marca, modelo, ano_modelo, combustivel,
                   mes_referencia, valor, atualizado_em
            FROM csv_importado
            """
        )
        con.unregister("csv_importado")

        depois = con.execute("SELECT COUNT(*) FROM fipe_historico_precos").fetchone()[0]
        meses_distintos = con.execute(
            "SELECT COUNT(DISTINCT mes_referencia) FROM fipe_historico_precos"
        ).fetchone()[0]
        marcas_distintas = con.execute(
            "SELECT COUNT(DISTINCT marca) FROM fipe_historico_precos"
        ).fetchone()[0]

        print("\n===== Resumo da importacao do CSV =====")
        print(f"Linhas no CSV:                          {len(df)}")
        print(f"fipe_historico_precos antes:            {antes}")
        print(f"fipe_historico_precos depois:           {depois}")
        print(f"Diferenca (linhas novas com PK inedita): {depois - antes}")
        print(f"Meses de referencia distintos (total):  {meses_distintos}")
        print(f"Marcas distintas (total):                {marcas_distintas}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
