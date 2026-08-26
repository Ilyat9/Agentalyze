# Production Deployment Guide

Полный путь от нуля до работающего в проде HTTP-сервиса Agentalyze. Локальный
CLI-сценарий (`docker-compose.yml` + Ollama, файловое хранилище) остаётся
без изменений и описан в README — этот гайд только про **сервисный режим**,
где к API обращаются несколько людей/автоматизаций.

## 0. Два режима использования — выберите осознанно

| | CLI-режим | Сервисный режим |
|---|---|---|
| Кто использует | один человек / CI | команда, автоматизации |
| Хранилище | JSON-файлы в `results/` | PostgreSQL (метаданные) + диск (трейсы) |
| Запуск | `agentalyze compare ...` | `agentalyze serve`, HTTP API |
| Auth | не нужен | Bearer API-ключи (хэшированные) |
| Docker | `docker compose up` | `docker-compose.service.yml` / Kubernetes |

Оба режима работают на одном образе и одной кодовой базе; CLI-возможности
сознательно сохранены.

## 1. Требования

- Docker (для k8s: кластер 1.28+, ingress-nginx, cert-manager по желанию)
- Домен с DNS на ingress для TLS (или свой сертификат)

## 2. Локальный сервисный стенд (docker compose)

```bash
cp providers.example.yaml providers.yaml   # настройте своих провайдеров
export OPENROUTER_API_KEY=sk-or-...        # секреты — только окружение/Vault

docker compose -f docker-compose.service.yml up -d --build

# Один раз: создать первый API-ключ (показывается ровно один раз):
docker compose -f docker-compose.service.yml run --rm agentalyze-api \
    agentalyze create-api-key --name first-client

curl -s http://localhost:8000/health | jq          # глубокая проверка
curl -s http://localhost:8000/runs \
    -H "Authorization: Bearer agt-..." | jq
```

## 3. Kubernetes

```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl create secret generic agentalyze-secrets -n agentalyze \
    --from-literal=OPENROUTER_API_KEY=... \
    --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 24)"
kubectl apply -f deploy/k8s/
kubectl -n agentalyze rollout status deploy/agentalyze-api

# Первый ключ:
kubectl -n agentalyze exec deploy/agentalyze-api -- \
    agentalyze create-api-key --name ci-bot
```

Состав манифестов:

- `deployment.yaml` — API-сервер. Отдельный воркер-деплоймент НЕ нужен:
  очередь задач выбрана in-process (см. «Решения» ниже); масштабирование по
  репликам ограничено бюджетом Chromium.
- `postgres.yaml` — StatefulSet БД; в проде предпочтителен managed-Postgres
  (RDS/Cloud SQL), тогда просто удалите файл и укажите DSN в секрете.
- `pvc.yaml` — том под трейсы/скриншоты (полные артефакты остаются на ФС).
- `ingress.yaml` — TLS-терминация (cert-manager).

### Zero-downtime и probes

- `readinessProbe → GET /readyz` — трафик не попадает на под без подключения
  к БД (и после накатки миграций).
- `livenessProbe → GET /livez` — процесс жив (лёгкая проверка, без сети).
- `startupProbe` — до 2 минут на первый старт с миграциями.
- `maxUnavailable: 0` в RollingUpdate — обрыва обслуживания нет.
- `GET /health` — ГЛУБОКАЯ проверка (БД + хотя бы один провайдер) для
  внешнего мониторинга, НЕ для probes: провайдерная недоступность не должна
  выкидывать под из ротации.

### Ресурсы

Каждая активная комбинация держит реальный Chromium: лимит памяти пода
рассчитан на `AGENTALYZE_MAX_ACTIVE_SUITE_RUNS × ~1GiB` браузеров + Python.
Значения в deployment.yaml — стартовые (requests 500m/1Gi, limits 2 CPU/3Gi),
откалибруйте по своему набору задач.

## 4. Миграция существующих результатов

Реестр прогонов переносится в БД идемпотентным скриптом (трейсы остаются на
диске, никуда не двигаются):

```bash
AGENTALYZE_DATABASE_URL=postgresql+asyncpg://... \
    python scripts/migrate_results_to_db.py --results-dir ./results --dry-run
# затем без --dry-run
```

