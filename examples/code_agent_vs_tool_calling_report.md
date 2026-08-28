# Tool-calling vs Code generation (smolagents) — сравнительный прогон

**Провайдер:** FakeProvider (детерминированный, без реального вызова модели).

Это НЕ то же самое, что собственный бенчмарк smolagents: набор задач, их домен и объём выборки здесь другие (30 задач Agentalyze, не задачи smolagents). Цифры ниже — честный результат именно этого прогона на этом наборе задач, не универсальное утверждение про code-agent вообще.

## Tool-calling vs Code generation (smolagents)

_Вычислено программно из реальных чисел прогонов ниже — без предположений и без переноса цифр из документации smolagents._

| Метрика | tool_calling | code |
| --- | ---: | ---: |
| Runs | 30 | 30 |
| Success rate | 16.7% | 16.7% |
| Avg steps | 1.9 | 1.9 |
| Avg cost | N/A | N/A |
| Avg wall-clock | 4.49s | 3.37s |

code-agent сделал в среднем **0.0% больше** шагов, чем tool-calling, на этом конкретном прогоне (1.9 vs 1.9 среднее число шагов).

**Распределение failure tags по стилю:**

| Tag | tool_calling | code |
| --- | ---: | ---: |
| `wrong_tool_choice` | 2 | 3 |
| `premature_done` | 23 | 23 |
| `graceful_give_up` | 2 | 2 |
| `code_execution_error` | 0 | 1 |
