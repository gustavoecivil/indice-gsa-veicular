"""
Ingestao dos dados abertos do RENAEST (Registro Nacional de Sinistros e
Estatisticas de Transito), publicados pela SENATRAN: acidentes de
transito por UF/municipio/tipo de veiculo envolvido/mes.

Investigacao feita antes de escrever o script (ver docs/FONTES.md pro
detalhamento completo, e senatran_frota.py pro RENAVAM, a outra fonte da
SENATRAN):
  - Mesmo portal do RENAVAM (dados.transportes.gov.br, CKAN publico sem
    token). O dataset RENAEST tem um recurso ZIP por mes desde
    outubro/2021, cada um com o HISTORICO COMPLETO desde entao (nao
    incremental) — nao e preciso baixar todos os 50+ zips, so o mais
    recente.
  - Cada zip tem 4 CSVs, NAO um arquivo agregado: `Acidentes_*.csv`
    (microdado, 1 linha por acidente — 35+ milhoes de linhas na captura
    usada aqui, ~2,7GB), `TipoVeiculo_*.csv` (1 linha por tipo de
    veiculo envolvido em cada acidente, chave `num_acidente` —
    ~10,4 milhoes de linhas), `Localidade_*.csv` (de-para
    codigo_ibge -> municipio/UF/regiao por mes de referencia) e
    `Vitimas_*.csv` (perfil demografico das vitimas — NAO ingerido
    aqui, fora do escopo desta tarefa que pediu tipo de veiculo/UF/
    municipio, nao perfil de vitima; fica disponivel no zip em
    data/raw/ se fizer falta depois).
  - `TipoVeiculo_*.csv` tem uma coluna `tipo_veiculo` REAL e
    categorica (35 valores distintos confirmados por varredura
    completa do arquivo: AUTOMOVEL, MOTOCICLETA, CAMINHAO, ONIBUS,
    BICICLETA etc) — diferente do RENAVAM (frota), que so tem
    marca/modelo. Esta e a fonte usada aqui pra "tipo de veiculo
    envolvido" pedido na tarefa.
  - O nome do recurso no CKAN segue o padrao "RENAEST - Mensal -
    MM-AAAA" (com espacamento inconsistente em alguns — "Mensal -MM-AAAA"
    sem espaco antes do mes, confirmado inspecionando os 54 recursos
    catalogados). O MM-AAAA no nome e o MES DE REFERENCIA do dado, que
    fica ~4 meses atras da data de upload do arquivo (ex.: o recurso
    "03-2026" foi publicado em 2026-07-12) — defasagem normal de
    consolidacao/homologacao dessas estatisticas pelos DETRANs
    estaduais antes de virarem RENAEST nacional, nao um bug do script.
  - Delimitador `;`, codificacao UTF-8, nomes de UF/municipio sem
    acento (ASCII simples) — nao houve ambiguidade de encoding pra
    resolver aqui, diferente do CSV cp1252 da ANEEL.

Grava em data/processed/indice_gsa.duckdb, tabela `senatran_acidentes`:
uf, codigo_ibge, municipio, ano_acidente, mes_acidente, tipo_veiculo,
quantidade_acidentes, quantidade_veiculos. Por padrao so processa os
ultimos N anos de acidentes (`--anos-recentes`, default 3) pra manter o
volume gerenciavel (a serie completa tem ~35 milhoes de acidentes desde
2018); `--tudo` processa a serie inteira.

**Duas metricas, de proposito, pra nao confundir uma coisa com a
outra**: `quantidade_acidentes` = COUNT(DISTINCT num_acidente) — quantos
acidentes distintos, naquele UF/municipio/mes, tiveram pelo menos um
veiculo daquele tipo envolvido. `quantidade_veiculos` = soma de
`qtde_veiculos` do RENAEST — quantos veiculos daquele tipo estiveram
envolvidos (um mesmo acidente pode ter 2+ veiculos do mesmo tipo, ou
tipos diferentes). Somar `quantidade_acidentes` entre todos os tipos de
veiculo de um UF/mes NAO bate com o total de acidentes daquele UF/mes
(um acidente com carro+moto conta nos dois tipos) — limitacao conhecida
da propria fonte (RENAEST publica por tipo de veiculo envolvido, nao um
total mutuamente exclusivo), documentada aqui em vez de escondida.

Uso:
    python scripts/ingestao/senatran_acidentes.py
    python scripts/ingestao/senatran_acidentes.py --anos-recentes 5
    python scripts/ingestao/senatran_acidentes.py --tudo
    python scripts/ingestao/senatran_acidentes.py --arquivo-local caminho.zip
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
DATASET_ID = "renaest"

ARQUIVOS_NECESSARIOS = ["Acidentes_", "TipoVeiculo_", "Localidade_"]


def localizar_recurso_mais_recente() -> tuple[str, str]:
    """Acha o recurso ZIP mais recente pelo mes/ano no NOME do recurso
    no CKAN ("RENAEST - Mensal - MM-AAAA"), nao pelo campo 'created'
    (que e a data de upload, defasada do mes de referencia real — ver
    docstring do modulo)."""
    resposta = requests.get(CKAN_PACKAGE_URL, params={"id": DATASET_ID}, timeout=60)
    resposta.raise_for_status()
    recursos = resposta.json()["result"]["resources"]

    candidatos = []
    for r in recursos:
        m = re.search(r"Mensal\s*-\s*(\d{2})-(\d{4})", r.get("name", ""))
        if not m or not r.get("url"):
            continue
        mes, ano = int(m.group(1)), int(m.group(2))
        candidatos.append(((ano, mes), r["url"], r["name"]))

    if not candidatos:
        raise SystemExit(
            "[erro] nenhum recurso RENAEST com nome 'Mensal - MM-AAAA' "
            "reconhecivel encontrado — layout do dataset em "
            "dados.transportes.gov.br pode ter mudado."
        )

    _, url, nome = max(candidatos, key=lambda item: item[0])
    return url, nome


def baixar_zip(url: str) -> Path:
    destino = RAW_DIR / url.rsplit("/", 1)[-1]
    if destino.exists():
        print(f"[info] {destino.name} ja baixado, pulando download")
        return destino
    print(f"[info] baixando {url} (arquivo grande, ~500MB)...")
    resposta = requests.get(url, timeout=REQUEST_TIMEOUT)
    resposta.raise_for_status()
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(resposta.content)
    print(f"[info] salvo em {destino} ({len(resposta.content):,} bytes)")
    return destino


def extrair_csvs(zip_path: Path) -> dict[str, Path]:
    """Extrai so os 3 CSVs necessarios (Acidentes, TipoVeiculo,
    Localidade) — Vitimas fica fora do escopo desta ingestao (ver
    docstring do modulo) e nao e extraido, pra economizar ~1,8GB."""
    extraidos: dict[str, Path] = {}
    with zipfile.ZipFile(zip_path) as z:
        for prefixo in ARQUIVOS_NECESSARIOS:
            nomes = [n for n in z.namelist() if n.startswith(prefixo)]
            if len(nomes) != 1:
                raise SystemExit(
                    f"[erro] esperava exatamente 1 arquivo comecando com "
                    f"'{prefixo}' no zip, encontrou {len(nomes)}: {nomes}"
                )
            nome = nomes[0]
            destino = RAW_DIR / nome
            if not destino.exists():
                print(f"[info] extraindo {nome} ({z.getinfo(nome).file_size:,} bytes)...")
                with z.open(nome) as origem, open(destino, "wb") as saida:
                    shutil.copyfileobj(origem, saida)
            chave = prefixo.rstrip("_").lower()
            extraidos[chave] = destino
    return extraidos


def ingerir(
    con: duckdb.DuckDBPyConnection, csvs: dict[str, Path], ano_corte: int | None
) -> None:
    filtro_ano = f"WHERE ano_acidente >= {ano_corte}" if ano_corte is not None else ""
    print(
        f"[info] agregando acidentes ({'desde ' + str(ano_corte) if ano_corte else 'serie completa'}) "
        "no DuckDB — pode levar varios minutos (arquivo fonte tem dezenas de milhoes de linhas)..."
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE senatran_acidentes AS
        WITH acidentes_recentes AS (
            SELECT num_acidente, uf_acidente AS uf, codigo_ibge, ano_acidente, mes_acidente
            FROM read_csv(?, delim=';', header=true)
            {filtro_ano}
        ),
        municipios AS (
            SELECT codigo_ibge, arg_max(municipio, mes_ano_referencia) AS municipio
            FROM read_csv(?, delim=';', header=true)
            GROUP BY codigo_ibge
        )
        SELECT
            a.uf,
            a.codigo_ibge,
            m.municipio,
            a.ano_acidente,
            a.mes_acidente,
            t.tipo_veiculo,
            COUNT(DISTINCT a.num_acidente) AS quantidade_acidentes,
            CAST(SUM(t.qtde_veiculos) AS BIGINT) AS quantidade_veiculos
        FROM acidentes_recentes a
        JOIN read_csv(?, delim=';', header=true) t ON a.num_acidente = t.num_acidente
        LEFT JOIN municipios m ON a.codigo_ibge = m.codigo_ibge
        GROUP BY a.uf, a.codigo_ibge, m.municipio, a.ano_acidente, a.mes_acidente, t.tipo_veiculo
        """,
        [csvs["acidentes"].as_posix(), csvs["localidade"].as_posix(), csvs["tipoveiculo"].as_posix()],
    )

    total = con.execute("SELECT COUNT(*) FROM senatran_acidentes").fetchone()[0]
    acidentes_distintos = con.execute(
        "SELECT COUNT(DISTINCT (uf, codigo_ibge, ano_acidente, mes_acidente)) FROM senatran_acidentes"
    ).fetchone()[0]
    periodo = con.execute(
        "SELECT MIN(ano_acidente), MAX(ano_acidente) FROM senatran_acidentes"
    ).fetchone()
    tipos = con.execute("SELECT COUNT(DISTINCT tipo_veiculo) FROM senatran_acidentes").fetchone()[0]
    print(
        f"[info] senatran_acidentes: {total:,} linhas (uf/municipio/mes/tipo_veiculo), "
        f"{tipos} tipos de veiculo distintos, periodo {periodo[0]}-{periodo[1]}, "
        f"{acidentes_distintos:,} combinacoes uf/municipio/mes distintas"
    )


