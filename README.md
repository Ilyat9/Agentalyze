# Agentalyze

Eval harness for LLM agents working with tools and a real browser. Instead of
generic benchmarks, Agentalyze focuses on a task suite of concrete agentic web
tasks (form filling, fact extraction with confidence, tool-error recovery) and
pinpoints *where exactly* an agent breaks — not just the success rate.

> **Проект в разработке, реализуется поэтапно. Текущая фаза: 5 — сравнение
> моделей и честные отчёты завершена. См. [`ROADMAP.md`](ROADMAP.md) для полного плана.**

## Требования

- Python **3.11+** (минимальная зафиксированная версия — см. `pyproject.toml`)

## Установка

```bash
pip install -e ".[dev,browser]"
playwright install chromium   # браузерные бинарники ставятся отдельно от Playwright
```

## Запуск одной задачи (Фаза 3)

Раннер берёт одну задачу из реестра, одного настроенного провайдера,
поднимает локальный сервер фикстур и реальный Chromium, гоняет ReAct-цикл
(модель действует через browser-инструменты: `navigate`, `click`,
`type_text`, `select_option`, `submit_form`, `extract_text`, `wait_for`) до
вызова `done(...)`, после чего задачу верифицирует программный верификатор
из Фазы 1. Полный трейс выполнения сохраняется в JSON.

```bash
# 1. Провайдеры описываются в providers.yaml (см. раздел Providers ниже)
cp providers.example.yaml providers.yaml   # и отредактируйте под себя

# 2. Прогон одной задачи одним провайдером
agentbench run --task form-fill-basic-01 --provider gpt-4o-mini-via-openrouter
```

Пример вывода:

```
==============================================================
Task:       form-fill-basic-01 (Fill the contact form)
Provider:   gpt-4o-mini-via-openrouter
Outcome:    success
Steps:      6
Tokens:     prompt=3120 completion=210 cost=N/A
Verifier:   Success marker '#success-marker' is present and visible.
Wall time:  14.2s
Trace:      9f0c.../trace.json
==============================================================
```

Полезные флаги: `--providers-config PATH`, `--results-dir PATH`,
`--fixtures-dir PATH` (переопределяют соответствующие переменные окружения);
`agentbench tasks` печатает все id задач. Код выхода — `0` только при
`SUCCESS`.

Артефакты прогона складываются в `AGENTALYZE_RESULTS_DIR` (по умолчанию
`./results`):

```
results/<run_id>/trace.json              # полный машинночитаемый трейс (RunTrace)
results/<run_id>/screenshots/step_N.png  # скриншот страницы после каждого действия
```

Трейс самодостаточен: для каждого шага он хранит весь контекст, отправленный
модели, её ответ, вызванный инструмент, результат действия, sha256-хэш DOM
после шага и путь к скриншоту. Итог классифицируется в `RunOutcome`
(`success`, `failure_verifier`, `failure_max_steps`, `failure_timeout`,
`failure_provider_error`, `failure_tool_error`, `failure_crash`) — на этих
сырых трейсах строятся аналитические Фазы 4–6.

## Analysis (Фаза 4)

Аналитический слой (`src/agentalyze/analysis/`) читает готовые `RunTrace`-объекты
и превращает их в структурированные метрики. Он ничего не запускает (ни браузер,
ни модель) и ничего не рисует — читаемые отчёты и сравнение моделей — это
Фаза 5 (см. раздел «Comparing providers» ниже); здесь только вычисления, всё
покрывается быстрыми unit-тестами (`pytest tests/analysis/`, доли секунды).

Что считается:

* **Failure-таксономия** (`failure_taxonomy.py`) — `classify_failure(trace)`
  присваивает неудачному прогону один или несколько тегов из `FailureTag`:
  неверный выбор инструмента, галлюцинация element_id, зацикливание,
  исчерпание бюджета «в движении» vs «в ступоре», проигнорированная ошибка
  инструмента, подозрение на преждевременное `done(success=true)`, осознанный
  отказ. Каждый тег — конкретная задокументированная эвристика с настраиваемым
  порогом, не «на глаз».
* **Агрегированные метрики** (`metrics.py`) — `compute_metrics(traces)`
  (трейсы одного провайдера): success rate, разбивка по исходам и по
  failure-тегам, стоимость, латентность p50/p95 вызова модели, среднее число
  шагов и рекурсивная разбивка по категориям задач (`by_category`) — видно,
  где именно модель проваливает («сильна в NAVIGATION, тонет в ERROR_RECOVERY»).
