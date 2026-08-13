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
