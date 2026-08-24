# Agentalyze

Eval harness for LLM agents working with tools and a real browser. Instead of
generic benchmarks, Agentalyze focuses on a task suite of concrete agentic web
tasks (form filling, fact extraction with confidence, tool-error recovery) and
pinpoints *where exactly* an agent breaks — not just the success rate.

> **Проект в разработке, реализуется поэтапно. Текущая фаза: 0 — базовый скелет.
> См. [`ROADMAP.md`](ROADMAP.md) для полного плана.**

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
pytest
```

Линтинг:

```bash
ruff check .
```

## Конфигурация

Настройки читаются из переменных окружения с префиксом `AGENTALYZE_`
(см. `src/agentalyze/config.py`):

| Переменная                | По умолчанию  | Описание                                    |
| ------------------------- | ------------- | ------------------------------------------- |
| `AGENTALYZE_FIXTURES_DIR` | `./fixtures`  | Директория с локальными HTML-фикстурами     |
| `AGENTALYZE_RESULTS_DIR`  | `./results`   | Куда складываются результаты прогонов       |
| `AGENTALYZE_LOG_LEVEL`    | `INFO`        | `DEBUG` / `INFO` / `WARNING` / `ERROR`      |

Также поддерживается опциональный файл `.env` в корне проекта.

## Структура

- `src/agentalyze/` — исходный код пакета
- `tests/` — тесты (`pytest`)
- `fixtures/` — локальные HTML-фикстуры для задач (заполняется в Фазе 1)

## Лицензия

MIT — см. [`LICENSE.md`](LICENSE.md).