def get_connection() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument(
        "--anos-recentes", type=int, default=3,
        help="Carrega so os acidentes dos ultimos N anos (default: 3).",
    )
    grupo.add_argument(
        "--tudo", action="store_true",
        help="Carrega a serie completa (desde outubro/2021), ignora --anos-recentes.",
    )
    parser.add_argument(
        "--arquivo-local", type=Path, default=None,
        help="Usa um ZIP RENAEST ja baixado em vez de consultar o CKAN.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    if args.arquivo_local is not None:
        if not args.arquivo_local.exists():
            raise SystemExit(f"[erro] arquivo local informado nao existe: {args.arquivo_local}")
        zip_path = args.arquivo_local
    else:
        url, nome = localizar_recurso_mais_recente()
        print(f"[info] recurso mais recente encontrado: {nome} ({url})")
        zip_path = baixar_zip(url)

    csvs = extrair_csvs(zip_path)
    ano_corte = None if args.tudo else date.today().year - args.anos_recentes + 1

    con = get_connection()
    try:
        ingerir(con, csvs, ano_corte)
    finally:
        con.close()
        # So o zip fica em data/raw/ — os CSVs extraidos somam ~3GB e sao
        # so intermediarios (mesma logica de espaco em disco do susep.py).
        for caminho in csvs.values():
            caminho.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
