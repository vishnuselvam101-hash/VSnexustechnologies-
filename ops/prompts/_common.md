# VNX-DNA engineering rules (every agent, every task)

You are working on VNX-DNA, a software platform for computational DNA data storage. It is a software simulation and
is not wet-lab validated. These rules override anything in the task text.

1. **Scope.** Work only in the current directory (an isolated git worktree on an `experiment/*` branch). Do not
   push, merge, tag, rewrite history, or touch files outside this worktree.
2. **Tests decide.** "Make it work" never permits skipping, deleting, weakening or `xfail`-ing a test, lowering a
   threshold, or catching and hiding an error. If a test is wrong, say so and explain; do not change it silently.
3. **Every behaviour change ships with tests** (unit, plus property/adversarial where inputs are untrusted).
4. **Never invent numbers.** Report only results you measured in this run, with the command that produced them.
   Benchmarks are produced by scripts; never type a measured value into docs by hand.
5. **Label claims** as ESTABLISHED FACT, PUBLISHED RESULT (with citation), EXPERIMENTAL OBSERVATION (with the
   command and seed), ENGINEERING ASSUMPTION, HYPOTHESIS, UNVERIFIED CLAIM or FUTURE WORK.
6. **No unsupported comparisons.** Never claim VNX-DNA beats another system without a reproducible comparison;
   unavailable competitor results are "NOT REPRODUCIBLE LOCALLY".
7. **No biological, synthesis, sequencing or commercial validation claims** — none exists.
8. **Formats are a compatibility promise.** Do not change on-disk formats (`src/vnxdna/container`, format docs)
   unless the task says so explicitly; V1/V2/V3 archives must keep decoding.
9. **Secrets.** Never read, print or copy `.env`, key files, tokens, `~/.ssh`, or anything under `*secrets*`.
10. **Uncertainty.** If you cannot do the task correctly, stop and write down what is unknown. A clear "not done,
    because…" is a valid result; a fabricated success is not.

Finish with a short report: what changed, which tests you ran and their result, and open questions.
