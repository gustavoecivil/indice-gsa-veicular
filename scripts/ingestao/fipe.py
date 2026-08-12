"""
Ingestao do catalogo FIPE (marcas, modelos, anos) via API parallelum v2.

Preenche 3 tabelas em data/processed/indice_gsa.duckdb:
  - fipe_marcas
  - fipe_modelos
  - fipe_anos

Nao busca preco por ano ainda (isso e a proxima etapa, com checkpoint
e throttling proprios, dado o volume de chamadas).

Uso:
    python scripts/ingestao/fipe.py
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import requests
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "processed" / "indice_gsa.duckdb"
ERROR_LOG_PATH = BASE_DIR / "data" / "raw" / "fipe_erros.log"

API_BASE_URL = "https://fipe.parallelum.com.br/api/v2"
VEHICLE_TYPE = "cars"

RATE_LIMIT_SECONDS = 0.25
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2
REQUEST_TIMEOUT = 20


def build_logger() -> logging.Logger:
    ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("fipe_ingestao")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(ERROR_LOG_PATH, encoding="utf-8")
    file_handler.setLevel(logging.ERROR)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    logger.addHandler(file_handler)

    return logger


logger = build_logger()


def fetch_json(session: requests.Session, url: str, headers: dict, context: str):
    """GET com ate MAX_RETRIES tentativas e backoff exponencial.

    Sempre aguarda RATE_LIMIT_SECONDS entre chamadas (sucesso ou falha)
    para respeitar a cota diaria da API gratuita.
    """
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)

            if response.status_code == 200:
                time.sleep(RATE_LIMIT_SECONDS)
                return response.json()

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else BACKOFF_BASE_SECONDS ** attempt
                last_error = RuntimeError(f"HTTP 429 (rate limit) em {url}")
                logger.info(
                    f"  [aviso] 429 em {context} (tentativa {attempt}/{MAX_RETRIES}), "
                    f"aguardando {wait:.1f}s"
                )
                time.sleep(wait)
                continue

            if response.status_code >= 500:
                last_error = RuntimeError(f"HTTP {response.status_code} em {url}")
                logger.info(
                    f"  [aviso] erro {response.status_code} em {context} "
                    f"(tentativa {attempt}/{MAX_RETRIES})"
                )
                time.sleep(BACKOFF_BASE_SECONDS ** attempt)
                continue

            response.raise_for_status()

        except requests.RequestException as exc:
            last_error = exc
            logger.info(
                f"  [aviso] falha de conexao em {context} "
                f"(tentativa {attempt}/{MAX_RETRIES}): {exc}"
            )
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_BASE_SECONDS ** attempt)

    time.sleep(RATE_LIMIT_SECONDS)
    raise RuntimeError(
        f"Falha ao buscar {context} apos {MAX_RETRIES} tentativas: {last_error}"
    )


def get_connection() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS fipe_marcas (
            codigo_marca VARCHAR PRIMARY KEY,
            nome_marca VARCHAR NOT NULL,
            atualizado_em TIMESTAMP NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS fipe_modelos (
            codigo_marca VARCHAR NOT NULL,
            codigo_modelo VARCHAR NOT NULL,
            nome_modelo VARCHAR NOT NULL,
            atualizado_em TIMESTAMP NOT NULL,
            PRIMARY KEY (codigo_marca, codigo_modelo)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS fipe_anos (
            codigo_marca VARCHAR NOT NULL,
            codigo_modelo VARCHAR NOT NULL,
            codigo_ano VARCHAR NOT NULL,
            nome_ano VARCHAR NOT NULL,
            atualizado_em TIMESTAMP NOT NULL,
            PRIMARY KEY (codigo_marca, codigo_modelo, codigo_ano)
        )
        """
    )
    return con


def upsert_marca(con: duckdb.DuckDBPyConnection, codigo_marca: str, nome_marca: str, now: datetime) -> None:
    con.execute(
        "INSERT OR REPLACE INTO fipe_marcas VALUES (?, ?, ?)",
        [codigo_marca, nome_marca, now],
    )


def upsert_modelo(
    con: duckdb.DuckDBPyConnection,
    codigo_marca: str,
    codigo_modelo: str,
    nome_modelo: str,
    now: datetime,
) -> None:
    con.execute(
        "INSERT OR REPLACE INTO fipe_modelos VALUES (?, ?, ?, ?)",
        [codigo_marca, codigo_modelo, nome_modelo, now],
    )


