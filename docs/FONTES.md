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

## ANEEL — Tarifa de Energia (TE) residencial por distribuidora

- **Nome oficial**: dataset "Tarifas homologadas das distribuidoras de
  energia elétrica", publicado pela Agência Nacional de Energia Elétrica
  (ANEEL) no Portal de Dados Abertos (roda em CKAN, mesmo padrão do
  dados.gov.br).
- **URL**: https://dadosabertos.aneel.gov.br/dataset/5a583f3e-1646-4f67-bf0f-69db4203e89e
  — recurso CSV direto:
  https://dadosabertos.aneel.gov.br/dataset/5a583f3e-1646-4f67-bf0f-69db4203e89e/resource/fcf2906c-7c32-4b9b-a637-054e7a5234f4/download/tarifas-homologadas-distribuidoras-energia-eletrica.csv
- **Investigação de acesso feita antes de escrever o script**: a API de
  busca do portal (`package_search`) e o domínio
  `dadosabertos.aneel.gov.br` inteiro estavam **inacessíveis** no
  momento desta ingestão (conexão fechada pelo servidor logo no
  handshake TLS) — testado tanto por requisição direta (Python/`requests`)
  quanto por um navegador real, com o mesmo resultado, então não é
  bloqueio de IP de datacenter, parece o serviço genuinamente fora do ar.
  Evidências via Wayback Machine mostram o domínio respondendo
  normalmente ainda em maio/2026 — a indisponibilidade parece recente e
  provavelmente temporária, não uma descontinuação da plataforma. Como
  alternativa cogitada no escopo da tarefa, o relatório público "Ranking
  das Tarifas" (`portalrelatorios.aneel.gov.br/luznatarifa/rankingtarifas`)
  foi inspecionado e confirmado como um relatório **Power BI incorporado**
  (renderização visual via `powerbi.js`), sem CSV/API exportável por trás
  — não é uma fonte alternativa viável.
  - **Fallback usado nesta ingestão**: cópia do CSV oficial obtida via
    [web.archive.org](https://web.archive.org) (Wayback Machine),
    capturada em 2025-05-02 (69.318.328 bytes, cp1252). O script
    (`scripts/ingestao/aneel_tarifas.py`) tenta a URL oficial ao vivo
    primeiro por padrão; a flag `--arquivo-local` permite apontar pra
    uma cópia baixada manualmente quando a fonte estiver fora do ar,
    como foi necessário aqui.
- **Formato real da fonte**: CSV único com o histórico completo de
  **todas** as resoluções homologatórias de tarifa já publicadas (não só
  a vigente), para todas as classes de consumidor e subgrupos de tensão
  — 265.690 linhas na captura usada, delimitador `;`, codificação
  **cp1252** (não UTF-8; confirmado byte a byte antes de assumir, já que
  a decodificação errada produz caracteres corrompidos silenciosamente
  em vez de erro).
- **Frequência de atualização da fonte**: contínua — cada distribuidora
  tem seu próprio ciclo de reajuste tarifário (normalmente anual), então
  a ANEEL atualiza o arquivo conforme cada uma é reajustada, não em lote.
- **Como é ingerida aqui**: `scripts/ingestao/aneel_tarifas.py` filtra o
  CSV pra Tarifa de Energia (TE) residencial **vigente** por
  distribuidora: classe `Residencial`, subclasse `Residencial` (exclui a
  subclasse subsidiada `Baixa Renda`), subgrupo `B1` (baixa tensão),
  modalidade `Convencional` (exclui tarifa branca, pré-pagamento e
  ABRACE) e base tarifária `Tarifa de Aplicação` (o valor efetivamente
  cobrado do consumidor, não a `Base Econômica`, que é só um componente
  de cálculo). "Vigente" = a resolução cujo período
  `[DatInicioVigencia, DatFimVigencia]` contém a data de geração do
  arquivo fonte (`DatGeracaoConjuntoDados`).
- **Mapeamento distribuidora → UF (a complexidade central desta fonte)**:
  o CSV da ANEEL **não tem coluna de UF**, só a sigla da distribuidora
  (`SigAgente`) e o CNPJ (`NumCNPJDistribuidora`). O script resolve a UF
  de cada distribuidora consultando o CNPJ na API pública
  **minhareceita.org** (espelho gratuito e rápido, sem limite de taxa
  observado, dos dados públicos de CNPJ da Receita Federal) e usando a
  **UF de registro da matriz** como proxy da UF da área de concessão.
  Isso é uma aproximação razoável — e não uma coincidência — porque a
  atividade principal dessas empresas é exclusivamente "Distribuição de
  energia elétrica" (CNAE 35.14-0/00), o que exige presença local
  obrigatória na área da concessão pra operar; não é o caso genérico de
  usar endereço de matriz como proxy de onde uma empresa qualquer atua.
  **Limitação conhecida**: o método atribui cada distribuidora a uma
  única UF; não foi identificado nenhum caso, entre as 102 distribuidoras
  com tarifa residencial vigente nesta rodada, de uma mesma distribuidora
  atendendo partes de mais de uma UF a partir da mesma matriz/CNPJ — mas
  se isso existir (é um cenário conhecido do setor em áreas de fronteira
  entre estados), o método atual não captura essa divisão. Resultado
  desta rodada: as 27 UFs (26 estados + DF) têm pelo menos uma
  distribuidora — cobertura completa. Nove UFs têm **mais de uma**
  distribuidora, concentradas em cooperativas rurais pequenas: SC (26),
  RS (19), SP (19), PR (5), RJ (5), MG (3), SE (3), GO (2), ES (2); as
  outras 18 UFs têm exatamente uma. O cache do mapeamento fica na tabela
  `aneel_distribuidora_uf` (evita reconsultar a API a cada execução; use
  `--forcar-uf` pra reconsultar tudo).
- **O que a tabela representa**: `aneel_tarifa_residencial` tem, por
  distribuidora (`distribuidora` = `SigAgente`) e sua UF de concessão
  (`uf`, resolvida como acima), a Tarifa de Energia residencial
  atualmente vigente em R$/kWh (`tarifa_te_reais_kwh` — convertida do
  R$/MWh publicado pela ANEEL), junto com o CNPJ, o período de vigência
  da resolução (`data_inicio_vigencia`/`data_fim_vigencia`) e
  `data_referencia` (data de geração do arquivo fonte usado nesta
  rodada). É uma tabela de **estado atual** (full-refresh a cada
  execução, ~100 linhas), não uma série histórica — o CSV fonte tem o
  histórico completo, mas só a fatia vigente é carregada aqui, já que o
  objetivo é estimar custo de recarga hoje, não estudar a evolução
  tarifária.
  **A tarifa média por UF** (usada no export estático pro
  alugaroucomprar, não nesta tabela) é a **média simples** entre as
  distribuidoras de cada UF, sem ponderar por número de
  consumidores/unidades atendidas — então nas UFs com muitas
  cooperativas pequenas (SC, RS) uma cooperativa minúscula pesa igual a
  uma distribuidora grande do mesmo estado. Simplificação aceitável pro
  escopo (estimativa aproximada de custo de recarga), documentada aqui
  em vez de escondida.

## SUSEP — AUTOSEG (sinistralidade/prêmio do seguro auto) e IVR (índice de veículos roubados)

- **Nome oficial**: "Dados estatísticos do Seguro de Automóveis -
  AUTOSEG" e "IVR - Índice de Veículos Roubados", publicados pela SUSEP
  (Superintendência de Seguros Privados).
- **URL**: AUTOSEG está catalogado no Portal de Dados Abertos
  (dados.gov.br), mas os arquivos em si são baixados direto do host da
  SUSEP:
  https://www2.susep.gov.br/redarq.asp?arq=Autoseg2019B.zip (1º semestre
  2019) e
  https://www2.susep.gov.br/redarq.asp?arq=Autoseg2020A.zip (2º semestre
  2019). IVR não está catalogado em dados.gov.br — vive só em
  https://www2.susep.gov.br/menuestatistica/RankRoubo/menu1.asp, uma
  ferramenta de consulta on-line.
