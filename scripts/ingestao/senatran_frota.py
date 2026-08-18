"""
Ingestao dos dados abertos do RENAVAM (Registro Nacional de Veiculos
Automotores), publicados pela SENATRAN: frota de veiculos por
UF/municipio/marca-modelo/ano de fabricacao, snapshot mensal.

Investigacao feita antes de escrever o script (ver docs/FONTES.md pro
detalhamento completo):
  - RENAVAM e RENAEST NAO estao no dados.gov.br (o catalogo usado por
    SUSEP/IBGE/ANEEL neste projeto) — vivem num portal proprio do
    Ministerio dos Transportes, dados.transportes.gov.br, que tambem
    roda CKAN mas SEM exigir token Bearer no `/api/3/action/package_show`
    (diferente do dados.gov.br, que passou a exigir — ver
    docs/FONTES.md da SUSEP). Confirmado com uma chamada direta antes de
    escrever qualquer coisa.
  - O dataset RENAVAM tem um recurso ZIP por mes desde maio/2013 (mais
    de 150 recursos ja catalogados), cada um com um unico TXT
    delimitado por `;`: `UF;Município;Marca Modelo;Ano Fabricação
    Veículo CRV;Qtd. Veículos`. NAO existe quebra por "tipo de veiculo"
    (categoria tipo AUTOMOVEL/MOTOCICLETA) nesse dataset especifico — a
    granularidade real e por marca/modelo. Este script usa
    `marca_modelo` como veio, sem inventar uma coluna `tipo_veiculo` que
    a fonte nao tem aqui (RENAEST, o outro dataset da SENATRAN ingerido
    em senatran_acidentes.py, tem tipo de veiculo real).
  - Cada arquivo mensal e um snapshot completo da frota naquele mes, nao
    incremental — baixar so o mes mais recente ja da o estado atual,
    sem precisar puxar os 150+ meses de historico (a tarefa pediu pra
    priorizar o dado mais recente disponivel).
  - O mes de referencia real esta no NOME do arquivo (ex.:
    "..._julho_2026.zip"), nao dentro do TXT — o campo `created` do
    CKAN reflete a data de upload no portal, nao o mes do dado (podem
    divergir). Por isso a busca do recurso mais recente aqui parseia o
    nome do arquivo, nao usa `created`.

Grava em data/processed/indice_gsa.duckdb, tabela `senatran_frota`:
uf, municipio, marca_modelo, ano_fabricacao_crv, quantidade,
mes_referencia (injetado a partir do mes/ano do nome do arquivo, ja que
o TXT em si nao tem essa coluna). Full-refresh a cada execucao — a
tabela representa "a frota no mes mais recente disponivel", nao uma
serie historica.

Uso:
    python scripts/ingestao/senatran_frota.py
    python scripts/ingestao/senatran_frota.py --arquivo-local caminho.zip --mes-referencia 2026-07-01
"""

from __future__ import annotations

import argparse
import re
import shutil
import zipfile
from datetime import date
from pathlib import Path

import duckdb
import requests

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "processed" / "indice_gsa.duckdb"
RAW_DIR = BASE_DIR / "data" / "raw"
REQUEST_TIMEOUT = 300

CKAN_PACKAGE_URL = "https://dados.transportes.gov.br/api/3/action/package_show"
DATASET_ID = "registro-nacional-de-veiculos-automotores-renavam"

MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}


def _normalizar(texto: str) -> str:
    subst = str.maketrans("áàâãéêíóôõúç", "aaaaeeiooouc")
    return texto.lower().translate(subst)


def localizar_recurso_mais_recente() -> tuple[str, date]:
    """Consulta a API publica do CKAN em dados.transportes.gov.br (sem
    token) e acha o recurso ZIP mais recente pelo mes/ano no NOME do
    arquivo (padrao "..._<mes_por_extenso>_<ano>.zip"), nao pelo campo
    'created' do CKAN — ver docstring do modulo."""
    resposta = requests.get(CKAN_PACKAGE_URL, params={"id": DATASET_ID}, timeout=60)
    resposta.raise_for_status()
    recursos = resposta.json()["result"]["resources"]

    candidatos = []
    for r in recursos:
        url = r.get("url", "")
        m = re.search(r"_([a-z]+)_(\d{4})\.zip$", _normalizar(url))
        if not m:
            continue
        mes = MESES_PT.get(m.group(1))
        if mes is None:
            continue
        candidatos.append((date(int(m.group(2)), mes, 1), url))

    if not candidatos:
        raise SystemExit(
            "[erro] nenhum recurso RENAVAM com mes/ano reconhecivel no nome do "
            "arquivo — layout do dataset em dados.transportes.gov.br pode ter "
            "mudado."
        )

    mes_referencia, url = max(candidatos, key=lambda item: item[0])
    return url, mes_referencia


