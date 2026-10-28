# Initial workspace investigation

These are exploratory local measurements, not a model ranking or a benchmark.
GLM-5.3-Flash-EXL3 served through the `glm-5.3` alias was used throughout.

## Original explanation run

Question: `Explain the new workspace mode to me.`

Run: `20261028T020835854026-31fd6812d85b`, before the optimization changes.

- Completed in 114.7 seconds: two workers took 9 and 8 turns, followed by synthesis.
- Four saved notes represented two findings and two automatically published finals.
- Both workers received a peer preview and explicitly cited the peer finding in
  their final prose. The old metadata had empty reference lists, understating this
  information flow. Citations establish a connection, not verification or benefit.
- Cumulative input was 182,237 tokens across calls, including 143,552 reported
  cache-hit tokens; output was 5,961 tokens. These are not unique context sizes.
- The answer was substantively useful but emphasized internal settings and added
  broad testing caveats that did little to answer the explanatory question.

## Same-evidence synthesis experiment

The original notes/outcomes were reconstructed into one frozen evidence message.
Both calls used that identical message (SHA-256 prefix `e6f081e6b6533c5c`) and an
explicit 8,192-token output allowance. No workers were rerun for these comparisons.
The old system prompt came from commit `03f1368`; the revised one is
`prompts/workspace/synthesize.md`. No model-specific addenda were introduced.

| Synthesis prompt | Model-call time | Input tokens | Output tokens | Answer words |
| --- | ---: | ---: | ---: | ---: |
| Original | 20.5 s | 4,487 | 1,134 | 645 |
| Revised | 16.8 s | 4,607 | 961 | 587 |

The revised answer was somewhat shorter, but retained an unnecessary testing
disclaimer and substantial configuration detail. One sample per prompt cannot
establish a quality or speed improvement. The reconstructed input also differs
from the historical synthesis request, so these are not exact reproductions of
the original answer. This is insufficient evidence for further shared-prompt
restrictions or a model-specific prompt patch.

Replay runs, in table order:

- `20261028T023713556276-6b0ad42a3c88`
- `20261028T023801160163-bbcd1e33a3c4`

## Updated engine smoke test

A separate, empty workspace asked where worker/synthesis model selection happens
and whether output limits inherit model configuration. Both workers inherited
GLM's configured 32,768-token limit, rather than the previous forced 4,096.

The run returned a labeled partial answer in 159.8 seconds. Both workers exhausted
12 turns; one published a substantive finding in its final turn, while the other
published none. The final synthesis used the saved contribution. There were no
peer citation links or delivered peer previews. Reporting correctly distinguished
the useful finding from the same worker's auto-published final text.

This different question, run during development, is not comparable to the original
run as a performance experiment. It does show why successful CLI delivery, answer
quality, useful publication, and collaboration must be measured separately.

## Next comparisons

Replay the frozen evidence with a second model, holding the evidence and prompt
constant. This isolates synthesis behavior. Then compare full investigations on
the same small set of questions, including a single-worker baseline; replay alone
cannot tell us whether multiple workers improve investigation quality. Measure
correctness and useful evidence as well as time, turn counts and cache-aware usage.
Keep model-specific steering in conditional `supplemental_prompt` addenda when
repeated evidence warrants it.
