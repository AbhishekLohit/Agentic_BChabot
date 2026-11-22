---
review_of: ARCHITECTURE-SPINE.md
lens: versions and reality-check (chart-feature additions)
date: 2026-11-22
reviewer: read-only lens; spine not edited
---

# Review — chart feature: versions, live defaults, reality checks

## Verdict

Most of what the chart feature relies on checks out against the repo, the venv and upstream: Plotly.js 2.35.2, LangGraph 1.0.8 behaviour, window functions in SQLite, the claim that Python `plotly` is never imported, and `ast.literal_eval` on the row strings. One committed rule doesn't match the code: AD-10 and AD-20 say an oversized convention-shaped result always carries the notice string. A SELECT-led window-function balance query is cut to 20 rows silently, with no notice. Two Stack entries are out of date: the langchain-core pin and the runtime Python. The ISO-week format depends on the SQLite version, and the spine doesn't say so.

## What was checked, and how

| Spine claim | How it was checked | Result |
| --- | --- | --- |
| Plotly.js 2.35.2 via CDN (`src/index.html:10`) | `curl -I https://cdn.plot.ly/plotly-2.35.2.min.js` | **200 OK**, 4.56 MB, Last-Modified 2024-10-29 |
| "Upstream major is 4.x" | GitHub releases page | Confirmed. v4.0.0 shipped 2026-08-24; the latest is **v4.3.0 (2026-11-10)** |
| 2.35.2 supports `Plotly.react`, bar and line (scatter `mode:'lines'`) traces, `config.responsive` | Existing `renderChart` (`index.html:1995-2011`) already calls `Plotly.react(..., {responsive:true, displayModeBar:false})` on 2.35.2. These APIs have been in 2.x since 2.0. | Confirmed |
| Security of 2.35.2 | Advisory search (GitLab advisories, OSV) | Only CVE-2023-46308 (prototype pollution, fixed in **2.25.2**) affects 2.x; 2.35.2 has the fix. No plotly.js CVE dated 2025–2026 found. Not affected |
| Python `plotly` never imported | Grep of `*.py` for `plotly` | Confirmed. The only hits are the `plotly_json` field and strings in `app.py:283,483,488,588,593` and `app_models.py:66`, which the seed already removes. Note that `plotly` (6.7.0) is still installed in `venv/` and listed unpinned in `requirements.txt`; the seed lists `requirements.txt` as changed. |
| Chart rules are stdlib only; rows parsed with `ast.literal_eval` | Read `sql_execution.py` and `app.py:106-107` (no `detect_types`, `row_factory = sqlite3.Row`). Probe ran the live DB rows through `str(dict(zip(cols,row)))` and then `literal_eval`. | Mostly OK. Without `detect_types`, sqlite3 returns only `int`, `float`, `str`, `None` and `bytes`, and all of them round-trip. No `datetime` can appear: `transaction_date` is TEXT `YYYY-MM-DD`. **`inf` fails** (see F-4). |
| SQLite window functions for FR-15 | `python -c "import sqlite3; print(sqlite3.sqlite_version)"` gave **3.50.4**, the same in system Python and `venv` (both Python **3.14.3**). Ran the FR-15 CTE (`ROW_NUMBER() OVER (PARTITION BY period ORDER BY transaction_date DESC, transaction_id DESC)`) against `transactions.db`, read-only. | Works. It returned 38 monthly periods for the first customer. Window functions need ≥ 3.25 (2018), so any Python 3.11+ build is fine. |
| LangGraph 1.0.8: a node returns the whole Pydantic state | Probe on the venv (`langgraph==1.0.8`): Pydantic `BaseModel` state, async nodes that mutate and return `s` | Works. In-place `metrics` additions survive across nodes. `ainvoke` returns a **dict**, not the model, and `app.py` already treats it that way. Each `updates` event carries every set field, not only the changed one. That is harmless with sequential nodes and matches AD-24. |
| Plain edge `join → chart_builder → response_generator` | `agent_graph.py:83-85` uses single-source `add_edge`s into `join` (not the list-form waiting edge). The probe ran `join → chart_builder → response_generator`. | Works. `join` fires once, from whichever single predecessor ran. |
| `stream_mode="messages"` metadata `langgraph_node` | Probe with `GenericFakeChatModel.astream` inside `response_generator`; `app.py:547-563` | Works. Token events carry `langgraph_node="response_generator"`. `chart_builder` makes no LLM call, so it emits no message events and the allowlist needs no change. |
| LangGraph version currency | PyPI mirror | Upstream is on **1.2.x (1.2.12, 2026-09-21)**. 1.0.8 is pinned and works. The probe also hit a `LangChainPendingDeprecationWarning` (`allowed_objects` default in `JsonPlusSerializer`), which is only a warning. |

