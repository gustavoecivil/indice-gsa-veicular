"""
Ingestao da Tarifa de Energia (TE, em R$/kWh) residencial homologada por
distribuidora, publicada pela ANEEL, pra completar o suporte a veiculos
eletricos no Indice GSA (custo de "abastecer" um eletrico).

Fonte oficial: dataset "Tarifas homologadas das distribuidoras de energia
eletrica" do Portal de Dados Abertos da ANEEL (roda em CKAN):
    https://dadosabertos.aneel.gov.br/dataset/5a583f3e-1646-4f67-bf0f-69db4203e89e
    Recurso CSV:
    https://dadosabertos.aneel.gov.br/dataset/5a583f3e-1646-4f67-bf0f-69db4203e89e/
    resource/fcf2906c-7c32-4b9b-a637-054e7a5234f4/download/
    tarifas-homologadas-distribuidoras-energia-eletrica.csv

Investigacao feita antes de escrever o script (ver docs/FONTES.md pro
detalhamento completo):
  - A API de busca do portal (package_search) e o dominio inteiro
    dadosabertos.aneel.gov.br estavam INACESSIVEIS no momento desta
    ingestao (conexao fechada pelo servidor, testado tanto via requisicao
    direta quanto via navegador real — nao e bloqueio de IP, o dominio
    parece fora do ar). O portalrelatorios.aneel.gov.br ("Ranking das
    Tarifas") respondeu normalmente, mas e um relatorio Power BI incorporado
    (renderizacao visual via powerbi.js), sem CSV/API exportavel por tras —
    confirmado inspecionando a pagina, nao assumido.
  - Como fallback, o dataset foi obtido via web.archive.org (Wayback
    Machine), que tinha uma copia do CSV oficial capturada em 2025-05-02
    (e evidencias de que o portal ainda estava no ar em maio/2026, entao a
    indisponibilidade parece recente/temporaria, nao uma descontinuacao).
  - O CSV NAO tem coluna de UF — so `SigAgente` (sigla da distribuidora) e
    `NumCNPJDistribuidora`. O mapeamento distribuidora -> UF usado aqui e
    o UF de registro do CNPJ (via API publica minhareceita.org), que pra
    uma distribuidora de energia (atividade principal exclusiva de
    distribuicao, presenca local obrigatoria pra operar a concessao) e um
    proxy confiavel da UF da area de concessao. Isso NAO cobre o caso raro
    de uma unica distribuidora atender partes de mais de uma UF a partir
    da mesma matriz — nao identificado nas ~102 distribuidoras atuais,
    mas e uma limitacao conhecida do metodo, documentada aqui em vez de
    ignorada.
  - Varias UFs tem multiplas distribuidoras (SC: 26, SP: 19, RS: 19, PR:
    5, RJ: 5, MG: 3, SE: 3, GO: 2, ES: 2) — sobretudo cooperativas rurais
    pequenas concentradas em SC/RS. As outras 18 UFs tem exatamente uma
    distribuidora residencial cada. Quando ha mais de uma, a tarifa por
    UF (no export estatico, nao nesta tabela) e a MEDIA SIMPLES entre
    elas — nao ponderada por numero de consumidores/unidades atendidas,
    entao uma cooperativa minuscula pesa igual a uma distribuidora grande
    do mesmo estado. Simplificacao aceitavel pro escopo (estimativa de
    custo de recarga), documentada em vez de escondida.

O que a tabela representa: `aneel_tarifa_residencial` tem, por
distribuidora (`distribuidora` = SigAgente) e sua UF de concessao
(`uf`), a Tarifa de Energia (TE) residencial atualmente vigente — classe
"Residencial", subclasse "Residencial" (exclui a subclasse subsidiada
"Baixa Renda"), subgrupo B1 (baixa tensao), modalidade "Convencional"
(exclui tarifa branca, pre-pagamento e ABRACE) e base tarifaria "Tarifa
de Aplicacao" (o valor efetivamente cobrado do consumidor, nao a "Base
Economica"). "Vigente" = a resolucao homologatoria cujo periodo
[DatInicioVigencia, DatFimVigencia] contem a data de geracao do arquivo
fonte.

Frequencia de atualizacao da fonte: por reajuste tarifario — cada
distribuidora tem seu proprio ciclo (normalmente anual), entao o arquivo
inteiro e atualizado continuamente pela ANEEL conforme cada distribuidora
e reajustada.

Grava em data/processed/indice_gsa.duckdb, tabelas:
  - aneel_distribuidora_uf: cache do mapeamento CNPJ -> UF (evita bater
    na API minhareceita.org de novo a cada execucao).
  - aneel_tarifa_residencial: tabela final (uf, distribuidora,
    cnpj_distribuidora, tarifa_te_reais_kwh, data_inicio_vigencia,
    data_fim_vigencia, data_referencia, atualizado_em). Full-refresh a
    cada execucao (a tabela representa "o estado vigente agora", nao uma
    serie historica — ~100 linhas, barato recriar).

Uso:
    python scripts/ingestao/aneel_tarifas.py
    python scripts/ingestao/aneel_tarifas.py --arquivo-local caminho.csv   # pula o download, usa CSV local
    python scripts/ingestao/aneel_tarifas.py --forcar-uf                  # ignora o cache e reconsulta todos os CNPJs
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import requests

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "processed" / "indice_gsa.duckdb"

CSV_URL = (
    "https://dadosabertos.aneel.gov.br/dataset/5a583f3e-1646-4f67-bf0f-69db4203e89e/"
    "resource/fcf2906c-7c32-4b9b-a637-054e7a5234f4/download/"
    "tarifas-homologadas-distribuidoras-energia-eletrica.csv"
)
CSV_PATH = BASE_DIR / "data" / "raw" / "tarifas-homologadas-distribuidoras-energia-eletrica.csv"
CNPJ_API_URL = "https://minhareceita.org/{cnpj}"
REQUEST_TIMEOUT = 120


def baixar_csv(arquivo_local: Path | None) -> Path:
    if arquivo_local is not None:
        if not arquivo_local.exists():
            raise SystemExit(f"[erro] arquivo local informado nao existe: {arquivo_local}")
        print(f"[info] usando CSV local informado: {arquivo_local}")
        return arquivo_local

    print(f"[info] baixando CSV da ANEEL de {CSV_URL} ...")
    try:
        resposta = requests.get(CSV_URL, timeout=REQUEST_TIMEOUT)
        resposta.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise SystemExit(
            "[erro] nao foi possivel baixar o CSV direto de dadosabertos.aneel.gov.br "
            f"({e!r}). O dominio ja esteve fora do ar durante o desenvolvimento deste "
            "script (ver docs/FONTES.md) — se o problema persistir, baixe manualmente "
            f"o recurso em {CSV_URL} (ou a copia mais recente disponivel em "
            "https://web.archive.org/web/*/" + CSV_URL + ") e rode de novo com "
            "--arquivo-local caminho/pro/arquivo.csv."
        )
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    CSV_PATH.write_bytes(resposta.content)
    print(f"[info] CSV salvo em {CSV_PATH} ({len(resposta.content):,} bytes)")
    return CSV_PATH


def get_connection() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS aneel_distribuidora_uf (
            cnpj_distribuidora VARCHAR PRIMARY KEY,
            sig_agente VARCHAR NOT NULL,
            uf VARCHAR,
            atualizado_em TIMESTAMP NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS aneel_tarifa_residencial (
            uf VARCHAR NOT NULL,
            distribuidora VARCHAR NOT NULL,
            cnpj_distribuidora VARCHAR NOT NULL,
            tarifa_te_reais_kwh DOUBLE NOT NULL,
            data_inicio_vigencia DATE NOT NULL,
            data_fim_vigencia DATE NOT NULL,
            data_referencia DATE NOT NULL,
            atualizado_em TIMESTAMP NOT NULL,
            PRIMARY KEY (cnpj_distribuidora, data_inicio_vigencia)
        )
        """
    )
    return con


