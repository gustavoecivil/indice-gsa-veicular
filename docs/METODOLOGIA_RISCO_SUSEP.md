# Metodologia — Sinistralidade e Roubo/Furto (SUSEP) por Categoria de Veículo

Calculado pelo script `scripts/analise/calcular_risco_susep.py`, a partir
das tabelas `susep_autoseg` e `susep_ivr` já ingeridas
(`scripts/ingestao/susep.py`).

**Isso NÃO está aplicado no simulador.** É só cálculo e documentação —
decidir como (ou se) combinar isso com o risco de preço já existente
(`docs/METODOLOGIA_RISCO.md`, baseado em variação da tabela FIPE) é uma
decisão separada, para depois de ver os números reais abaixo.

## Passo 1 — o que existe de verdade nas tabelas (investigado antes de cruzar qualquer coisa)

### `susep_autoseg`

Colunas reais: `COD_TARIF`, `REGIAO`, `COD_MODELO`, `ANO_MODELO`, `SEXO`,
`IDADE`, `EXPOSICAO1`, `PREMIO1`, `EXPOSICAO2`, `PREMIO2`, `IS_MEDIA`,
`FREQ_SIN1`, `INDENIZ1`, `FREQ_SIN2`, `INDENIZ2`, `FREQ_SIN3`,
`INDENIZ3`, `FREQ_SIN4`, `INDENIZ4`, `FREQ_SIN9`, `INDENIZ9`, `ENVIO`.

**Não existe uma coluna de "categoria de risco cadastral"** separada da
categoria de preço que este projeto usa. O que existe:

- `COD_TARIF` é a **categoria tarifária** — tipo de veículo, não faixa de
  preço (de-para em `auto_cat.csv`, dentro do zip, não ingerido como
  tabela): `1`=Passeio nacional, `2`=Passeio importado, `3`=Pick-up,
  `4`=Veículo de Carga, `5`=Motocicleta, `6`=Ônibus, `7`=Utilitários,
  `9`=Outros. Os valores distintos presentes na tabela são exatamente
  `[1, 2, 3, 4, 5, 6, 7, 9]`.
- `FREQ_SIN1`..`FREQ_SIN9` são contagens de sinistro **por causa**
  (de-para em `auto_cau.csv`): `1`=Roubo ou furto, `2`=Colisão parcial,
  `3`=Colisão Perda Total, `4`=Incêndio, `9`=Outros. `INDENIZ1`..`9` é o
  valor pago para cada causa. Confirmado na documentação oficial da
  SUSEP (`DEFINICOES_AUTOSEG.pdf`, extraído do zip): **`EXPOSICAO2` e
  `PREMIO2` são "campo não utilizado"** — só `EXPOSICAO1`/`PREMIO1` têm
  dado real. `EXPOSICAO1` é "quantidade de veículos expostos", ponderada
  pelo tempo de vigência da apólice dentro da janela semestral — uma
  medida real de exposição ao risco, não uma contagem de apólices.
