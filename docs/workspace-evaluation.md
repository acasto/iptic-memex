# Initial workspace investigation

These are exploratory measurements, not a model ranking or a benchmark.
Investigations used GLM-5.3-Flash-EXL3 through the local `glm-5.3` alias.
Synthesis was compared with `gpt-6.1` (configured model name `gpt-6.1-sol`)
through OpenAI's Responses API after explicit approval to send the saved evidence.

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
All four calls used that identical message (SHA-256 prefix `e6f081e6b6533c5c`) and an
explicit 8,192-token output allowance. No workers were rerun for these comparisons.
The old system prompt came from commit `03f1368`; the revised one is
`prompts/workspace/synthesize.md`. No model-specific addenda were introduced.

| Model | Synthesis prompt | Model-call time | Input tokens | Output tokens | Answer words |
| --- | --- | ---: | ---: | ---: | ---: |
| GLM 5.3 Flash | Original | 20.5 s | 4,487 | 1,134 | 645 |
| GLM 5.3 Flash | Revised | 16.8 s | 4,607 | 961 | 587 |
| GPT-6.1 | Original | 9.0 s | 4,449 | 600 | 353 |
| GPT-6.1 | Revised | 10.7 s | 4,567 | 586 | 391 |

GLM's revised answer was somewhat shorter, but retained an unnecessary testing
disclaimer and substantial configuration detail. GPT-6.1's answers better matched
the broad explanatory question with either prompt: purpose, workflow, usage, and
the practical limits. Its revised answer ended with a relevant limitation about
unproven quality benefits rather than auditing which implementation files a worker
had not read. Both models remained broadly consistent with the historical notes.

The system prompt checksums match across models for each prompt version
(`11f66d09c3d3f9a6` original, `87fd0c468fecc3f4` revised). GPT used its configured
medium reasoning effort. Provider-specific defaults and inference settings were
not normalized, and the older GLM records do not freeze all generation settings.
Token counts are provider-reported. Calls ran at different times; the two GPT calls
ran concurrently. One sample per cell cannot establish a general quality or speed
advantage, and the revised prompt did not uniformly shorten responses.

The reconstructed input differs from the historical synthesis request, so these
are not exact reproductions of the original answer. The results support testing
GLM workers with optional GPT-6.1 synthesis, while keeping the shared engine and
prompts model-neutral. They do not establish how GPT-6.1 would investigate or
collaborate as a worker, or justify further shared-prompt restrictions.

Replay runs, in table order:

- `20261028T023713556276-6b0ad42a3c88`
- `20261028T023801160163-bbcd1e33a3c4`
- `20261028T025502887454-82dd0d49571a`
- `20261028T025503201768-0c0a2e44973c`

The first two GPT requests failed because CLI `-l` supplied `max_tokens` as a
string, which Responses rejected for `max_output_tokens`. CLI parsing now produces
a positive integer before any session is created; regression tests cover normal
completion, agent and workspace entry points. Successful rows above are the retries.

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

Repeat the synthesis comparisons on additional questions. Then compare full investigations on
the same small set of questions, including a single-worker baseline; replay alone
cannot tell us whether multiple workers improve investigation quality. Measure
correctness and useful evidence as well as time, turn counts and cache-aware usage.
Keep model-specific steering in conditional `supplemental_prompt` addenda when
repeated evidence warrants it.