* **Калибровка уверенности** (`calibration.py`) —
  `compute_calibration_report(traces)` собирает пары «заявленная confidence из
  done(...) против фактического вердикта верификатора», бинует [0,1] и считает
  ECE. При малом числе непустых бинов отчёт явно предупреждает о низкой
  статистической значимости.
* **Стоимость** (`pricing.py` + `cost.py`) — перевод токенов в USD по
  редактируемой таблице цен: скопируйте
  [`pricing.example.yaml`](pricing.example.yaml) в `pricing.yaml`. Цены НЕ
  захардкожены в коде — проверяйте актуальные цены у своего провайдера перед
  финансовыми решениями. Провайдер без записи в таблице даёт «стоимость
  неизвестна» (`None`); локальный Ollama с `free: true` даёт честные `$0.0`.

Пример:

```python
from pathlib import Path

from agentalyze.analysis import classify_failure, compute_calibration_report, compute_metrics, load_pricing
from agentalyze.runner.trace import load_trace

traces = [load_trace(p) for p in Path("results").glob("*/trace.json")]

for t in traces:
    if not t.success:
        print(t.task_id, [tag.value for tag in classify_failure(t)])

metrics = compute_metrics(traces, pricing=load_pricing(Path("pricing.yaml")))
print(metrics.success_rate, metrics.p95_latency_seconds)
print({cat.value: m.success_rate for cat, m in metrics.by_category.items()})

calibration = compute_calibration_report(traces)
print(calibration.ece, calibration.low_statistics_warning)
```

## Comparing providers (Фаза 5)

Оркестратор (`src/agentalyze/orchestration/`) прогоняет **весь** набор задач
(или его подмножество) **несколькими** провайдерами последовательно — одна
комбинация «задача × провайдер» за другой, — сохраняя каждый трейс на диск по
мере выполнения (сбой в середине долгого прогона не теряет уже готовые
результаты) и печатая прогресс `[i/N]`. Параллельное выполнение в этой фазе
намеренно не реализовано: `max_concurrent > 1` отклоняется с явной ошибкой.

```bash
# Прогон двух провайдеров на двух категориях задач
agentbench compare --providers gpt-4o-mini-via-openrouter,llama3.1-8b-local \
    --category form_fill,error_recovery

# Или на всех 18 задачах сразу
agentbench compare --providers gpt-4o-mini-via-openrouter,llama3.1-8b-local --all-tasks

# Найти и открыть конкретные интересные трейсы готового прогона
agentbench inspect --suite-run <suite_run_id> --tag looping
agentbench inspect --suite-run <suite_run_id> --outcome failure_verifier
```

Перед стартом каждый выбранный провайдер проходит `health_check()`: если
провайдер недоступен, команда завершается явной ошибкой, не начиная заведомо
мёртвый прогон (никаких интерактивных промптов — CLI пригоден для автоматизации).

Артефакты прогона:

```
results/<suite_run_id>/suite_run.json   # весь SuiteRunResult (все трейсы + метрики)
results/<suite_run_id>/report.md        # Markdown-отчёт сравнения
results/<run_id>/trace.json             # отдельные трейсы, как в Фазе 3
```

Отчёт содержит шесть секций: метаданные прогона, сводную таблицу провайдеров,
разбивку по категориям задач, failure breakdown с человеческим объяснением
тегов, калибровку уверенности (ECE печатается только если статистика
достаточна — иначе явно написано «недостаточно данных») и **честный итоговый
вывод**, вычисляемый программно из чисел конкретного прогона: он прямо называет
случаи, когда «лучший в таблице» — не лучший выбор. Фрагмент отчёта:

```markdown
## Summary

| Provider | Tasks | Success rate | Avg cost / task | Avg steps | p50 latency | p95 latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `gpt-4o-mini-via-openrouter` | 3 | 66.7% | $0.0120 | 2.3 | 1.20s | 3.10s |
| `llama3.1-8b-local`          | 3 | 33.3% | $0.0080 | 4.7 | 6.90s | 9.40s |

## Honest conclusion

- Провайдер **gpt-4o-mini-via-openrouter** даёт наивысший общий success rate
  (66.7%), но провайдер **llama3.1-8b-local** стоит на 33.3% дешевле при
  success rate 33.3%. Выбор зависит от того, что важнее для конкретного случая
  использования: максимальное качество (gpt-4o-mini-via-openrouter) или
  минимальная цена (llama3.1-8b-local).
```

