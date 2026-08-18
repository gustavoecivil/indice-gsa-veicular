"""
Backfill do historico de precos da FIPE (plano pago, fipe.api.br) pra
alem dos 12 meses ja cobertos por fipe_historico.py.

fipe_historico.py cobre os ultimos 12 meses (setembro/2025 a
agosto/2026 no momento em que este script foi escrito) porque e o que a
rota `priceHistory` devolve automaticamente. Este script busca meses
MAIS ANTIGOS, um de cada vez, usando o parametro de query `reference`
da API v2 — testado e validado manualmente antes de escrever o script:

    GET /{vehicleType}/brands/{marca}/models/{modelo}/years/{ano}?reference=<codigo>

`reference` e um codigo inteiro que identifica uma tabela mensal da
FIPE. `GET /references` lista todos os codigos disponiveis — confirmado
que vai de 62 (janeiro/2001) a 336 (agosto/2026, o mes mais recente no
momento), 308 tabelas ao todo, numeracao sequencial sem buracos (1
codigo = 1 mes). O campo `referenceMonth` da resposta bate EXATAMENTE
com o formato ja gravado em fipe_historico_precos.mes_referencia (ex.:
`reference=325` devolveu `"referenceMonth": "setembro de 2025"`, que e
o mes mais antigo que ja tinhamos — confirma que os dois metodos
produzem o mesmo formato de string, sem risco de duplicar o mesmo mes
com grafias diferentes).

**Alcance desta execucao vs. do mecanismo**: o alvo concreto pedido foi
"mais 1 ano de historico" — ou seja, os `--meses-extras` (default 12)
codigos de reference imediatamente anteriores a fronteira fixa que
fipe_historico.py mantem (o mes mais recente disponivel em /references,
menos 11 — a janela rolante de 12 meses que a rota `priceHistory` sempre
devolve). Esse alvo e um INTERVALO FIXO calculado a partir de
/references (nao de fipe_historico_precos) — cada execucao volta a
trabalhar no mesmo intervalo ate ele estar 100% completo (todo
combo x mes com checkpoint), e so entao pararia de ter trabalho.

**Bug real encontrado e corrigido durante o teste manual**: a primeira
versao deste script calculava o alvo com `MIN(mes_referencia)` sobre
fipe_historico_precos — mas mes_referencia e uma string tipo "abril de
2026", e MIN() de VARCHAR compara alfabeticamente, nao
cronologicamente ("abril de 2026" < "setembro de 2025" porque 'a' vem
antes de 's', mesmo abril/2026 sendo mais recente). Isso fez a primeira
execucao mirar no mes errado. Pior: mesmo corrigindo pra usar o codigo
numerico certo, uma versao seguinte recalculava o alvo a cada execucao
a partir de "qual e o mes mais antigo que ja tem ALGUM dado" — como o
teste de 5 minutos so processou ~20 das 30.433 combinacoes de um mes
antes de parar por tempo, esse mes passava a contar como "ja coberto" e
o script pulava pro proximo bloco de 12 meses, abandonando as ~30.400
combinacoes restantes daquele mes pra sempre (nenhum codigo de
reference seria re-visitado depois de avancar). Por isso o alvo agora e
um intervalo FIXO (nao recalculado a partir do que existe em
fipe_historico_precos) e o progresso e medido comparando
fipe_backfill_checkpoint contra o tamanho do catalogo (fipe_anos) pra
cada codigo — so avanca pro proximo alvo quando o atual estiver
genuinamente 100% completo. Ampliar o alcance pra alem de "1 ano extra"
no futuro e so rodar de novo com `--meses-extras` maior (ex.: 24, 60...
ate o limite de 274 pra chegar em janeiro/2001) — nao e automatico por
design, pra manter o escopo previsivel em vez de crescer sozinho.

**Por que isso demora dias, nao minutos**: 30.433 combinacoes
marca/modelo/ano x 12 meses-alvo = ~365 mil chamadas HTTP so pra 1 ano
de historico, a 0,15s de rate limit = ~15h de trabalho continuo. Por
isso o script:
  - roda no MAXIMO `--tempo-maximo-minutos` (default 120 = 2h) por
    execucao e para sozinho quando o tempo acaba (nao tenta processar
    tudo de uma vez);
  - salva checkpoint por combinacao de (marca, modelo, ano, reference)
    em fipe_backfill_checkpoint — nao em fipe_historico_checkpoint
    (tabela separada porque a granularidade e diferente: o script
    mensal so precisa saber "esse combo ja foi processado", esse aqui
    precisa saber "esse combo, NESSE mes especifico, ja foi
    processado", ja que agora sao dezenas de meses possiveis, nao um
    so);
  - e IDEMPOTENTE: os precos em si vao pra fipe_historico_precos com o
    mesmo INSERT OR REPLACE / PRIMARY KEY (codigo_fipe, ano_modelo,
    combustivel, mes_referencia) do fipe_historico.py — rodar de novo
    sobre o mesmo mes nao duplica nem precisa de deduplicacao extra.
  - referencias muito antigas (testado ate 2001) responderam bem mais
    lentas/instaveis que as recentes nos testes manuales (timeout em
    40s onde referencias de 2024/2025 respondem em menos de 1s) — o
    timeout de requisicao aqui e maior que o do fipe_historico.py
    (40s vs 20s) por causa disso, com o mesmo retry/backoff.
  - combos que genuinamente NAO EXISTEM naquele mes (veiculo lancado
    depois da tabela de referencia pedida) sao esperados e normais
    aqui — diferente do historico recente, onde toda combinacao do
    catalogo atual deveria ter preco em todos os ultimos 12 meses. Um
    erro 400/404 da API pra uma combinacao marca/modelo/ano num
    reference antigo e tratado como "sem dado nesse mes" (marca
    checkpoint como processado, NAO conta como falha de rede) — assim
    o script nao fica tentando de novo toda noite algo que sabemos que
    nao existe.

Uso:
    python scripts/ingestao/fipe_backfill_historico.py
    python scripts/ingestao/fipe_backfill_historico.py --tempo-maximo-minutos 5   # teste rapido
    python scripts/ingestao/fipe_backfill_historico.py --meses-extras 6           # so 6 meses a mais, nao 12
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "processed" / "indice_gsa.duckdb"
ERROR_LOG_PATH = BASE_DIR / "data" / "raw" / "fipe_backfill_erros.log"

API_BASE_URL = "https://fipe.parallelum.com.br/api/v2"
VEHICLE_TYPE = "cars"

RATE_LIMIT_SECONDS = 0.15
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2
REQUEST_TIMEOUT = 40  # referencias antigas respondem mais devagar, ver docstring

MESES_EXTRAS_PADRAO = 12
TEMPO_MAXIMO_MINUTOS_PADRAO = 120

PRICE_RE = re.compile(r"[^\d,]")
ERROS_HTTP_SEM_DADO = {400, 404}


def build_logger() -> logging.Logger:
    ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("fipe_backfill_ingestao")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(ERROR_LOG_PATH, encoding="utf-8")
    file_handler.setLevel(logging.ERROR)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)

    return logger


logger = build_logger()


def parse_preco(preco_texto: str) -> float:
    limpo = PRICE_RE.sub("", preco_texto)
    limpo = limpo.replace(".", "").replace(",", ".")
    return float(limpo)


class SemDadoNesseMes(Exception):
    """Combinacao marca/modelo/ano genuinamente sem preco nesse reference
    (veiculo ainda nao existia, por exemplo) — nao e falha de rede."""


def fetch_json(session: requests.Session, url: str, headers: dict, params: dict, context: str):
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)

            if response.status_code == 200:
                time.sleep(RATE_LIMIT_SECONDS)
                return response.json()

            if response.status_code in ERROS_HTTP_SEM_DADO:
                time.sleep(RATE_LIMIT_SECONDS)
                raise SemDadoNesseMes(f"HTTP {response.status_code} em {context}")

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else BACKOFF_BASE_SECONDS ** attempt
                last_error = RuntimeError(f"HTTP 429 (rate limit) em {url}")
                logger.info(f"  [aviso] 429 em {context} (tentativa {attempt}/{MAX_RETRIES}), aguardando {wait:.1f}s")
                time.sleep(wait)
                continue

            if response.status_code >= 500:
                last_error = RuntimeError(f"HTTP {response.status_code} em {url}")
                logger.info(f"  [aviso] erro {response.status_code} em {context} (tentativa {attempt}/{MAX_RETRIES})")
                time.sleep(BACKOFF_BASE_SECONDS ** attempt)
                continue

            response.raise_for_status()

        except SemDadoNesseMes:
            raise
        except requests.RequestException as exc:
            last_error = exc
            logger.info(f"  [aviso] falha de conexao em {context} (tentativa {attempt}/{MAX_RETRIES}): {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_BASE_SECONDS ** attempt)

    time.sleep(RATE_LIMIT_SECONDS)
    raise RuntimeError(f"Falha ao buscar {context} apos {MAX_RETRIES} tentativas: {last_error}")


def listar_referencias(session: requests.Session, headers: dict) -> list[dict]:
    """GET /references — lista todos os codigos de tabela mensal
    disponiveis na API, do mais recente ao mais antigo."""
    data = fetch_json(session, f"{API_BASE_URL}/references", headers, {}, "lista de references")
    return data


def get_connection() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    # Mesma tabela final do fipe_historico.py — o backfill so preenche
    # meses que faltam nela, com o mesmo schema/chave primaria.
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
    # Checkpoint proprio do backfill — granularidade (combo, reference),
    # diferente do fipe_historico_checkpoint (so combo), ver docstring.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS fipe_backfill_checkpoint (
            codigo_marca VARCHAR NOT NULL,
            codigo_modelo VARCHAR NOT NULL,
            codigo_ano VARCHAR NOT NULL,
            codigo_referencia VARCHAR NOT NULL,
            teve_dado BOOLEAN NOT NULL,
            processado_em TIMESTAMP NOT NULL,
            PRIMARY KEY (codigo_marca, codigo_modelo, codigo_ano, codigo_referencia)
        )
        """
    )
    return con


