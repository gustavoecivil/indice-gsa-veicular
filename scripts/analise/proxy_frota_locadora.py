"""
Proxy de "carros mais usados por locadoras/frota corporativa", a partir
dos modelos mais vendidos no Brasil (Fenabrave) -- ver docs/FONTES.md
pra investigacao completa e limitacoes.

Investigacao feita antes de escrever este script (Passo 1 da tarefa):
  - senatran_frota (RENAVAM) tem so 6 colunas: uf, municipio,
    marca_modelo, ano_fabricacao_crv, quantidade, mes_referencia --
    CONFIRMADO que nao existe granularidade de tipo de proprietario
    (pessoa fisica/juridica) nem nada equivalente. O dataset fonte da
    SENATRAN (RENAVAM) nunca publicou esse recorte -- nao e uma falha
    da ingestao, e o teto real da fonte. Sem esse dado, nao da pra
    calcular a proporcao PJ/PF por modelo como pedido no Passo 2A.

  - Passo 2B (fallback, usado aqui): ranking publico de emplacamento
    total por modelo (Fenabrave), sob a premissa de que os modelos mais
    vendidos no varejo em geral tambem tendem a ser os mais comprados
    em volume por locadoras/frotistas (que compram em escala do que ja
    e popular/tem rede de pecas/revenda liquida bem, nao o contrario).
    ISSO E UM PROXY, NAO CONFIRMACAO: nao ha garantia de que a
    locadora X tem o carro Y -- so que os 50 modelos abaixo sao os que
    mais emplacaram no Brasil no periodo, e por isso sao a aposta mais
    razoavel sem dado direto de frota/locacao.

  - Fonte do ranking: Fenabrave (Federacao Nacional da Distribuicao de
    Veiculos Automotores), dados oficiais de emplacamento, via
    agregador Auto Reporter (autoreporter.news/emplacamentos), julho de
    2026 (mensal, nao acumulado do ano). Motos foram excluidas do
    ranking bruto capturado (a pagina mistura carros e motos) -- so
    veiculos de passeio/utilitarios/picapes ficaram aqui, que e o
    universo relevante pra "frota de locadora".

  - Cruzamento com fipe_historico_precos: a FIPE cataloga por
    trim/motorizacao especifico (ex.: "Strada 1.3 mpi Fire 8V 67cv CE"),
    nao por "modelo" no sentido comercial amplo (ex.: "Strada") -- entao
    1 modelo do ranking Fenabrave bate com VARIOS codigos FIPE (de 2 a
    116 nesta rodada). Por isso a tabela de saida guarda a LISTA de
    codigos por modelo (`codigos_fipe`), nao um unico codigo -- usar um
    so seria arbitrario e sub-representaria o modelo. Pra uso como
    filtro (marca, modelo) no simulador, o padrao de busca real e
    marca = `marca_fipe` E modelo comecando com `modelo_fipe_prefixo`.

  - 2 modelos (Jaecoo 7, Omoda 5) exigiram ajuste manual: a FIPE nao
    repete o nome da marca no campo `modelo` pra essas marcas novas
    (chinesas, lancadas 2025/2026) -- o modelo aparece so como "7
    Elite..." ou "5 Luxury...", sem "Jaecoo"/"Omoda" no texto. Resolvido
    filtrando por marca exata em vez de por texto no nome do modelo.

Grava em data/processed/proxy_frota_locadora.json.

Uso:
    python scripts/analise/proxy_frota_locadora.py
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "processed" / "indice_gsa.duckdb"
OUTPUT_PATH = BASE_DIR / "data" / "processed" / "proxy_frota_locadora.json"

FONTE = "Fenabrave (via Auto Reporter, autoreporter.news/emplacamentos)"
PERIODO_REFERENCIA = "julho de 2026"

# Top 50 modelos de veiculos de passeio/utilitarios mais emplacados no
# Brasil em julho/2026, segundo a Fenabrave (motos excluidas). Ver
# docstring do modulo pra fonte e limitacoes.
RANKING_FENABRAVE = [
    (1, "Strada", "Fiat", "Pickup Pequena", 14912),
    (2, "Polo", "VW", "Hatch Pequeno", 10340),
    (3, "Tera", "VW", "SUV", 10165),
    (4, "Onix", "GM", "Hatch Pequeno", 9313),
    (5, "Argo", "Fiat", "Hatch Pequeno", 8612),
    (6, "Dolphin Mini", "BYD", "Hatch Pequeno", 7265),
    (7, "T Cross", "VW", "SUV", 7070),
    (8, "Song", "BYD", "SUV", 6834),
    (9, "Dolphin", "BYD", "Hatch Pequeno", 6492),
    (10, "EX2", "Geely", "Hatch Pequeno", 5898),
    (11, "Creta", "Hyundai", "SUV", 5880),
    (12, "Toro", "Fiat", "Pickup Grande", 5798),
    (13, "Haval H6", "GWM", "SUV", 5673),
    (14, "Tracker", "GM", "SUV", 5582),
    (15, "Saveiro", "VW", "Pickup Pequena", 5328),
    (16, "Mobi", "Fiat", "Hatch Pequeno", 5193),
    (17, "Yaris Cross", "Toyota", "SUV", 5036),
    (18, "Fastback", "Fiat", "SUV", 4849),
    (19, "Compass", "Jeep", "SUV", 4848),
    (20, "Kwid", "Renault", "Hatch Pequeno", 4382),
    (21, "Pulse", "Fiat", "SUV", 4242),
    (22, "Onix Plus", "GM", "Sedan Compacto", 3888),
    (23, "Tiggo 5X", "Caoa Chery", "SUV", 3879),
    (24, "HR-V", "Honda", "SUV", 3858),
    (25, "Nivus", "VW", "SUV", 3728),
    (26, "Corolla Cross", "Toyota", "SUV", 3678),
    (27, "WR-V", "Honda", "SUV", 3477),
    (28, "Ranger", "Ford", "Pickup Grande", 3402),
    (29, "Hilux", "Toyota", "Pickup Grande", 3369),
    (30, "Kicks", "Nissan", "SUV", 3175),
    (31, "Jaecoo 7", "Jaecoo", "SUV", 3061),
    (32, "HB20", "Hyundai", "Hatch Pequeno", 3037),
    (33, "Renegade", "Jeep", "SUV", 2934),
    (34, "Sonic", "GM", "SUV", 2927),
    (35, "Virtus", "VW", "Sedan Compacto", 2922),
    (36, "Kait", "Nissan", "SUV", 2902),
    (37, "S10", "GM", "Pickup Grande", 2810),
    (38, "I20", "Hyundai", "Hatch Pequeno", 2668),
    (39, "Omoda 5", "Omoda", "SUV", 2552),
    (40, "Rampage", "Ram", "Pickup Grande", 2338),
    (41, "Fiorino", "Fiat", "Furgao Pequeno", 2280),
    (42, "Cronos", "Fiat", "Sedan Pequeno", 2237),
    (43, "Tiggo 7", "Caoa Chery", "SUV", 2218),
    (44, "Spin", "GM", "Grandcab", 1854),
    (45, "Montana", "GM", "Pickup Grande", 1769),
    (46, "Corolla", "Toyota", "Sedan Medio", 1665),
    (47, "HB20S", "Hyundai", "Sedan Pequeno", 1626),
    (48, "Duster", "Renault", "SUV", 1611),
    (49, "King", "BYD", "Sedan Medio", 1605),
    (50, "Triton", "Mitsubishi", "Pickup Grande", 1526),
]

# marca Fenabrave -> palavra-chave de marca na FIPE (case-insensitive,
# via LIKE) -- confirmado contra os valores reais de
# fipe_historico_precos.marca antes de escrever isto.
MARCA_FIPE = {
    "Fiat": "FIAT", "VW": "VOLKSWAGEN", "GM": "CHEVROLET", "BYD": "BYD",
    "Geely": "GEELY", "Hyundai": "HYUNDAI", "GWM": "GWM", "Toyota": "TOYOTA",
    "Jeep": "JEEP", "Renault": "RENAULT", "Caoa Chery": "CHERY",
    "Honda": "HONDA", "Ford": "FORD", "Nissan": "NISSAN",
    "Jaecoo": "JAECOO", "Omoda": "OMODA", "Ram": "RAM", "Mitsubishi": "MITSUBISHI",
}

# modelo Fenabrave -> prefixo/token real usado no campo `modelo` da
# FIPE, quando difere do nome comercial curto. "Jaecoo 7" e "Omoda 5"
# tem tratamento especial na query (ver montar_query) porque a FIPE nao
# repete a marca no nome do modelo pra essas duas.
MODELO_FIPE_PREFIXO = {
    "T Cross": "T-CROSS",
    "Onix Plus": "ONIX%PLUS",
    "Jaecoo 7": "7",
    "Omoda 5": "5",
}


def montar_query(marca_fenabrave: str, modelo_fenabrave: str) -> tuple[str, list[str]]:
    marca_kw = MARCA_FIPE.get(marca_fenabrave, marca_fenabrave.upper())
    modelo_kw = MODELO_FIPE_PREFIXO.get(modelo_fenabrave, modelo_fenabrave.upper())

    if marca_fenabrave in ("Jaecoo", "Omoda"):
        # FIPE nao repete a marca no nome do modelo pra essas -- casa
        # por marca EXATA + modelo comecando com o numero do modelo.
        where = "marca = ? AND UPPER(modelo) LIKE ?"
        params = [marca_fenabrave, f"{modelo_kw}%"]
    else:
        where = "UPPER(marca) LIKE ? AND UPPER(modelo) LIKE ?"
        params = [f"%{marca_kw}%", f"%{modelo_kw}%"]

    return where, params


def main() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)

    resultados = []
    sem_match = []
    for rank, modelo, marca, categoria, unidades in RANKING_FENABRAVE:
        where, params = montar_query(marca, modelo)
        linhas = con.execute(
            f"""
            SELECT DISTINCT codigo_fipe, modelo, marca
            FROM fipe_historico_precos
            WHERE {where}
            ORDER BY modelo
            """,
            params,
        ).fetchall()

        codigos = sorted({r[0] for r in linhas})
        marcas_fipe_encontradas = sorted({r[2] for r in linhas})

        if not codigos:
            sem_match.append((rank, modelo, marca))

        resultados.append(
            {
                "rank_fenabrave": rank,
                "modelo": modelo,
                "marca": marca,
                "categoria": categoria,
                "unidades_emplacadas": unidades,
                "periodo_referencia": PERIODO_REFERENCIA,
                "marca_fipe": marcas_fipe_encontradas[0] if marcas_fipe_encontradas else None,
                "qtd_codigos_fipe": len(codigos),
                "codigos_fipe": codigos,
                "exemplo_nomes_fipe": sorted({r[1] for r in linhas})[:3],
            }
        )

    con.close()

    if sem_match:
        print(f"[aviso] {len(sem_match)} modelo(s) sem nenhum codigo FIPE encontrado:")
        for rank, modelo, marca in sem_match:
            print(f"   #{rank} {modelo} ({marca})")

    saida = {
        "fonte": FONTE,
        "periodo_referencia": PERIODO_REFERENCIA,
        "metodologia": (
            "Proxy de frota corporativa/locadora via ranking de emplacamento total "
            "(Fenabrave) — senatran_frota (RENAVAM) nao tem granularidade de tipo de "
            "proprietario (pessoa fisica/juridica), entao nao da pra calcular a "
            "concentracao PJ real. Ver docs/FONTES.md pra detalhamento e limitacoes."
        ),
        "modelos": resultados,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)

    total_com_match = sum(1 for r in resultados if r["qtd_codigos_fipe"] > 0)
    total_codigos = sum(r["qtd_codigos_fipe"] for r in resultados)
    print(f"\n[info] {len(resultados)} modelos processados, {total_com_match} com codigo FIPE mapeado")
    print(f"[info] {total_codigos} codigos FIPE distintos cobertos ao todo")
    print(f"[info] gravado em {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
