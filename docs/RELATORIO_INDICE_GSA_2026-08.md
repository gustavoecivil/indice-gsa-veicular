# Índice GSA — Relatório de Custo Real de Posse de Veículos

*Gerado em 2026-08-13, a partir de dado real já ingerido no projeto (FIPE + ANP + IBGE/SIDRA).*

## Resumo

O preço da tabela FIPE se mostrou historicamente estável mês a mês — variação média de 2,11% entre os veículos com histórico real acompanhado, o que reforça a FIPE como referência confiável de curto prazo. O combustível já não é assim: a gasolina custa 22,29% a mais em RR do que em MG, e o etanol varia ainda mais entre estados (60,06% do mais caro ao mais barato) — onde o carro roda pesa tanto quanto qual carro é. Na manutenção, o conserto de automóvel subiu em 12 meses de 2,09% em Salvador - BA a 10,29% em Grande Vitória - ES — uma diferença regional grande o suficiente pra merecer entrar na conta do custo total de posse.

## FIPE — estabilidade de preço

Entre os 2.253 veículos (de um catálogo de 50.838 combinações código FIPE/ano/combustível) que têm histórico real de 2 ou mais meses ao longo de 12 meses de dado disponível, a variação de preço mês a mês foi de **2,11%** em média (coeficiente de variação — desvio padrão sobre a média do preço de cada veículo), com amplitude média (máximo menos mínimo sobre a média) de 6,38%.

Isso confirma o achado que já tínhamos ao calcular o risco por categoria (`docs/METODOLOGIA_RISCO.md`): a tabela FIPE se move pouco de um mês pro outro. O risco real de depreciação de um veículo está muito mais em revenda/mercado no horizonte de anos do que em oscilação mês a mês da própria tabela.

## ANP — combustível por região

### Gasolina comum

**5 UFs mais caras**

| UF | Preço médio |
|---|---|
| RR | R$ 7,57/l |
| RO | R$ 7,36/l |
| AM | R$ 7,31/l |
| AC | R$ 7,26/l |
| TO | R$ 7,07/l |

**5 UFs mais baratas**

| UF | Preço médio |
|---|---|
| MG | R$ 6,19/l |
| RS | R$ 6,28/l |
| SP | R$ 6,35/l |
| MS | R$ 6,43/l |
| PB | R$ 6,47/l |

Gasolina comum custa **22,29%** a mais em RR (R$ 7,57/l) do que em MG (R$ 6,19/l), na semana de referência mais recente (2026-08-02).

### Etanol hidratado

**5 UFs mais caras**

| UF | Preço médio |
|---|---|
| AP | R$ 5,81/l |
| RR | R$ 5,41/l |
| SE | R$ 5,34/l |
| RO | R$ 5,26/l |
| RN | R$ 5,23/l |

**5 UFs mais baratas**

| UF | Preço médio |
|---|---|
| SP | R$ 3,63/l |
| MT | R$ 3,71/l |
| MS | R$ 3,90/l |
| MG | R$ 3,94/l |
| PR | R$ 4,13/l |

Etanol hidratado custa **60,06%** a mais em AP (R$ 5,81/l) do que em SP (R$ 3,63/l), na semana de referência mais recente (2026-08-02).

O padrão geográfico se repete nos dois combustíveis: Norte e Nordeste concentram os preços mais altos (custo logístico de distribuição), Sudeste e Sul os mais baixos. Pra quem está simulando o custo de posse, a UF de uso do carro muda o gasto com combustível tanto quanto — às vezes mais do que — a escolha entre gasolina e etanol dentro do mesmo posto.

## IBGE/SIDRA — manutenção por região metropolitana

Variação acumulada em 12 meses do subitem `5102011.Conserto de automóvel` do IPCA, mês de referência 2026-07-01, para as 10 regiões metropolitanas com essa abertura disponível (média das regiões: 5,62%):

| Região metropolitana | Acum. 12 meses | Variação do mês |
|---|---|---|
| Grande Vitória - ES | 10,29% | 1,57% |
| Rio de Janeiro - RJ | 8,81% | 1,68% |
| Recife - PE | 7,65% | 1,66% |
| Curitiba - PR | 6,93% | 0,27% |
| Belo Horizonte - MG | 5,78% | 1,00% |
| Belém - PA | 5,52% | 0,36% |
| São Paulo - SP | 3,90% | 2,61% |
| Fortaleza - CE | 2,97% | -0,54% |
| Porto Alegre - RS | 2,22% | -0,71% |
| Salvador - BA | 2,09% | 3,07% |

**Grande Vitória - ES** teve a maior alta acumulada (10,29%) e **Salvador - BA** a menor (2,09%). Acima da média das regiões: Grande Vitória - ES, Rio de Janeiro - RJ, Recife - PE, Curitiba - PR, Belo Horizonte - MG. Abaixo: Belém - PA, São Paulo - SP, Fortaleza - CE, Porto Alegre - RS, Salvador - BA. Manter e consertar carro num desses dois extremos representa uma diferença de mais de 8,20% em 12 meses só nessa linha de custo.

## Metodologia e limitações

- **FIPE**: o cálculo de variação usa só veículos com 2+ meses de histórico real (2.253 de 50.838 combinações no catálogo) — a maior parte do catálogo veio de um único CSV mensal e ainda não tem série temporal própria pra medir variação. Ver `docs/METODOLOGIA_RISCO.md` para o detalhamento por categoria de veículo (popular/intermediário/premium/elétrico).

- **ANP**: preço médio de revenda por semana e UF, direto da série histórica oficial da ANP (não passa por posto individual, é a média semanal já agregada pela própria agência). A semana de referência pode variar ligeiramente entre UFs quando alguma não teve pesquisa de preço concluída na semana mais recente.

- **IBGE/SIDRA**: cobertura territorial restrita às 10 regiões metropolitanas que o IPCA abre nessa tabela (Belém, Fortaleza, Recife, Salvador, Belo Horizonte, Grande Vitória, Rio de Janeiro, São Paulo, Curitiba, Porto Alegre) — não é o Brasil inteiro, e nem todo estado tem uma região metropolitana coberta. Não existe um subitem "Manutenção e Acessórios" único no IPCA: usamos `Conserto de automóvel`, que é o subitem que melhor representa mão de obra de manutenção; `Acessórios e peças` também está ingerido na mesma tabela (`ibge_manutencao_veiculos`) e pode entrar em uma próxima versão deste relatório.

- **Todos os três** ainda têm histórico curto (2-3 anos de série real, a maior parte concentrada em 2023-2026) — números tendem a ficar mais estáveis e mais confiáveis conforme a ingestão mensal for acumulando mais meses. Este relatório reflete o dado disponível em 2026-08-13 e deve ser regenerado periodicamente, não tratado como estático.

---

**Gustavo Santos Analytics — Cultura Data-Driven**
