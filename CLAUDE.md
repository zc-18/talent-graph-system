# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

智岗图谱 (TalentGraph AI) — a "多源异构数据驱动岗位和能力图谱构建与动态演化分析" system for the 挑战杯/iFlytek competition (XH-202621). Two parts under `talent-graph-system/`: a Python/FastAPI **backend** (`backend/`) and a React/Vite **frontend** (`frontend/`). Live deploy: `http://101.200.184.201:8200/`.

## Commands

All backend commands run from `backend/` (Python managed by **uv**, not pip/venv directly):

```bash
cd backend
uv sync                                              # install deps
uv run uvicorn app.main:app --port 8200              # run API + serves built frontend at /
uv run pytest --cov=app --cov-report=term-missing    # tests + coverage (target ≥60%)
uv run pytest tests/test_matching.py::test_full_match  # run a single test

# Data / graph pipeline (writes to the CLOUD MySQL the app reads):
uv run python data/generate_dataset.py        # generate seed_jds.json + ground_truth.json (roles in ROLES dict)
uv run python data/generate_resumes.py        # generate test_resumes.json
uv run python data/run_pipeline.py --reset --use-cache   # clean→parse→cross-validate→persist graph
uv run python data/seed_relations.py          # inject skill prerequisite/related/drives edges
uv run python data/rediscover_new_jobs.py     # (re)create the is_new jobs via discovery (KEYWORDS list)
uv run python data/evaluate.py all            # JD/resume/match accuracy (reads parsed_cache.json)
uv run python data/export_deliverables.py     # export test-data samples + 综合测试报告.json
uv run python data/md_to_docx.py              # convert docs/_source/*.md → docs/*.docx
```

Frontend (from `frontend/`, npm):

```bash
npm install
npm run dev        # vite dev server, proxies /api → 127.0.0.1:8200 (run backend too)
npm run build      # outputs frontend/dist (served by backend as static)
```

PPT deck: `cd docs/ppt_build && npm install pptxgenjs && node build.js` (embeds screenshots from `docs/截图/`).

## Deploy model (important)

- **Code deploy is manual**: `npm run build` → tar `backend/app` + `backend/.env` + `frontend/dist` (as `static/`) → upload to server `/opt/talent-graph/backend/` → `systemctl restart talent-graph`. The server only ever receives the built tarball; **never edit code on the server** — local files are the single source of truth.
- **Data is NOT in code**: the knowledge graph lives in the cloud MySQL `talent_graph`. Local `data/*.py` scripts write into it; the production backend reads it live. So "publishing new jobs/data" = run the local pipeline scripts (no redeploy needed); redeploy only when `app/` or frontend code changes.
- The server is tiny (~786MB free RAM). Backend runs as a **single uvicorn worker** via systemd `talent-graph.service` with `MemoryMax=420M`. Do not build Docker images on the server.

## Architecture (big picture)

**Backend pipeline** (`backend/app/services/`) is the core; routers are thin wrappers over it:
`ingest.build_graph_from_dataset` orchestrates: `cleaning` (SimHash dedup / inflation / lag) → `extraction.parse_jd` (DeepSeek structured JD parse) → cluster JDs by `ingest.title_key` → `hallucination.aggregate_capabilities` (the anti-hallucination cross-validation: a skill needs ≥2 independent non-duplicate sources or web evidence to be a confident/required capability; everything carries a confidence score + evidence) → `graph_service.upsert_job` persists Job/Skill/JobSkill/Evidence.

Other services: `discovery` (new-job discovery, RAG-grounded via Tavily+Serper in `clients.multi_source_search`), `evolution` (diff old vs new capabilities → add/delete/modify changes), `resume` (PDF/Word parse), `matching` (multi-dimensional person-job match + learning path).

**External clients** (`app/clients.py`): DeepSeek (LLM, OpenAI-compatible), BGE embeddings, Tavily, Serper. All keys in `backend/.env` (read via `app/config.py`).

**Two non-obvious gotchas baked into the code:**
- `matching.py` semantic match is **guarded to CJK-only strings**: the BGE model (`bge-small-zh`) returns *degenerate/identical* vectors for pure-English tokens (Spark/Hive/Java all ≈ same vector), which would create false matches. English synonyms are handled by the dictionary in `taxonomy.normalize_skill` instead; semantic match only runs between Chinese strings, with a degeneracy guard.
- `run_pipeline.py --use-cache` reuses `data/parsed_cache.json` (keyed by JD text hash) so only *new* JDs hit the LLM; it always writes the merged cache back, which `evaluate.py` then reuses for free.

**Frontend** (`frontend/src/`): React + Vite + Tailwind + Framer Motion + ECharts. `App.tsx` = sidebar + routed pages (`pages/`) + `ChatBot.tsx` (streaming SSE assistant). `components/ui.tsx` = shared Card/Badge/etc; `components/Select.tsx` = custom dropdown (native selects look inconsistent). `api.ts` = typed axios client. The panorama force-graph (`pages/Panorama.tsx`) is the visual centerpiece: nodes are flat linear-gradient "coins" (jobs) + minimal flat dots (skills); edge hover-emphasis is **disabled** to avoid flicker; light theme uses `public/bg.png` + `public/graph-bg.png`.

## Submission materials

`docs/` holds the deliverables: 作品设计与实现方案 / 测试方案与报告 / 部署说明 / 演示视频脚本 / 技术答辩文档 (交付为 Word `.docx`；Markdown 源在 `docs/_source/`，由 `md_to_docx.py` 生成 .docx), `智岗图谱_作品介绍.pptx`, `测试数据/` (graph I/O examples + datasets + eval results), `截图/`. When dataset size changes, the headline numbers (job/JD/skill counts, accuracy) are scattered across these docs + `ppt_build/build.js` — re-run eval/export then update them together.