## Findings

### F-1 — HIGH — Window-function balance SQL can be capped silently, so AD-20's `too_many_time_points` never fires

**Evidence.** `sql_execution.py:24` decides whether a query is an aggregate with `\b(count|sum|avg|min|max|group_concat)\b\s*\(`. Window functions (`ROW_NUMBER`, `LAST_VALUE`, `FIRST_VALUE`) don't match. The rewrite also runs only when the query starts with `select` (line 23). A balance query in subquery form, such as `SELECT period, balance FROM (SELECT …, ROW_NUMBER() OVER (… ORDER BY transaction_date DESC …) rn FROM transactions WHERE customer_id=…) WHERE rn=1 ORDER BY period`, therefore:

- is treated as non-aggregate
- has an `ORDER BY` (the one inside `OVER(...)` counts as well)
- gets `LIMIT 20` appended (line 30)

That returns the **20 oldest** periods with **no** `[System Notification …]` string. The live DB has 38 monthly periods for one customer, so "my balance by month" hits this case. `chart_builder` then draws a line chart that is wrong without any sign of it. AD-10 says every oversized result gets the notice, and AD-20 says a convention-shaped result with >20 points reports `too_many_*`. Neither holds on this path. A `WITH`-led CTE skips the rewrite and reaches the Python cap, which appends the notice correctly.

**Fix (pick one, and record it in AD-10 and AD-20):**
- (a) In AD-20, require the FR-15 balance pattern and every period or category query to be a `WITH … SELECT` CTE, and give the prompt example in that form. Add an AD-10 as-is line: "the SQL rewrite applies only to statements starting with `SELECT` and doesn't recognise window functions".
- (b) Stronger: add a **Chart feature** line to AD-10 so that `sql_execution` treats `\bover\s*\(` and `\bgroup\s+by\b` as aggregate-like and skips the `LIMIT` rewrite, leaving the cap to the Python stage and its notice.

Either way, add a UJ row to the evaluation dataset with a >20-period balance trend.

### F-2 — MEDIUM — Stack table doesn't match the installed environment

- `requirements.txt` pins `langchain-core==1.2.13`, and so does the spine's Stack table. The venv has **langchain-core 1.4.0**, because the unpinned `langchain-anthropic` resolved to **1.4.3**, which requires `langchain-core>=1.3.2` (from its `METADATA`). A fresh `pip install -r requirements.txt` therefore either fails to resolve or installs a different langchain-anthropic from the one the demo was verified on. (`pip check` inside the venv is clean, so the gap is between `requirements.txt` and the venv, not within the venv.)
- The spine says "Python 3.11+", but every check here ran on **3.14.3**, which is the runtime in fact.

**Fix:** List the versions actually verified in the Stack table (Python 3.14.3; langchain-core 1.4.0; langchain-anthropic 1.4.3; langchain-google-genai 4.2.3; langchain-groq 1.1.2). Pin them in `requirements.txt` as part of the chart change, since the seed already touches that file. Or record the conflict as an explicit `[ASSUMPTION]` and keep it in Deferred.

### F-3 — MEDIUM — The `YYYY-Www` period format depends on the SQLite version

AD-20 allows `YYYY-Www`, but doesn't say which strftime produces it:

- ISO weeks need `%G-W%V`, which SQLite added in **3.46.1** (2024-08). Here it works on 3.50.4, but "Python 3.11+" doesn't guarantee that version. Many 3.11 and 3.12 builds bundle 3.40–3.45, where `%V` and `%G` aren't supported.
- `%Y-W%W` works everywhere but isn't ISO. It produces `W00` and splits weeks at the year boundary, so it gives wrong labels and extra Time Points.

