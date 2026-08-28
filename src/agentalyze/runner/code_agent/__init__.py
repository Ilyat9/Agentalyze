"""Second runner backend: smolagents' ``CodeAgent`` (code generation) instead
of structured tool calling.

See ``model_adapter.py`` (Provider -> smolagents.Model), ``tool_adapters.py``
(browser tools -> smolagents.Tool), and ``loop.py`` (CodeAgent run -> RunTrace)
for the actual integration. Optional: importing this package requires the
``code-agent`` extra (``pip install -e ".[code-agent]"``); the default
tool-calling runner (``agentalyze.runner.react_loop``) never imports it.
"""

from __future__ import annotations
