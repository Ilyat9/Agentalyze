#!/usr/bin/env bash
# =============================================================================
# compare_local_vs_cloud.sh — полный end-to-end сценарий Agentalyze:
# сравнение облачной модели (gpt-4o-mini через OpenRouter) с локальной
# Ollama-моделью (llama3.1:8b) на одной категории задач, затем демонстрация
# regression-режима на двух последовательных прогонах.
#
# Всё работает в Docker (см. docker-compose.yml в корне репозитория):
# локально ставить Python, Playwright или системные библиотеки Chromium
# не нужно. Нужен только Docker с compose-плагином и ключ OpenRouter.
#
# Использование:
#   export OPENROUTER_API_KEY=sk-or-...
#   ./examples/compare_local_vs_cloud.sh
#
# Что произойдёт, шаг за шагом:
#   0. Preflight: Docker, compose, ключ.
#   1. Сгенерируется providers.yaml под СОТОВУЮ сеть compose: Ollama доступна
#      агенту по http://ollama:11434/v1 (имя сервиса!), НЕ по localhost —
#      localhost внутри контейнера agentbench указывает на сам контейнер.
#   2. Поднимется сервис ollama (docker compose up -d ollama).
#   3. Скачается модель llama3.1:8b (один раз, дальше живёт в volume).
#   4. Прогон №1: agentbench compare на категории navigation (3 задачи,
#      оба провайдера) — быстрый и дешёвый срез всего suite.
#   5. Прогон №2: тот же compare повторно — вторая точка для сравнения.
#   6. Первый прогон помечается baseline'ом, второй сверяется с ним через
#      agentbench regression-check (exit 1 при регрессиях — так этот режим
#      встраивается в CI).
# =============================================================================

set -euo pipefail

BANNER_WIDTH=64

banner() {
    printf '\n%s\n%s\n%s\n' \
        "$(printf '%*s' "$BANNER_WIDTH" '' | tr ' ' '=')" \
        "== $*" \
        "$(printf '%*s' "$BANNER_WIDTH" '' | tr ' ' '=')"
}

MODEL="llama3.1:8b"
CLOUD_PROVIDER="gpt-4o-mini-via-openrouter"
LOCAL_PROVIDER="llama31-8b-local"
CATEGORY="navigation"

banner "Шаг 0/6 — Preflight"

command -v docker >/dev/null 2>&1 \
    || { echo "ОШИБКА: docker не найден в PATH."; exit 1; }
docker compose version >/dev/null 2>&1 \
    || { echo "ОШИБКА: docker compose plugin недоступен."; exit 1; }

if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
    echo "ОШИБКА: OPENROUTER_API_KEY не задан."
    echo "  export OPENROUTER_API_KEY=sk-or-...   # https://openrouter.ai/keys"
    exit 1
fi

cd "$(dirname "$0")/.."   # все пути ниже — от корня репозитория

banner "Шаг 1/6 — providers.yaml под сеть compose"

if [[ -f providers.yaml ]]; then
    BACKUP="providers.yaml.bak.$(date +%Y%m%d-%H%M%S)"
    mv providers.yaml "$BACKUP"
    echo "Существующий providers.yaml сохранён как $BACKUP"
fi

cat > providers.yaml <<EOF
providers:
  # Облачная модель: ключ берётся из окружения (пробрасывается compose'ом).
  - name: ${CLOUD_PROVIDER}
    kind: openai_compatible
    base_url: https://openrouter.ai/api/v1
    api_key_env_var: OPENROUTER_API_KEY
    model_name: openai/gpt-4o-mini

  # Локальная модель ВНУТРИ сети compose — адрес = имя сервиса ollama,
  # а НЕ localhost (localhost внутри контейнера agentbench — это он сам).
  - name: ${LOCAL_PROVIDER}
    kind: ollama
    base_url: http://ollama:11434/v1
    model_name: ${MODEL}
EOF
echo "providers.yaml записан."

banner "Шаг 2/6 — запуск сервиса Ollama"

docker compose up -d ollama
echo "Ждём готовности Ollama..."
for _ in $(seq 1 30); do
    if curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
        echo "Ollama отвечает."
        break
    fi
    sleep 2
done
curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1 \
    || { echo "ОШИБКА: Ollama не поднялась за 60 секунд."; exit 1; }

banner "Шаг 3/6 — загрузка модели ${MODEL}"

# Скачивание идёт один раз: модели персистентны в named volume ollama_data.
docker compose run --rm ollama ollama pull "${MODEL}"

banner "Шаг 4/6 — Прогон №1: compare, категория '${CATEGORY}'"

# Маленькое подмножество suite вместо всех 18 задач: минуты, а не часы,
# и центы, а не доллары. Полный suite — осознанное решение, а не дефолт.
docker compose run --rm agentbench compare \
    --providers "${CLOUD_PROVIDER},${LOCAL_PROVIDER}" \
    --category "${CATEGORY}"

banner "Шаг 5/6 — Прогон №2: тот же compare повторно"

docker compose run --rm agentbench compare \
    --providers "${CLOUD_PROVIDER},${LOCAL_PROVIDER}" \
    --category "${CATEGORY}"

banner "Шаг 6/6 — Regression-check: прогон №1 (baseline) vs прогон №2"

latest_suite_run() {
    # n-й по свежести каталог results/, содержащий suite_run.json
    local n="$1"
    for d in $(ls -t results); do
        [[ -f "results/$d/suite_run.json" ]] || continue
        n=$((n - 1))
        [[ "$n" -eq 0 ]] && { printf '%s' "$d"; return 0; }
    done
    return 1
}

BASELINE_RUN="$(latest_suite_run 2)" \
    || { echo "ОШИБКА: не нашёл два suite-прогона в results/."; exit 1; }
NEW_RUN="$(latest_suite_run 1)"

echo "baseline: $BASELINE_RUN"
echo "new:      $NEW_RUN"

docker compose run --rm agentbench set-baseline --suite-run "$BASELINE_RUN"

# Контракт кодов выхода: 0 — регрессий нет, 1 — есть регрессии (шаг падает —
# именно так режим встраивается в CI-гейт), 2 — проблема конфигурации.
set +e
docker compose run --rm agentbench regression-check \
    --baseline "$BASELINE_RUN" \
    --new "$NEW_RUN"
GATE_STATUS=$?
set -e

echo
echo "regression-check завершился с кодом $GATE_STATUS:"
case "$GATE_STATUS" in
    0) echo "  0 — регрессий нет (или только улучшения/нейтральные различия)." ;;
    1) echo "  1 — обнаружены регрессии; в CI такой шаг красит PR." ;;
    *) echo "  $GATE_STATUS — конфигурационная проблема, проверьте id прогонов." ;;
esac

cat <<EOF

Готово. Артефакты:
  results/${NEW_RUN}/report.md            — Markdown-отчёт сравнения моделей
  results/${NEW_RUN}/suite_run.json       — машинночитаемая сводка прогона
  results/${NEW_RUN}/regression_report.json — отчёт regression-check
  results/<run_id>/trace.json             — пошаговые трейсы каждой задачи

Дальше: откройте report.md, загляните в trace.json неудачных задач и
посмотрите README.md — разделы «Running evaluations» и «Regression checks».
EOF
