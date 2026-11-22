# PRD Quality Review — FinTell — Transaction Charts

Reviewed: `prd.md` and `addendum.md` (2026-11-22). Stakes calibration: internal demo, single local instance, seeded data. Length (about 5–6 pages) and NFR depth fit those stakes.

## Overall verdict

This PRD is in good shape for a demo. Its thesis is clear: the feature is defined by when it holds back. The eligibility rules have numeric boundaries that can be tested (3–8 Categories, 3–12 Time Points, 2/3, 8/9, 12/13), and the non-goals cut off the likely scope creep (top-N, "Other", regrouping, pie). The main risk is a promise that the Text Reply will not change. FR-3, §5 and the Glossary all make that promise, FR-5's note and FR-15's new balance answers both break it, and "identical" cannot be tested on LLM-generated text. A second risk is that the Skip Reason set has no precedence order and no label for chart-path failure, even though FR-14 and the evaluation dataset both depend on it. Fix those two points and the PRD can go to architecture.

## Decision-readiness — adequate

Most decisions are stated as decisions, not considerations. The PRD gives what was chosen and what was given up: "Past the limit, FinTell does not truncate to a top-N, add an 'Other' bar, or regroup" (FR-1). An explicit request does not override the rules (FR-5). Only the first qualifying SQL Sub-query gets a Chart (FR-6). A presenter who pushes back on "why no chart for my 14 merchants?" will find that objection answered in UJ-4 and FR-5.

The weak spot is one real tension the PRD never raises. FR-5 puts a line in the **Text Reply** that depends on the Chart Decision. FR-10 says time to first token is "unchanged", and FR-13 says the chart path must never affect the Text Reply. The Glossary says the Text Reply "never depends on the Chart". Taken together, these force the Chart Decision to be made before `response_generator` streams its first token, cheaply and without any way to fail into the text. The addendum notes the mechanics ("needs a defined way to reach `response_generator` without a second text writer", AD-3/AD-5), but the PRD makes no product decision and has no `[NOTE FOR PM]` callout. Open Questions 2 and 3 are real questions with sensible defaults.

### Findings
- **[medium]** FR-5 note couples the Text Reply to the chart path, and the trade-off is not stated (§3 "Text Reply", FR-5, FR-10, FR-13) — If the explicit-request note is part of the Text Reply, then a chart-path failure (FR-13) or a slow Chart Decision (FR-10) can change or delay the text, which the PRD promises will never happen. *Fix:* Make a product decision and record it as a `[NOTE FOR PM]`. Either (a) the note is a separate UI line outside the Text Reply, shown only when the Chart Decision is "no chart" and the request was explicit, or (b) the note is part of the Text Reply, the Decision must exist before text generation starts, and the PRD states what happens to the note if the chart path fails (for example, the note is omitted).
- **[low]** Open Question 2 has no owner, and both OQ-2 and OQ-3 change counting (§8) — Whether a Time Point is "whatever the SQL grouped by" and whether missing months are counted both move results across the 12-point line in FR-2. OQ-3 has an owner; OQ-2 does not. *Fix:* Add an owner to OQ-2. State that the evaluation dataset labels follow the defaults until the questions are resolved.

## Substance over theater — strong

There is no furniture to speak of. The single protagonist (Priya) and the presenter JTBD both drive requirements: the presenter's job ("never on the ones where they'd look silly") is the reason for FR-14's trace-panel visibility and SM-1's 100% target. The Vision can only belong to this feature ("defined as much by when it holds back as by when it draws"). The NFR-type content is product-specific: no time-to-first-token regression, endpoint parity, and the fail-safe chart path. Nothing reads like a generic "scalable/secure" line.

### Findings
- **[low]** Hedged latency bound (SM-3) — "≤ about 2 s added" and "no measurable change" are hedges in the one place the PRD sets a time bound. *Fix:* Use "≤ 2 s median added for charted Replies; ≤ 200 ms median change for un-charted Replies and for time to first token", or whatever tolerance the demo machine can actually hit.

