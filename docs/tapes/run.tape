# Анимированное демо: запуск одной задачи.
#
# Честность важнее демо: реальный `run` требует живую модель и Chromium,
# поэтому tape реплеит финальный summary НАСТОЯЩЕГО прогона (локальная
# Ollama, qwen25-7b-local, trace results/a479c38a-ab1e-4855-84fd-4ab32b1e87e2/)
# внутри quoted-heredoc — текст внутри heredoc shell не исполняет.
#
# Первый кадр GIF — чистый промпт, последний — итоговый summary с длинной
# паузой (это и превью, и точка лупа). Никаких промежуточных кадров-«вспышек».
# Регенерация: scripts/gen_cli_demos.sh (требуется `brew install vhs`).
Output docs/assets/cli-run.gif
Set Shell bash
Set FontSize 16
Set LineHeight 1.3
Set Width 900
Set Height 560
Set Padding 24
Set Theme {"background":"#0d1117","foreground":"#e6edf3","cursor":"#e6edf3","black":"#484f58","red":"#ff7b72","green":"#7ee787","yellow":"#ffa657","blue":"#79c0ff","magenta":"#d2a8ff","cyan":"#a5d6ff","white":"#e6edf3","brightBlack":"#6e7681","brightRed":"#ffa198","brightGreen":"#aff5b4","brightYellow":"#ffa657","brightBlue":"#a5d6ff","brightMagenta":"#d2a8ff","brightCyan":"#a5d6ff","brightWhite":"#f0f6fc"}
Set WindowBar Colorful
Set BorderRadius 10
Set TypingSpeed 12ms

Sleep 500ms
Type "source .venv/bin/activate && export PS1='$ '" Enter
Sleep 300ms
Type "cat <<'EOF'  # summary, реплеенный из реального прогона (trace a479c38a)" Enter
Sleep 300ms
Type "agentalyze run --task nav-simple-link-01 --provider qwen25-7b-local" Enter
Sleep 1800ms
Type "==============================================================" Enter
Sleep 350ms
Type "Task:       nav-simple-link-01 (Follow the documentation link)" Enter
Sleep 300ms
Type "Provider:   qwen25-7b-local" Enter
Sleep 300ms
Type "Outcome:    success" Enter
Sleep 300ms
Type "Steps:      3" Enter
Sleep 300ms
Type "Tokens:     prompt=4352 completion=63 cost=N/A" Enter
Sleep 300ms
Type "Verifier:   Fixture reached its terminal state (#success-marker present and visible)." Enter
Sleep 300ms
Type "Wall time:  36.9s" Enter
Sleep 300ms
Type "Trace:      a479c38a-ab1e-4855-84fd-4ab32b1e87e2/trace.json" Enter
Sleep 300ms
Type "==============================================================" Enter
Sleep 300ms
Type "EOF" Enter
Sleep 3500ms
