# Proposal to upstream — fixes found while using ReadyLLM on an AMD Radeon RX 9070 XT

Version 1.1.1 — 2026-10-01 21:55 (text in English for the original author; ready to paste into issues / pull requests)

Hi! While running ReadyLLM on a Windows PC with a Radeon RX 9070 XT we found and fixed several problems.
Each fix is small, independent and keeps backward compatibility with existing `targets.json` files.
Fork with all changes: https://github.com/chicco83/ReadyLLM-AMD (branch `claude/wizardly-cray-fk41b9`).
Suggested split into separate PRs, in this order:

## PR 1 — Settings: saving a machine creates a duplicate entry instead of updating it
- **Cause:** `Settings.jsx` always starts from an empty form without `id`; `Target()` generates a new uuid and
  `upsert_target` appends it to `targets.json`.
- **Fix:** the Settings page lets you pick a saved machine (form keeps its `id`); `upsert_target(match_identity=True)`
  also matches the same machine without `id` (ssh: host+port+user, local: name).
- Files: `frontend/src/pages/Settings.jsx`, `backend/app/models/target.py`, `backend/app/api/target.py`.

## PR 2 — Hardware detection for AMD GPUs (currently only `nvidia-smi` is tried)
- **Cause:** `_detect_gpu_static` / `_collect_gpu` only call `nvidia-smi` (and `system_profiler` on macOS), so a Radeon card is
  reported as "no GPU" (no VRAM-based recommendations, empty monitor).
- **Fix:** Windows: registry `HardwareInformation.qwMemorySize` (WMI `AdapterRAM` is a uint32 and caps at 4 GB) with
  `Win32_VideoController` fallback; realtime via `Win32_PerfFormattedData_GPUPerformanceCounters_*` (class names are not
  localized, unlike `Get-Counter` paths). Linux: sysfs `amdgpu` + `lspci` + `rocminfo` (gfx arch). Adds `vendor` to the GPU dict.
- File: `backend/app/services/collectors.py`.

## PR 3 — Recursive model scan
- `/api/deploy/models` and `/api/store/downloaded` only listed the top-level folder. New `services/model_scanner.py` searches
  subfolders (depth ≤ 8), returns paths relative to `models_dir`, skips non-first shards (`-0000N-of-0000M`) and `mmproj*`.

## PR 4 — llama.cpp backend choice (CUDA / ROCm-HIP / Vulkan / CPU) + robust one-click install
- New `Target.llama_backend` (default `auto`: NVIDIA→CUDA, AMD/Intel→Vulkan, Apple→Metal, else CPU) and a selector in Settings.
- Windows installer picks the matching release asset (`cuda`, `hip-radeon`, `vulkan`, `cpu`) instead of always CUDA; CUDA also
  downloads the `cudart` zip. Linux build uses `GGML_CUDA` / `GGML_HIP`+`AMDGPU_TARGETS` / `GGML_VULKAN`.
- Network robustness: TLS 1.2 forced (PowerShell 5.1), User-Agent, fallback to the HTML release page when the GitHub API is
  blocked/rate-limited (403), 3 download retries with size check, optional `READYLLM_GH_PROXY` mirror, and `_run_step(check=True)`
  so real errors are surfaced instead of discarded.
- File: `backend/app/services/installer.py`.

## Optional PR 5 — Italian localization
- Italian UI language and Italian translation of code comments/messages (logic unchanged, verified by comparing the AST with
  string literals masked). Only if you want a third language; PRs 1–4 do not depend on it.

## Caveats
- Not tested on real AMD hardware inside our cloud environment; the logic was unit-tested with simulated command output.
  We will report results from the real RX 9070 XT PC.
- Temperature/power for AMD GPUs on Windows are not exposed (shown as 0).
