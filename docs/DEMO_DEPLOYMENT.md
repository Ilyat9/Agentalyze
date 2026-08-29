# DEMO_DEPLOYMENT — публичное BYOK-демо на бесплатном хостинге

Этот документ описывает разворачивание **публичного демо для портфолио**:
анонимный посетитель вставляет **свой собственный** API-ключ
(OpenRouter/OpenAI-совместимый) и запускает 1–3 короткие задачи в реальном
headless Chromium против локальных HTML-фикстур.

Это **не** то же самое, что `docs/DEPLOYMENT.md` (self-hosted продакшн с БД,
аутентификацией и K8s — там ключами управляет администратор через
`providers.yaml`). Здесь модель доверия другая: ключ приходит **per-request
от анонимного посетителя**, и весь дизайн построен вокруг этого.

---

## 1. Выбор хостинга (проверено в августе 2026)

| Хостинг | Бесплатный тир | RAM | Вердикт для Chromium |
|---|---|---|---|
| **Свой Mac/ПК + Cloudflare Tunnel** | да, бессрочно, без карты и аккаунта | память машины (для демо хватает 1–2 GB) | ✅ выбран (см. §7) |
| Hugging Face Spaces (Docker SDK) | ~~да~~ **НЕТ с 2025: Docker Spaces требуют PRO ($9/мес)**, free — только Static/Gradio | 16 GB (на PRO) | ❌ без подписки |
| Render | да (Web Services free) | **512 MB** | ❌ headless Chromium + Playwright в 512 MB не помещается (OOM на реальных прогонах) |
| Fly.io | бесплатного тира **больше нет** (pay-as-you-go, для новых org — только триал-кредит) | — | ❌ не «бесплатно» |
| Railway | бесплатного тира нет, только разовый кредит $5 | — | ❌ не «бесплатно» |
| Oracle Cloud Always Free | да (ARM 4 OCPU / 24 GB, навсегда) | 24 GB | ✅ отличная альтернатива, но регистрация требует верификации картой — из РФ недоступна без иностр. карты |

Изначально планировалась HF Spaces, но в 2025 HF закрыл бесплатный Docker:
API отвечает «hosting Docker Spaces on free cpu-basic requires a PRO
subscription». Из оставшегося бесплатно-навсегда без карты рабочий путь —
**туннель к собственной машине** (§7): сервер всё равно платит только за
хостинг (модели оплачивают посетители своими ключами).

## 2. Модель угроз и реализованные гарантии

Публичный анонимный сайт, куда люди сами вставляют секреты. Реализовано и
покрыто тестами (`tests/demo/`):

1. **HTTPS-only**. `POST /demo/run` отклоняет (403) запросы, пришедшие не по
   HTTPS (`X-Forwarded-Proto` от TLS-терминатора платформы; localhost —
   исключение для локальной разработки). Дополнительно сервер **отказывается
   отправлять ключ на не-HTTPS `base_url`** провайдера.
2. **Ключ никогда не логируется.** Функция редактирования
   (`agentalyze.demo.redaction`) применяется в САМЫЙ РАННИЙ момент — ключ
   регистрируется в редакторе сразу после чтения raw body. Глобальный
   structlog-процессор маскирует зарегистрированные секреты в **каждой**
   строке логов, включая полностью отрендеренные трейсбеки необработанных
   исключений. Тест `test_demo_key_masking.py` скармливает конвейеру
   исключение, в сообщение которого намеренно вставлен ключ, и проверяет, что
   в логах есть `[REDACTED]` и нет сырого ключа. Тело провайдерской ошибки
   никогда не цитируется в ответе — посетитель получает только классификацию
   (`test_demo_hardening.py::test_provider_error_never_echoes_the_key`).
3. **Ключ не сохраняется нигде.** Никаких БД/диска/`results/`. Артефакты
   прогона (trace.json, скриншоты — они ключа не содержат) пишутся в
   персональный temp-каталог запроса и удаляются при выходе из обработчика.
   Ключ «забывается» в `finally` (`unregister_secret`) сразу после ответа.
4. **Rate-limit по IP** — `slowapi` (тот же лимитер, что и у `POST /runs`),
   по умолчанию `3 per hour` (`AGENTALYZE_DEMO_RATE_LIMIT`), ответ 429.
5. **Жёсткое ограничение стоимости прогона** — только allowlist из 3
   `easy`-задач (≤ 8 шагов), таймаут прогона 90 c, таймаут одного обращения к
   провайдеру 20 c, ретраев нет (демо должно падать быстро, а не жечь бюджет
   посетителя), параллельно максимум 1 прогон (семофор).
