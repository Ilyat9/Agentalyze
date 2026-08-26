# =============================================================================
# Kubernetes-манифесты продакшн-развёртывания Agentalyze API.
#
# Архитектурное решение (документировано в docs/DEPLOYMENT.md): очередь задач
# НЕ вынесена в отдельный сервис — прогоны исполняются in-process за глобальным
# семафором (AGENTALYZE_MAX_ACTIVE_SUITE_RUNS), поэтому отдельный Deployment
# воркеров не нужен; масштабирование по репликам ограничено бюджетом Chromium.
#
# Применение:
#   kubectl apply -f deploy/k8s/namespace.yaml
#   kubectl create secret generic agentalyze-secrets -n agentalyze \
#       --from-literal=OPENROUTER_API_KEY=sk-or-... \
#       --from-literal=POSTGRES_PASSWORD=...
#   kubectl apply -f deploy/k8s/
#
# TLS на Ingress: раскомментируйте секцию tls и создайте cert-manager
# Certificate или собственный Secret agentalyze-tls.
# =============================================================================
