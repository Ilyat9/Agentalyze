# Contributing

Спасибо за интерес к Agentalyze! Самый ценный и самый простой вклад —
**новая задача в task-suite**: именно набор задач определяет, что харнес
умеет увидеть в поведении агента. Ниже — чек-лист, как добавить задачу так,
чтобы она прошла CI с первого раза.

## Как добавить новую задачу в task-suite (чек-лист)

### Шаг 0. Придумайте задачу под конкретный failure mode

Каждая задача в реестре отвечает на вопрос «что эта задача должна выявить об
агенте?» — это первое предложение комментария у каждой записи в
[`src/agentalyze/tasks/registry.py`](src/agentalyze/tasks/registry.py).
Хорошая задача целится в **один наблюдаемый режим отказа**, например:

* скрытый элемент, который появляется только после правильного действия
  (`nav-dropdown-menu-02`);
* выбор единственного настоящего элемента среди похожих приманок
  (`distractor-links-02`);
* настойчивость при повторяющихся преходящих сбоях (`err-flaky-widget-03`).

Если вы не можете сформулировать «Reveals: …» одним предложением — задача
пока не готова.

### Шаг 1. HTML-фикстура — `fixtures/<категория>/<имя>.html`

- [ ] Файл самодостаточен: никаких внешних скриптов, стилей, шрифтов, CDN.
- [ ] Есть однозначный маркер успеха (например, `#success-marker` или
      `data-opened="true"`), который выставляет **страница**, а не агент.
- [ ] Для extraction-задач: форма записи ответа использует стандартные
      селекторы `#recorded-answer` и `#recorded-confidence`.
- [ ] Фикстура проходит автоматическую проверку:
      тест [`tests/tasks/test_fixtures_valid.py`](tests/tasks/test_fixtures_valid.py)
      открывает каждую фикстуру в Chromium и прогоняет её reference-сценарий.

### Шаг 2. Reference-сценарий — `src/agentalyze/tasks/reference.py`

Добавьте запись `REFERENCE["<task-id>"] = _ref(...)` — программную
последовательность действий (`fill`/`click`/`select`/`check`), которая
приводит фикстуру к успеху без агента. Это то, что делает проверку фикстур
из шага 1 возможной; без записи в `reference.py` CI упадёт.

### Шаг 3. Запись в реестре — `src/agentalyze/tasks/registry.py`

```python
Task(
    id="my-category-name-04",        # kebab-case, категория + номер
    category=C.MY_CATEGORY,          # существующая или новая категория
    title="Human-readable title",
    description="Дословная инструкция агенту — она же промпт.",
    fixture_path="my_category/name_04.html",
    fixture_url_path="/my_category/name_04.html",
    verifier_id="verify-my-check",   # или MARKER для «дойти до финала»
    max_steps=10,
    timeout_seconds=120,
    difficulty="easy" | "medium" | "hard",
    tags=["..."],
    expected_failure_modes=[FailureTag.LOOPING],  # ОБЯЗАТЕЛЬНО, см. ниже
)  # Reveals: одно предложение о том, что задача выявляет.
```

Поле `expected_failure_modes: list[FailureTag]` — структурированный аналог
комментария «Reveals: …» (значения — из enum `FailureTag` в
[`src/agentalyze/analysis/failure_taxonomy.py`](src/agentalyze/analysis/failure_taxonomy.py)).
Оно питает индекс `agentalyze tasks --tag <FailureTag>`; **обязательное** для
новых задач с тех пор, как индекс по тегам появился — PR без него не проходит
проверку `tests/tasks/test_cli_tag_filter.py::test_every_registered_task_declares_failure_modes`.

После записи в реестр задача автоматически доступна всем командам CLI
(`run`, `compare`, `tasks`) — отдельной регистрации нигде больше не нужно.

### Шаг 4. Верификатор — `src/agentalyze/tasks/verifiers.py`

- [ ] Сначала попробуйте переиспользовать существующий из словаря
      `VERIFIERS`: маркер присутствия, сравнение числа/даты/текста ответа.
- [ ] Новый верификатор — класс с `async def verify(self, page: Page) ->
      VerificationResult`, добавленный одной записью в `VERIFIERS`
      (реестр вместо `if/elif` — новые верификаторы не трогают старый код).
- [ ] Верификатор смотрит **только на финальный DOM** и никогда на шаги
      агента; `reason` обязан объяснять вердикт — по нему потом читают
      отчёты.

### Шаг 5. Локальная проверка перед PR

```bash
pytest -m "not browser and not requires_ollama and not e2e_live"  # быстро
ruff check .
mypy src            # strict — обязателен
pytest -m browser   # если меняли фикстуры/верификаторы: реальный Chromium
```

- [ ] Все быстрые тесты зелёные, ruff и mypy strict чистые.
- [ ] Обновите счётчик задач в бейдже README, если он изменился
      (`30 tasks · N categories · N verifiers`).

## Другие виды вклада

* **Новый failure-tag таксономии** — см.
  [`src/agentalyze/analysis/failure_taxonomy.py`](src/agentalyze/analysis/failure_taxonomy.py):
  каждая эвристика обязана иметь документированное обоснование порога и юнит-тесты.
* **Новые метрики / раздел отчёта** — помните: слой отчёта только форматирует
  числа из слоя `analysis` и честно помечает малые выборки, ничего не пересчитывая.
* **Баг-репорты** — приложите `results/<run_id>/trace.json`: трейс самодостаточен
  и воспроизводит контекст полностью.

Предложение новой задачи удобно оформить issue'ем по шаблону
[`.github/ISSUE_TEMPLATE/new_task_suite_case.md`](.github/ISSUE_TEMPLATE/new_task_suite_case.md) —
но PR без issue тоже приветствуется.
