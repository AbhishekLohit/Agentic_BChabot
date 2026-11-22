# Review — Versions & Reality Check

- **Spine:** `ARCHITECTURE-SPINE.md` (FinTell baseline, 2026-11-22)
- **Lens:** Is every committed technology/version confirmed against the project or the web, rather than asserted from memory? Brownfield baseline, so "verified" means it matches `requirements.txt` and the code; the web is used only for material concerns (retired, end-of-life, drift).
- **Date of check:** 2026-11-22

## Verdict

The Stack table matches the repo exactly. But the spine's provider hot-swap claim (`openai | anthropic | gemini | groq`) relies on two non-default model ids in `src/app_setting.py` that the providers have already retired, plus a third that is close to retirement, and the spine never records these ids. The default Anthropic path also rests on an unpinned `langchain-anthropic`.

## 1. Stack table vs. project (reality check)

| Stack row | Spine | Source of truth | Match |
| --- | --- | --- | --- |
| Python | 3.11+ | CLAUDE.md / AGENTS.md; pandas 3.0.x itself requires Python >= 3.11 | Yes |
| FastAPI | 0.136.0 | `requirements.txt:16` | Yes |
| uvicorn | 0.41.0 | `requirements.txt:102` | Yes |
| LangGraph | 1.0.8 | `requirements.txt:42` | Yes |
| langchain-core | 1.2.13 | `requirements.txt:39` | Yes |
| langchain | 1.2.10 | `requirements.txt:37` | Yes |
| langchain-openai | 1.1.10 | `requirements.txt:41` | Yes |
| langchain-anthropic / -google-genai / -groq | unpinned | `requirements.txt:40,109,110` (no specifier) | Yes |
| langchain-chroma | 1.1.0 | `requirements.txt:38` | Yes |
| chromadb | 1.5.0 | `requirements.txt:11` | Yes |
| pydantic | 2.13.2 | `requirements.txt:69` | Yes |
| pydantic-settings | 2.13.0 | `requirements.txt:70` | Yes |
| redis | 7.2.0 | `requirements.txt:78` | Yes |
| slowapi | 0.1.9 | `requirements.txt:1` | Yes |
| pandas | 3.0.1 | `requirements.txt:65` | Yes |
| SQLite | stdlib | code uses `sqlite3` | Yes |
| Plotly.js (CDN) | 2.35.2 | `src/index.html:10` (`cdn.plot.ly/plotly-2.35.2.min.js`) | Yes |
| Default chat model | `claude-sonnet-4-6` | `src/app_setting.py:4,12` (`LLM_PROVIDER="anthropic"`) | Yes |
| Embedding model | `text-embedding-3-small` | `src/app_setting.py:23` | Yes |

Every row is traceable to the repo, and no row is asserted without a source. The `sources:` frontmatter lists `requirements.txt` and `src/`, which fits.

## 2. Web check: is it still current?

| Item | Status found | Material? |
| --- | --- | --- |
| `claude-sonnet-4-6` (default) | Active on Anthropic's model-deprecations page; retirement "not sooner than February 17, 2027" | No. Fine for the baseline. Worth a "Revisit when" note. |
| `text-embedding-3-small` | No shutdown notice found | No |
| `GROQ_MODEL = "llama3-70b-8192"` (`app_setting.py:11`) | **Shut down 2025-08-30.** Calls fail with `model_decommissioned`. The suggested replacement, `llama-3.3-70b-versatile`, was itself shut down on 2026-08-16. | **Yes** |
| `GEMINI_MODEL = "gemini-1.5-flash"` (`app_setting.py:10`) | **Discontinued 2025-09-24** (the alias pointed to `-002`) | **Yes** |
| `OPENAI_MODEL = "gpt-4o-mini"` (`app_setting.py:9`) | Deprecated. Removal from the API is reported for 2027-01-14, about 7 weeks away. | **Yes** (upcoming) |
| Plotly.js 2.35.2 via `cdn.plot.ly` | Still a pinned, served build. Plotly.js v3 exists, but nothing is broken, and the renderer is unused code according to the spine. | No |
| FastAPI / LangGraph 1.x / LangChain 1.x / chromadb 1.x / pydantic 2.x / pandas 3.x / redis 7.x | Current major lines. No EOL or yanked status found worth flagging. | No |
| slowapi 0.1.9 | Still the latest release, with a slow release cadence. Not deprecated. | No |

