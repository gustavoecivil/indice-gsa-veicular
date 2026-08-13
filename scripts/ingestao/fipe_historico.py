"""
Ingestao do historico de precos da FIPE (plano pago, fipe.api.br).

Le as combinacoes marca/modelo/ano ja gravadas em fipe_anos (populadas
pelo scripts/ingestao/fipe.py) e busca, para cada uma, o preco e o
historico de precos (priceHistory) via API. Grava tudo em
data/processed/indice_gsa.duckdb, tabela fipe_historico_precos.

Cada combinacao processada com sucesso e marcada em
fipe_historico_checkpoint. Se o script for interrompido (crash, sessao
fechada, etc.) e rodar de novo, combinacoes ja marcadas sao puladas —
retoma do ponto onde parou em vez de refazer tudo.

O token de assinatura (FIPE_API_TOKEN) e lido do .env e usado apenas no
header da requisicao HTTP — nunca e impresso, logado ou exposto.

Uso:
    python scripts/ingestao/fipe_historico.py
    python scripts/ingestao/fipe_historico.py --limit 200   # smoke test
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "processed" / "indice_gsa.duckdb"
ERROR_LOG_PATH = BASE_DIR / "data" / "raw" / "fipe_erros.log"

API_BASE_URL = "https://fipe.parallelum.com.br/api/v2"
VEHICLE_TYPE = "cars"

RATE_LIMIT_SECONDS = 0.15
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2
REQUEST_TIMEOUT = 20

PRICE_RE = re.compile(r"[^\d,]")


def build_logger() -> logging.Logger:
    ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("fipe_historico_ingestao")
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


def parse_preco(preco_texto: str) -> float:
    """Converte 'R$ 10.063,00' para 10063.0."""
    limpo = PRICE_RE.sub("", preco_texto)
    limpo = limpo.replace(".", "").replace(",", ".")
    return float(limpo)


def fetch_json(session: requests.Session, url: str, headers: dict, context: str):
    """GET com ate MAX_RETRIES tentativas e backoff exponencial.

    Sempre aguarda RATE_LIMIT_SECONDS entre chamadas (sucesso ou falha).
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
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS fipe_historico_checkpoint (
            codigo_marca VARCHAR NOT NULL,
            codigo_modelo VARCHAR NOT NULL,
            codigo_ano VARCHAR NOT NULL,
            processado_em TIMESTAMP NOT NULL,
            PRIMARY KEY (codigo_marca, codigo_modelo, codigo_ano)
        )
        """
    )
    return con


def marcar_checkpoint(
    con: duckdb.DuckDBPyConnection,
    codigo_marca: str,
    codigo_modelo: str,
    codigo_ano: str,
    now: datetime,
) -> None:
    con.execute(
        "INSERT OR REPLACE INTO fipe_historico_checkpoint VALUES (?, ?, ?, ?)",
        [codigo_marca, codigo_modelo, codigo_ano, now],
    )


def upsert_preco(
    con: duckdb.DuckDBPyConnection,
    codigo_fipe: str,
    marca: str,
    modelo: str,
    ano_modelo: int,
    combustivel: str,
    mes_referencia: str,
    valor: float,
    now: datetime,
) -> None:
    con.execute(
        "INSERT OR REPLACE INTO fipe_historico_precos VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [codigo_fipe, marca, modelo, ano_modelo, combustivel, mes_referencia, valor, now],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Processa apenas as N primeiras combinacoes marca/modelo/ano "
            "(uso em smoke test). Sem esse parametro, processa tudo que "
            "estiver em fipe_anos."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv(BASE_DIR / ".env")
    token = os.getenv("FIPE_API_TOKEN", "").strip()

    if not token:
        logger.error(
            "FIPE_API_TOKEN nao definido no .env — necessario para o "
            "historico de precos (recurso do plano pago)."
        )
        sys.exit(1)

    headers = {"X-Subscription-Token": token}
    session = requests.Session()
    con = get_connection()

    try:
        total_catalogo = con.execute("SELECT COUNT(*) FROM fipe_anos").fetchone()[0]
        if total_catalogo == 0:
            logger.error(
                "fipe_anos esta vazia — rode scripts/ingestao/fipe.py "
                "primeiro para popular o catalogo marca/modelo/ano."
            )
            sys.exit(1)

        combos = con.execute(
            """
            SELECT a.codigo_marca, a.codigo_modelo, a.codigo_ano
            FROM fipe_anos a
            LEFT JOIN fipe_historico_checkpoint c
                ON a.codigo_marca = c.codigo_marca
                AND a.codigo_modelo = c.codigo_modelo
                AND a.codigo_ano = c.codigo_ano
            WHERE c.codigo_marca IS NULL
            ORDER BY a.codigo_marca, a.codigo_modelo, a.codigo_ano
            """
        ).fetchall()

        ja_processados = total_catalogo - len(combos)
        if ja_processados:
            logger.info(
                f"[info] {ja_processados} combinacoes ja tinham checkpoint de "
                f"execucao anterior — retomando do restante\n"
            )

        if args.limit is not None:
            combos = combos[: args.limit]

        total_combos = len(combos)
        if total_combos == 0:
            logger.info("[info] nada a processar — todas as combinacoes ja tem checkpoint")
            return
        logger.info(f"[info] {total_combos} combinacoes marca/modelo/ano a processar\n")

        total_precos_gravados = 0
        total_falhas = 0
        marca_atual = None

        for i, (codigo_marca, codigo_modelo, codigo_ano) in enumerate(combos, start=1):
            if codigo_marca != marca_atual:
                marca_atual = codigo_marca
                logger.info(f"[marca {codigo_marca}] iniciando...")

            context = f"preco de {codigo_marca}/{codigo_modelo}/{codigo_ano}"
            try:
                detalhe = fetch_json(
                    session,
                    f"{API_BASE_URL}/{VEHICLE_TYPE}/brands/{codigo_marca}"
                    f"/models/{codigo_modelo}/years/{codigo_ano}",
                    headers,
                    context,
                )
            except RuntimeError as exc:
                logger.error(f"Pulando {context}: {exc}")
                total_falhas += 1
                continue

            codigo_fipe = detalhe.get("codeFipe", "")
            marca = detalhe.get("brand", "")
            modelo = detalhe.get("model", "")
            ano_modelo = detalhe.get("modelYear")
            combustivel = detalhe.get("fuel", "")

            # priceHistory so vem populado pela rota baseada em codigo Fipe.
            historico_context = f"historico de {codigo_fipe}/{codigo_ano}"
            try:
                detalhe_historico = fetch_json(
                    session,
                    f"{API_BASE_URL}/{VEHICLE_TYPE}/{codigo_fipe}/years/{codigo_ano}/history",
                    headers,
                    historico_context,
                )
                historico = detalhe_historico.get("priceHistory") or []
            except RuntimeError as exc:
                logger.error(f"Sem historico para {historico_context}: {exc}")
                historico = []

            if not historico:
                historico = [
                    {
                        "month": detalhe.get("referenceMonth", ""),
                        "price": detalhe.get("price", "R$ 0,00"),
                    }
                ]

            now = datetime.now(timezone.utc)
            for entrada in historico:
                try:
                    valor = parse_preco(entrada["price"])
                except (KeyError, ValueError) as exc:
                    logger.error(f"Preco invalido em {context}: {entrada!r} ({exc})")
                    continue

                upsert_preco(
                    con,
                    codigo_fipe,
                    marca,
                    modelo,
                    ano_modelo,
                    combustivel,
                    entrada["month"],
                    valor,
                    now,
                )
                total_precos_gravados += 1

            marcar_checkpoint(con, codigo_marca, codigo_modelo, codigo_ano, now)

            if i % 200 == 0 or i == total_combos:
                logger.info(
                    f"  ... {i}/{total_combos} combinacoes processadas "
                    f"({total_precos_gravados} precos gravados, {total_falhas} falhas)"
                )

        marcas_com_dado = con.execute(
            "SELECT COUNT(DISTINCT marca) FROM fipe_historico_precos"
        ).fetchone()[0]
        total_db = con.execute("SELECT COUNT(*) FROM fipe_historico_precos").fetchone()[0]
        meses_distintos = con.execute(
            "SELECT COUNT(DISTINCT mes_referencia) FROM fipe_historico_precos"
        ).fetchone()[0]

        logger.info("\n===== Resumo da ingestao de historico =====")
        logger.info(f"Combinacoes ja com checkpoint (puladas): {ja_processados}")
        logger.info(f"Combinacoes processadas nesta execucao: {total_combos}")
        logger.info(f"Falhas (puladas):                       {total_falhas}")
        logger.info(f"Linhas de preco gravadas nesta execucao: {total_precos_gravados}")
        logger.info("--- Totais atuais no DuckDB ---")
        logger.info(f"fipe_historico_precos:      {total_db}")
        logger.info(f"marcas distintas com dado:  {marcas_com_dado}")
        logger.info(f"meses de referencia distintos: {meses_distintos}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
