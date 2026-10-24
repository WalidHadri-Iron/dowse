# Developing Dowse

Dowse is Python (the engine, the command line and the app process) and a web interface (`web/`) shown in WebView2.
Dependencies are managed with [uv](https://docs.astral.sh/uv/); everything runs on Windows (10 or 11, 64-bit).

## Layout

| | |
|---|---|
| `src/dowse/` | the engine: crawler and rules, readers (`reading.py`, `extract/`), the model runtime (`runtime.py`, `embedder.py`), the index (`store.py`), ranking (`searcher.py`), the live watcher, the CLI |
| `src/dowse/desktop/` | the app: tray, windows (pywebview), the local HTTP server, setup, Explorer's menu |
| `web/` | the interface: Svelte 5, TypeScript, three.js |
| `tests/` | engine and app tests; `tests/reference/` holds the reference vectors every runtime must reproduce |
| `evals/` | the ranking evaluation sets, run on the testbed (`scripts/make_testbed.py`, `scripts/hard_testbed.py`) |
| `packaging/`, `scripts/build_installer.py`, `scripts/build_mac_app.py` | the Windows installer; Dowse.app and its disk image (`packaging/macos/`) |

## The interface

The windows show a web interface, built into the package and served by the app on 127.0.0.1 with a per-run token.
`cd web && npm install && npm run build` after changing it (the build goes to `src/dowse/desktop/static`). For work on
it, `uv run python -m dowse.desktop.dev --port-file dev.json` runs the app without windows, and `npm run dev` (with
`DOWSE_API` set to its address) serves the interface with hot reload.

## Tests

`uv run pytest`. The `server` tests (`tests/test_reference.py`) start llama-server and check its vectors against the
reference vectors in `tests/reference`. Set `DOWSE_BACKEND=cpu` (or `vulkan`, `cuda`) to test a particular runtime.
Every llama.cpp update must pass them before `runtime.TAG` moves.

Interface: `cd web && npm run check && npm test && npm run build`. The texture-quality tests use Node's native
TypeScript support (Node 22.18+ or 24+).

## Ranking

Ranking changes are measured before and after, on the testbed alone and among another index's files:

```powershell
uv run python scripts/make_testbed.py; uv run python scripts/hard_testbed.py
uv run python scripts/ranking_experiment.py evals/testbed.json evals/hard.json --fresh
```

What it measured last is recorded in `searcher.py`'s docstring; [how-it-works.md](how-it-works.md) explains the
corrections it compares.

## Performance

`uv run python scripts/benchmark.py <a folder> --kinds` reads the folder from scratch in a throwaway data folder
(the installed model and runtime are reused; your index and settings are never touched), then times searches on it.
Add `--backend cpu` to measure the processor alone. The README's numbers are in `docs/bench/`.

## Installer

`uv run python scripts/build_installer.py` builds `dist/Dowse-Setup-<version>.exe`: Python's embeddable package, Dowse
and its locked dependencies, and Inno Setup (`packaging/dowse.iss`), fetched at pinned versions and checked.

On a Mac (Apple Silicon, with Xcode's command line tools), `uv run python scripts/build_mac_app.py` builds
`dist/Dowse.app` and `dist/Dowse-<version>.dmg` (for macOS 14 or later): a relocatable Python (python-build-standalone) with Dowse and its
locked dependencies inside the app, and a small program (`packaging/macos/launcher.c`) that embeds it, so macOS shows
the app as Dowse. It's signed ad hoc unless `DOWSE_MAC_IDENTITY` names a Developer ID, and notarized when
`DOWSE_NOTARY_PROFILE` names saved notary credentials (see the script's docstring).