def upsert_ano(
    con: duckdb.DuckDBPyConnection,
    codigo_marca: str,
    codigo_modelo: str,
    codigo_ano: str,
    nome_ano: str,
    now: datetime,
) -> None:
    con.execute(
        "INSERT OR REPLACE INTO fipe_anos VALUES (?, ?, ?, ?, ?)",
        [codigo_marca, codigo_modelo, codigo_ano, nome_ano, now],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit-marcas",
        type=int,
        default=None,
        help=(
            "Processa apenas as N primeiras marcas (uso em smoke test, "
            "pra nao estourar a cota diaria da API). Sem esse parametro, "
            "processa o catalogo completo."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv(BASE_DIR / ".env")
    token = os.getenv("FIPE_API_TOKEN", "").strip()
    headers = {"X-Subscription-Token": token} if token else {}
    if not token:
        logger.info(
            "[info] FIPE_API_TOKEN nao definido - usando limite gratuito de "
            "500 req/dia (com token: 1000 req/dia).\n"
        )

    session = requests.Session()
    con = get_connection()

    total_marcas = 0
    total_modelos = 0
    total_anos = 0

    try:
        try:
            brands = fetch_json(
                session, f"{API_BASE_URL}/{VEHICLE_TYPE}/brands", headers, "lista de marcas"
            )
        except RuntimeError as exc:
            logger.error(f"Nao foi possivel buscar a lista de marcas: {exc}")
            sys.exit(1)

        if args.limit_marcas is not None:
            brands = brands[: args.limit_marcas]
            logger.info(f"[info] --limit-marcas ativo: processando {len(brands)} marca(s)\n")

        for brand in brands:
            codigo_marca = brand["code"]
            nome_marca = brand["name"]
            now = datetime.now(timezone.utc)

            upsert_marca(con, codigo_marca, nome_marca, now)
            total_marcas += 1

            try:
                models = fetch_json(
                    session,
                    f"{API_BASE_URL}/{VEHICLE_TYPE}/brands/{codigo_marca}/models",
                    headers,
                    f"modelos da marca {nome_marca}",
                )
            except RuntimeError as exc:
                logger.error(f"Pulando marca '{nome_marca}' (modelos): {exc}")
                continue

            logger.info(f"{nome_marca}: {len(models)} modelos encontrados")

            for model in models:
                codigo_modelo = str(model["code"])
                nome_modelo = model["name"]
                now = datetime.now(timezone.utc)

                upsert_modelo(con, codigo_marca, codigo_modelo, nome_modelo, now)
                total_modelos += 1

                try:
                    years = fetch_json(
                        session,
                        f"{API_BASE_URL}/{VEHICLE_TYPE}/brands/{codigo_marca}"
                        f"/models/{codigo_modelo}/years",
                        headers,
                        f"anos do modelo {nome_marca}/{nome_modelo}",
                    )
                except RuntimeError as exc:
                    logger.error(
                        f"Pulando modelo '{nome_marca}/{nome_modelo}' (anos): {exc}"
                    )
                    continue

                now = datetime.now(timezone.utc)
                for year in years:
                    upsert_ano(
                        con,
                        codigo_marca,
                        codigo_modelo,
                        year["code"],
                        year["name"],
                        now,
                    )
                    total_anos += 1

        marcas_db = con.execute("SELECT COUNT(*) FROM fipe_marcas").fetchone()[0]
        modelos_db = con.execute("SELECT COUNT(*) FROM fipe_modelos").fetchone()[0]
        anos_db = con.execute("SELECT COUNT(*) FROM fipe_anos").fetchone()[0]

        logger.info("\n===== Resumo da ingestao =====")
        logger.info(f"Marcas processadas nesta execucao:  {total_marcas}")
        logger.info(f"Modelos processados nesta execucao: {total_modelos}")
        logger.info(f"Anos gravados nesta execucao:       {total_anos}")
        logger.info("--- Totais atuais no DuckDB ---")
        logger.info(f"fipe_marcas:  {marcas_db}")
        logger.info(f"fipe_modelos: {modelos_db}")
        logger.info(f"fipe_anos:    {anos_db}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