def normalizar_mes_referencia(mes_barra: str) -> str:
    """Converte o formato de /references ('setembro/2025') pro mesmo
    formato ja usado em fipe_historico_precos.mes_referencia
    ('setembro de 2025'), confirmado identico ao que a propria API
    devolve no campo referenceMonth da rota de detalhe."""
    mes, ano = mes_barra.split("/")
    return f"{mes} de {ano}"


def determinar_referencias_alvo(referencias_api: list[dict], meses_extras: int) -> list[tuple[str, str]]:
    """Calcula um intervalo FIXO de codigos de reference: os
    `meses_extras` codigos imediatamente anteriores a fronteira que
    fipe_historico.py mantem (mes mais recente disponivel em
    /references, menos 11 — a janela rolante de 12 meses que a rota
    `priceHistory` sempre devolve).

    Deliberadamente NAO depende do conteudo de fipe_historico_precos
    (nem de checkpoint) pra decidir o alvo — só de /references, que e
    estavel. Ver docstring do modulo pro bug que isso substituiu
    (recalcular o alvo a partir de "qual mes ja tem algum dado" fazia o
    script abandonar meses parcialmente processados)."""
    por_codigo = {int(r["code"]): normalizar_mes_referencia(r["month"]) for r in referencias_api}
    codigo_mais_recente = max(por_codigo)
    fronteira = codigo_mais_recente - 11  # mes mais antigo da janela rolante do fipe_historico.py

    alvo = []
    for codigo in range(fronteira - 1, fronteira - 1 - meses_extras, -1):
        if codigo not in por_codigo:
            logger.info(f"[aviso] codigo de reference {codigo} nao existe em /references, parando a busca pra tras aqui.")
            break
        alvo.append((str(codigo), por_codigo[codigo]))

    return alvo


