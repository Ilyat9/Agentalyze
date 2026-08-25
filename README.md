# Agentalyze

Eval harness for LLM agents working with tools and a real browser. Instead of
generic benchmarks, Agentalyze focuses on a task suite of concrete agentic web
tasks (form filling, fact extraction with confidence, tool-error recovery) and
pinpoints *where exactly* an agent breaks — not just the success rate.

> **Проект в разработке, реализуется поэтапно. Текущая фаза: 3 — раннер
> (ReAct-цикл, трейсы) завершена. См. [`ROADMAP.md`](ROADMAP.md) для полного плана.**

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
- `tests/` — тесты (`pytest`)
- `fixtures/` — локальные HTML-фикстуры для задач (по подпапкам-категориям)
- `providers.example.yaml` — шаблон конфигурации LLM-провайдеров

## Лицензия

MIT — см. [`LICENSE.md`](LICENSE.md).