## 3. Findings

### F1 — Hot-swap to Groq or Gemini is broken: their default model ids are retired (High)

- **Evidence:** `src/app_setting.py:10-11` defaults to `gemini-1.5-flash` and `llama3-70b-8192`, and `clients/llm_client.py:49,63` uses them unless they are overridden. Groq's deprecations page lists `llama3-70b-8192` as shut down on 2025-08-30, with error `model_decommissioned`. Google lists Gemini 1.5 Flash as discontinued on 2025-09-24. The spine (AD-14, the deployment diagram "Anthropic default · OpenAI · Gemini · Groq") and CLAUDE.md both present these as working providers.
- **Fix:** Add rows to the Stack table for `OPENAI_MODEL`, `GEMINI_MODEL` and `GROQ_MODEL` with their current values, marked **"retired — non-functional as-is"**, so the baseline records the truth. Add a Deferred row: "Refresh non-default provider model ids. Revisit before anyone uses `/update-settings` with gemini or groq." The actual id refresh belongs to a code change (or a `.env` override), not to the spine.

### F2 — `gpt-4o-mini` is deprecated, with removal reported for 2027-01-14 (Medium)

- **Evidence:** `src/app_setting.py:9`. OpenAI's deprecations page lists the `gpt-4o-mini-2024-07-18` snapshot as deprecated, with API removal on 2027-01-14. The replacement name in the search snippet could not be confirmed, so check the live page.
- **Fix:** Record the id and its sunset date in the Stack table. Add a Deferred row "Revisit before 2027-01-14". Also note `claude-sonnet-4-6`'s "not before 2027-02-17" retirement floor next to the default-model row, so the baseline carries its own expiry date.

### F3 — The default provider's integration package is unpinned (Medium)

- **Evidence:** `requirements.txt:40` `langchain-anthropic` has no specifier, yet it is imported on the default path (`llm_client.py:81`). `langchain-core` is pinned at 1.2.13, so pip resolves whichever `langchain-anthropic` release is compatible at install time. That makes the default path non-reproducible across installs, and it can fail to resolve if a future release raises its core floor. The same applies to `langchain-google-genai` and `langchain-groq` (lines 109-110) and to `langchain-text-splitters`, `beautifulsoup4`, `openpyxl` and `lxml` (lines 112-115), which the offline builders use (`barclays_help_ingest.py:35-36`).
- **Fix:** Pin each of these to the version currently installed (`pip freeze | findstr ...`), at minimum `langchain-anthropic`. In the spine, replace "unpinned in requirements.txt" with the pinned versions, or keep the row and add a Deferred entry that names the drift risk explicitly.

### F4 — Unused `plotly` Python dependency, unpinned (Low)

- **Evidence:** `requirements.txt:111` `plotly` has no specifier, and no `import plotly` exists anywhere in the repo. The only Plotly use is the browser CDN script, which the spine's "Unused visualization code" note already calls dead.
- **Fix:** Add `plotly` (Python) to the spine's unused-visualization note so the chart-feature architecture decides its fate. Pin it or drop it at that point. No Stack row is needed.

## 4. What passed

- Every Stack version is verifiable in-repo, and the spine does not invent versions.
- The default model and the embedding model are both current.
- Pinning the Plotly CDN to an exact version is the right practice. The spine just should not imply the version matters while the renderer is unused.

## Sources

- [Groq deprecations](https://console.groq.com/docs/deprecations) · [Groq changelog](https://console.groq.com/docs/changelog) · [mem0 issue showing `model_decommissioned`](https://github.com/mem0ai/mem0/issues/4694)
- [Gemini API deprecations](https://ai.google.dev/gemini-api/docs/deprecations) · [Vertex AI Gemini 1.5 Flash page](https://cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/1-5-flash) · [Google forum: 1.5 Flash 002 discontinuation](https://discuss.ai.google.dev/t/gemini-1-5-flash-002-discontinuation-date-is-september-24-2025-its-too-short-we-hopt-at-least-5-years/47703)
- [Anthropic model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations)
- [OpenAI deprecations](https://developers.openai.com/api/docs/deprecations) · [OpenAI community: 2026 shutdown notice](https://community.openai.com/t/deprecation-notice-upcoming-model-shutdowns-in-2026/1379553)