def contar_pendentes_por_codigo(con: duckdb.DuckDBPyConnection, codigo_referencia: str) -> int:
    total_catalogo = con.execute("SELECT COUNT(*) FROM fipe_anos").fetchone()[0]
    ja_checkpointados = con.execute(
        "SELECT COUNT(*) FROM fipe_backfill_checkpoint WHERE codigo_referencia = ?", [codigo_referencia]
    ).fetchone()[0]
    return total_catalogo - ja_checkpointados


def montar_lista_trabalho(
    con: duckdb.DuckDBPyConnection, alvo: list[tuple[str, str]]
) -> list[tuple[str, str, str, str, str]]:
    """Devolve o trabalho pendente do PRIMEIRO codigo do alvo que ainda
    nao esta 100% completo (nao do alvo inteiro de uma vez) — garante
    que um codigo e esgotado antes de avancar pro proximo, em vez de
    espalhar poucas combinacoes por varios meses diferentes."""
    for codigo_referencia, mes_referencia in alvo:
        pendentes = contar_pendentes_por_codigo(con, codigo_referencia)
        if pendentes == 0:
            continue

        logger.info(f"[info] trabalhando em {mes_referencia} (reference={codigo_referencia}) — {pendentes} combinacoes pendentes")
        return con.execute(
            """
            SELECT a.codigo_marca, a.codigo_modelo, a.codigo_ano, ?, ?
            FROM fipe_anos a
            LEFT JOIN fipe_backfill_checkpoint c
                ON a.codigo_marca = c.codigo_marca
                AND a.codigo_modelo = c.codigo_modelo
                AND a.codigo_ano = c.codigo_ano
                AND c.codigo_referencia = ?
            WHERE c.codigo_marca IS NULL
            ORDER BY a.codigo_marca, a.codigo_modelo, a.codigo_ano
            """,
            [codigo_referencia, mes_referencia, codigo_referencia],
        ).fetchall()

    return []


