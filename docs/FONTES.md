# Fontes de Dados — Índice GSA Veicular

Registro de cada fonte externa usada no projeto: origem oficial, como é
acessada, frequência de atualização e o que a tabela resultante no
DuckDB (`data/processed/indice_gsa.duckdb`) representa.

## FIPE — Tabela de preços de veículos

- **Nome oficial**: Tabela FIPE (Fundação Instituto de Pesquisas
  Econômicas), acessada via API pública **fipe.parallelum.com.br** (API
  v2) e, para o histórico mensal completo, via o plano pago do mesmo
  provedor (**fipe.api.br** / fipe.online).
- **URL**: https://fipe.parallelum.com.br/api/v2
- **Frequência de atualização da fonte**: mensal (a FIPE publica uma
  tabela nova todo início de mês).
- **Como é ingerida aqui**:
  - `scripts/ingestao/fipe.py` — catálogo de marcas/modelos/anos
    (tabelas `fipe_marcas`, `fipe_modelos`, `fipe_anos`).
  - `scripts/ingestao/fipe_historico.py` — preço e histórico de preço
    (`priceHistory`) por combinação marca/modelo/ano, via API do plano
    pago, com checkpoint pra retomar em caso de interrupção (tabela
    `fipe_historico_precos`, checkpoint em
    `fipe_historico_checkpoint`).
  - `scripts/ingestao/fipe_importar_csv.py` — importação em lote do CSV
    completo da tabela FIPE de um mês (baixado manualmente do plano
    pago em fipe.online), na mesma tabela `fipe_historico_precos`. Bem
    mais rápido que ir combinação por combinação via API quando se quer
    o catálogo inteiro de um mês de uma vez.
  - Agendamento mensal automático via Agendador de Tarefas do Windows
    (`scripts/run_fipe_mensal.ps1`) — ver `docs/DECISOES.md`.
- **O que a tabela representa**: `fipe_historico_precos` tem o preço
  FIPE mensal real por `codigo_fipe`/`ano_modelo`/`combustivel`, com uma
  linha por combinação e mês de referência (`mes_referencia`).

## ANP — Preço médio semanal de combustíveis por UF

- **Nome oficial**: Levantamento de Preços de Combustíveis — Série
  Histórica Semanal por Estado, publicada pela Agência Nacional do
  Petróleo, Gás Natural e Biocombustíveis (ANP).
- **URL**: https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/precos-revenda-e-de-distribuicao-combustiveis/shlp/semanal/semanal-estados-desde-2013.xlsx
  (arquivo público, sem necessidade de conta ou autenticação; página de
  origem com as demais granularidades —
  [Brasil/Regiões/Municípios](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/precos-revenda-e-de-distribuicao-combustiveis/serie-historica-do-levantamento-de-precos)).
- **Por que direto da ANP e não via Base dos Dados**: o dataset tratado
  `br_anp_precos_combustiveis` do [basedosdados.org](https://basedosdados.org)
  roda em cima do BigQuery e exige projeto/credencial de faturamento do
  Google Cloud, que não está disponível neste ambiente. A própria ANP já
  publica a mesma série histórica semanal por estado pronta, em um único
  arquivo `.xlsx` público — então a ingestão usa essa fonte direto em
  vez de travar esperando credencial do BigQuery. Se um projeto de
  faturamento do Google Cloud ficar disponível no futuro, dá pra migrar
  pra `basedosdados` sem mudar o formato da tabela final.
- **Frequência de atualização da fonte**: semanal (pesquisa de preços
  semanal por posto revendedor; a ANP publica o arquivo agregado por
  estado com defasagem de poucos dias em relação à semana pesquisada).
- **Como é ingerida aqui**: `scripts/ingestao/anp.py` baixa o `.xlsx`
  público (série completa desde 30/12/2012), lê a aba
  `ESTADOS - DESDE 30.12.2012` e grava em `anp_precos_combustivel`. Por
  padrão só carrega os últimos 3 anos (`--anos-recentes`, ajustável) pra
  manter a primeira carga enxuta; a série completa desde 2012 pode ser
  carregada com `--tudo` quando fizer sentido expandir o histórico. Sem
  agendamento automático ainda — rodar manualmente até decidir a
  frequência ideal (a fonte é semanal, mas o volume e o ritmo real de
  atualização do arquivo ainda não foram observados o suficiente pra
  automatizar).
- **O que a tabela representa**: `anp_precos_combustivel` tem o preço
  médio de revenda por semana (`semana_referencia` = início da semana
  pesquisada) e por UF, para gasolina comum, gasolina aditivada, etanol
  hidratado, óleo diesel, óleo diesel S10, GLP e GNV — junto com
  preço mínimo/máximo, desvio padrão e número de postos pesquisados
  naquela semana/UF/combustível, tal como publicado pela ANP.

## IBGE/SIDRA — Custo de manutenção de veículos por região metropolitana

- **Nome oficial**: Índice Nacional de Preços ao Consumidor Amplo
  (IPCA), tabela SIDRA **7060** — "IPCA - Variação mensal, acumulada no
  ano, acumulada em 12 meses e peso mensal, para o índice geral, grupos,
  subgrupos, itens e subitens de produtos e serviços (a partir de
  janeiro/2020)", publicada pelo IBGE.
- **URL**: https://sidra.ibge.gov.br/tabela/7060 (dados consultados via
  API do SIDRA, `https://apisidra.ibge.gov.br/values/...`).
