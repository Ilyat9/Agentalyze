# Agentalyze

Eval harness for LLM agents working with tools and a real browser. Instead of
generic benchmarks, Agentalyze focuses on a task suite of concrete agentic web
tasks (form filling, fact extraction with confidence, tool-error recovery) and
pinpoints *where exactly* an agent breaks — not just the success rate.

> **Проект в разработке, реализуется поэтапно. Текущая фаза: 1 — task-suite
> завершена. См. [`ROADMAP.md`](ROADMAP.md) для полного плана.**

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
pytest                # быстрый прогон: без тестов, требующих Chromium
pytest -m browser     # интеграционные тесты фикстур и верификаторов (нужен Chromium)
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
  - `src/agentalyze/tasks/` — реестр задач, модели, сервер фикстур, верификаторы
- `tests/` — тесты (`pytest`)
- `fixtures/` — локальные HTML-фикстуры для задач (по подпапкам-категориям)

## Лицензия

MIT — см. [`LICENSE.md`](LICENSE.md).
