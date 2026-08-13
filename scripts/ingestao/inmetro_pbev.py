"""
Ingestao da tabela PBEV (Programa Brasileiro de Etiquetagem Veicular),
do INMETRO — consumo oficial de combustivel por marca/modelo/versao.

Fonte e formato: o INMETRO so publica isso em PDF (nao ha planilha
estruturada equivalente disponivel na pagina oficial — foi conferido
antes de escrever este script). O PDF nao e escaneado, tem texto real
extraivel, mas a grade da tabela (bordas/linhas) nao e perfeitamente
detectada pelo parser de tabelas do pdfplumber em 100% das linhas: uma
pequena fracao das linhas (~3,6% na rodada de referencia — 31 de 864)
fica com celulas mescladas (o texto de 2-3 veiculos adjacentes se funde
numa celula so, as vezes intercalado caractere a caractere). Esse
script detecta essas linhas corrompidas comparando campos categoricos
(categoria, tipo de propulsao, combustivel, classificacoes, selo
CONPET) contra os valores reais e validos encontrados na inspecao
manual do arquivo, e DESCARTA as linhas que nao batem — documentado no
resumo final e em docs/FONTES.md, em vez de tentar forcar uma
reconstrucao arriscada dessas poucas linhas.

Mapeamento de colunas confirmado empiricamente (comparando linhas de
veiculos a gasolina, flex, diesel, eletricos e hibridos plug-in entre
si), e nao a partir do cabecalho oficial do PDF, ja que o cabecalho vem
com texto rotacionado que nenhum extrator consegue linearizar direito:
    0  Categoria
    1  Marca
    2  Modelo
    3  Versao
    4  Motor
    5  Tipo de Propulsao (Combustao / Hibrido / Plug-in / Eletrico)
    9  Combustivel: codigo G=Gasolina, F=Flex, D=Diesel, E=Eletrico
    17 Quilometragem por litro — Etanol, Cidade   (so populado se Flex)
    18 Quilometragem por litro — Etanol, Estrada  (so populado se Flex)
    19 Quilometragem por litro — Gasolina/Diesel, Cidade
    20 Quilometragem por litro — Gasolina/Diesel, Estrada
    21 Quilometragem por litro equivalente (eletrico), Cidade — so
       populado se Eletrico ou Plug-in
    22 Quilometragem por litro equivalente (eletrico), Estrada — idem
    23 Consumo Energetico (MJ/km)
    24 Autonomia no modo Eletrico (km) — so populado se Eletrico/Plug-in
    25 Classificacao PBE — Categoria (letra A-E)
    26 Classificacao PBE — Geral (letra A-E)
    27 Selo CONPET de Eficiencia Energetica (SIM / -)

As colunas de emissoes/poluentes (indices 10-16) NAO sao extraidas: o
cabecalho dessa parte da tabela nao ficou claro o suficiente na
inspecao pra rotular cada uma com confianca, e nao sao necessarias pro
escopo deste projeto (consumo de combustivel). Melhor deixar de fora do
que inventar rotulo errado.

Grava em data/processed/indice_gsa.duckdb, tabela
inmetro_consumo_veiculos.

Uso:
    python scripts/ingestao/inmetro_pbev.py
"""

from __future__ import annotations

import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pdfplumber
import requests

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "processed" / "indice_gsa.duckdb"
PDF_PATH = BASE_DIR / "data" / "raw" / "inmetro_pbev.pdf"

PAGINA_LISTAGEM = (
    "https://www.gov.br/inmetro/pt-br/assuntos/avaliacao-da-conformidade/"
    "programa-brasileiro-de-etiquetagem/tabelas-de-eficiencia-energetica/"
    "veiculos-automotivos-pbe-veicular"
)
REQUEST_TIMEOUT = 60
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

CATEGORIAS_VALIDAS = {
    "Extra Grande", "Utilitário Esportivo Compacto", "Médio", "Picape",
    "Comercial", "Grande", "Utilitário Esportivo Grande 4x4",
    "Utilitário Esportivo Grande", "Esportivo", "Fora de Estrada Grande",
    "Compacto", "Picape Compacta", "Sub Compacto", "Minivan",
    "Fora de Estrada Compacto", "Utilitário Esportivo Compacto 4x4",
}
PROPULSOES_VALIDAS = {"Combustão", "Elétrico", "Plug-in", "Híbrido"}
COMBUSTIVEIS_VALIDOS = {"G", "F", "D", "E"}
CLASSIFICACOES_VALIDAS = {"A", "B", "C", "D", "E"}
SELO_CONPET_VALIDOS = {"-", "SIM"}