## Strategic coherence — strong

The thesis is stated, and the metrics follow from it. SM-1 (no-chart correctness, 100%) is primary and stricter than SM-2 (chart correctness, ≥ 90%), so the PRD puts "never look silly" ahead of "always impress". SM-C1 (chart rate, "more charts is not better") is the right counter-metric for this thesis. The scope is an experience MVP, and the scope logic matches it: bar/line only, single series, one Chart, no persistence. FR-15 (balance trend) is the one feature that changes *answers* rather than presentation. It serves UJ-2, but it is a different kind of change (see Scope honesty).

### Findings
- **[medium]** The Vision promises "every time", but SM-2 accepts 1 in 10 misses (§1 vs SM-2) — "see the right picture appear ... every time" conflicts with a ≥ 90% target for charted questions. The no-chart half ("see none, every time") does match SM-1's 100%. *Fix:* Change the Vision line to "never see a chart where it doesn't belong, and see the right one on the demo questions", or state the asymmetry explicitly in SM-2.
- **[low]** The counter-metrics have no measurement method or alarm level (SM-C1, SM-C2) — SM-C1 has no expected band. SM-C2 ("must not get shorter or weaker") has no method at all. *Fix:* For SM-C1, report the chart rate on the dataset and flag it if it exceeds the labelled share. For SM-C2, compare Text Reply length (or a quick manual review) on the dataset with the feature on and off.

## Done-ness clarity — adequate

Most FRs are very testable: the boundary pairs in FR-1/FR-2, "13 points get no Chart", "Time Points are plotted in time order, whatever order the rows came back in", and FR-12's "no error or placeholder". Three gaps will hurt story creation. First, the Text Reply invariance in FR-3 cannot be verified and is contradicted elsewhere. Second, the Skip Reason outcome is undefined when cases overlap or the chart path fails. Third, "explicitly asks for a chart" is the trigger for FR-5's visible behaviour, and it is never defined.

