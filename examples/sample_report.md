# Agentalyze — Suite Run Report

- **Suite run ID:** `20260820-140000-sample-fake-providers`
- **Дата прогона:** 2026-08-20 17:13:47 MSK
- **Провайдеры:** gpt-4o-mini-via-openrouter, llama31-8b-local
- **Задач в прогоне:** 18
- **Общая длительность:** 827.0s

> **Это заранее сгенерированный пример** — получен производственным
> конвейером отчёта (`compute_metrics` + `render_report`) на
> синтетических трейсах двух вымышленных провайдеров; реальная модель
> не вызывалась. Воспроизведение: `python examples/generate_sample_report.py`.


## Summary

| Provider | Tasks | Success rate | Avg cost / task | Avg steps | p50 latency | p95 latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `gpt-4o-mini-via-openrouter` | 18 | 83.3% | $0.0035 | 5.9 | 1.40s | 1.70s |
| `llama31-8b-local` | 18 | 50.0% | $0.0000 | 7.2 | 5.50s | 6.50s |

## Breakdown by task category

### NAVIGATION (3 task(s))

> ⚠️ **Малая выборка:** в этой категории всего 3 задач(и) — приведённые цифры заметно менее надёжны, чем для категории с 5+ задачами.

| Provider | Success rate | Runs failed |
| --- | ---: | ---: |
| `gpt-4o-mini-via-openrouter` | 100.0% | 0 |
| `llama31-8b-local` | 66.7% | 1 |

### FORM_FILL (3 task(s))

> ⚠️ **Малая выборка:** в этой категории всего 3 задач(и) — приведённые цифры заметно менее надёжны, чем для категории с 5+ задачами.

| Provider | Success rate | Runs failed |
| --- | ---: | ---: |
| `gpt-4o-mini-via-openrouter` | 100.0% | 0 |
| `llama31-8b-local` | 33.3% | 2 |

### EXTRACTION (3 task(s))

> ⚠️ **Малая выборка:** в этой категории всего 3 задач(и) — приведённые цифры заметно менее надёжны, чем для категории с 5+ задачами.

| Provider | Success rate | Runs failed |
| --- | ---: | ---: |
| `gpt-4o-mini-via-openrouter` | 100.0% | 0 |
| `llama31-8b-local` | 66.7% | 1 |

### MULTI_STEP (3 task(s))

> ⚠️ **Малая выборка:** в этой категории всего 3 задач(и) — приведённые цифры заметно менее надёжны, чем для категории с 5+ задачами.

| Provider | Success rate | Runs failed |
| --- | ---: | ---: |
| `gpt-4o-mini-via-openrouter` | 66.7% | 1 |
| `llama31-8b-local` | 66.7% | 1 |

### ERROR_RECOVERY (3 task(s))

> ⚠️ **Малая выборка:** в этой категории всего 3 задач(и) — приведённые цифры заметно менее надёжны, чем для категории с 5+ задачами.

| Provider | Success rate | Runs failed |
| --- | ---: | ---: |
| `gpt-4o-mini-via-openrouter` | 66.7% | 1 |
| `llama31-8b-local` | 33.3% | 2 |

### DISTRACTOR (3 task(s))

> ⚠️ **Малая выборка:** в этой категории всего 3 задач(и) — приведённые цифры заметно менее надёжны, чем для категории с 5+ задачами.

| Provider | Success rate | Runs failed |
| --- | ---: | ---: |
| `gpt-4o-mini-via-openrouter` | 66.7% | 1 |
| `llama31-8b-local` | 33.3% | 2 |

## Failure breakdown

### `gpt-4o-mini-via-openrouter`

**По исходам (outcome):**

| Outcome | Count |
| --- | ---: |
| failure_verifier (agent finished, verifier disagreed) | 2 |
| failure_max_steps (step budget exhausted) | 1 |

**Почему (failure tags; один прогон может получить несколько тегов):**

| Tag | Count | Что это значит |
| --- | ---: | --- |
| `looping` | 2 | repeated identical tool calls verbatim |
| `step_budget_exceeded_stuck` | 1 | burned the budget pushing an unchanged page state |
| `tool_error_mishandled` | 1 | repeated a failing action without adapting to the error |
| `premature_done` | 1 | SUSPECTED premature done(success=true): claimed done too early, verifier disagreed |
| `graceful_give_up` | 1 | agent explicitly declared failure via done(success=false) |

### `llama31-8b-local`

**По исходам (outcome):**

| Outcome | Count |
| --- | ---: |
| failure_verifier (agent finished, verifier disagreed) | 7 |
| failure_max_steps (step budget exhausted) | 2 |

**Почему (failure tags; один прогон может получить несколько тегов):**

| Tag | Count | Что это значит |
| --- | ---: | --- |
| `wrong_tool_choice` | 2 | invoked a nonexistent tool, or never used a state-changing tool on a verifier failure |
| `looping` | 4 | repeated identical tool calls verbatim |
| `step_budget_exceeded_stuck` | 2 | burned the budget pushing an unchanged page state |
| `tool_error_mishandled` | 2 | repeated a failing action without adapting to the error |
| `premature_done` | 3 | SUSPECTED premature done(success=true): claimed done too early, verifier disagreed |
| `graceful_give_up` | 2 | agent explicitly declared failure via done(success=false) |

## Confidence calibration

### `gpt-4o-mini-via-openrouter`

ECE = **0.2471** (17 прогон(ов) с confidence).

| Bin | Runs | Avg claimed confidence | Observed success rate |
| --- | ---: | ---: | ---: |
| [0.5, 0.6) | 1 | 0.55 | 100.0% |
| [0.6, 0.7) | 2 | 0.60 | 100.0% |
| [0.7, 0.8) | 3 | 0.72 | 100.0% |
| [0.8, 0.9) | 4 | 0.83 | 100.0% |
| [0.9, 1.0) | 7 | 0.91 | 71.4% |

### `llama31-8b-local`

ECE = **0.4438** (16 прогон(ов) с confidence).

| Bin | Runs | Avg claimed confidence | Observed success rate |
| --- | ---: | ---: | ---: |
| [0.5, 0.6) | 1 | 0.55 | 100.0% |
| [0.6, 0.7) | 1 | 0.60 | 100.0% |
| [0.7, 0.8) | 2 | 0.72 | 100.0% |
| [0.8, 0.9) | 4 | 0.84 | 50.0% |
| [0.9, 1.0) | 8 | 0.92 | 37.5% |

## Honest conclusion

_Вычислено программно из чисел этого прогона — без участия языковой модели._

- Провайдер **gpt-4o-mini-via-openrouter** даёт наивысший общий success rate (83.3%), но провайдер **llama31-8b-local** стоит на 100.0% дешевле при success rate 50.0%. Выбор зависит от того, что важнее для конкретного случая использования: максимальное качество (gpt-4o-mini-via-openrouter) или минимальная цена (llama31-8b-local).
