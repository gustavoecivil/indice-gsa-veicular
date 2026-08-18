# Registro de Decisões — Índice GSA Veicular

## 2026-08-12 — Início do projeto
Projeto de dados separado do simulador (alugaroucomprar). Objetivo: cruzar
FIPE (histórico de preço/depreciação), ANP (combustível), IBGE/SIDRA
(manutenção por região), SUSEP (risco/seguro) e SENATRAN (frota/acidentes)
para construir o Índice GSA de Custo Real de Posse — conteúdo de
autoridade e, no futuro, fonte de dado real para substituir os valores
heurísticos provisórios do simulador.

Fase 1: FIPE + ANP + IBGE (depreciação + energia + manutenção por cidade).
Fase 2: SUSEP + SENATRAN (risco/seguro, frota, acidentes).

## 2026-08-18 — Dataset Kaggle "franckepeixoto/tabela-fipe" descartado; backfill de histórico FIPE construído

Validado contra o gabarito (VW Gol 1996, código 005028-8): bateu exato em
jan/2020 (R$ 7.635,00), mas jan/2013 e jun/2001 não existem pra esse
veículo no dataset — cobertura mensal contínua só de jul/2017 a ago/2022
(arquivo nunca atualizado desde então). O arquivo inteiro cobre só 5.283
códigos FIPE distintos (~17% do nosso catálogo de 30.433) — amostra
esparsa, não extração sistemática. Descartado.

Construído `scripts/ingestao/fipe_backfill_historico.py` em vez disso,
usando o parâmetro `reference` da API paga (fipe.parallelum.com.br/api/v2)
pra buscar meses anteriores aos 12 já cobertos por `fipe_historico.py`.
`GET /references` confirma 308 tabelas mensais disponíveis, de
janeiro/2001 (código 62) a agosto/2026 (código 336) — dá pra recuar até
2001 rodando com `--meses-extras` maior no futuro; o padrão atual (12 =
"1 ano extra") é escopo deliberado, não limite técnico.

Dois bugs reais encontrados e corrigidos durante o teste manual (por isso
o teste de 5 minutos pedido na tarefa valeu a pena antes de agendar):
1. `MIN(mes_referencia)` em SQL compara a string "abril de 2026" como
   menor que "setembro de 2025" (ordem alfabética, não cronológica) —
   mirava no mês errado.
2. Recalcular o alvo a cada execução a partir de "qual mês já tem algum
   dado" fazia o script abandonar um mês parcialmente processado (poucas
   dezenas de 30.433 combinações) e pular pro bloco de 12 meses seguinte,
   perdendo o resto permanentemente.

Correção: o alvo agora é um intervalo FIXO calculado só a partir de
`/references` (não do conteúdo já gravado), e o progresso é medido
comparando `fipe_backfill_checkpoint` contra o tamanho do catálogo por
código de referência — só avança pro próximo mês quando o atual estiver
100% completo.

Tarefa agendada `IndiceGSA_BackfillHistorico` criada (diária, 03:00, como
SYSTEM — diferente da `IndiceGSA_IngestaoFIPE` mensal existente, que roda
como usuário interativo `gusta`) via
`scripts/run_fipe_backfill.ps1`. Testada rodando manualmente (script
direto, 5 min) e também disparada uma vez via `schtasks /run` pra
confirmar que funciona sob o contexto SYSTEM antes de deixar agendada.