Категории с малым числом задач (< 5) помечаются в отчёте как малая выборка, а
не подаются с той же уверенностью, что и наполненные категории.

## Regression checks in CI (Фаза 6)

Фаза 6 добавляет сравнение двух прогонов **во времени**: вот baseline-прогон,
вот новый прогон после изменения промпта/логики агента — что изменилось?
Само выполнение задач не изменилось (`run_task`/`run_suite` из Фаз 3/5
используются как есть) — это новый слой поверх уже сохранённых результатов.

### Команды

```bash
# Явное указание обоих прогонов:
agentbench regression-check --baseline <suite_run_id> --new <suite_run_id>

# Без --baseline: используется baseline, помеченный ранее:
agentbench set-baseline --suite-run <suite_run_id>
agentbench regression-check --new <suite_run_id>

# Посмотреть diff, не прерывая скрипт ненулевым кодом возврата:
agentbench regression-check --baseline <id> --new <id> --allow-regressions
```

Baseline хранится в простом файле-указателе `{results_dir}/current_baseline.txt`
и обновляется **только явно** командой `set-baseline` — никогда автоматически
после прогона. Baseline — это осознанное решение пользователя: «текущее
состояние — новая точка отсчёта».

### Сравнение по парам (task, provider)

Трейсы сопоставляются по паре `(task_id, provider_name)`. Сравниваются только
общие провайдеры двух прогонов; провайдеры, присутствующие лишь в одном из
них, явно перечисляются в отчёте (`providers_only_in_baseline` /
`providers_only_in_new`) — несоответствие не замалчивается. Шесть статусов
для каждой пары:

| Статус | Значение |
| ------ | -------- |
| `still_passing` | SUCCESS → SUCCESS |
| `still_failing` | FAILURE_* → FAILURE_* (любой вид неудачи в обоих прогонах) |
| `regressed` | SUCCESS → FAILURE_* |
| `fixed` | FAILURE_* → SUCCESS |
| `newly_added` | задача есть в новом прогоне, отсутствовала в baseline (набор вырос) |
| `removed` | задача была в baseline, исчезла из нового прогона |

Полный машинночитаемый отчёт сохраняется в
`{results_dir}/{new_suite_run_id}/regression_report.json`.

### Коды возврата (CI-gate)

| Код | Когда |
| --- | ----- |
| `0` | Регрессий нет (или передан `--allow-regressions`) |
| `1` | Есть хотя бы одна регрессия (`regressed_count > 0`) и флаг `--allow-regressions` не передан |
| `2` | Проблема использования: неизвестный id прогона, baseline не установлен |

Это делает команду пригодной как gate в CI: шаг workflow падает, если задачи
стали проходить хуже:

```yaml
- run: agentbench regression-check --new "$NEW_RUN_ID"   # exit 1 => PR красный
```

### Активация GitHub Actions workflow

В репозитории лежит **шаблон** `.github/workflows/regression-check.yml.example`.
Расширение `.example` выбрано намеренно: workflow требует реального доступа к
LLM-провайдеру и секретов API-ключей, которых у типичного форкнутого
репозитория нет — поэтому он никогда не запустится сам по себе. Чтобы
активировать:

1. Переименуйте файл: `git mv .github/workflows/regression-check.yml.example .github/workflows/regression-check.yml`.
2. Добавьте API-ключ в секреты репозитория (Settings → Secrets and variables → Actions),
   например `OPENROUTER_API_KEY`, и укажите его в `env:` шага compare.
3. Закоммитьте реальный `providers.yaml` (по образцу `providers.example.yaml`)
   и подставьте имя провайдера в `--providers`.
4. Зафиксируйте baseline: подставьте известный `BASELINE_RUN_ID` в env шага gate
   и обеспечьте наличие артефактов этого прогона (кеш/перезапуск в CI), либо
   один раз выполните `agentbench set-baseline --suite-run <id>` внутри CI.
5. Сузьте триггер `paths:` под реальные места промптов/логики вашего агента —
   текущий фильтр нарочно широкий.
6. В CI запускайте компактное подмножество suite (`--category` с 1–2
   категориями или короткий список задач): regression-check должен занимать
   минуты, а не часы; полные прогоны — для scheduled/manual запусков.

