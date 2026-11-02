# Adaptive research: first TUI pilot

The first pilot does **not demonstrate an advantage over one capable investigator**.
The adaptive engine successfully launched, bounded, and collected independent work,
but that branch consumed six calls without publishing evidence. Keep adaptive
research experimental; for this kind of code audit, start with one continuing
investigation and use branches selectively.

## Task and controls

The brief was recovered verbatim from the October workspace runs:

> The TUI mode hasn't been touched in a while. Take a look and let me know what should be updated.

All runs used `ds-4.1-flash` on the Sparks for research and fresh synthesis, official
file/cmd tools, deny writes, no MCP/global file memory, an 8,192 output-token
ceiling, and fresh notes stores. They inspected the same archived source at
`514180c92b863ae24a012ed0b85f8055bdba3645`; its tracked file hashes remained unchanged.
The archive excludes user configuration and old workspace artifacts. It has no
`.git`, so history-based staleness assessment was unavailable to every workflow.

Each workflow received at most **31 application provider calls including synthesis**,
600 seconds total, and a 120-second synthesis reserve. Fixed workers received
15 calls each plus synthesis. Single/adaptive shared 30 research calls plus
synthesis; coordination and model-backed helpers count against that allowance.
This equalizes the call ceiling, not token use or actual compute.

A rubric was saved before inspecting outputs. It emphasized actionable priorities,
original-source evidence, behavioral checking, correct treatment of the deliberately
deferred TUI migration, uncertainty, and finding-driven changes in investigation.

## Observations

| Workflow | Calls including synthesis | Wall time | Published findings | Outcome |
| --- | ---: | ---: | ---: | --- |
| Single, corrected baseline | 29 | 339 s | 2 | completed |
| Fixed: two workers | 31 | 202 s | 1 | partial; both workers hit their call limits |
| Adaptive: lead plus optional branch | 30 | 259 s | 1 | partial; branch hit its allowance |

These outcomes describe execution and the lead's own coverage assessment, not
independent certification of answer correctness. All workflows incurred failed
tool operations: 5, 3, and 7 respectively. Failures included malformed shell calls,
question-ID bookkeeping errors, absent git metadata, and an unavailable Textual
installation in the command container.

Cumulative provider-reported accounting:

| Workflow | Input tokens | Cache-hit tokens (part of input) | Output tokens | Reasoning tokens |
| --- | ---: | ---: | ---: | ---: |
| Single | 1,033,811 | 937,984 | 21,258 | 7,416 |
| Fixed | 558,087 | 454,656 | 14,757 | 3,363 |
| Adaptive | 709,896 | 604,160 | 16,546 | 3,731 |

Input totals include repeated history across calls; they are not distinct source
volume. Cache behavior and execution order affect latency. These figures do not
establish a local compute-cost ranking.

The single investigator produced a useful, fairly broad migration/update plan.
The fixed workers performed substantially overlapping exploration, did not consume
one another's notes, and published little before exhausting their separate budgets.
Fixed parallelism was faster here, with less completed evidence and a thinner answer.

The adaptive lead recorded a working brief, opened a question, launched a branch,
and later assessed coverage. However, its branch question largely repeated the
whole TUI audit: runtime migration, Textual drift, bindings, commands, and tests.
It cited no starting finding. The branch spent its six-call reservation exploring,
encountered environment/tool failures, and published no finding or final summary.
The lead continued its own investigation and supplied the only synthesis finding.
There is **no recorded evidence-to-branch-to-changed-recommendation benefit** in
this run. Starting a branch is not itself successful adaptation.

## Answer quality and independent checks

All three answers identified the duplicate Ctrl+S binding and the legacy-runtime
migration. They also offered useful testing/documentation recommendations. None
established a successful full TUI run in the command environment.

Independent local probes of the same frozen source, excluded from model inputs,
confirmed:

- `MemexTUIApp.BINDINGS` contains both stream-toggle and priority cancel for Ctrl+S.
- Calling the actual idle-cancel method with no active token emits "Turn cancelled."
- Passing an empty failed result through `TurnExecutor._finish_turn` displays
  "(no response)" and emits "Assistant ready."