6. **Явное согласие и честное объяснение** — видимый блок над формой:
   ключ обрабатывается **на сервере** (не «всё в браузере»), только для
   одного запроса, не сохраняется/не логируется; чекбокс согласия обязателен.
7. **SSRF-гард** (найден adversarial-пробой живого демо): в публичном режиме
   ключ не отправляется на приватные/loopback/link-local адреса даже по
   HTTPS — проверяются и буквальные IP, и результаты DNS-резолва (диапазон
   198.18.0.0/15 исключён: это fake-IP DNS локальных VPN-клиентов). В dev-
   режиме (`demo_https_required=false`) localhost разрешён для локальных
   серверов моделей.
8. **Лимит тела запроса** — больше 64 KB отклоняется кодом 413 ДО чтения
   тела в память: анонимный посетитель не может исчерпать память маленького
   хостинга горой POST-запросов.

Прочее: тело запроса парсится **вручную из raw body** (не через pydantic
request-валидацию FastAPI — её 422-ответы эхо-ят `input`, т.е. могли бы
вернуть ключ в теле ответа); Ollama **не предлагается** — у анонимного
посетителя почти никогда нет публично доступного Ollama-эндпоинта, поэтому
демо принимает только облачные OpenAI-совместимые провайдеры.

## 3. Что деплоится

`deploy/hf-space/` содержит:

- `README.md` — front matter Space (`sdk: docker`, `app_port: 7860`);
- `Dockerfile` — тот же runtime, что и корневой `Dockerfile` проекта, но с
  `CMD ["serve", "--host", "0.0.0.0", "--port", "7860", "--demo-mode"]`
  (корневой образ обязан оставаться CLI-инструментом с `--help`; Spaces не
  умеют переопределять команду). Содержимое синхронизируется вручную — при
  изменении корневого Dockerfile поправьте и этот файл.

Переменные окружения демо (все уже выставлены в `deploy/hf-space/Dockerfile`):

| Переменная | Значение | Смысл |
|---|---|---|
| `AGENTALYZE_DEMO_MODE_ENABLED` | `1` | монтирует демо-роутер (`agentalyze serve --demo-mode` — эквивалент) |
| `AGENTALYZE_DEMO_RATE_LIMIT` | `3 per hour` | лимит прогонов с одного IP |
| `AGENTALYZE_DEMO_RUN_TIMEOUT_SECONDS` | `90` | жёсткий бюджет прогона |
| `AGENTALYZE_DEMO_PROVIDER_TIMEOUT_SECONDS` | `20` | таймаут одного запроса к провайдеру |
| `AGENTALYZE_DEMO_MAX_CONCURRENT_RUNS` | `1` | сколько Chromium одновременно |
| `AGENTALYZE_CHROMIUM_LAUNCH_ARGS` | `--disable-dev-shm-usage,--disable-gpu` | снижение потребления памяти Chromium |
| `AGENTALYZE_DATABASE_URL` | `sqlite+aiosqlite:////tmp/…` | /tmp — единственное место, доступное на запись на free-тарифе |
| `AGENTALYZE_RESULTS_DIR` | `/tmp/results` | туда обычные прогоны не пишутся (демо вообще пишет только в temp-каталог запроса) |

Health-check: переиспользуются существующие `/livez` и `/readyz` — HF Spaces
проверяет сам порт; дополнительно можно мониторить `GET /livez` вручную.

## 4. Пошаговый деплой на HF Spaces (воспроизводимая инструкция)

> Выполняется один раз руками (в этой среде нет доступа к аккаунту HF),
> занимает ~10 минут, всё бесплатно.

1. Создайте Space: https://huggingface.co/new-space → имя, например
   `agentalyze-demo` → **SDK: Docker** (blank template) → **Public** →
   CPU Basic (free).
2. Загрузите репозиторий проекта в Space так, чтобы в корне Space оказались:
   `src/`, `fixtures/`, `migrations/`, `alembic.ini`, `pyproject.toml`,
   `README.md`, `LICENSE.md`, `providers.example.yaml`,
   `pricing.example.yaml` — плюс `deploy/hf-space/Dockerfile` **переименованный
   в `Dockerfile`** (это единственная замена) и `deploy/hf-space/README.md`
   вместо корневого README (Space читает front matter из корневого README).

   Через git (подставьте свой неймспейс):

   ```bash
   git clone https://huggingface.co/spaces/<user>/agentalyze-demo
   cd agentalyze-demo
   # скопировать проект БЕЗ локального мусора
   git -C /path/to/agentalyze archive HEAD | tar -x -C .
   # подменить Space-специфичные файлы
   cp /path/to/agentalyze/deploy/hf-space/Dockerfile ./Dockerfile
   cp /path/to/agentalyze/deploy/hf-space/README.md ./README.md
   git add -A && git commit -m "deploy: public BYOK demo" && git push
   ```
