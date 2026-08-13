# Metodologia do Risco de Depreciação por Categoria

Calculado em: **2026-08-13 16:48 UTC**, pelo script `scripts/analise/calcular_risco_categoria.py`.

Esses números substituem os valores heurísticos inventados que o simulador usava antes (5%/10%/18%/15%) por uma variação real de preço, calculada a partir do histórico de preços FIPE já importado neste projeto (tabela `fipe_historico_precos`).

## Como cada veículo foi classificado em categoria

A classificação **não usa lista de marca** — isso evitaria viés subjetivo sobre o que é "popular" ou "premium". Em vez disso:

1. **Elétrico**: qualquer combinação código FIPE/ano/combustível cujo campo `combustivel` seja `Elétrico` vira, sozinha, a categoria `eletrico`.
2. Para todos os outros (Gasolina, Diesel, Flex, Álcool, Híbrido, Gás Natural), pegamos o **preço mais recente de cada veículo** e calculamos os quartis dessa distribuição de preço:
   - **popular**: preço ≤ R$ 24.520,50 (25% mais baratos)
   - **intermediario**: R$ 24.520,50 < preço ≤ R$ 145.713,00 (50% do meio)
   - **premium**: preço > R$ 145.713,00 (25% mais caros)

Os quartis são calculados só sobre os veículos não-elétricos — como o elétrico já vira categoria própria, não faz sentido deixá-lo puxar os limites de preço dos veículos a combustão/híbridos.

## Quantos veículos em cada categoria

| Categoria | Veículos na categoria (preço mais recente) | Veículos com histórico real (2+ meses) usados no cálculo de risco |
|---|---|---|
| popular | 12472 | 770 |
| intermediario | 24943 | 1241 |
| premium | 12472 | 186 |
| eletrico | 951 | 56 |

## Como o risco (variação real de preço) foi calculado

Para cada veículo que tem **2 ou mais meses distintos** de preço no histórico, calculamos:

- **Coeficiente de variação (CV)**: desvio padrão do preço ao longo dos meses disponíveis, dividido pela média do preço desse veículo no período. É o número usado no `risco_categoria.json`.
- **Amplitude percentual**: (preço máximo − preço mínimo) / preço médio, calculada em paralelo só como conferência cruzada do CV.

O risco de cada categoria é a **média do CV dos veículos daquela categoria** que têm histórico real. Veículos com um único mês de dado (a maior parte do catálogo, importada de uma vez via CSV de agosto/2026) entram na definição da categoria (pelo preço), mas não entram nessa conta — não têm variação pra medir.

## Resultado

| Categoria | Risco (CV médio) | Amplitude média (cross-check) |
|---|---|---|
| popular | 1.63% | 4.96% |
| intermediario | 2.31% | 7.03% |
| premium | 2.59% | 7.67% |
| eletrico | 2.71% | 7.31% |

JSON exportado: `data/processed/risco_categoria.json`

```json
{
  "popular": 0.0163,
  "intermediario": 0.0231,
  "premium": 0.0259,
  "eletrico": 0.0271
}
```

## Período de histórico usado

- 12 meses de referência distintos disponíveis no banco, de **setembro de 2025** a **agosto de 2026**.
- 75309 linhas de preço no total, para 50838 combinações distintas de código FIPE/ano-modelo/combustível.

## Limitações — seja honesto sobre isso ao usar o número

- **O histórico real ainda é curto.** A maior parte do catálogo (~48 mil veículos) foi importada de uma vez só via CSV completo da FIPE referente a um único mês (agosto/2026) — para esses veículos não existe ainda variação mês a mês pra medir. Só um subconjunto de cerca de 2.200 veículos foi acompanhado mês a mês (a maioria com os 12 meses de setembro/2025 a agosto/2026, via ingestão pela API do plano pago), e é só esse subconjunto que entra no cálculo do risco.
- **A categoria elétrico tem a menor amostra com histórico real** (56 veículos) — o número tende a ser mais instável que o das outras categorias e deve ser revisado quando houver mais meses acumulados.
- Os quartis de preço usam o preço **mais recente disponível** de cada veículo, que na prática é o mês do CSV mais recente pra quase todo mundo — não há problema de mistura de meses diferentes nessa parte da conta.
- Conforme a ingestão mensal (`scripts/ingestao/fipe_historico.py` / `fipe_importar_csv.py`) for acumulando mais meses, este script deve ser rodado de novo para refinar os números — o risco calculado aqui tende a ficar mais confiável com mais meses de dado real.