def encontrar_pdf_mais_recente() -> str:
    """Busca a pagina de listagem e pega o primeiro link @@download/file
    (a pagina lista as tabelas do ciclo mais recente primeiro)."""
    resposta = requests.get(
        PAGINA_LISTAGEM, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
    )
    resposta.raise_for_status()
    match = re.search(r'href="([^"]+\.pdf/@@download/file)"', resposta.text)
    if not match:
        raise RuntimeError(
            "Nao encontrei nenhum link '.pdf/@@download/file' na pagina do INMETRO — "
            "o layout da pagina pode ter mudado. Confira manualmente: " + PAGINA_LISTAGEM
        )
    return match.group(1)


def baixar_pdf(url: str) -> Path:
    print(f"[info] baixando PDF do INMETRO: {url}")
    resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    resposta.raise_for_status()
    PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    PDF_PATH.write_bytes(resposta.content)
    print(f"[info] PDF salvo em {PDF_PATH} ({len(resposta.content):,} bytes)")
    return PDF_PATH


def numero_ou_none(valor: str):
    if valor is None:
        return None
    valor = valor.strip()
    if valor in ("", "\\", "ND", "N.A.", "-"):
        return None
    try:
        return float(valor)
    except ValueError:
        return None


def linha_valida(row: list) -> bool:
    if len(row) != 28:
        return False
    if any("\n" in (c or "") for c in row):
        return False
    if row[0] not in CATEGORIAS_VALIDAS:
        return False
    if row[5] not in PROPULSOES_VALIDAS:
        return False
    if row[9] not in COMBUSTIVEIS_VALIDOS:
        return False
    if row[25] not in CLASSIFICACOES_VALIDAS or row[26] not in CLASSIFICACOES_VALIDAS:
        return False
    if row[27] not in SELO_CONPET_VALIDOS:
        return False
    return True


def extrair_linhas(pdf_path: Path) -> tuple[list[dict], int, int]:
    registros = []
    total_linhas_dado = 0
    linhas_descartadas = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for tabela in page.extract_tables():
                for row in tabela:
                    if not row or row[0] in (None, "Categoria"):
                        continue  # cabecalho ou linha vazia
                    if len(row) != 28:
                        continue  # fragmento de tabela que nao e a tabela principal
                    total_linhas_dado += 1

                    if not linha_valida(row):
                        linhas_descartadas += 1
                        continue

                    registros.append(
                        {
                            "categoria": row[0],
                            "marca": row[1],
                            "modelo": row[2],
                            "versao": row[3],
                            "motor": row[4],
                            "tipo_propulsao": row[5],
                            "combustivel": row[9],
                            "consumo_cidade_etanol_km_l": numero_ou_none(row[17]),
                            "consumo_estrada_etanol_km_l": numero_ou_none(row[18]),
                            "consumo_cidade_km_l": numero_ou_none(row[19]),
                            "consumo_estrada_km_l": numero_ou_none(row[20]),
                            "consumo_cidade_eletrico_km_l_equiv": numero_ou_none(row[21]),
                            "consumo_estrada_eletrico_km_l_equiv": numero_ou_none(row[22]),
                            "consumo_energetico_mj_km": numero_ou_none(row[23]),
                            "autonomia_eletrica_km": numero_ou_none(row[24]),
                            "classificacao_categoria": row[25],
                            "classificacao_geral": row[26],
                            "selo_conpet": row[27],
                        }
                    )

    return registros, total_linhas_dado, linhas_descartadas


def normalizar(texto: str) -> str:
    """minusculo, sem acento, so alfanumerico e espaco, espacos colapsados."""
    if not texto:
        return ""
    sem_acento = (
        unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    )
    minusculo = sem_acento.lower()
    so_alfanumerico = re.sub(r"[^a-z0-9 ]", " ", minusculo)
    return re.sub(r"\s+", " ", so_alfanumerico).strip()


def construir_indice_fipe(con: duckdb.DuckDBPyConnection) -> list[dict]:
    linhas = con.execute(
        """
        SELECT mo.codigo_marca, mo.codigo_modelo, m.nome_marca, mo.nome_modelo
        FROM fipe_modelos mo
        JOIN fipe_marcas m ON mo.codigo_marca = m.codigo_marca
        """
    ).fetchall()
    return [
        {
            "codigo_marca": codigo_marca,
            "codigo_modelo": codigo_modelo,
            "nome_marca": nome_marca,
            "nome_modelo": nome_modelo,
            "marca_norm": normalizar(nome_marca),
            "modelo_norm": normalizar(nome_modelo),
        }
        for codigo_marca, codigo_modelo, nome_marca, nome_modelo in linhas
    ]