- **Investigação feita antes de escrever o script**: o IPCA **não tem**
  um subitem único literalmente chamado "Manutenção e Acessórios para
  Veículos" — foi preciso consultar a classificação real da tabela 7060
  (classificação 315, "Geral, grupo, subgrupo, item e subitem") pra
  confirmar isso antes de inventar um rótulo que não existe. Dentro do
  grupo "5.Transportes" > item "5102.Veículo próprio", o que existe de
  fato são dois subitens separados que juntos cobrem manutenção de
  veículo:
  - `5102009.Acessórios e peças` (código SIDRA 7645)
  - `5102011.Conserto de automóvel` (código SIDRA 7647)

  A tabela 7060 só cobre nível territorial de "Região Metropolitana" no
  código N7 (rotulado pelo IBGE como "Região Metropolitana até 2020") —
  10 regiões: Belém, Fortaleza, Recife, Salvador, Belo Horizonte, Grande
  Vitória, Rio de Janeiro, São Paulo, Curitiba e Porto Alegre. Outras
  capitais pesquisadas pelo IPCA (Rio Branco, São Luís, Aracaju, Campo
  Grande, Goiânia, Brasília) só têm abertura por município isolado
  (nível N6), sem agrupamento de região metropolitana — ficaram fora do
  escopo deste script, que é especificamente "por região metropolitana".
- **Frequência de atualização da fonte**: mensal (o IPCA é publicado
  todo início de mês, com o dado do mês anterior).
- **Como é ingerida aqui**: `scripts/ingestao/ibge_manutencao.py`
  consulta a API do SIDRA pros dois subitens acima, nível territorial
  N7, variáveis "IPCA - Variação mensal" e "IPCA - Variação acumulada em
  12 meses" (as únicas relevantes que essa tabela expõe — não há um
  índice absoluto de preço nela, só variações percentuais). Por padrão
  só carrega os últimos 3 anos (`--anos-recentes`, ajustável); a série
  completa da tabela 7060 (desde jan/2020) pode ser carregada com
  `--tudo`. Sem agendamento automático ainda.
- **O que a tabela representa**: `ibge_manutencao_veiculos` tem, por
  região metropolitana (`regiao_metropolitana`, `codigo_regiao`) e
  subitem (`subitem`, `codigo_subitem`), a variação percentual mensal
  (`variacao_mensal_percentual`) e acumulada em 12 meses
  (`variacao_acumulada_12m_percentual`) do custo de acessórios/peças e
  de conserto de automóvel, por mês de referência (`mes_referencia`).
  Não tem histórico anterior a janeiro/2020 nessa tabela (a série
  anterior está na tabela SIDRA 1419, ainda não integrada).

## INMETRO/PBEV — Consumo de combustível por marca/modelo/versão

- **Nome oficial**: Programa Brasileiro de Etiquetagem Veicular (PBEV),
  publicado pelo INMETRO (Instituto Nacional de Metrologia, Qualidade e
  Tecnologia).
- **URL**: página de listagem —
  https://www.gov.br/inmetro/pt-br/assuntos/avaliacao-da-conformidade/programa-brasileiro-de-etiquetagem/tabelas-de-eficiencia-energetica/veiculos-automotivos-pbe-veicular
  — o script baixa direto o PDF mais recente listado ali (link
  `.../@@download/file`, sempre o primeiro da lista, que é o ciclo mais
  atual).