Скрипт безопасно перезапускать: уже перенесённые suite_run_id пропускаются.

## 5. Конфигурация (env, префикс AGENTALYZE_)

| Переменная | Дефолт | Назначение |
|---|---|---|
| `DATABASE_URL` | sqlite+aiosqlite:///./agentalyze.db | метаданные; в проде — postgresql+asyncpg |
| `API_AUTH_REQUIRED` | true | Bearer-аутентификация POST /runs |
| `API_RUNS_RATE_LIMIT` | 5/minute | лимит запусков на ключ (slowapi); `none` отключает |
| `MAX_ACTIVE_SUITE_RUNS` | 2 | глобальный семафор одновременных прогонов |
| `LOG_FORMAT` | console | `json` — структурные логи для Loki/ELK |
| `VAULT_ADDR` | (пусто) | включить Vault KV-v2 как источник секретов |

## 6. Секреты

Дефолт — переменные окружения (как раньше). Опционально Vault:

```yaml
# providers.yaml — без изменений
api_key_env_var: OPENROUTER_API_KEY
```
```bash
vault kv put secret/OPENROUTER_API_KEY value=sk-or-...
export AGENTALYZE_VAULT_ADDR=https://vault.internal
export VAULT_TOKEN=...
```
Разрешение: Vault → env-fallback (недоступный Vault деградирует к прежнему
поведению, а не валит сервис). Ключи провайдеров никогда не возвращаются
через API ни в каком виде.

## 7. Наблюдаемость

- `/metrics` — Prometheus-формат: `agentalyze_suite_runs_total`,
  `agentalyze_suite_runs_active`, `agentalyze_provider_calls_total`
  (по типам ошибок ProviderError-иерархии),
  `agentalyze_provider_call_seconds`, `agentalyze_regressions_total`.
- Логи: `AGENTALYZE_LOG_FORMAT=json` — одна JSON-строка на запись (structlog,
  совместим с любой системой агрегации).
- Алерты (рекомендация, настраивается снаружи): `/health` 5xx подряд,
  рост `suite_runs_total{status="failed"}`, исчерпание
  `MAX_ACTIVE_SUITE_RUNS`.

## 8. Нагрузочное тестирование перед релизом

```bash
pip install -e ".[loadtest]"
AGT_API_KEY=agt-... AGT_PROVIDER=<имя> \
locust -f loadtest/locustfile.py --host https://agentalyze.example.com \
    --users 20 --spawn-rate 2 --run-time 3m
```

Ожидания (критерии прохождения): GET-эндпоинты p95 < 100 мс при 20
одновременных пользователях; POST /runs отдаёт 202 в пределах rate-limit и
429 сверх него; ошибок 5xx нет. Rate-limit считается НА ключ, поэтому
несколько легитимных клиентов не блокируют друг друга.

## 9. Решения и их обоснование

1. **In-process фоновые задачи вместо celery/arq.** Целевая нагрузка —
   единицы параллельных прогонов, упирающихся в глобальный семафор Chromium;
   брокер добавил бы stateful-сервис, не изменив ни одного реального предела.
   Статусы прогонов персистятся в SQL, поэтому переживают рестарт чтением.
   Известное ограничение: прогон исполняется в той реплике, что приняла
   запрос, и теряется при её смерти (см. docs/SECURITY_REVIEW.md, риски).
   Путь масштабирования — замена `SuiteRunManager` на брокер; изоляция —
   внутри одного модуля.
2. **SQLite-дефолт / Postgres для прода.** SQLite (WAL) честно хорош для
   одного процесса self-hosted; конкурентная многопроцессная запись — только
   Postgres. Код одинаков, отличается лишь URL.
3. **HTTP-статус regression-check = 200 при валидной проверке**, исход гейта
   в теле (`regressed`). Exit-code CI — соглашение процесса, не семантика
   HTTP; 5xx зарезервированы за инфраструктурными сбоями.
4. **Минималистичная аутентификация** (хэшированные статические ключи) вместо
   OAuth/JWT: доверенная команда с ручной раздачей ключей; ротация =
   revoke+create. Обоснование рисков — docs/SECURITY_REVIEW.md.




