# Worked example — capturing a paper's *internals*

This example exists to show the agent (and you) what "Wikidata, not Wikipedia"
means in practice. It takes one short paper and turns it into structured
knowledge: not a summary of what the paper is *about*, but the concrete
variables, factors, models, equations, assumptions and quantitative claims the
paper actually asserts.

### Files

| File | What it is |
|---|---|
| `paper.md` | A short **fictional** paper on wheel-inertial factor-graph SLAM for skid-steer UGVs. Deliberately compact, but with real domain structure. |
| `build_example.sh` | The full ingest → extraction → index → search sequence, with the LLM reasoning "frozen" into literal `kb` commands. |

### Running it

```bash
./examples/factor-graph-slam/build_example.sh /tmp/demo-kb
```

If the `kb` console script is not on your `PATH`, point the script at an
interpreter instead:

```bash
KB=".venv/bin/python -m kb.cli.main" \
  ./examples/factor-graph-slam/build_example.sh /tmp/demo-kb
```

Set `KB_EMBEDDER_BACKEND=hash` to use the deterministic offline embedder rather
than downloading a local model.

`tests/test_e2e_smoke.py` runs this exact script and then asserts on the
resulting graph, so the example cannot silently rot as the CLI evolves.

### What gets built

From a single ~70-line document the script produces roughly 25 domain entities,
30 relations, and 3 reified claims — all carrying `origin` and `sources`:

- **Variables** — the unknowns being estimated: body pose `x_k` (domain `SE(3)`,
  dimension 6) and IMU bias `b_k` (domain `R^6`).
- **Factors** — IMU preintegration, slip-aware wheel odometry, and pose prior,
  each with a `factor_type` and wired to the variables they `CONNECTS`.
- **Noise models** — a Gaussian IMU model and a Huber robust loss, attached via
  `HAS_NOISE` to the factors and sensor that use them.
- **Models and machinery** — a slip-aware differential-drive `MotionModel`, a
  `StateEstimator`, the `iSAM2` `Solver`, and `GTSAM` as the implementing `Tool`.
- **Formal content** — the MAP objective as an `Equation` (in LaTeX) and two
  explicit `Assumption` nodes (locally planar terrain, white Gaussian IMU noise).
- **Evaluation context** — the `Rellis-3D` dataset and the `ATE` metric.
- **Claims** — three reified assertions with qualifiers and confidence, each
  `SUPPORTS`-linked to the source document.

### Inspecting the result

Walk the factor graph's structure:

```bash
kb graph query 'MATCH (g:FactorGraph)-[:HAS_FACTOR]->(f:Factor)-[:CONNECTS]->(v:Variable)
                RETURN g.name, f.name, v.name' --kb /tmp/demo-kb
```

```
WIFG-SLAM factor graph | IMU preintegration factor        | body pose x_k
WIFG-SLAM factor graph | IMU preintegration factor        | IMU bias b_k
WIFG-SLAM factor graph | Slip-aware wheel-odometry factor | body pose x_k
WIFG-SLAM factor graph | Pose prior factor                | body pose x_k
```

See every claim with its evidence and confidence:

```bash
kb graph query 'MATCH (d:Document)-[:SUPPORTS]->(c:Claim)-[:ABOUT]->(s)
                RETURN d.id, c.predicate, c.qualifiers, c.confidence, s.name' \
  --kb /tmp/demo-kb
```

Or search across documents *and* entities at once:

```bash
kb search 'slip aware wheel odometry' --kb /tmp/demo-kb --json
```

### Why this shape

Note what is **absent**: there is no node whose content is "this paper is about
SLAM". The document is present as provenance, and everything worth knowing has
been lifted into typed entities and claims that can be queried, compared across
papers, and contradicted by a future source. That is the whole point — a prose
summary would have been a failed extraction.

Because claims are reified, a second paper reporting a different ATE for the
same method does not overwrite anything: it adds its own `Claim` node with its
own sources and confidence, and the disagreement becomes visible and
queryable rather than lost.