- A minimal mounted app using the frozen `ChatInput` and actual app key handler,
  with host Textual 8.0.2, routes Up to chat scrolling while leaving the input
  cursor unchanged. This supports the initial baseline's navigation concern;
  it is not a full Memex app compatibility test.

The answers also contain overclaims that should not become implementation tasks
without checking. Both single/adaptive descriptions call TUI the last unmigrated
UI, although Web also retains legacy execution. The adaptive answer proposes
blanket CLI-only gating for `manage_chats` and `save_code`, while those actions
already subclass StepwiseAction and explicitly support nonblocking interaction.
It suggests a speculative pre-1.0 Textual version range without establishing
compatibility, and describes a legacy agent-loop ownership that is at best
imprecise after removal of `run_agent_loop`. The corrected baseline also repeats
stale command-gating documentation and proposes small runtime-configuration
changes whose ordering must be checked against the TUI output adapter.

The consistent finding is the need for a planned TUI adapter migration and verified
UX fixes. The experiment is not evidence that every recommendation is correct,
or authorization to apply the generated update lists wholesale.

## What to improve next

1. Make behavioral verification available in the read-only command environment.
   Textual was absent there even though host reference probes could exercise it.
   Repeated source reading cannot substitute for a cheap targeted test.
2. Improve branch requests and collection: a focused consequential uncertainty,
   relevant evidence, a clear return artifact, and an opportunity to publish before
   the allowance ends. Preserve useful partial observations when a branch exhausts
   its budget. Increasing worker count would not address the observed failure.
3. Improve bookkeeping/tool ergonomics. Question updates failed with unknown IDs;
   shell calls also failed across treatments. Inspect the request/repair contract
   before adding model-specific steering. Preserve enough observable tool evidence
   to audit a claimed check without sharing private reasoning histories.
4. Require source checks for consequential recommendations, especially when docs
   conflict with implementation. The repository still contains stale guidance.
5. Repeat on a task with meaningful competing hypotheses or directions, and rotate
   run order. Retain the single-agent baseline and the same synthesis procedure.

No model-specific prompt addendum was introduced. The observed problems concern
verification capability, handoff/finalization, and tool/state contracts; one local
model pilot does not justify weakening shared behavior or claiming a universal
multi-agent advantage.

## Implementation, correction, and artifacts

The experiment is available as `workspace research --brief FILE`, with `single`
and `adaptive` strategies, explicit goal/question state, one optional concurrent
branch, a shared ledger, and frozen synthesis replay. Correctness tests cover
budgets including nested helpers, publication repair/idempotency, branch concurrency,
timeouts, cancellation, a lead crash, and controlled stopping at call boundaries.
The final offline suite passed **678 tests** (one existing MCP SDK deprecation warning).
Process-group checks were also run with process signaling permitted outside the
Codex sandbox, which had denied a signal to an orphaned test child.

Implementation commits are `00b3ae9` and correction `da328e6`. The initial single
pilot uncovered the timestamp correction: the assessment was saved, but recording
its decision event supplied `time` twice and returned a tool error. That pilot is
retained separately and excluded from the table. The corrected single baseline
was repeated after the fixed/adaptive runs. Run order, warm caches, a single trial
per treatment, and the correction prevent strong causal or statistical claims.

Local artifacts are under ignored `temp/Nov26_Work/tui-experiment/`:

- `rubric.md`, `profile.json`, `source-manifest.json`, and `comparison.json`;
- `single-corrected-answer.md`, `fixed-answer.md`, `adaptive-answer.md`;
- corresponding result/report JSON, separate workspace stores, and source snapshot;
- `reference-checks.json`, `navigation-probe.json`, and their probe scripts;
- the original single pilot and corrected-repeat scripts/results.

Primary run IDs:

- Single: `20261102T040837461819-b43c26e7da1a`
- Fixed: `20261102T035946386388-68b2d60b8ae5`
- Adaptive: `20261102T040308213084-dd3dab91e5f6`

User `config.ini`/`models.ini` changes were preserved and remain uncommitted.