Подробные комментарии — прямо в теле `.yml.example`.

## Запуск тестов

```bash
pytest                # быстрый прогон: без тестов, требующих Chromium или Ollama
pytest -m browser     # интеграционные тесты фикстур, верификаторов и раннера (нужен Chromium)
pytest -m requires_ollama  # интеграционный тест провайдера с реальным Ollama
pytest -m e2e_live    # самый редкий: реальная модель + реальный браузер на одной easy-задаче
```

Тесты, требующие реального Chromium, помечены маркером `browser` и исключены
из прогона по умолчанию (для них нужно `pip install -e ".[browser]" &&
playwright install chromium`).

Линтинг:

```bash
ruff check .
```

## Task suite

Набор из **18 агентных веб-задач** в 6 категориях (по 3 задачи на категорию,
с нарастающей сложностью easy → medium → hard):

| Категория        | Что проверяет                                                              |
| ---------------- | -------------------------------------------------------------------------- |
| `navigation`     | поиск и переход по ссылке/меню/табу                                        |
| `form_fill`      | заполнение и отправка форм (включая клиентскую валидацию и зависимые поля) |
| `extraction`     | извлечение факта + явная самооценка уверенности агента                     |
| `multi_step`     | последовательность из 3+ действий на разных состояниях одной фикстуры      |
| `error_recovery` | восстановление после намеренно сломанного элемента/страницы                |
| `distractor`     | выбор правильного элемента среди визуально похожих неправильных            |

Каждая задача — декларативное описание (`Task`), локальная HTML-фикстура с
DOM-маркером успеха и программный верификатор, который смотрит только на
финальное состояние страницы. Никакого агента и LLM в этой фазе нет —
раннер появится в Фазе 3.

Посмотреть реестр задач:

```bash
python -c "from agentalyze.tasks.registry import TASKS; \
  [print(t.id, '|', t.category.value, '|', t.difficulty, '|', t.title) for t in TASKS]"
```

Проверить, что все фикстуры технически решаемы (программно, без агента):

```bash
pytest -m browser
# или: python -m agentalyze.tasks.validate_fixtures
```

Слои чётко разделены: публичное описание задачи (`agentalyze.tasks.models.Task`)
не содержит селекторов; служебные reference-селекторы для валидации живут в
`agentalyze.tasks.reference` и **никогда не передаются агенту**.

## Providers

Единый интерфейс вызова LLM (`agentalyze.providers`) скрывает конкретные
бэкенды за протоколом `Provider` с двумя методами: `chat_completion()`
(возвращает `CompletionResult` с токенами, латентностью и `finish_reason`) и
`health_check()` (лёгкая проверка доступности, никогда не кидает исключений).
ReAct-цикла и агента здесь нет — это «телефонная трубка» для раннера Фазы 3.

Поддерживаются два типа провайдеров:

| Тип                | Что это                                                                 |
| ------------------ | ----------------------------------------------------------------------- |
| `openai_compatible`| Любой API с контрактом `/v1/chat/completions`: OpenAI, OpenRouter, Together, Groq, ... — одна реализация, разные `base_url`/ключи |
| `ollama`           | Локальный Ollama через его OpenAI-совместимый endpoint; тонкая обёртка над `openai_compatible` |

### Настройка через providers.yaml

Скопируйте шаблон и поправьте под себя:

```bash
cp providers.example.yaml providers.yaml
```

Формат — список именованных провайдеров; путь к файлу задаётся настройкой
`AGENTALYZE_PROVIDERS_CONFIG_PATH` (по умолчанию `./providers.yaml`):

```yaml
providers:
  - name: gpt-4o-mini-via-openrouter   # уникальное человекочитаемое имя
    kind: openai_compatible            # или "ollama"
    base_url: https://openrouter.ai/api/v1
    api_key_env_var: OPENROUTER_API_KEY  # имя env-переменной, НЕ сам ключ!
    model_name: openai/gpt-4o-mini
    # timeout_seconds: 120              # опционально
    # retry:                            # опциональные override'ы retry
    #   max_attempts: 3
    #   initial_wait_seconds: 1.0
    #   multiplier: 2.0
    #   max_wait_seconds: 30.0

  - name: llama31-8b-local
    kind: ollama
    model_name: llama3.1:8b             # base_url по умолчанию http://localhost:11434/v1
```