- **Formato real da fonte**: só existe em **PDF** — não há planilha
  Excel/CSV estruturada equivalente publicada pelo INMETRO (conferido
  antes de escrever o script, pra não perder tempo forçando um formato
  que não existe). O PDF tem texto real extraível (não é digitalização
  escaneada), mas o cabeçalho da tabela vem com texto rotacionado que
  nenhum extrator consegue linearizar direito — por isso o mapeamento de
  colunas usado no script foi confirmado **empiricamente**, comparando
  linhas de veículos a gasolina, flex, diesel, elétricos e híbridos
  plug-in entre si, em vez de confiar no cabeçalho.
- **Frequência de atualização da fonte**: por ciclo (o INMETRO publica
  uma tabela nova a cada poucos meses — o arquivo mais recente no
  momento desta ingestão era do "18º Ciclo", atualizado em junho/2026).
- **Como é ingerida aqui**: `scripts/ingestao/inmetro_pbev.py` usa
  `pdfplumber` pra extrair as tabelas do PDF. Uma fração pequena das
  linhas (~3,6% nesta rodada — 31 de 864) sai com células mescladas — o
  texto de 2-3 veículos adjacentes se funde numa célula só, às vezes
  intercalado caractere a caractere, por imprecisão na detecção de
  grade do PDF. O script identifica essas linhas comparando campos
  categóricos (categoria, tipo de propulsão, combustível, classificação,
  selo CONPET) contra os valores reais e válidos do arquivo, e
  **descarta** as que não batem, em vez de arriscar reconstruir errado —
  documentado no resumo da execução.
- **O que a tabela representa**: `inmetro_consumo_veiculos` tem, por
  veículo (`categoria`, `marca`, `modelo`, `versão`, `motor`,
  `tipo_propulsao`, `combustivel`), o consumo oficial em quilômetros por
  litro na cidade e na estrada — `consumo_cidade_km_l` /
  `consumo_estrada_km_l` para o combustível líquido principal do veículo
  (gasolina ou diesel, dependendo do `combustivel`), e
  `consumo_cidade_etanol_km_l` / `consumo_estrada_etanol_km_l` quando o
  veículo é flex. Veículos elétricos e híbridos plug-in também têm
  `consumo_cidade_eletrico_km_l_equiv` / `consumo_estrada_eletrico_km_l_equiv`
  (quilometragem por litro equivalente, métrica oficial do PBEV pra
  comparar eficiência elétrica com combustão), além de
  `consumo_energetico_mj_km` e `autonomia_eletrica_km`. As colunas de
  emissões/poluentes da tabela original **não foram extraídas** — o
  cabeçalho rotacionado não deu confiança suficiente pra rotular cada
  uma corretamente, e não são necessárias pro escopo deste projeto.
- **Cruzamento com a FIPE**: `inmetro_pbev.py` também tenta casar cada
  veículo do PBEV com um modelo da tabela `fipe_modelos`, por
  aproximação de texto normalizado (minúsculo, sem acento, marca e
  modelo comparados separadamente). Grava o resultado em
  `fipe_pbev_match`, com uma coluna `confianca`:
  - `exato`: o modelo inteiro do PBEV aparece como prefixo do
    `nome_modelo` da FIPE dentro da mesma marca (ex.: PBEV "HB20S" casa
    exato com FIPE "HB20S Comfort 1.0-12V...").
  - `aproximado`: o modelo do PBEV aparece em algum lugar dentro do
    `nome_modelo` da FIPE, mas não como prefixo exato (ex.: PBEV
    "VELAR" dentro de FIPE "Range R. VELAR 2.0 4x4...").
  - `sem_match`: nenhum candidato encontrado (marca não reconhecida ou
    modelo não aparece em nenhum nome_modelo da marca na FIPE).

  Taxa de cruzamento obtida nesta rodada: **76,7%** dos 833 veículos do
  PBEV (75,8% exato + 1,0% aproximado) — bem acima do patamar de 20%
  que seria motivo de preocupação. Os ~23% sem match são majoritariamente
  modelos muito recentes (ex.: lançamentos 2026) que ainda não têm
  histórico de preço na FIPE, ou marcas novas no mercado brasileiro cujo
  catálogo FIPE ainda é incompleto — não é uma falha do método de
  comparação de texto, é a FIPE genuinamente não ter esses veículos
  ainda. Esse cruzamento é a nível de marca/modelo (não por ano/versão
  específica da FIPE) — refinamento fino fica para uma próxima etapa.
