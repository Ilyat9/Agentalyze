> **ЭТО РЕАЛЬНЫЙ ПРОГОН** (не синтетический пример, в отличие от
> [`sample_report.md`](sample_report.md)).
>
> - **Дата:** 2026-08-26 (MSK), выполнен локально на MacBook Air M2 (16 ГБ).
> - **Провайдер:** `qwen25-7b-local` — локальная Ollama 0.32.7,
>   модель `qwen2.5:7b-instruct-q4_K_M` (4.7 ГБ, Q4_K_M) через её
>   OpenAI-совместимый endpoint; API-ключи не использовались.
> - **Suite:** все 30 задач реестра, одна комбинация «задача × провайдер»
>   каждая, суммарно ~37 минут.
> - Файл получен конвейером `agentalyze compare --all-tasks` без ручных
>   правок чисел; шапка добавлена вручную только для пометки реальности
>   прогона.

---

# Agentalyze — Suite Run Report

- **Suite run ID:** `a31a8c66-6594-4116-a79e-c1ed62e77f6f`
- **Дата прогона:** 2026-08-26 08:31:12 MSK
- **Провайдеры:** qwen25-7b-local
- **Задач в прогоне:** 30
- **Общая длительность:** 2244.3s

## Summary

| Provider | Tasks | Success rate | Avg cost / task | Avg steps | p50 latency | p95 latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `qwen25-7b-local` | 30 | 53.3% | N/A | 7.6 | 6.31s | 18.12s |

> Стоимость `N/A`: для этих провайдеров нет записей в pricing-таблице — "неизвестная цена" не то же самое, что "бесплатно".

## Breakdown by task category

### NAVIGATION (5 task(s))

| Provider | Success rate | Runs failed |
| --- | ---: | ---: |
| `qwen25-7b-local` | 80.0% | 1 |

### FORM_FILL (5 task(s))

| Provider | Success rate | Runs failed |
| --- | ---: | ---: |
| `qwen25-7b-local` | 80.0% | 1 |

### EXTRACTION (5 task(s))

| Provider | Success rate | Runs failed |
| --- | ---: | ---: |
| `qwen25-7b-local` | 60.0% | 2 |

### MULTI_STEP (5 task(s))

| Provider | Success rate | Runs failed |
| --- | ---: | ---: |
| `qwen25-7b-local` | 20.0% | 4 |

### ERROR_RECOVERY (5 task(s))

| Provider | Success rate | Runs failed |
| --- | ---: | ---: |
| `qwen25-7b-local` | 40.0% | 3 |

### DISTRACTOR (5 task(s))

| Provider | Success rate | Runs failed |
| --- | ---: | ---: |
| `qwen25-7b-local` | 40.0% | 3 |

## Failure breakdown

### `qwen25-7b-local`

**По исходам (outcome):**

| Outcome | Count |
| --- | ---: |
| failure_verifier (agent finished, verifier disagreed) | 8 |
| failure_max_steps (step budget exhausted) | 1 |
| failure_timeout (wall-clock budget exhausted) | 5 |

**Почему (failure tags; один прогон может получить несколько тегов):**

| Tag | Count | Что это значит |
| --- | ---: | --- |
| `hallucinated_element` | 7 | referenced element ids absent from the latest snapshot |
| `looping` | 1 | repeated identical tool calls verbatim |
| `step_budget_exceeded_while_progressing` | 4 | ran out of step/time budget while still moving the page forward |
| `step_budget_exceeded_stuck` | 2 | burned the budget pushing an unchanged page state |
| `tool_error_mishandled` | 3 | repeated a failing action without adapting to the error |
| `graceful_give_up` | 5 | agent explicitly declared failure via done(success=false) |

## Confidence calibration

### `qwen25-7b-local`

Недостаточно данных для оценки калибровки: Low statistical significance: only 1 non-empty calibration bin(s) from 7 run(s) with confidence. Treat ECE=0.2286 as indicative, not reliable.

## Honest conclusion

_Вычислено программно из чисел этого прогона — без участия языковой модели._

- В прогоне только один провайдер (`qwen25-7b-local`): сравнение "лучший результат vs лучший выбор" требует минимум двух провайдеров.
