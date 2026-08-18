"""
Ingestao dos dados abertos da SUSEP: AUTOSEG (sinistralidade/premio do
seguro de automoveis) e IVR (Indice de Veiculos Roubados).

Investigacao feita antes de escrever o script (ver docs/FONTES.md pro
detalhamento completo):
  - A API antiga do CKAN em dados.gov.br (`/api/3/action/*`) agora exige
    token Bearer (401 em qualquer chamada, mesmo package_search) — o
    portal migrou pra um backend proprio. O endpoint real usado pelo
    frontend em React e `/api/publico/conjuntos-dados/...` (sem
    autenticacao), descoberto inspecionando as chamadas de rede do site
    no navegador, nao documentado publicamente.
  - **AUTOSEG** esta catalogado em dados.gov.br (dataset
    "dados-estatisticos-do-seguro-de-automoveis-autoseg", organizacao
    SUSEP), mas o catalogo esta parado desde 2021 — so lista 2 recursos
    reais (1º e 2º semestre de 2019). Nao ha recurso mais recente
    catalogado ali; os arquivos sao baixados diretamente do host da
    SUSEP (www2.susep.gov.br), nao do dados.gov.br em si (o dataset la e
    so um catalogo de links).
  - **IVR nao esta no catalogo de dados abertos.** As 11 datasets da
    SUSEP em dados.gov.br (conferidos via
    `/api/publico/conjuntos-dados/buscar?idOrganizacao=<id-susep>`) nao
    incluem IVR. O IVR vive inteiramente num sistema legado da SUSEP
    (www2.susep.gov.br/menuestatistica/RankRoubo), como uma ferramenta
    de consulta on-line (formulario ASP classico com filtros de
    categoria/regiao/sexo/idade), nao um arquivo pra baixar. Ingerido
    aqui via scraping do HTML de resultado com todos os filtros em
    "Todas"/"Todos" (agregado nacional por modelo).

O que cada tabela representa:
  - `susep_autoseg`: uma linha por combinacao
    categoria-tarifaria/regiao/modelo/ano-modelo/sexo/idade-do-condutor,
    com exposicao (veiculos-ano segurados), premio, importancia segurada
    media, frequencia de sinistros e indenizacoes — exatamente as
    colunas do arquivo `arq_casco_comp.csv` da SUSEP (a granularidade
    que bate com a descricao oficial do AUTOSEG: "classificadas de
    acordo com categoria, modelo e ano do veiculo, regiao... e perfil do
    segurado"). Os codigos (COD_TARIF, REGIAO, COD_MODELO, SEXO, IDADE)
    NAO sao decodificados aqui — ficam exatamente como vieram; as
    tabelas de-para (auto_cat.csv, auto_reg.csv, auto2_vei.csv etc, que
    tambem vem dentro do zip) nao foram ingeridas nesta rodada.
  - **Escopo consciente**: cada zip semestral do AUTOSEG tem, alem do
    `arq_casco_comp.csv` usado aqui, dois outros arquivos na mesma
    granularidade de exposicao/premio/sinistro mas com quebra geografica
    mais fina — `arq_casco3_comp.csv` (por CEP, ~1,1 GB descomprimido) e
    `arq_casco4_comp.csv` (por cidade, ~455 MB). Nao foram ingeridos
    nesta rodada pra manter o volume de dado gerenciavel (so o
    `arq_casco_comp.csv` de 2 semestres ja soma ~6,4 milhoes de linhas);
    ficam disponiveis nos zips baixados em data/raw/ se fizerem falta
    depois.
  - `susep_ivr`: o indice de roubo/furto (%) por modelo de veiculo,
    agregado nacionalmente (todas as categorias tarifarias, regioes,
    sexos e faixas etarias combinadas) — o mesmo agregado que a
    ferramenta da SUSEP mostra quando nenhum filtro especifico e
    escolhido. Sem indicacao explicita de mes/ano de referencia na
    pagina de resultado (limitacao da propria fonte, nao do script); o
    texto da SUSEP diz que reflete "o ultimo envio semestral". A
    presenca de modelos recentes (Fiat Argo, Renault Kwid etc, lancados
    2017+) com volume de exposicao consistente com frota atual sugere
    dado corrente, nao historico antigo.

Grava em data/processed/indice_gsa.duckdb, tabelas susep_autoseg e
susep_ivr. As duas ingestoes sao independentes — uma falhar nao impede a
outra de rodar.

Uso:
    python scripts/ingestao/susep.py
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path
from zipfile import ZipFile

import duckdb
import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "processed" / "indice_gsa.duckdb"
RAW_DIR = BASE_DIR / "data" / "raw"
REQUEST_TIMEOUT = 180

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

AUTOSEG_SEMESTRES = [
    ("autoseg_2019b", "https://www2.susep.gov.br/redarq.asp?arq=Autoseg2019B.zip", "2019B"),
    ("autoseg_2020a", "https://www2.susep.gov.br/redarq.asp?arq=Autoseg2020A.zip", "2020A"),
]
AUTOSEG_ARQUIVO_PRINCIPAL = "arq_casco_comp.csv"

IVR_MENU_URL = "https://www2.susep.gov.br/menuestatistica/RankRoubo/menu1.asp"
IVR_RESP_URL = "https://www2.susep.gov.br/menuestatistica/RankRoubo/resp_menu1.asp"
IVR_PAYLOAD_TODAS = {
    "cod_tarif": "Todas",
    "regiao": "Todas",
    "ano_modelo": "",
    "sexo": "Todos",
    "idade": "Todas",
    "id": "",
}


def baixar_zip_autoseg(nome: str, url: str) -> Path:
    destino = RAW_DIR / f"{nome}.zip"
    if destino.exists():
        print(f"[info] {destino.name} ja baixado, pulando download")
        return destino
    print(f"[info] baixando {url} ...")
    resposta = requests.get(url, headers=HTTP_HEADERS, timeout=REQUEST_TIMEOUT)
    resposta.raise_for_status()
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(resposta.content)
    print(f"[info] salvo em {destino} ({len(resposta.content):,} bytes)")
    return destino


def ingerir_autoseg(con: duckdb.DuckDBPyConnection) -> None:
    # Retomavel por semestre (nao dropa a tabela inteira a cada rodada):
    # cada arquivo tem ~300MB / ~3 milhoes de linhas, e o ambiente onde
    # isso foi desenvolvido ficou sem espaco em disco (chegou a 0 bytes
    # livres) e sem memoria no meio da ingestao mais de uma vez — sem
    # isso, cada nova tentativa refazia o semestre que ja tinha
    # carregado com sucesso, desperdicando o pouco recurso disponivel.
    # A coluna ENVIO (ja vem no CSV da SUSEP) identifica o semestre —
    # usada aqui como chave de "ja processado", no lugar de uma tabela
    # de checkpoint separada.
    tabela_existe = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'susep_autoseg'"
    ).fetchone()[0] > 0
    semestres_ja_carregados = set()
    if tabela_existe:
        semestres_ja_carregados = {
            row[0] for row in con.execute("SELECT DISTINCT ENVIO FROM susep_autoseg").fetchall()
        }

    for nome, url, envio in AUTOSEG_SEMESTRES:
        if envio in semestres_ja_carregados:
            print(f"[info] semestre {envio} ja carregado em susep_autoseg, pulando")
            continue

        zip_path = baixar_zip_autoseg(nome, url)
        csv_path = RAW_DIR / f"{nome}_{AUTOSEG_ARQUIVO_PRINCIPAL}"
        if not csv_path.exists():
            with ZipFile(zip_path) as z, z.open(AUTOSEG_ARQUIVO_PRINCIPAL) as origem, open(
                csv_path, "wb"
            ) as destino:
                shutil.copyfileobj(origem, destino)  # streaming — arquivo tem ~300MB+

        print(f"[info] carregando {csv_path.name} no DuckDB...")
        # Le direto com o leitor nativo de CSV do DuckDB em vez de pandas
        # pra esse arquivo especifico: pandas.read_csv nesse volume usa
        # memoria demais pro ambiente onde isso rodou.
        leitura = f"""
            SELECT * FROM read_csv(
                '{csv_path.as_posix()}',
                delim=';', decimal_separator=',', header=true
            )
        """
        if not tabela_existe:
            con.execute(f"CREATE TABLE susep_autoseg AS {leitura}")
            tabela_existe = True
        else:
            con.execute(f"INSERT INTO susep_autoseg {leitura}")

        # Apaga o CSV extraido assim que carregado — e so um intermediario
        # (o zip original fica), e o espaco em disco foi um problema real
        # nesse ambiente.
        csv_path.unlink()

    total = con.execute("SELECT COUNT(*) FROM susep_autoseg").fetchone()[0]
    semestres = con.execute(
        "SELECT DISTINCT ENVIO FROM susep_autoseg ORDER BY 1"
    ).fetchall()
    print(f"[info] susep_autoseg: {total:,} linhas ({[s[0] for s in semestres]})")


def buscar_ivr_html() -> str:
    sessao = requests.Session()
    r1 = sessao.get(IVR_MENU_URL, headers=HTTP_HEADERS, timeout=60)
    r1.raise_for_status()

    headers_post = dict(HTTP_HEADERS)
    headers_post["Referer"] = IVR_MENU_URL
    r2 = sessao.post(
        IVR_RESP_URL, data=IVR_PAYLOAD_TODAS, headers=headers_post, timeout=60
    )
    r2.raise_for_status()
    r2.encoding = "iso-8859-1"
    return r2.text


def ingerir_ivr(con: duckdb.DuckDBPyConnection) -> None:
    print("[info] consultando IVR (www2.susep.gov.br/menuestatistica/RankRoubo)...")
    html = buscar_ivr_html()

    tabelas = pd.read_html(io.StringIO(html), header=0, decimal=",", thousands=".")
    if not tabelas:
        raise RuntimeError("Nenhuma tabela encontrada na resposta do IVR — layout da pagina pode ter mudado.")

    df = tabelas[0]
    df.columns = ["modelo", "indice_roubo_furto_pct", "veiculos_expostos", "numero_sinistros"]

    con.execute("DROP TABLE IF EXISTS susep_ivr")
    con.execute(
        """
        CREATE TABLE susep_ivr (
            modelo VARCHAR NOT NULL,
            indice_roubo_furto_pct DOUBLE,
            veiculos_expostos DOUBLE,
            numero_sinistros DOUBLE
        )
        """
    )
    con.executemany(
        "INSERT INTO susep_ivr VALUES (?, ?, ?, ?)",
        df[["modelo", "indice_roubo_furto_pct", "veiculos_expostos", "numero_sinistros"]].values.tolist(),
    )

    total = con.execute("SELECT COUNT(*) FROM susep_ivr").fetchone()[0]
    print(f"[info] susep_ivr: {total:,} linhas (agregado nacional por modelo)")


def get_connection() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH))


def main() -> None:
    con = get_connection()
    try:
        try:
            ingerir_autoseg(con)
        except Exception as exc:  # nao deixa AUTOSEG falhar impedir o IVR
            print(f"[erro] falha ingerindo AUTOSEG: {exc!r}")

        try:
            ingerir_ivr(con)
        except Exception as exc:
            print(f"[erro] falha ingerindo IVR: {exc!r}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