def casar_veiculo_com_fipe(marca: str, modelo: str, indice_fipe: list[dict]) -> dict | None:
    """Casa um (marca, modelo) do PBEV com um modelo da FIPE por
    aproximacao de texto normalizado. Confianca:
      - exato: o modelo INTEIRO do PBEV (todas as palavras, na ordem)
        aparece como prefixo do nome_modelo da FIPE — ex: PBEV "HB20S"
        casa exato com FIPE "HB20S Comfort 1.0-12V...", mas PBEV
        "TIGGO 5X" NAO casa exato com FIPE "TIGGO 2.0 16V..." (falta o
        "5X"), dentro da mesma marca.
      - aproximado: o modelo do PBEV aparece em algum lugar dentro do
        nome_modelo da FIPE (dentro da mesma marca), mas nao como
        prefixo exato — ex.: PBEV "VELAR" dentro de FIPE
        "Range R. VELAR 2.0 4x4...".
    Quando ha mais de um candidato no mesmo nivel de confianca, fica com
    o nome_modelo mais curto (mais proximo de ser so o nome base, sem
    tanta informacao de versao/motor grudada).
    """
    marca_norm = normalizar(marca)
    modelo_norm = normalizar(modelo)
    if not marca_norm or not modelo_norm:
        return None

    candidatos_marca = [
        f for f in indice_fipe if marca_norm in f["marca_norm"] or f["marca_norm"] in marca_norm
    ]
    if not candidatos_marca:
        return None

    exatos = [
        f
        for f in candidatos_marca
        if f["modelo_norm"] == modelo_norm or f["modelo_norm"].startswith(modelo_norm + " ")
    ]
    if exatos:
        melhor = min(exatos, key=lambda f: len(f["modelo_norm"]))
        return {**melhor, "confianca": "exato"}

    modelo_norm_com_bordas = f" {modelo_norm} "
    aproximados = [
        cand
        for cand in candidatos_marca
        if modelo_norm_com_bordas in f" {cand['modelo_norm']} "
    ]
    if aproximados:
        melhor = min(aproximados, key=lambda f: len(f["modelo_norm"]))
        return {**melhor, "confianca": "aproximado"}

    return None