- **Investigação de acesso feita antes de escrever o script**:
  - A API antiga do CKAN em `dados.gov.br/api/3/action/*` agora exige
    token Bearer — `package_search`, `package_show`, qualquer chamada,
    todas retornam 401. O portal migrou pra um backend próprio; o
    endpoint real usado pelo frontend React é
    `dados.gov.br/api/publico/conjuntos-dados/...` (sem autenticação),
    descoberto inspecionando as chamadas de rede do site no navegador —
    não documentado publicamente, mas funcional.
  - O dataset AUTOSEG nesse catálogo está **parado desde 2021**: só
    lista 2 recursos reais de dados (1º e 2º semestre de **2019**),
    apesar da fonte ser descrita como semestral. Não é uma falha do
    script — é o estado real do catálogo, confirmado consultando o JSON
    de metadados do dataset (campo `dataUltimaAtualizacaoRecurso`:
    `2021-06-28T19:14:40`).
  - **IVR não está no catálogo de dados abertos.** As 11 datasets da
    SUSEP em dados.gov.br (conferidas via
    `/api/publico/conjuntos-dados/buscar?idOrganizacao=<id-susep>`) não
    incluem IVR sob nenhum nome. Buscas por "IVR", "veículos roubados",
    "roubo furto" no catálogo não retornam nada. O IVR só existe como
    ferramenta de consulta on-line legada
    (`www2.susep.gov.br/menuestatistica/RankRoubo`) — um formulário ASP
    clássico com filtros de categoria tarifária/região/sexo/idade do
    condutor, sem opção de download em massa. Confirmado por busca
    dedicada antes de assumir que só existe como página visual.
