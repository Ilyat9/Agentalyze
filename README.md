# Agentalyze

Eval harness for LLM agents working with tools and a real browser. Instead of
generic benchmarks, Agentalyze focuses on a task suite of concrete agentic web
tasks (form filling, fact extraction with confidence, tool-error recovery) and
pinpoints *where exactly* an agent breaks — not just the success rate.

> **Проект в разработке, реализуется поэтапно. Текущая фаза: 2 — provider
> layer завершена. См. [`ROADMAP.md`](ROADMAP.md) для полного плана.**

## Требования

- Python **3.11+** (минимальная зафиксированная версия — см. `pyproject.toml`)

## Установка

```bash
pip install -e ".[dev]"
```

Опциональная группа `browser` ставит Playwright, но **не** браузерные бинарники —
они устанавливаются отдельной командой (появится в Фазе 3):

```bash
pip install -e ".[browser]"
playwright install chromium
```

## Запуск тестов

```bash
pytest                # быстрый прогон: без тестов, требующих Chromium или Ollama
pytest -m browser     # интеграционные тесты фикстур и верификаторов (нужен Chromium)
pytest -m requires_ollama  # интеграционный тест провайдера с реальным Ollama
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
- `tests/` — тесты (`pytest`)
- `fixtures/` — локальные HTML-фикстуры для задач (по подпапкам-категориям)
- `providers.example.yaml` — шаблон конфигурации LLM-провайдеров

## Лицензия

MIT — см. [`LICENSE.md`](LICENSE.md).