def carregar_tarifas_vigentes(con: duckdb.DuckDBPyConnection, csv_path: Path):
    """Le o CSV da ANEEL e filtra a tarifa residencial (TE) vigente por
    distribuidora: classe Residencial, subclasse Residencial (exclui
    Baixa Renda), subgrupo B1, modalidade Convencional, base tarifaria
    "Tarifa de Aplicacao", com a data de geracao do arquivo dentro do
    periodo de vigencia da resolucao."""
    con.execute(
        f"""
        CREATE OR REPLACE VIEW tarifas_aneel_raw AS
        SELECT * FROM read_csv(
            '{csv_path.as_posix()}',
            delim=';', quote='"', header=true, encoding='cp1252', ignore_errors=true
        )
        """
    )

    data_referencia = con.execute(
        "SELECT MAX(DatGeracaoConjuntoDados) FROM tarifas_aneel_raw"
    ).fetchone()[0]
    if data_referencia is None:
        raise SystemExit("[erro] CSV vazio ou sem coluna DatGeracaoConjuntoDados reconhecivel.")

    linhas = con.execute(
        """
        SELECT SigAgente, NumCNPJDistribuidora, DatInicioVigencia, DatFimVigencia, VlrTE
        FROM tarifas_aneel_raw
        WHERE DscClasse = 'Residencial'
          AND DscSubGrupo = 'B1'
          AND DscSubClasse = 'Residencial'
          AND DscDetalhe = 'Não se aplica'
          AND DscModalidadeTarifaria = 'Convencional'
          AND DscBaseTarifaria = 'Tarifa de Aplicação'
          AND ? BETWEEN DatInicioVigencia AND DatFimVigencia
          AND SigAgente <> 'Não Informado'
        ORDER BY SigAgente
        """,
        [data_referencia],
    ).fetchall()

    return linhas, data_referencia