def get_connection() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS inmetro_consumo_veiculos (
            id INTEGER NOT NULL,
            categoria VARCHAR NOT NULL,
            marca VARCHAR NOT NULL,
            modelo VARCHAR NOT NULL,
            versao VARCHAR NOT NULL,
            motor VARCHAR NOT NULL,
            tipo_propulsao VARCHAR NOT NULL,
            combustivel VARCHAR NOT NULL,
            consumo_cidade_etanol_km_l DOUBLE,
            consumo_estrada_etanol_km_l DOUBLE,
            consumo_cidade_km_l DOUBLE,
            consumo_estrada_km_l DOUBLE,
            consumo_cidade_eletrico_km_l_equiv DOUBLE,
            consumo_estrada_eletrico_km_l_equiv DOUBLE,
            consumo_energetico_mj_km DOUBLE,
            autonomia_eletrica_km DOUBLE,
            classificacao_categoria VARCHAR,
            classificacao_geral VARCHAR,
            selo_conpet VARCHAR,
            atualizado_em TIMESTAMP NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS fipe_pbev_match (
            inmetro_id INTEGER NOT NULL,
            codigo_marca_fipe VARCHAR,
            codigo_modelo_fipe VARCHAR,
            nome_marca_fipe VARCHAR,
            nome_modelo_fipe VARCHAR,
            confianca VARCHAR NOT NULL,
            atualizado_em TIMESTAMP NOT NULL,
            PRIMARY KEY (inmetro_id)
        )
        """
    )
    return con


def main() -> None:
    url_pdf = encontrar_pdf_mais_recente()
    pdf_path = baixar_pdf(url_pdf)

    registros, total_linhas_dado, linhas_descartadas = extrair_linhas(pdf_path)
    if not registros:
        print("[erro] nenhum registro valido extraido do PDF — abortando sem gravar.")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    con = get_connection()
    try:
        con.execute("DELETE FROM inmetro_consumo_veiculos")
        con.executemany(
            """
            INSERT INTO inmetro_consumo_veiculos VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    i,
                    r["categoria"], r["marca"], r["modelo"], r["versao"], r["motor"],
                    r["tipo_propulsao"], r["combustivel"],
                    r["consumo_cidade_etanol_km_l"], r["consumo_estrada_etanol_km_l"],
                    r["consumo_cidade_km_l"], r["consumo_estrada_km_l"],
                    r["consumo_cidade_eletrico_km_l_equiv"], r["consumo_estrada_eletrico_km_l_equiv"],
                    r["consumo_energetico_mj_km"], r["autonomia_eletrica_km"],
                    r["classificacao_categoria"], r["classificacao_geral"], r["selo_conpet"],
                    now,
                )
                for i, r in enumerate(registros, start=1)
            ],
        )

        total_db = con.execute("SELECT COUNT(*) FROM inmetro_consumo_veiculos").fetchone()[0]
        marcas_distintas = con.execute(
            "SELECT COUNT(DISTINCT marca) FROM inmetro_consumo_veiculos"
        ).fetchone()[0]
        combustao_stats = con.execute(
            """
            SELECT MIN(consumo_cidade_km_l), MAX(consumo_cidade_km_l), AVG(consumo_cidade_km_l)
            FROM inmetro_consumo_veiculos
            WHERE combustivel IN ('G', 'F', 'D') AND consumo_cidade_km_l IS NOT NULL
            """
        ).fetchone()

        print("\n===== Resumo da ingestao PBEV/INMETRO =====")
        print(f"Linhas de dado no PDF (28 colunas, exceto cabecalho): {total_linhas_dado}")
        print(f"Linhas descartadas (celulas mescladas/corrompidas na extração): {linhas_descartadas}")
        print(f"Taxa de aproveitamento: {(1 - linhas_descartadas / total_linhas_dado) * 100:.1f}%")
        print(f"Veiculos gravados em inmetro_consumo_veiculos: {total_db}")
        print(f"Marcas distintas: {marcas_distintas}")
        print(
            f"Consumo cidade (combustão) — min/max/média km/l: "
            f"{combustao_stats[0]:.1f} / {combustao_stats[1]:.1f} / {combustao_stats[2]:.1f}"
        )

        tabelas_fipe = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('fipe_marcas', 'fipe_modelos')"
            ).fetchall()
        }
        if {"fipe_marcas", "fipe_modelos"} - tabelas_fipe:
            print(
                "\n[aviso] fipe_marcas/fipe_modelos nao encontradas — pulando o "
                "cruzamento com a FIPE (rode scripts/ingestao/fipe.py antes se quiser essa parte)."
            )
        else:
            indice_fipe = construir_indice_fipe(con)
            matches = []
            n_exato = n_aproximado = n_sem_match = 0
            for i, r in enumerate(registros, start=1):
                resultado = casar_veiculo_com_fipe(r["marca"], r["modelo"], indice_fipe)
                if resultado is None:
                    matches.append((i, None, None, None, None, "sem_match", now))
                    n_sem_match += 1
                else:
                    matches.append(
                        (
                            i,
                            resultado["codigo_marca"],
                            resultado["codigo_modelo"],
                            resultado["nome_marca"],
                            resultado["nome_modelo"],
                            resultado["confianca"],
                            now,
                        )
                    )
                    if resultado["confianca"] == "exato":
                        n_exato += 1
                    else:
                        n_aproximado += 1

            con.execute("DELETE FROM fipe_pbev_match")
            con.executemany(
                "INSERT INTO fipe_pbev_match VALUES (?, ?, ?, ?, ?, ?, ?)", matches
            )

            total_matches = len(matches)
            taxa_match = (n_exato + n_aproximado) / total_matches
            print("\n===== Cruzamento PBEV x FIPE =====")
            print(f"Veiculos PBEV avaliados: {total_matches}")
            print(f"  match exato:      {n_exato} ({n_exato / total_matches * 100:.1f}%)")
            print(f"  match aproximado: {n_aproximado} ({n_aproximado / total_matches * 100:.1f}%)")
            print(f"  sem match:        {n_sem_match} ({n_sem_match / total_matches * 100:.1f}%)")
            print(f"Taxa total de cruzamento: {taxa_match * 100:.1f}%")
            if taxa_match < 0.20:
                print(
                    "[aviso] taxa de cruzamento abaixo de 20% — documentar como limitação "
                    "em docs/FONTES.md em vez de forçar mais (ver instrução original)."
                )
    finally:
        con.close()


if __name__ == "__main__":
    main()
