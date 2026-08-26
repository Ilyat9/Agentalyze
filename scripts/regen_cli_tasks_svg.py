"""Regenerate docs/assets/cli-tasks.svg from the current task registry.

Throwaway maintenance script: keeps the README screenshot in sync with the
registry without manual pixel-pushing. Run from the repo root:

    python scripts/regen_cli_tasks_svg.py
"""

from __future__ import annotations

import html
from pathlib import Path

from agentalyze.tasks.registry import TASKS

W = 960
LINE_H = 21
TOP_Y = 66
FIRST_LINE_Y = 87


def main() -> None:
    lines = [f"{len(TASKS)} registered tasks:"]
    for t in TASKS:
        lines.append(
            f"  {t.id:<30} {t.category.value:<15} {t.difficulty:<6} {t.title}"
        )

    height = FIRST_LINE_Y + LINE_H * len(lines) + 24

    texts = [
        (
            f'<text x="24" y="{TOP_Y}" font-family="Menlo,Consolas,monospace" '
            f'font-size="13"><tspan fill="#7ee787" font-weight="bold">$</tspan>'
            f'<tspan fill="#e6edf3"> agentalyze tasks</tspan></text>'
        )
    ]
    for i, line in enumerate(lines):
        y = FIRST_LINE_Y + LINE_H * i
        bold = ' font-weight="bold"' if i == 0 else ""
        texts.append(
            f'<text x="24" y="{y}" font-family="Menlo,Consolas,monospace" '
            f'font-size="13" fill="#79c0ff"{bold}>{html.escape(line)}</text>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" '
        f'viewBox="0 0 {W} {height}" role="img" '
        f'aria-label="agentalyze CLI: список зарегистрированных задач">\n'
        f'  <rect width="{W}" height="{height}" rx="10" fill="#0d1117"/>\n'
        f'  <rect width="{W}" height="38" rx="10" fill="#161b22"/>\n'
        f'  <rect y="28" width="{W}" height="10" fill="#161b22"/>\n'
        f'  <circle cx="20" cy="19" r="6" fill="#ff5f56"/>\n'
        f'  <circle cx="40" cy="19" r="6" fill="#ffbd2e"/>\n'
        f'  <circle cx="60" cy="19" r="6" fill="#27c93f"/>\n'
        f'  <text x="470" y="23" text-anchor="middle" '
        f'font-family="-apple-system,Segoe UI,sans-serif" font-size="12" '
        f'fill="#8b949e">terminal — agentalyze tasks</text>\n'
        f'  ' + "".join(texts) + "\n</svg>\n"
    )
    out = Path("docs/assets/cli-tasks.svg")
    out.write_text(svg, encoding="utf-8")
    print(f"written {out}: {len(lines)} lines, {W}x{height}")


if __name__ == "__main__":
    main()