### Findings
- **[high]** "Text Reply is identical to what FinTell returns today" cannot be tested, and FR-5 and FR-15 contradict it (FR-3 consequence 2; §5 bullet 6; FR-5; FR-15) — `response_generator` is an LLM, so the same question does not give byte-identical text today. Also, a `too_many_categories` case with an explicit request *must* add FR-5's note, and a balance trend with more than 12 Time Points (FR-15 consequence 4) gets a different answer from today because the SQL changes. *Fix:* Replace "identical" with a checkable statement: "the Text Reply is produced by the unchanged response flow from the same SQL results; the only added text is the FR-5 note when the user explicitly asked for a chart." Then list FR-15 as the second deliberate exception, here and in §5.
- **[medium]** No Skip Reason for chart-path failure, and no precedence when several cases apply (FR-3 table, FR-13, FR-14, FR-6) — FR-14 requires *every* trace to carry a Chart Decision with a Skip Reason, but FR-13 failures have no label. Overlapping cases have no defined winner: an empty result also has fewer than 3 points, a fallback can also be empty, and a Raw Transaction List can have more than 12 rows. FR-6's "no Sub-query qualifies" case is also unlabelled. FR-3 sidesteps this with "run on its own", but the addendum's `expected_skip_reason` column cannot be filled in without a precedence order. *Fix:* Add a `chart_error` (or similar) Skip Reason. State the evaluation order of the table, for example fallback → no_transaction_data → empty_result → raw_list → unsuitable_shape → too_few_points → too_many_*. State which Skip Reason is reported when several SQL Sub-queries all fail to qualify (for example, the first Sub-query's).
- **[medium]** "Explicitly asks for a chart" is undefined (FR-5, UJ-3, UJ-4) — The FR-5 note appears only on an explicit request, but it is unclear whether "show my balance trend", "plot", "graph" or "visualize" count. UJ-2's "Show my balance trend" sits right on that boundary. *Fix:* Add a Glossary term (for example "Explicit Chart Request") with inclusion examples (chart, graph, plot, visualise, a named chart type) and exclusion examples ("show my…", "trend").
- **[medium]** The direction rule has no Chart Decision outcome (FR-8 consequence 3) — "A Chart shows one direction only ... It never mixes income and outgoing amounts" does not say what happens when an SQL Sub-query result *does* mix them: no Chart (`unsuitable_shape`?), or a requirement on the generated SQL? Without that, it cannot be tested. *Fix:* State the outcome, for example "a result whose amounts mix directions without being net or balance gets no Chart (`unsuitable_shape`)", and add one such question to the evaluation dataset.
- **[low]** Missing bounds on "unchanged" and "desktop width" (FR-10 consequence 1, FR-9 consequence 2) — "Unchanged" needs a tolerance (see SM-3). "The demo's desktop width" needs a number. *Fix:* Give a tolerance, and name a width such as 1280 px.
- **[low]** Silent type substitution (FR-4 consequence 2 vs FR-5) — A pie request that qualifies gets a Bar Chart, and nothing says whether the Text Reply mentions the substitution. FR-5's note covers only "no chart". *Fix:* State it either way, for example "the substitution is not mentioned".
- **[low]** "Last transaction in that period" has no tie-break (FR-15 consequence 2) — If several transactions share the last `transaction_date`, "last" is ambiguous. *Fix:* Name the ordering (for example `transaction_date`, then `transaction_id`), or hand it to architecture explicitly.

## Scope honesty — adequate

The Non-Goals section does real work: each bullet closes a request a demo audience would actually make. The `transactions` schema is explicitly frozen. §6.2 gives a revisit trigger for each deferral. The Assumptions Index is honestly empty: all five draft assumptions are recorded as confirmed on 2026-11-22, and there are no orphan `[ASSUMPTION]` tags. For an internal demo, the open-items density (two Open Questions, zero assumptions) is fine. The gap is that FR-15 quietly widens the feature from presentation into answer behaviour.

### Findings
- **[high]** FR-15 changes text answers, but §5 says no Text Reply changes except FR-5 (FR-15; §5 bullet 6; §6.1 bullet 2) — Answering a balance trend as end-of-period balances means a different SQL answer and therefore different text, whether or not a Chart is shown. The addendum says it "almost certainly needs a `prompts/text2sql_generator.yml` change" plus cache invalidation. This is a deliberate behaviour change, but the PRD's own non-goal denies it. *Fix:* Add FR-15 as an exception in §5 and in FR-3. State in §6.1 that this is a change to text-to-SQL behaviour and not only to presentation. Optionally add a `[NOTE FOR PM]` that cached SQL must be cleared before the demo.
- **[low]** FR-12, FR-7 and FR-6 are listed as "Out of Scope" (§6.2) — FR-12 is an in-scope requirement with a testable consequence ("no error or placeholder" on reload), yet it appears only under Out of Scope, and §6.1 never lists it. *Fix:* Add FR-12 to §6.1. Reword the §6.2 items as "persisting Charts (see FR-12)", "multi-series (excluded by FR-7)", and so on.
- **[low]** No `[NOTE FOR PM]` callouts anywhere — §0 commits to inline tags, and the two real tensions (FR-5 vs FR-10/FR-13, FR-15 vs §5) are where callouts belong. *Fix:* Add callouts at those two points.

## Downstream usability — adequate

The Glossary is present, short and mostly respected. Capitalised terms (Reply, Text Reply, Chart, Data Point, Skip Reason, SQL Sub-query) are used consistently in the FRs. Every FR names the UJ it realises (except FR-9 to FR-14, which are cross-cutting, and that is reasonable). Spine references (AD-3, AD-5, AD-10, AD-12, AD-13, AD-16) give architecture clean entry points, and the addendum's deferred-item table maps each one to an FR. The main extraction gap is SM-to-FR traceability for what the evaluation harness will actually check.

### Findings
- **[medium]** SM traceability does not cover the Skip Reason or the FR-5 note (§7, addendum "Evaluation dataset") — SM-1 measures only the *absence* of a Chart, so it validates FR-5's "no Chart" but not the note FR-5 requires. No SM checks that the Skip Reason in the trace matches the label, even though the dataset has `expected_skip_reason` and FR-14 makes the reason visible. FR-4's substitution (pie → bar) produces a Chart, so it belongs under SM-2, not SM-1. FR-8, FR-9, FR-12 and FR-14 have no SM. That is acceptable, since their consequences are the checks, but say so. *Fix:* Add an SM such as "Skip Reason match ≥ 90%" (diagnostic), add "FR-5 note present iff explicit and no chart" to SM-1 or a new SM, move FR-4 to SM-2, and add one line saying the presentation FRs are verified by their consequences.
- **[low]** "Chart field is empty" vs the existing `null` `visualization` key (FR-11 consequence 2; addendum) — "Empty" could mean `null`, `{}` or `[]`. FR-11 is the only place the PRD specifies response shape. *Fix:* Say "present with the same null/empty value in both endpoints", and let architecture fix the literal.

## Shape fit — strong

The shape fits an internal, single-presenter demo feature. There is one protagonist across four short UJs, and the UJs are load-bearing because the demo script *is* the user journeys. There is no persona sprawl. NFRs are folded into the FRs and SMs rather than placed in a separate section, which suits the stakes. Brownfield references are accurate against AGENTS.md: `amount_usd` is always positive with direction from `transaction_type`, `balance` is a running balance, `tests/data/` holds data only, and query-cache invalidation is covered. The scope stops at the right altitude, because "how" is pushed to the addendum. No findings.

## Mechanical notes

- **Glossary drift:**
  - "Sub-query" / "Later Sub-queries" (FR-6) for **SQL Sub-query**.
  - "result", "SQL result", "transaction results", "SQL Sub-query result" are used interchangeably and never defined. Consider a Glossary entry for "Result" (the rows returned for one SQL Sub-query).
  - "chart path" (FR-13, FR-14, SM-5) is a key term with no Glossary entry.
  - Lowercase "category" (FR-7 "spend per category per month", FR-8 "category or time axis", UJ-1) collides with the Glossary's **Category**, which can also mean a merchant brand.
  - "period" (FR-8, FR-15 "end-of-period") is used next to **Time Point**.
  - Lowercase "bar or line chart" (FR-4) and "chart type" are used for **Bar Chart** / **Line Chart**.
  - "spend" / "spending" / "outgoing" (FR-8) name one direction with three words.
- **Skip Reason set:** The Glossary lists five Skip Reasons "for example", while the FR-3 table has eight (`too_many_time_points`, `no_transaction_data` and `unsuitable_shape` are missing from the Glossary). Because Skip Reasons are "fixed", the Glossary should point to the FR-3 table as the full set, plus any failure label you add.
- **ID order:** FR-1 to FR-15 are contiguous and unique. Placing FR-15 in §4.1 does not hurt reading in itself. It does, however, fall outside the ranges used elsewhere: §6.1 "Chart Eligibility Rules (FR-1 to FR-7)" and the §7 preamble "every rule in FR-1 to FR-7" both leave out FR-15, even though it sits in §4.1 and SM-2 validates it. Add "and FR-15" to the §7 preamble so the evaluation dataset covers it.
- **UJ protagonists:** All four UJs name Priya and carry their context inline. UJ-2's "11 monthly points" depends on today's date (November 2026). That is fine for the demo, but the evaluation labels will go stale after a data `shift`.
- **Assumptions Index roundtrip:** Clean. There are no inline `[ASSUMPTION]` tags, and the index says so and explains why.
- **Cross-references:** All FR, UJ, SM and AD references resolve. FR-13's "spine's deferred item on who may write `fallback_response`" resolves through the addendum table.
- **Required sections:** All present for the stakes: Vision, users, UJs, Glossary, FRs, Non-Goals, Scope, SMs with counter-metrics, Open Questions, Assumptions.