- **Formato real da fonte**:
  - AUTOSEG: cada zip semestral tem **14 CSVs diferentes** (não um
    arquivo único) — 3 tabelas "fato" grandes com granularidade de
    exposição/prêmio/sinistro em recortes geográficos diferentes
    (`arq_casco_comp.csv` por região+perfil do segurado, ~330MB;
    `arq_casco3_comp.csv` por CEP, ~1,1GB; `arq_casco4_comp.csv` por
    cidade, ~455MB), duas tabelas de totais regionais pequenas
    (`PremReg.csv`, `SinReg.csv`) e várias tabelas de-para pequenas
    (`auto_cidade.csv`, `auto_reg.csv`, `auto2_vei.csv` etc.).
    Delimitador `;`, separador decimal `,` (padrão brasileiro).
  - IVR: HTML puro (tabela `<table>` simples dentro da resposta de um
    POST em formulário ASP), sem JSON/CSV por trás.
- **Frequência de atualização da fonte**: AUTOSEG é semestral (mas o
  catálogo em dados.gov.br está travado em 2019, ver acima); IVR não
  informa data de referência na própria ferramenta de consulta — o
  texto da SUSEP diz que reflete "o último envio semestral", sem
  precisar qual.
- **Como é ingerida aqui**: `scripts/ingestao/susep.py`.
  - AUTOSEG: baixa os 2 zips, extrai só o `arq_casco_comp.csv` de cada
    um (a granularidade que bate com a descrição oficial do AUTOSEG —
    "classificadas de acordo com categoria, modelo e ano do veículo,
    região... e perfil do segurado") e carrega em `susep_autoseg` via
    leitor nativo de CSV do DuckDB (não pandas — esse arquivo específico
    tem ~3 milhões de linhas por semestre, e o leitor do DuckDB é bem
    mais leve em memória que `pandas.read_csv` nesse volume; pandas
    continua sendo usado normalmente pro IVR, que é pequeno).
    **Escopo consciente**: os outros dois arquivos "fato" de cada zip
    (`arq_casco3_comp.csv` por CEP e `arq_casco4_comp.csv` por cidade,
    juntos ~1,5GB por semestre) e as tabelas de-para não foram
    ingeridos nesta rodada, pra manter o volume gerenciável — ficam
    disponíveis nos zips baixados em `data/raw/` se fizerem falta
    depois. Os códigos (`COD_TARIF`, `REGIAO`, `COD_MODELO`, `SEXO`,
    `IDADE`) não são decodificados — ficam exatamente como vieram no
    CSV original, sem inventar schema.
  - **Retomável por semestre**: a máquina onde isso foi desenvolvido
    ficou sem espaço em disco (chegou a 0 bytes livres) e sem memória no
    meio da ingestão mais de uma vez — cada semestre é ~300MB
    descompactado. Por isso `ingerir_autoseg` não dropa a tabela inteira
    a cada execução: verifica quais valores de `ENVIO` já estão em
    `susep_autoseg` e pula os semestres já carregados, além de apagar o
    CSV extraído logo depois de carregar cada semestre (só o zip original
    fica em `data/raw/`). Rodar de novo depois de uma falha por falta de
    recurso retoma do semestre que faltou, em vez de refazer tudo.
  - IVR: faz o fluxo completo do formulário (GET na página do
    formulário pra pegar cookie de sessão ASP, POST em
    `resp_menu1.asp` com todos os filtros em "Todas"/"Todos" pra pegar o
    agregado nacional por modelo) e faz parsing do HTML de resposta com
    `pandas.read_html`.
- **O que cada tabela representa**:
  - `susep_autoseg`: uma linha por combinação categoria
    tarifária/região/modelo/ano-modelo/sexo/idade-do-condutor, com
    exposição (veículos-ano segurados), prêmio, importância segurada
    média, frequência de sinistros e indenizações — colunas exatamente
    como vieram do `arq_casco_comp.csv` da SUSEP (`COD_TARIF`, `REGIAO`,
    `COD_MODELO`, `ANO_MODELO`, `SEXO`, `IDADE`, `EXPOSICAO1`,
    `PREMIO1`, ..., `ENVIO`). 6.339.587 linhas (2 semestres de 2019).
  - `susep_ivr`: índice de roubo/furto (%) por modelo de veículo,
    agregado nacionalmente (todas as categorias/regiões/sexos/idades
    combinados) — 499 linhas, uma por modelo. **Limitação**: é só o
    agregado nacional; a ferramenta permite filtrar por região/perfil do
    segurado, mas isso exigiria uma chamada por combinação de filtro
    (centenas/milhares de combinações possíveis) contra um sistema ASP
    legado e frágil — fora de escopo nesta rodada.

## SENATRAN — RENAVAM (frota de veículos) e RENAEST (acidentes de trânsito)

- **Nome oficial**: "Registro Nacional de Veículos Automotores" (RENAVAM)
  e "Registro Nacional de Sinistros e Estatísticas de Trânsito"
  (RENAEST), publicados pela SENATRAN (Secretaria Nacional de Trânsito,
  Ministério dos Transportes).
- **URL**: os dois datasets vivem num portal **próprio** do Ministério
  dos Transportes — **dados.transportes.gov.br** — não no dados.gov.br
  (o catálogo geral usado por SUSEP/IBGE/ANEEL neste projeto).
  - RENAVAM: https://dados.transportes.gov.br/dataset/registro-nacional-de-veiculos-automotores-renavam
  - RENAEST: https://dados.transportes.gov.br/dataset/renaest
- **Investigação de acesso feita antes de escrever os scripts**:
  dados.transportes.gov.br também roda CKAN, mas — diferente do
  dados.gov.br (que passou a exigir token Bearer em toda chamada, ver
  seção SUSEP acima) — o endpoint público
  `/api/3/action/package_show?id=<dataset>` respondeu normalmente **sem
  autenticação**, confirmado com uma chamada direta antes de escrever
  qualquer script. Os dois datasets foram achados no catálogo por busca
  direta (não exigiu inspecionar chamadas de rede do site, diferente de
  SUSEP/ANEEL).
- **Formato real da fonte**: um recurso **ZIP por mês** em cada
  dataset, cada ZIP contendo o **snapshot/histórico completo até aquele
  mês** (não incremental) — baixar só o recurso mais recente já dá o
  dado atual, sem precisar puxar os 50-150+ meses de histórico
  catalogados.
  - **RENAVAM**: 156 recursos catalogados, mensal desde maio/2013. Cada
    ZIP tem um único TXT delimitado por `;`, snapshot daquele mês:
    `UF;Município;Marca Modelo;Ano Fabricação Veículo CRV;Qtd.
    Veículos`. **Não existe** quebra por categoria "tipo de veículo"
    nesse dataset — a granularidade real é por marca/modelo (ex.:
    "HONDA/BIZ EX"), bem mais fina que uma categoria de tipo. O mês de
    referência real está só no **nome do arquivo** (ex.:
    `..._julho_2026.zip`), não dentro do TXT.
  - **RENAEST**: 54 recursos catalogados, mensal desde outubro/2021.
    Cada ZIP tem **4 CSVs microdado** (não um agregado): `Acidentes_*`
    (1 linha por acidente, colunas incluindo `uf_acidente`,
    `codigo_ibge`, `ano_acidente`, `mes_acidente`, `num_acidente`),
    `TipoVeiculo_*` (1 linha por tipo de veículo envolvido em cada
    acidente, chave `num_acidente`, coluna `tipo_veiculo` com 35
    valores distintos confirmados — AUTOMOVEL, MOTOCICLETA, CAMINHAO,
    ONIBUS, BICICLETA etc.), `Localidade_*` (de-para `codigo_ibge` →
    município/UF/região) e `Vitimas_*` (perfil demográfico das
    vítimas — **não ingerido**, fora do escopo desta tarefa). O mês de
    referência real está no **nome do recurso** no CKAN
    (`"RENAEST - Mensal - MM-AAAA"`, com espaçamento inconsistente em
    parte dos 54 recursos), não num campo dentro dos CSVs. Delimitador
    `;`, codificação UTF-8, nomes de UF/município **sem acento** (ASCII
    simples) — sem ambiguidade de encoding pra resolver aqui, diferente
    do CSV cp1252 da ANEEL.
- **Frequência de atualização da fonte**: RENAVAM é mensal e corrente
  (recurso mais recente encontrado nesta ingestão: julho/2026).
  RENAEST é mensal mas com **defasagem de consolidação** — o recurso
  mais recente catalogado no momento desta ingestão referenciava
  abril/2026, publicado em 13/08/2026 (~4 meses de atraso), refletindo
  o tempo de homologação dos dados pelos DETRANs estaduais antes de
  virarem RENAEST nacional. Um novo recurso apareceu no catálogo entre
  o início e o fim desta tarefa (de "03-2026" pra "04-2026") — sinal de
  que a busca do recurso mais recente pelo nome (não hardcoded) era a
  escolha certa, já que uma URL fixa teria ficado desatualizada em
  poucos dias.
- **Como é ingerida aqui**:
  - `scripts/ingestao/senatran_frota.py`: consulta o CKAN, acha o
    recurso mais recente pelo mês/ano no **nome do arquivo** (não pelo
    campo `created` do CKAN, que é data de upload e pode divergir do
    mês de referência), baixa o ZIP, extrai o TXT e carrega **por
    completo** via leitor nativo de CSV do DuckDB (22.690.877 linhas,
    ~1,2GB descomprimido — carregado sem problema, ordem de grandeza
    similar ao `arq_casco_comp.csv` da SUSEP). Grava em
    `senatran_frota` (uf, municipio, marca_modelo,
    ano_fabricacao_crv, quantidade, mes_referencia — este último
    injetado a partir do nome do arquivo). Full-refresh a cada
    execução.
  - `scripts/ingestao/senatran_acidentes.py`: mesmo padrão de busca do
    recurso mais recente (aqui pelo nome do recurso no CKAN), baixa o
    ZIP (~500MB) e extrai só os 3 CSVs necessários (Acidentes,
    TipoVeiculo, Localidade — Vitímas fica de fora, economiza ~1,8GB).
    Por padrão carrega só os últimos 3 anos de acidentes
    (`--anos-recentes`, ajustável; `--tudo` pra série completa desde
    outubro/2021) — o volume completo é dezenas de milhões de linhas,
    grande demais pra carregar sempre por padrão. Agrega via SQL
    (DuckDB lendo os CSVs direto, sem pandas) juntando `Acidentes` com
    `TipoVeiculo` por `num_acidente` e com `Localidade` pra resolver o
    nome do município, agrupando por
    uf/codigo_ibge/município/ano/mês/tipo_veiculo. Grava em
    `senatran_acidentes`. Full-refresh a cada execução.
- **O que cada tabela representa**:
  - `senatran_frota`: frota de veículos por UF, município,
    **marca/modelo** (não "tipo de veículo" — a fonte não tem essa
    quebra, ver acima) e ano de fabricação, no mês de referência mais
    recente disponível. Inclui 3 valores de UF que são rótulos da
    própria SENATRAN pra registros sem UF resolvida (`Sem Informação`,
    `Não se Aplica`, `Não Identificado`) — mantidos como vieram, sem
    filtrar, já que representam ~3M veículos reais (`Sem Informação`)
    que não devem ser descartados silenciosamente de um total nacional.
  - `senatran_acidentes`: acidentes de trânsito por UF, código IBGE do
    município (+ nome, quando resolvido via `Localidade`), ano/mês e
    tipo de veículo envolvido, com **duas métricas separadas** (de
    propósito, pra não confundir uma coisa com a outra):
    `quantidade_acidentes` (COUNT DISTINCT de `num_acidente` — quantos
    acidentes distintos tiveram pelo menos um veículo daquele tipo
    envolvido) e `quantidade_veiculos` (soma de `qtde_veiculos` do
    RENAEST — quantos veículos daquele tipo estiveram envolvidos, pode
    superar `quantidade_acidentes` quando há mais de um veículo do
    mesmo tipo no mesmo acidente). **Limitação conhecida, da própria
    fonte**: somar `quantidade_acidentes` entre todos os tipos de
    veículo de um UF/mês não bate com o total real de acidentes daquele
    UF/mês, porque um acidente com carro+moto conta nos dois tipos —
    o RENAEST publica por tipo de veículo envolvido, não um total
    mutuamente exclusivo. Nesta rodada (`--anos-recentes 3`, padrão):
    309.203 linhas, cobrindo 2024–2026, 35 tipos de veículo distintos e
    86.665 combinações UF/município/mês distintas.

## Proxy de frota corporativa/locadora — ranking Fenabrave (fallback, sem dado direto de PJ)

- **Motivação**: identificar os modelos de veículo mais usados por
  locadoras/frota corporativa, pra uso futuro como filtro no simulador
  ou em análises. Não existe dataset público brasileiro com "frota da
  locadora X por modelo" — essa informação é comercialmente sensível e
  não é aberta.
- **Investigação feita antes de escrever o script**: verificado
  primeiro se `senatran_frota` (RENAVAM, já integrada — ver seção
  SENATRAN acima) teria a granularidade necessária. **Não tem**: as
  únicas 6 colunas da tabela são `uf`, `municipio`, `marca_modelo`,
  `ano_fabricacao_crv`, `quantidade`, `mes_referencia` — nenhuma coluna
  de tipo de proprietário (pessoa física/jurídica) ou equivalente. Essa
  granularidade **nunca existiu no arquivo fonte do RENAVAM** (o TXT
  mensal só tem `UF;Município;Marca Modelo;Ano Fabricação Veículo
  CRV;Qtd. Veículos` — confirmado na ingestão original, não é uma
  limitação introduzida por este projeto). Não dá pra calcular
  proporção PJ/PF por modelo com o dado disponível.
- **Fallback usado (proxy, não confirmação direta)**: ranking de
  emplacamento total por modelo no Brasil, publicado pela **Fenabrave**
  (Federação Nacional da Distribuição de Veículos Automotores), obtido
  via o agregador **Auto Reporter**
  (autoreporter.news/emplacamentos) — referência **julho de 2026**
  (mensal, não acumulado do ano). Premissa: os modelos mais vendidos no
  varejo em geral tendem a ser também os mais comprados em volume por
  locadoras/frotistas (que compram em escala do que já é popular, tem
  rede de peças ampla e boa liquidez de revenda), não o contrário. **Não
  é confirmação de que a locadora X tem o carro Y** — é uma aposta
  razoável na ausência de dado direto, e deve ser tratada como tal em
  qualquer uso futuro.
- **Como é calculado aqui**: `scripts/analise/proxy_frota_locadora.py`
  cruza os 50 modelos mais emplacados (ranking hardcoded no script, com
  marca, categoria e unidades emplacadas) contra
  `fipe_historico_precos`, casando por marca (com mapeamento manual
  Fenabrave→FIPE, ex.: "GM"→"CHEVROLET", "VW"→"VOLKSWAGEN") e por texto
  no nome do modelo. Duas marcas novas (chinesas, lançadas em
  2025/2026 — Jaecoo e Omoda) exigiram tratamento especial: a FIPE não
  repete o nome da marca no campo `modelo` pra elas (aparece só "7
  Elite..." ou "5 Luxury...", sem "Jaecoo"/"Omoda" no texto) — resolvido
  casando por marca exata em vez de por texto no modelo.
- **Por que a saída guarda uma LISTA de códigos por modelo, não um
  código único**: a FIPE cataloga por trim/motorização específico (ex.:
  "Strada 1.3 mpi Fire 8V 67cv CE"), não por "modelo" no sentido
  comercial (ex.: "Strada") — 1 modelo do ranking Fenabrave bate com
  entre 2 (Omoda 5, King) e 116 (HB20) códigos FIPE distintos nesta
  rodada. Usar um código só seria arbitrário e sub-representaria o
  modelo. Pra filtrar no simulador, o padrão real é (`marca_fipe`,
  prefixo/token do `modelo`), não um `codigo_fipe` isolado.
- **O que o arquivo representa**: `data/processed/proxy_frota_locadora.json`
  tem, por modelo (rank, nome, marca, categoria, unidades emplacadas em
  julho/2026), a marca como aparece na FIPE, a lista completa de
  `codigos_fipe` que casaram, e até 3 nomes de exemplo. **Resultado
  desta rodada**: 50/50 modelos com pelo menos 1 código FIPE mapeado
  (nenhum ficou sem match), 1.143 códigos FIPE distintos cobertos ao
  todo. Não grava tabela no DuckDB — é um artefato JSON derivado,
  reproduzível rodando o script de novo (o ranking Fenabrave, por ser
  hardcoded e datado de julho/2026, precisa ser atualizado manualmente
  se for reusar isso mais adiante no tempo).