def resolver_uf_por_cnpj(
    con: duckdb.DuckDBPyConnection, cnpjs: list[str], forcar: bool
) -> dict[str, str | None]:
    """Resolve UF de cada CNPJ de distribuidora via cache local
    (aneel_distribuidora_uf) + API publica minhareceita.org pros que
    faltarem. A UF de registro do CNPJ e usada como proxy da UF da area
    de concessao (ver docstring do modulo pra limitacoes do metodo)."""
    cache_existente = {}
    if not forcar:
        cache_existente = dict(
            con.execute("SELECT cnpj_distribuidora, uf FROM aneel_distribuidora_uf").fetchall()
        )

    faltando = [c for c in cnpjs if c not in cache_existente]
    if faltando:
        print(
            f"[info] resolvendo UF via minhareceita.org pra {len(faltando)} CNPJ(s) "
            f"(de {len(cnpjs)} distribuidoras) — {len(cnpjs) - len(faltando)} ja em cache."
        )
    resolvidos_agora = {}
    now = datetime.now(timezone.utc)
    for i, cnpj in enumerate(faltando, start=1):
        try:
            r = requests.get(CNPJ_API_URL.format(cnpj=cnpj), timeout=30)
            uf = r.json().get("uf") if r.status_code == 200 else None
            if uf is None:
                print(f"[aviso] nao consegui resolver UF pro CNPJ {cnpj} (status {r.status_code}).")
        except requests.exceptions.RequestException as e:
            uf = None
            print(f"[aviso] erro consultando CNPJ {cnpj}: {e!r}")
        resolvidos_agora[cnpj] = uf
        if i % 20 == 0:
            print(f"[info] {i}/{len(faltando)} CNPJs resolvidos...")

    if resolvidos_agora:
        con.executemany(
            """
            INSERT INTO aneel_distribuidora_uf (cnpj_distribuidora, sig_agente, uf, atualizado_em)
            VALUES (?, '', ?, ?)
            ON CONFLICT (cnpj_distribuidora) DO UPDATE SET uf = excluded.uf, atualizado_em = excluded.atualizado_em
            """,
            [(cnpj, uf, now) for cnpj, uf in resolvidos_agora.items()],
        )

    return {**cache_existente, **resolvidos_agora}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arquivo-local",
        type=Path,
        default=None,
        help="Usa um CSV ja baixado em vez de tentar baixar de dadosabertos.aneel.gov.br "
        "(util quando o portal esta fora do ar — ver docs/FONTES.md).",
    )
    parser.add_argument(
        "--forcar-uf",
        action="store_true",
        help="Ignora o cache aneel_distribuidora_uf e reconsulta minhareceita.org pra todos os CNPJs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_path = baixar_csv(args.arquivo_local)

    con = get_connection()
    try:
        linhas, data_referencia = carregar_tarifas_vigentes(con, csv_path)
        if not linhas:
            print("[erro] nenhuma tarifa residencial vigente encontrada — abortando sem gravar.")
            sys.exit(1)

        cnpjs = sorted({cnpj for _, cnpj, _, _, _ in linhas})
        uf_por_cnpj = resolver_uf_por_cnpj(con, cnpjs, args.forcar_uf)

        now = datetime.now(timezone.utc)
        registros = []
        sem_uf = []
        for sig_agente, cnpj, inicio, fim, te_str in linhas:
            uf = uf_por_cnpj.get(cnpj)
            if uf is None:
                sem_uf.append(sig_agente)
                continue
            te_reais_mwh = float(te_str.replace(",", "."))
            registros.append(
                (uf, sig_agente, cnpj, round(te_reais_mwh / 1000, 4), inicio, fim, data_referencia, now)
            )

        if sem_uf:
            print(f"[aviso] {len(sem_uf)} distribuidora(s) sem UF resolvida, excluida(s): {sem_uf}")

        con.execute("DELETE FROM aneel_tarifa_residencial")
        con.executemany(
            """
            INSERT INTO aneel_tarifa_residencial VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            registros,
        )

        total = con.execute("SELECT COUNT(*) FROM aneel_tarifa_residencial").fetchone()[0]
        ufs_distintas = con.execute(
            "SELECT COUNT(DISTINCT uf) FROM aneel_tarifa_residencial"
        ).fetchone()[0]
        multi_distribuidora = con.execute(
            """
            SELECT uf, COUNT(*) n FROM aneel_tarifa_residencial
            GROUP BY uf HAVING COUNT(*) > 1 ORDER BY n DESC
            """
        ).fetchall()
        media_geral = con.execute(
            "SELECT AVG(tarifa_te_reais_kwh) FROM aneel_tarifa_residencial"
        ).fetchone()[0]

        print("\n===== Resumo da ingestao ANEEL - Tarifa Residencial (TE) =====")
        print(f"Data de referencia (geracao do arquivo fonte): {data_referencia}")
        print(f"Distribuidoras carregadas: {total}")
        print(f"UFs cobertas: {ufs_distintas} / 27")
        print(f"UFs com mais de uma distribuidora: {multi_distribuidora}")
        print(f"Tarifa TE media geral: R$ {media_geral:.4f}/kWh")
    finally:
        con.close()


if __name__ == "__main__":
    main()