3. Space соберёт образ (~5–10 минут: тяжёлый слой зависимостей кэшируется) и
   поднимет `agentalyze serve --port 7860 --demo-mode`.
4. Проверка: откройте `https://<user>-agentalyze-demo.hf.space/demo` —
   должна отрисоваться демо-страница; `GET /demo/tasks` — JSON allowlist;
   `GET /livez` — `{"status": "alive"}`.
5. Сквозная проверка руками: вставьте реальный ключ (с балансовой защитой —
   задачи стоят копейки), запустите задачу «Follow the documentation link»,
   убедитесь, что приходит честная сводка (исход, шаги, токены, время).

Обновление демо = повторный `git push` в Space-репозиторий (пересборка).

## 5. Известные ограничения

- **Холодный старт.** После ~48 ч неактивности Space засыпает; первый запрос
  поднимает контейнер десятки секунд (до минуты). Демо-страница предупреждает
  об этом текстом над формой.
- **1 одновременный прогон.** Второй посетитель получит честный 503 «demo is
  busy» — это защита памяти/CPU free-тарифа, а не бага.
- **`X-Forwarded-Proto` доверенный.** HTTPS-граница определяется заголовком,
  который выставляет ingress платформы; прямого обхода через платформенный
  ingress нет (HTTP редиректится на HTTPS самой платформой).
- **Через git push в HF может понадобиться LFS** для бинарников; файлы проекта
  текстовые, кроме PNG-скриншотов в `results/` (они в демо-образ не входят,
  `.gitignore`/`archive HEAD` их исключают).
- Корневой `Dockerfile` и `deploy/hf-space/Dockerfile` дублируют слои сборки —
  при изменении одного не забудьте второй (помечено в шапке обоих файлов).

## 6. Чек-лист проверок перед выкладыванием

- [x] `pytest tests/demo/` — 20 тестов: демо выключено по умолчанию, allowlist,
      rate-limit 429 без вызова раннера, таймаут, маскирование ключа в логах/ответах
      (включая трейсбек), HTTPS-граница, эфемерность артефактов.
- [x] `ruff check .` и `mypy src` — чисто.
- [x] `docker build` + smoke: `/livez`, `/demo`, `/demo/tasks` отвечают.
- [x] Ручной e2e с реальным ключом OpenRouter (`openai/gpt-4o-mini`, задача
      `nav-simple-link-01`: success за 2 шага, ~2.3k токенов ≈ $0.0004).

## 7. Деплой без карты и без аккаунта: свой Mac/ПК + Cloudflare Tunnel

Рабочий путь «бесплатно навсегда», проверенный в том числе из РФ
(западные облака требуют иностр. карту или блокируют регистрацию):

1. `brew install cloudflared` (или бинарник с
   https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).
2. Поднять демо: `agentalyze serve --host 127.0.0.1 --port 8903 --demo-mode`.
3. Быстрый туннель (без аккаунта): `cloudflared tunnel --url http://localhost:8903`
   → выдаёт URL вида `https://<random>.trycloudflare.com`, HTTPS включён.
   cloudflared проксирует с заголовком `X-Forwarded-Proto: https`, поэтому
   HTTPS-гард демо пропускает запросы, а HTTP-вариант URL отклоняется.

Особенности и их решение:

- **HTTPS из коробки** — TLS терминируется на границе Cloudflare, наш
  `_require_https` видит `X-Forwarded-Proto: https`.
- **Rate-limit за прокси.** Все запросы приходят к FastAPI с
  `127.0.0.1` (адрес cloudflared), что схлопнуло бы всех посетителей в один
  бакет «3 в час на всех». `_client_key` поэтому доверяет заголовку
  `CF-Connecting-IP`, но ТОЛЬКО с loopback-сокета (спуфинг с удалённого
  адреса не даёт бакетов). Покрыто тестом
  `test_behind_local_proxy_visitors_get_separate_ip_buckets`.
- **Ограничения quick-туннеля**: URL случайный и меняется при перезапуске;
  демо живёт, пока запущены туннель и Mac. Для демонстраций/скринкастов
  этого достаточно.