- `COD_MODELO` **bate direto com o `codigo_fipe`** de
  `fipe_historico_precos` — confirmado por igualdade exata: 7.963 de
  8.375 códigos distintos (95,1%) casam. A própria SUSEP documenta isso
  ("Os códigos de modelos são os da codificação padronizada da tabela
  FIPE"). Não precisou de fuzzy match.
- 6.339.587 linhas, cobrindo 2 semestres de 2019 (`ENVIO` = `2019B`,
  `2020A` — ver `docs/FONTES.md` para o porquê de só esses 2 semestres
  estarem disponíveis).

### `susep_ivr`

Colunas reais: `modelo` (VARCHAR), `indice_roubo_furto_pct`,
`veiculos_expostos`, `numero_sinistros`. 499 linhas, uma por modelo,
agregado nacional (ver `docs/FONTES.md` para o método de coleta via
scraping do formulário da SUSEP).

`modelo` **não é código FIPE** — é um nome de modelo agrupado (ex.:
`"GM CHEVROLET ONIX"`, que junta várias variantes/trims de um mesmo
modelo). O de-para real para essa granularidade está em `auto2_vei.csv`
(também dentro dos zips do AUTOSEG, também não ingerido como tabela —
usado só como lookup auxiliar por este script): coluna `CODIGO` = código
FIPE, coluna `GRUPO` = o mesmo nome de modelo agrupado que o IVR usa.
Confirmado por igualdade exata de texto, combinando `auto2_vei.csv` dos
2 semestres para maximizar cobertura: **495 dos 499 modelos do IVR
(99,2%) casam** com um `GRUPO`. Os 4 sem match: `RENAULT OUTROS` (bucket
genérico, sem modelo único para casar — exclusão esperada) e `GM
CHEVROLET SONIC`, `GM CHEVROLET SPIN`, `RENAULT DUSTER` (não encontrados
em nenhum dos 2 semestres do catálogo `auto2_vei` baixado).

## Passo 2 — como o cruzamento foi feito (e as aproximações necessárias)

1. **Classificação por categoria de preço**: reproduz a mesma lógica de
   `calcular_risco_categoria.py` (preço mais recente de cada veículo em
   `fipe_historico_precos`, quartis sobre a população não-elétrica) —
   mas agregada por `codigo_fipe` puro, não por
   `codigo_fipe|ano_modelo|combustível`. **Aproximação**: o AUTOSEG
   (`COD_MODELO`) e o `auto2_vei.csv` não têm o mesmo recorte por
   ano-modelo/combustível que a tabela original usa, então o preço
   representativo de cada `codigo_fipe` aqui é a **média** entre suas
   variantes de ano/combustível disponíveis em `fipe_historico_precos`
   — não o preço de um ano específico. Um veículo é `eletrico` se
   qualquer uma de suas variantes for elétrica. 11.357 códigos FIPE
   classificados (p25 = R$ 24.520,50, p75 = R$ 145.713,00 — mesmos
   limiares da rodada mais recente de `calcular_risco_categoria.py`,
   como esperado, já que vêm da mesma tabela).

2. **Sinistralidade AUTOSEG**: `SUM(FREQ_SINx) / SUM(EXPOSICAO1)` por
   categoria, junção direta `COD_MODELO = codigo_fipe`, restrita a
   `COD_TARIF IN (1, 2)` (Passeio nacional/importado — a mesma população
   de carro de passeio que a classificação popular/intermediário/premium
   cobre; caminhão/moto/ônibus ficam de fora). Essa é exatamente a mesma
   fórmula que a própria SUSEP usa para calcular o IVR ("divisão entre o
   número de sinistros ocorridos e o número de veículos expostos"), o
   que permite comparar os dois resultados como cross-check.

3. **Roubo/furto IVR**: junta `susep_ivr.modelo` com `auto2_vei.GRUPO`
   para achar os códigos FIPE de cada grupo, e usa a **categoria mais
   frequente (moda)** entre esses códigos como a categoria do grupo.
   **Aproximação**: um "grupo" do IVR pode conter variantes de mais de
   uma categoria de preço (ex.: um modelo com versão básica popular e
   versão topo intermediária) — não há como saber, só com o IVR, quanto
   do "veículos expostos" reportado veio de cada variante específica; a
   moda entre os códigos do grupo é a melhor aproximação disponível sem
   essa informação. A média do índice por categoria é **ponderada pelos
   veículos expostos de cada grupo** (não é média simples), para não
   deixar um modelo de amostra pequena pesar igual a um modelo com
   dezenas de milhares de veículos.

## Resultado

| Categoria | Sinistralidade geral (AUTOSEG) | Roubo/furto (AUTOSEG) | Roubo/furto (IVR) | Exposição AUTOSEG | Veículos expostos IVR | Modelos IVR |
|---|---|---|---|---|---|---|
| popular | 39,29% | 0,65% | 4,88% | 1.249.264 | 1.294.579 | 193 |
| intermediario | 32,49% | 0,62% | 3,78% | 10.679.977 | 5.390.591 | 263 |
| premium | 27,83% | 0,34% | 4,51% | 50.221 | 102.040 | 38 |
| eletrico | 81,21% | 0,57% | 0,00% | **176** | **14** | **1** |

JSON exportado: `data/processed/risco_susep_categoria.json`

## Achado que precisa de destaque: AUTOSEG e IVR discordam em magnitude, mas concordam em direção

O índice de roubo/furto calculado a partir do **AUTOSEG** (0,34%–0,65%)
e o índice publicado pelo **IVR** (3,78%–4,88%) para as mesmas
categorias diferem por um fator de **~7 a 13x** — mesmo usando
exatamente a mesma fórmula (confirmada na documentação oficial da
SUSEP). Isso não é um bug de cruzamento: a classificação por categoria
bate (mesmos p25/p75, mesma base FIPE), a fórmula bate
(`FREQ_SIN1`/`EXPOSICAO1` é literalmente a definição do índice que a
SUSEP usa), e `EXPOSICAO2` já foi descartado por ser campo não
utilizado. A explicação mais provável é **temporal**: o AUTOSEG ingerido
aqui é só de 2019 (os únicos 2 semestres disponíveis no catálogo — ver
`docs/FONTES.md`), enquanto o IVR reflete "o último envio semestral" no
momento da consulta (sem data exata disponível na própria ferramenta,
mas a composição da frota — presença forte de Fiat Argo, Renault Kwid
etc, lançados 2017+ — sugere um período bem mais recente). Sete anos é
tempo suficiente para o cenário de roubo/furto de veículos no Brasil
mudar bastante (rastreadores, alarmes, mudança no perfil da frota
roubada). **Os dois números não devem ser tratados como diretamente
comparáveis em valor absoluto** — mas a **ordem relativa entre
categorias concorda em um ponto central**: `popular` tem o maior índice
de roubo/furto nas duas fontes, consistente com o padrão histórico
conhecido no Brasil (carros populares são historicamente os mais visados
para desmanche/revenda de peças). A posição de `premium` diverge entre
as fontes (mais baixo no AUTOSEG, segundo mais alto no IVR) — um ponto
genuinamente ambíguo que este documento não tenta resolver artificialmente.

## Limitações — seja honesto sobre isso ao usar o número

- **`eletrico` tem amostra irrelevante nas duas fontes** — 176 unidades
  de exposição e 15 códigos FIPE no AUTOSEG; 1 único modelo e 14
  veículos expostos no IVR. O `sinistralidade_geral_pct` de 81,21% para
  elétrico é resultado de amostra pequena (143 sinistros sobre 176
  exposições), não um sinal real — **não usar esse número para nada até
  haver amostra maior**.
- **AUTOSEG e IVR não são do mesmo período** (ver seção acima) — combinar
  os dois exige tratar isso como duas fotografias de momentos diferentes,
  não uma médIa de uma coisa só.
- **`sinistralidade_geral_pct`** (todas as causas, não só roubo/furto)
  não tem uma fonte independente pra cross-check como o roubo/furto tem
  contra o IVR — trate como um número exploratório, não validado por uma
  segunda fonte.
- A classificação por categoria usa o **preço médio entre variantes de
  ano/combustível** de cada `codigo_fipe` (não o preço de um ano
  específico) — um veículo cujo preço oscila bastante entre anos pode,
  em teoria, estar classificado numa categoria "média" que não reflete
  bem nenhum ano específico dele.
- O agrupamento do IVR por categoria usa a **moda** entre os códigos FIPE
  de cada grupo — para grupos com variantes de categorias diferentes,
  isso simplifica pra uma categoria só.
- 4 modelos do IVR (`RENAULT OUTROS`, `GM CHEVROLET SONIC`, `GM
  CHEVROLET SPIN`, `RENAULT DUSTER`) ficaram sem categoria resolvida e
  foram excluídos do agregado — 0,8% dos 499 modelos.

## Como isso poderia se combinar com o risco de preço (proposta, não decisão)

O risco já calculado em `docs/METODOLOGIA_RISCO.md` mede **oscilação de
preço na própria tabela FIPE** (coeficiente de variação de até 12 meses
de histórico) — é uma medida de estabilidade de valor de mercado. O que
este documento calcula é **probabilidade de sinistro/roubo** — uma
dimensão de risco completamente diferente (frequência de evento de
perda, não variação de valor). Não são a mesma coisa e não deveriam ser
somados ou misturados sem uma decisão explícita de **o que o número
final representa** no simulador:

- Poderiam entrar como **dois fatores separados** (ex.: risco de
  revenda/depreciação de um lado, custo esperado de sinistro/seguro do
  outro), já que respondem a perguntas diferentes do usuário.
- Ou o roubo/furto poderia informar especificamente o **custo de seguro
  esperado** (que já é um campo separado no simulador), não a faixa de
  risco de revenda.
- Combinar os dois num único "risco" só faria sentido se houvesse uma
  razão de negócio clara pra tratá-los como a mesma coisa — o que não é
  óbvio aqui.

Essa decisão fica para uma tarefa separada, depois de discutir qual
desses caminhos faz mais sentido pro simulador.