**Про секреты.** В YAML хранится только *имя переменной окружения* с ключом
(`api_key_env_var`), сам ключ читается в рантайме. Поэтому оба файла безопасно
коммитятся: `providers.example.yaml` — шаблон в репозитории;
`providers.yaml`, созданный пользователем, секретов не содержит по схеме и
может быть закоммичен при желании (если вы всё же положите туда ключ напрямую,
добавьте его в `.gitignore` сами). Если нужная env-переменная не установлена,
`load_providers()` падает с понятной ошибкой вида
`Provider 'X' requires environment variable 'Y', which is not set`.

Загрузка:

```python
import asyncio
from pathlib import Path

from agentalyze.providers import ChatMessage, load_providers

providers = load_providers(Path("providers.yaml"))
result = asyncio.run(
    providers["gpt-4o-mini-via-openrouter"].chat_completion(
        [ChatMessage(role="user", content="ping")]
    )
)
print(result.message.content, result.total_tokens)
```

### Retry и ошибки

Ошибки провайдера образуют иерархию `ProviderError` и делятся на:

* **retryable** — `ProviderConnectionError`, `ProviderTimeoutError`,
  `ProviderRateLimitError`;
* **non-retryable** — `ProviderAuthError`, `ProviderInvalidResponseError`
  (например, невалидный JSON в tool call arguments), `ProviderConfigError`.

Каждый загруженный провайдер автоматически обёрнут в `RetryingProvider`
(на `tenacity`): до 3 попыток с экспоненциальным backoff (~1 c база, ×2,
потолок 30 c, джиттер), только для retryable-ошибок. Параметры настраиваются
per-provider через секцию `retry` в `providers.yaml`. Исключения SDK наружу
не утекают — вызывающий код работает только с подклассами `ProviderError`.

### Интеграционный тест с реальным Ollama

Тесты, требующие запущенного Ollama на `localhost:11434`, помечены маркером
`requires_ollama` и исключены из прогона по умолчанию:

```bash
pytest -m requires_ollama
```

## Конфигурация

Настройки читаются из переменных окружения с префиксом `AGENTALYZE_`
(см. `src/agentalyze/config.py`):

| Переменная                            | По умолчанию       | Описание                                    |
| ------------------------------------- | ------------------ | ------------------------------------------- |
| `AGENTALYZE_FIXTURES_DIR`             | `./fixtures`       | Директория с локальными HTML-фикстурами     |
| `AGENTALYZE_RESULTS_DIR`              | `./results`        | Куда складываются результаты прогонов       |
| `AGENTALYZE_LOG_LEVEL`                | `INFO`             | `DEBUG` / `INFO` / `WARNING` / `ERROR`      |
| `AGENTALYZE_PROVIDERS_CONFIG_PATH`    | `./providers.yaml` | YAML-конфиг именованных LLM-провайдеров (см. раздел «Providers») |

Также поддерживается опциональный файл `.env` в корне проекта.

## Структура

- `src/agentalyze/` — исходный код пакета
  - `src/agentalyze/tasks/` — реестр задач, модели, сервер фикстур, верификаторы
  - `src/agentalyze/providers/` — единый интерфейс LLM-провайдеров, factory, retry
  - `src/agentalyze/runner/` — ReAct-цикл, browser-инструменты, наблюдение страницы, формат трейса (`trace.py`), CLI (`cli.py`)
  - `src/agentalyze/analysis/` — failure-таксономия, агрегированные метрики, калибровка уверенности, цены/стоимость (Фаза 4)
  - `src/agentalyze/orchestration/` — прогон suite несколькими провайдерами, Markdown-отчёты сравнения, подкоманды CLI `compare`/`inspect` (Фаза 5)
  - `src/agentalyze/regression/` — diff двух прогонов (`diff.py`), baseline-указатель и загрузка прогонов (`storage.py`), подкоманды CLI `regression-check`/`set-baseline` (Фаза 6)
- `tests/` — тесты (`pytest`)
- `fixtures/` — локальные HTML-фикстуры для задач (по подпапкам-категориям)
- `providers.example.yaml` — шаблон конфигурации LLM-провайдеров
- `pricing.example.yaml` — шаблон таблицы цен для расчёта стоимости прогонов

## Лицензия

MIT — см. [`LICENSE.md`](LICENSE.md).