def marcar_checkpoint(
    con: duckdb.DuckDBPyConnection,
    codigo_marca: str, codigo_modelo: str, codigo_ano: str, codigo_referencia: str,
    teve_dado: bool, now: datetime,
) -> None:
    con.execute(
        "INSERT OR REPLACE INTO fipe_backfill_checkpoint VALUES (?, ?, ?, ?, ?, ?)",
        [codigo_marca, codigo_modelo, codigo_ano, codigo_referencia, teve_dado, now],
    )


def upsert_preco(
    con: duckdb.DuckDBPyConnection,
    codigo_fipe: str, marca: str, modelo: str, ano_modelo: int, combustivel: str,
    mes_referencia: str, valor: float, now: datetime,
) -> None:
    con.execute(
        "INSERT OR REPLACE INTO fipe_historico_precos VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [codigo_fipe, marca, modelo, ano_modelo, combustivel, mes_referencia, valor, now],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--tempo-maximo-minutos", type=int, default=TEMPO_MAXIMO_MINUTOS_PADRAO,
        help=f"Para a execucao apos esse tempo, salvando checkpoint (default: {TEMPO_MAXIMO_MINUTOS_PADRAO}).",
    )
    parser.add_argument(
        "--meses-extras", type=int, default=MESES_EXTRAS_PADRAO,
        help=f"Quantos meses a mais buscar pra tras do mes mais antigo ja gravado (default: {MESES_EXTRAS_PADRAO} = 1 ano).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv(BASE_DIR / ".env")
    token = os.getenv("FIPE_API_TOKEN", "").strip()

    if not token:
        logger.error("FIPE_API_TOKEN nao definido no .env — necessario pro historico (recurso do plano pago).")
        sys.exit(1)

    headers = {"X-Subscription-Token": token}
    session = requests.Session()
    con = get_connection()
    inicio = time.monotonic()
    limite_segundos = args.tempo_maximo_minutos * 60

    try:
        referencias_api = listar_referencias(session, headers)
        alvo = determinar_referencias_alvo(referencias_api, args.meses_extras)
        if not alvo:
            logger.info("[info] nenhum mes-alvo novo pra buscar (ja no limite de /references disponiveis).")
            return

        logger.info(
            f"[info] intervalo-alvo (--meses-extras {args.meses_extras}): {alvo[-1][1]} ate {alvo[0][1]} "
            f"(codigos {alvo[-1][0]}-{alvo[0][0]})"
        )

        trabalho = montar_lista_trabalho(con, alvo)
        total_pendente = len(trabalho)
        if total_pendente == 0:
            logger.info(
                f"[info] intervalo-alvo inteiro ja completo ({len(alvo)} meses, 100% checkpointado) — "
                "nada a processar. Aumente --meses-extras pra buscar mais historico."
            )
            return
        logger.info(
            f"[info] {total_pendente} combinacoes pendentes no mes em processamento — "
            f"limite de {args.tempo_maximo_minutos} min nesta execucao\n"
        )

        total_gravados = 0
        total_sem_dado = 0
        total_falhas = 0
        parou_por_tempo = False

        for i, (codigo_marca, codigo_modelo, codigo_ano, codigo_referencia, mes_referencia) in enumerate(trabalho, start=1):
            if time.monotonic() - inicio >= limite_segundos:
                parou_por_tempo = True
                break

            context = f"{codigo_marca}/{codigo_modelo}/{codigo_ano} @ reference={codigo_referencia} ({mes_referencia})"
            now = datetime.now(timezone.utc)
            try:
                detalhe = fetch_json(
                    session,
                    f"{API_BASE_URL}/{VEHICLE_TYPE}/brands/{codigo_marca}/models/{codigo_modelo}/years/{codigo_ano}",
                    headers,
                    {"reference": codigo_referencia},
                    context,
                )
            except SemDadoNesseMes:
                marcar_checkpoint(con, codigo_marca, codigo_modelo, codigo_ano, codigo_referencia, False, now)
                total_sem_dado += 1
                continue
            except RuntimeError as exc:
                logger.error(f"Falha (rede) em {context}: {exc}")
                total_falhas += 1
                continue

            try:
                valor = parse_preco(detalhe["price"])
            except (KeyError, ValueError) as exc:
                logger.error(f"Preco invalido em {context}: {detalhe!r} ({exc})")
                total_falhas += 1
                continue

            upsert_preco(
                con,
                detalhe.get("codeFipe", ""),
                detalhe.get("brand", ""),
                detalhe.get("model", ""),
                detalhe.get("modelYear"),
                detalhe.get("fuel", ""),
                detalhe.get("referenceMonth", mes_referencia),
                valor,
                now,
            )
            marcar_checkpoint(con, codigo_marca, codigo_modelo, codigo_ano, codigo_referencia, True, now)
            total_gravados += 1

            if i % 200 == 0:
                decorrido_min = (time.monotonic() - inicio) / 60
                logger.info(
                    f"  ... {i}/{total_pendente} ({decorrido_min:.1f} min decorridos) — "
                    f"{total_gravados} gravados, {total_sem_dado} sem dado, {total_falhas} falhas"
                )

        total_db = con.execute("SELECT COUNT(*) FROM fipe_historico_precos").fetchone()[0]
        meses_distintos = con.execute("SELECT COUNT(DISTINCT mes_referencia) FROM fipe_historico_precos").fetchone()[0]
        mes_mais_antigo_final = con.execute("SELECT MIN(mes_referencia) FROM fipe_historico_precos").fetchone()[0]

        logger.info("\n===== Resumo do backfill =====")
        logger.info(f"Parou por tempo esgotado: {parou_por_tempo} (limite {args.tempo_maximo_minutos} min)")
        logger.info(f"Combinacoes processadas nesta execucao: {min(i, total_pendente)}/{total_pendente}")
        logger.info(f"Precos gravados:  {total_gravados}")
        logger.info(f"Sem dado nesse mes (esperado, nao e erro): {total_sem_dado}")
        logger.info(f"Falhas de rede (retomam na proxima execucao): {total_falhas}")
        logger.info("--- Totais atuais no DuckDB ---")
        logger.info(f"fipe_historico_precos: {total_db} linhas, {meses_distintos} meses distintos, mes mais antigo: {mes_mais_antigo_final}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