def baixar_zip(url: str) -> Path:
    destino = RAW_DIR / url.rsplit("/", 1)[-1]
    if destino.exists():
        print(f"[info] {destino.name} ja baixado, pulando download")
        return destino
    print(f"[info] baixando {url} ...")
    resposta = requests.get(url, timeout=REQUEST_TIMEOUT)
    resposta.raise_for_status()
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(resposta.content)
    print(f"[info] salvo em {destino} ({len(resposta.content):,} bytes)")
    return destino


def extrair_txt(zip_path: Path) -> Path:
    with zipfile.ZipFile(zip_path) as z:
        nomes = [n for n in z.namelist() if n.lower().endswith(".txt")]
        if len(nomes) != 1:
            raise SystemExit(
                f"[erro] esperava exatamente 1 TXT dentro do zip, encontrou "
                f"{len(nomes)}: {nomes}"
            )
        destino = RAW_DIR / nomes[0]
        if not destino.exists():
            print(f"[info] extraindo {nomes[0]} ({z.getinfo(nomes[0]).file_size:,} bytes)...")
            with z.open(nomes[0]) as origem, open(destino, "wb") as saida:
                shutil.copyfileobj(origem, saida)  # streaming — arquivo tem ~1,2GB
    return destino


def ingerir(con: duckdb.DuckDBPyConnection, txt_path: Path, mes_referencia: date) -> None:
    print(
        f"[info] carregando {txt_path.name} no DuckDB (arquivo grande, ~22M "
        "linhas — pode levar alguns minutos)..."
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE senatran_frota AS
        SELECT
            "UF" AS uf,
            "Município" AS municipio,
            "Marca Modelo" AS marca_modelo,
            "Ano Fabricação Veículo CRV" AS ano_fabricacao_crv,
            CAST(CAST("Qtd. Veículos" AS DOUBLE) AS BIGINT) AS quantidade,
            CAST(? AS DATE) AS mes_referencia
        FROM read_csv(?, delim=';', header=true, encoding='utf-8')
        """,
        [mes_referencia, txt_path.as_posix()],
    )
    total = con.execute("SELECT COUNT(*) FROM senatran_frota").fetchone()[0]
    ufs = con.execute("SELECT COUNT(DISTINCT uf) FROM senatran_frota").fetchone()[0]
    soma = con.execute("SELECT SUM(quantidade) FROM senatran_frota").fetchone()[0]
    print(
        f"[info] senatran_frota: {total:,} linhas, {ufs} UFs, frota total "
        f"somada: {soma:,.0f} veiculos (mes_referencia={mes_referencia})"
    )


def get_connection() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--arquivo-local", type=Path, default=None,
        help="Usa um ZIP ja baixado em vez de consultar o CKAN (requer --mes-referencia).",
    )
    parser.add_argument(
        "--mes-referencia", type=str, default=None,
        help="YYYY-MM-DD, obrigatorio junto com --arquivo-local.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    if args.arquivo_local is not None:
        if args.mes_referencia is None:
            raise SystemExit("[erro] --mes-referencia e obrigatorio junto com --arquivo-local.")
        if not args.arquivo_local.exists():
            raise SystemExit(f"[erro] arquivo local informado nao existe: {args.arquivo_local}")
        zip_path = args.arquivo_local
        mes_referencia = date.fromisoformat(args.mes_referencia)
    else:
        url, mes_referencia = localizar_recurso_mais_recente()
        print(f"[info] recurso mais recente encontrado: {url} (mes_referencia={mes_referencia})")
        zip_path = baixar_zip(url)

    txt_path = extrair_txt(zip_path)

    con = get_connection()
    try:
        ingerir(con, txt_path, mes_referencia)
    finally:
        con.close()
        # So o zip fica em data/raw/ — o TXT extraido e so intermediario
        # (~1,2GB) e o espaco em disco ja foi problema real neste projeto
        # (ver susep.py).
        txt_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
