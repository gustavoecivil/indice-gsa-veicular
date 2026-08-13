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
