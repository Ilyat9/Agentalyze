---
title: Agentalyze Live Demo
emoji: 🕵️
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# Agentalyze — public BYOK demo

A live, honest demo of the Agentalyze eval harness: pick a tiny benchmark
task, paste **your own** OpenRouter/OpenAI API key, and watch a real agent
drive a real headless Chromium against local HTML fixtures. The key is used
for your single request only — never logged, never stored.

**This Space exposes the opt-in demo surface** (`AGENTALYZE_DEMO_MODE_ENABLED=1`,
`agentalyze serve --demo-mode`). Rate limits and the 90-second run budget are
enforced server-side; see `docs/DEMO_DEPLOYMENT.md` in the repository for the
full threat model and deployment guide.
