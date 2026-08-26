"""One-off measurement: is a results-index needed? (see report, task 4)."""

import json
import time
from pathlib import Path

results = Path("results")
dirs = [d for d in results.iterdir() if d.is_dir()]
sizes = [
    (d / "suite_run.json").stat().st_size
    for d in dirs
    if (d / "suite_run.json").exists()
]
print(f"suite runs on disk: {len(dirs)}; suite_run.json total: {sum(sizes)/1024:.0f} KiB")

from agentalyze.orchestration.suite_runner import load_suite_run

t0 = time.perf_counter()
traces = 0
for d in dirs:
    try:
        traces += len(load_suite_run(results, d.name).traces)
    except FileNotFoundError:
        pass
dt = time.perf_counter() - t0
print(f"WORST CASE full scan+parse of ALL {len(dirs)} runs: {dt*1000:.1f} ms "
      f"({traces} traces)")

t0 = time.perf_counter()
for _ in range(100):
    journal = results / "baseline_journal.jsonl"
    if journal.exists():
        journal.read_text(encoding="utf-8")
print(f"100x journal read (what --baseline auto does): "
      f"{(time.perf_counter()-t0)*1000:.2f} ms total")

t0 = time.perf_counter()
loaded = 0
for d in dirs[:10]:
    p = d / "suite_run.json"
    if p.exists():
        json.loads(p.read_text(encoding="utf-8"))
        loaded += 1
print(f"{loaded}x single-run JSON load (regression-check loads exactly 2): "
      f"{(time.perf_counter()-t0)*1000:.2f} ms")