- **Постоянная ссылка (опционально)**: бесплатный CF-аккаунт (только email,
  без карты) + собственный домен (в РФ регистрируется на российскую карту,
  ~200–800 ₽/год) → Cloudflare (free plan) → named tunnel
  (`cloudflared tunnel login`, `cloudflared tunnel create agentalyze-demo`,
  маршрут в config.yml, запуск как launchd-сервис `brew services start
  cloudflared`). Получается постоянный HTTPS-URL на своём домене.
- Mac должен быть включён; останавливается всё простым Ctrl+C обоих
  процессов (сервера и туннеля).

§4 (HF Spaces) остаётся в документе как опция для тех, у кого есть PRO или
иностр. карта: файлы `deploy/hf-space/` готовы к загрузке без изменений.

## 8. Разнесённая схема «0 ₽ / без карты / always-on»: лёгкий оркестратор + Browser-infra

Ещё один способ разбить «всё на один сервер»: браузер выносится в отдельный
сервис (Browserless.io — free-план без карты: 1000 units ≈ 8 часов браузера
в месяц, 2 параллельных браузера, Playwright по CDP; альтернатива — Steel.dev,
Launch-план $0 + $30 кредитов one-time). Тогда:

- **оркестратор** (FastAPI-демо без Chromium) влезает в 512 MB бесплатного
  тира (Render Free — без карты);
- **Chromium** работает в облаке browser-infra провайдера по CDP.

Требуемые настройки (уже реализованы в коде):

| Переменная | Смысл |
|---|---|
| `AGENTALYZE_BROWSER_CDP_ENDPOINT` | ws/wss CDP-эндпоинт провайдера (например `wss://production-sfo.browserless.io/chrome?token=...`). Задан → раннер делает `connect_over_cdp` вместо локального launch |
| `AGENTALYZE_DEMO_FIXTURE_BASE_URL` | **обязателен в этом режиме**: корневой URL раздачи фикстур `https://<host>` — удалённый браузер не видит 127.0.0.1 оркестратора. Фикстуры — не секретные тестовые страницы; они раздаются **с корня хоста** (`GET /{fixture_path}` — как локальный FixtureServer, т.к. ссылки в фикстурах абсолютные), роут-catch-all регистрируется последним, API-эндпоинты всегда приоритетны; path traversal отвергается |

Ключ посетителя по-прежнему живёт ТОЛЬКО в оркестраторе и уходит в API
провайдера; браузер получает лишь страницы задач — security-модель не
меняется. Покрыто тестами: `tests/demo/test_demo_remote_browser.py`
(контракты конфигурации) и `tests/runner/test_cdp.py` (реальный Chromium
через CDP с `fixture_base_url`).

### 8.1. Фактический прод-деплой (выполнен, август 2026)

Схема из §8 развёрнута и проверена на живом URL:

- **Стек**: Render Free (Frankfurt, Docker, `Dockerfile.demo` — slim-оркестратор
  ~562 MB без локального Chromium) + Browserless Free (Chromium по CDP, SFO).
- **Live URL**: `https://agentalyze-demo.onrender.com/demo`
- **Сервисная конфигурация Render**: runtime Docker, Dockerfile Path =
  `./Dockerfile.demo`, Docker Command **пуст** (старт вшит в образ:
  `AGENTALYZE_SERVE_ON_START=1`, порт из `PORT`), Health Check = `/livez`,
  Blueprint-файл `render.yaml` в корне репо. 5 переменных окружения —
  см. §8 + `AGENTALYZE_DATABASE_URL`/`AGENTALYZE_RESULTS_DIR` (в /tmp —
  эфемерно, на free-тире диска нет).

**Важный урок деплоя**: самодостаточный образ с встроенным Chromium
(4.12 GB, ./Dockerfile) на Render Free не стартует вообще — мгновенный
«Exited with status 128» с нулевым выводом процесса, воспроизводимо и с
очисткой кэша. Slim-образ без Chromium (~562 MB) стартует нормально.
Поэтому для бесплатного PaaS деплоить ТОЛЬКО `Dockerfile.demo`.

Прожитые проверки публичного URL (все выполнены по HTTPS снаружи):
`/livez` 200, `/readyz` 200, `/demo` 200 (RU-страница), `/demo/tasks` 200
(allowlist 3 задачи), фикстуры с корня `/navigation/simple_link_01.html` 200,
неизвестный путь 404, path traversal 404, `POST /runs`/`GET /runs` без Bearer
→ 401, `/demo/run` без ключа → 400, неизвестный task_id → 400 с allowlist,
plain HTTP → 301/307 на HTTPS; e2e-цепочка Render→Browserless→OpenRouter
прошла (с невалидным ключом — честный `failure_provider_error`, ключ в
ответе/логах отсутствует).
