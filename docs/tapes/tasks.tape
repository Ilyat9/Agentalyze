# Анимированные CLI-демки (Charm VHS)
#
# Источники GIF в docs/assets/*.gif. Регенерация:
#   scripts/gen_cli_demos.sh   (требуется `brew install vhs`)
#
# Демки показывают ТОЛЬКО реальный вывод: `tasks` выполняется по-настоящему
# (офлайн, без API-ключей); `run` — реплей финального summary настоящего
# прогона из results/ (см. README: «честность важнее демо»).
Output docs/assets/cli-tasks.gif
Set Shell bash
Set FontSize 16
Set LineHeight 1.3
Set Width 960
Set Height 880
Set Padding 24
Set Theme {"background":"#0d1117","foreground":"#e6edf3","cursor":"#e6edf3","black":"#484f58","red":"#ff7b72","green":"#7ee787","yellow":"#ffa657","blue":"#79c0ff","magenta":"#d2a8ff","cyan":"#a5d6ff","white":"#e6edf3","brightBlack":"#6e7681","brightRed":"#ffa198","brightGreen":"#aff5b4","brightYellow":"#ffa657","brightBlue":"#a5d6ff","brightMagenta":"#d2a8ff","brightCyan":"#a5d6ff","brightWhite":"#f0f6fc"}
Set WindowBar Colorful
Set BorderRadius 10
Set TypingSpeed 12ms

Type@400ms "source .venv/bin/activate && export PS1='$ '" Enter
Sleep 300ms
Type "agentalyze tasks" Enter
Sleep 2500ms
Type "agentalyze tasks --tag looping" Enter
Sleep 2200ms