**Fix:** Pin the prompt and the `chart_rules` regex to `strftime('%G-W%V', transaction_date)`, and add "SQLite ≥ 3.46.1 (verified 3.50.4)" to the Stack table. Or drop week granularity from v1.

### F-4 — LOW — Values that won't parse or serialise

- **Non-finite floats.** SQLite returns `inf` on real overflow (the probe confirmed `SELECT 1e308*10` gives `inf`; `0.0/0.0` gives `NULL`). `str({'v': inf})` makes `literal_eval` fail with `ValueError`. FastAPI's `JSONResponse` serialises with `allow_nan=False`, so an `inf` that slipped into a `ChartPoint` would turn the whole envelope into a 500, not just drop the Chart.
  **Fix:** AD-19 or AD-20 should say: parse each row inside the rules' own try; a row that fails to parse makes the result `unsuitable_shape`; `ChartPoint.value` must pass `math.isfinite`.
- **`period` from the INTEGER `year` column.** `SELECT year AS period` returns an `int`, not the ISO string AD-20 assumes.
  **Fix:** Have the prompt use `CAST(year AS TEXT)` or `strftime('%Y', …)`, and have the rules accept a 4-digit int, coerced with `str()`.
- **Duplicate column names.** These collapse in `dict(zip(columns,row))` (probe: `SELECT 1 AS a, 2 AS a` gives `{'a': 2}`). Harmless for the convention, but the rules should not assume `len(columns)` matches the dict.

### F-5 — LOW — Plotly 2.35.2: fine for v1; make the UI code ready for an upgrade and less dependent on the network

- Plotly 3.0 removed string-valued `title` and `titlefont` and friends, and 4.0 removed the mapbox traces, the Chart Studio config keys and TinyColor colour parsing. None of these affect bar or line charts, but a `ChartSpec → Plotly` mapping written with string titles would break on upgrade.
  **Fix:** Add a line to AD-23: use `title: {text}`, `xaxis.title.text` and `yaxis.title.text`; no `titlefont`; colours as hex or `rgb()` from the CSS tokens. Add an `integrity`/`crossorigin` SRI hash to the pinned CDN `<script>`.
- CDN reachability: in this environment an organisational web filter blocked **unpkg.com** ("Content Delivery Networks" category) during review fetches. `cdn.plot.ly` answered `curl` from this machine. AD-15 and AD-23 already degrade gracefully, but the Deferred "vendor Plotly" trigger should also say "or before demoing on a filtered corporate network".
- The existing `renderChart` `catch` shows "Chart could not be rendered." That conflicts with AD-23's "render nothing". It is covered by the seed's `index.html` change, but should be listed under removed or changed behaviour so it isn't kept by accident.

## Not verified (state as `[ASSUMPTION]` or verify before build)

- Behaviour on LangGraph 1.2.x. Verified only on the pinned 1.0.8.
- The browser-side behaviour of `config.responsive` on 2.35.2 inside the chat bubble. Not run in a browser here; it is inferred from the existing renderer.

## Sources

- plotly.js releases: https://github.com/plotly/plotly.js/releases
- plotly.js v3 changes: https://plotly.com/javascript/version-3-changes/
- Migrating to v4: https://plotly.com/javascript/guides/migrating-to-v4/
- CVE-2023-46308: https://osv.dev/vulnerability/CVE-2023-46308 , https://advisories.gitlab.com/npm/plotly.js/CVE-2023-46308/
- LangGraph on PyPI: https://pypi.org/project/langgraph/ (mirror: https://simple-repository.app.cern.ch/project/langgraph)
- SQLite 3.46.1 release log (`%G %V %U %g`): https://sqlite.org/releaselog/3_46_1.html ; date functions: https://sqlite.org/lang_datefunc.html
- Local probes: `transactions.db` (read-only), `venv/` package metadata, `src/agents/sql_execution.py`, `src/agent_graph.py`, `src/app.py`, `src/index.html`
