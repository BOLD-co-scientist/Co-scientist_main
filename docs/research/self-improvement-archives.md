# Self-improvement archives: DGM, HyperAgents, MCTS-AHD — and what coscientist already has

**Written:** 2026-09-06, during the R19 design discussion ("the self-improvement side is too manual and too dumb").
**Companion plan:** [docs/plans/R19-guided-evolution-search.md](../plans/R19-guided-evolution-search.md).
**Scope:** a reference note. It records *how* the three reference systems store and search a tree of agents, verified against the papers and against the local HyperAgents checkout (`../old/HyperAgents`), then maps each concept onto the pieces coscientist has today. Nothing here is a decision; decisions live in the plan file.

---

## 0. The problem this note serves

Self-improvement in coscientist today is three things, all human-driven or single-shot:

| Mechanism | What it does | Direction comes from |
|---|---|---|
| R7 skill library | At session end the supervisor may propose an instruction-only `SKILL.md`; HITL-approved; per-user | the session itself |
| R16 reflection | After a research turn, one LLM call over *that session's* trajectory + harness inventory proposes ≤5 evolution commands as cards | one session |
| Evolution agent | A human-typed (or card-prefilled) command → worktree → edit → tests → `propose_merge` → HITL → merge → `ver/<id>` node (R17) | the human |

Nothing measures whether an evolution helped, nothing conditions on the tree of previous evolutions, nothing plans ahead of the researcher's next need, and nothing proposes *features* (a visualizer, a new interface, a found open-source tool) unless a session visibly failed. The early idea slide that started R19 is transcribed below so the intent is preserved verbatim.

> **"Heuristic Search" for human-guided RSI** — inspiration from AI Scientist, DGM and HyperAgents; runs *in addition to* human-prompted evolution.
> 1. **Sequential goal setting:** (a) what is the problem setting; (b) what related literature is there; (c) what can we learn from that literature, thus methods to hold in the arsenal; (d) human intuition: what method / pipeline / search space works well for this specific problem setting; (e) what deliverables are there, how do we verify the solution. Based on these answers, a set of subgoals is obtained.
> 2. **Use these subgoals to guide the agent-harness evolution:** (a) a meta agent plans how to evolve, the human approves and refines; (b) at certain bifurcations the human can ask to explore both branches, both get added to the evo tree; (c) use the HyperAgents code framework to implement these modifications.

The follow-up thought: fold this into the onboarding phase (O1/O2), where the five-question brief already captures most of item 1.

---

## 1. Darwin Gödel Machine (DGM) — arXiv:2505.22954

### 1.1 What the archive is

The archive is the set of **all** agents ever produced that passed a minimal viability check. It is never pruned. Each archived agent record holds:

- the agent's code (in practice: the patch it added on top of its parent);
- its evaluation results and logs on the benchmark;
- its parent (lineage pointer);
- viability flags (compiled; can still edit code).

**Storage trick.** The full code of a node is *not* stored. A node is rebuilt by taking the root repository and applying the lineage's patches in order, root → node, inside a fresh container. So the "tree" is a set of parent pointers plus per-node patches. In the HyperAgents checkout this is literal:

```
outputs/<run>/
  archive.jsonl                # append-only; each line = {"current_genid": N, "archive": [ids alive so far]}
  gen_<id>/
    metadata.json              # parent_genid, prev_patch_files (lineage), curr_patch_files (this node),
                               # run_eval, run_full_eval, valid_parent, can_select_next_parent, ...
    <domain>_eval/report.json  # the score (the fitness)
    *.diff                     # this node's patch
```

`analysis/visualize_archive.py` rebuilds the DiGraph from `parent_genid` alone. The tree is implicit; the archive file is just an id list.

### 1.2 One iteration

1. **Select a parent** from the eligible archive (eligible = compiled + retains the ability to edit code + has a score).
2. **Diagnose.** The parent reads its *own* benchmark evaluation logs and writes a diagnosis and a "next feature to implement".
3. **Implement.** That proposal is handed back to the parent as a problem statement; it implements the feature into a copy of its own codebase, producing a child.
4. **Check + evaluate in stages** (below). A child that does not compile or cannot edit code is discarded.
5. **Add the child to the archive** regardless of whether it beat its parent. Repeat.

The point of step 5 is *stepping stones*: a temporarily worse node can be the parent of a later better one. Hill-climbing (keep only the best) is exactly what DGM argues against; the archive "allows the parallel exploration of many different paths through the search space".

### 1.3 Parent selection (soft, not greedy)

Selection probability is "roughly proportional to each agent's performance score and inversely proportional to the number of its children", with every eligible node keeping non-zero probability. As implemented in `utils/gl_utils.py::select_parent` (method `score_child_prop`; method `score_prop` omits the child term):

```
h_i  = sigmoid( λ · (score_i − midpoint) )        λ = 10
p_i  ∝ h_i / (1 + children_i)                      normalized over eligible nodes
```

DGM uses midpoint 0.5; the HyperAgents variant sets the midpoint to the mean of the top-3 scores (adaptive, so the sigmoid stays informative when scores cluster). The `1/(1+children)` term is what stops the loop from repeatedly expanding the current best and is the "novelty pressure" of the method.

### 1.4 Staged evaluation (how it stays affordable)

| Stage | SWE-bench | Polyglot | Advance when |
|---|---|---|---|
| 1 | 10 tasks | 10 tasks | compiles + can edit code |
| 2 | 50 tasks | 50 tasks | stage-1 score above 40 % |
| 3 | 200 tasks | — | stage-2 above 40 % **and** in the top 2 of the archive |

In the local code the 0.4 threshold appears as `test_more_threshold = 0.4  # same setting as that in DGM`, and `run_full_eval` on the node records whether the larger evaluation ran; a node evaluated only on the small subset has its score discounted by the subset fraction (`get_saved_score`).

### 1.5 Results and safety (for the record)

SWE-bench 20.0 % → 50.0 %, Polyglot 14.2 % → 30.7 %; improvements transfer across foundation models and to other languages. Safety: sandboxed execution, strict time limits, a complete auditable lineage enabling rollback, human oversight, no deployment into real development environments.

---

## 2. HyperAgents — arXiv:2603.19461 (Meta), local checkout `../old/HyperAgents`

### 2.1 What "self-referential" adds to DGM

DGM's meta-level (how a child is proposed, selected, evaluated) is fixed, hand-written code. HyperAgents make **the task agent and the meta agent one editable program**, so the system can improve *how it improves* ("metacognitive self-modification"): persistent memory for the meta agent, performance tracking, better diagnosis, etc. Those meta-level improvements are shown to transfer across domains (paper review, robotics reward design → olympiad math grading) and to compound across runs.

What stays fixed in the main experiments, deliberately: **parent selection and the evaluation harness** ("a handcrafted parent selection mechanism that is not subject to modification in order to isolate the effects of hyperagent self-modification"); Appendix E.5 reports preliminary results with editable selection.

### 2.2 The mechanics in the checkout

- `generate_loop.py` is the DGM outer loop: `select_parent` → build a container at the parent (apply lineage diffs) → run `MetaAgent.forward(repo_path, eval_path, iterations_left, instruction)` → compile check → staged eval → write `metadata.json` + patch → `update_and_save_archive`.
- `meta_agent.py` is 21 lines: the instruction defaults to *"Modify any part of the codebase at `{repo_path}`."* The meta agent is given the repo, the folder of previous evaluation results, and the number of remaining iterations. That is the whole prompt; everything else is meant to be discovered.
- `select_next_parent.py` lives **inside the repo the agent edits** and is executed inside the container after applying the lineage's diffs (`select_next_parent_container`). That is the hook that makes selection itself evolvable; `valid_parent` and `can_select_next_parent` flags on each node guard the loop when an edited selector breaks.
- Local additions in this checkout (not in the upstream paper): `agent/meta_kickoff.py` composes a **mission + current-status preamble** from a repo snapshot, an eval snapshot and an optional human goal, using a cheap model, and prepends it to the meta agent's instruction; `agent/human_interface.py` + `router.py` implement a **human router** whose `request_human_response` blocks until a human answers, so a fork the agent flags ("If you face a fork between meaningfully different approaches, make your reasoning explicit") can be surfaced.

The kickoff preamble is R16's reflect step relocated to *before* modification. The human router is the "human approves and refines" half of the slide.

---

## 3. MCTS-AHD — arXiv:2501.08603 (ICML 2025)

Monte Carlo Tree Search for LLM-based Automatic Heuristic Design. Different problem (evolving a heuristic function for combinatorial optimization), but it is the reference for **searching a tree of *ideas* whose nodes are cheap to propose and expensive to evaluate**, which is our situation.

### 3.1 Node

Each node stores: the heuristic's executable code `h`; a short natural-language description of the idea (the "thought"); its measured objective `g(h)`; a value `Q`; a visit count `N`. The description is re-derived from the final code by a second LLM call ("thought alignment", ≤3 sentences) so the idea and the implementation cannot drift apart.

### 3.2 Selection

Standard UCT with normalized value and a decaying exploration constant:

```
UCT(c) = (Q(c) − q_min) / (q_max − q_min)  +  λ · sqrt( ln(N(parent) + 1) / N(c) )
λ = λ0 · (T − t) / T,   λ0 = 0.1,   T = total evaluation budget (1,000 in the paper)
```

**Progressive widening:** a node may receive new children only while `floor(N(node)^α) ≥ |children(node)|`, α = 0.5. A node must be *visited* (its subtree evaluated) before it is allowed to grow further.

### 3.3 Expansion is a typed action set, not one prompt

| Action | What the LLM is asked to do |
|---|---|
| **i1** initialization | write a new heuristic from scratch |
| **m1** mutation | change the mechanism / formulas of the parent |
| **m2** mutation | change the parent's parameters only |
| **e1** crossover (root) | blend 2–5 heuristics drawn from *different subtrees*; triggered at the root by widening |
| **e2** crossover (elite) | combine the parent with a reference from the elite set `E` (top-10 so far) |
| **s1** path reasoning | analyse every unique heuristic on the root → leaf path and propose the next step of that line of thought |

Per expansion: `2k + 2` children (k of each mutation, one e2, one s1).

### 3.4 Simulation and backpropagation

No rollout: a child is evaluated directly on the dataset, `Q(leaf) ← g(h)`, `N(leaf) ← 1`. Backpropagation:

```
Q(node) ← max over children Q(child)
N(node) ← sum over children N(child)
```

Max-backup means a branch that produced one excellent descendant stays attractive even if most of its children were poor, which is the tree-search analogue of DGM's stepping stones.

### 3.5 Why it is the right third reference

DGM stores only *implemented and evaluated* agents. Our pipeline wants to *propose before implementing* and let a human choose. MCTS-AHD's node = thought + code split, its typed actions, the s1 path-conditioning, progressive widening and max-backup are exactly the bookkeeping that makes "propose → human picks → implement → measure → backpropagate" a coherent search rather than a stream of suggestions.

---

## 4. Side by side

| | DGM | HyperAgents | MCTS-AHD |
|---|---|---|---|
| Node | full agent (patch on parent) + eval logs | same | heuristic code + thought + `g`, `Q`, `N` |
| Storage | `archive.jsonl` + `gen_<id>/metadata.json`, parent pointer, patch chain | same | explicit tree in memory / logs |
| Selection | `sigmoid(λ(s−m)) / (1+children)`, sampled | same (fixed in main runs; editable variant in appendix) | UCT, decaying λ, progressive widening |
| Expansion | parent reads own logs → "next feature" → implements | same, with an editable meta agent | six typed actions, `2k+2` children |
| Evaluation | staged benchmark subsets | staged subsets, `valid_parent` | direct evaluation, budget `T` |
| Backprop | none (flat archive) | none | `Q = max`, `N = sum` |
| What can evolve | task agent only | task agent + meta agent (+ selection in appendix) | the heuristic only |
| Human role | oversight | oversight; local router for forks | none |

---

## 5. What coscientist already has, in these terms

| Concept | coscientist piece | State |
|---|---|---|
| Archive of versions | R17: `ver/<id>` tags + `state/archive/evolutions/<id>/{meta.json, diff.patch, rationale.md, smoke.log, decision.json}` + `index.json` (`scaffold/archive.py`) | built; backfill of pre-R17 nodes pending |
| Parent pointer | `meta.json.base_sha` (git sha of the base commit); the git DAG itself | built; resolving `base_sha` → parent *version id* is a small lookup to add |
| Materializing a node | `git checkout` of the tag (`POST /versions/{id}/activate`), detached, `state/` untouched | built and stress-verified |
| Expanding from a node | `POST /evolution/commands {command, base}` → worktree off that commit → `propose_merge` | built |
| Compile check | strict-path smoke + golden-fixture compat gate in `propose_merge` (auto-reject before HITL) | built |
| Diagnosis / "next feature" | R16 `research/reflect.py` + `prompts/evo_reflect.md` → `reflection.json` → suggestion cards | built; conditions on **one session**, never on the tree |
| Kickoff preamble (mission + status) | O1 brief as the supervisor's first turn; O2 five-question brief + advisor (planned) | partial |
| Crossover with another lineage | O2 harness / tool import from another tenant's version (planned) | planned |
| Evaluation harness for A/B | R15 `autonomous: true` replays a session with HITL auto-answered (merges excluded) | built, API-only |
| Fitness on a node | — | **missing** |
| Selection rule | human picks; no policy | **missing** |
| Proposer conditioned on tree + goals | — | **missing** |
| Goal model of the researcher's task | O2 brief v2 has Q0–Q4; no ordered subgoals, no "human intuition" field | **partial** |
| Outer loop without a typed command | R16 fires after sessions only | **partial** |
| Capability discovery (find an open-source equivalent) | `deep_research` tool exists for research, not for harness planning | **missing** |
| Editable meta-level | the evolution agent may rewrite `evolution/`, `prompts/`, `roles/`, `tools/` in its tenant repo; the platform (`api/`, `ui3/`) is human-only (R17 Tier-2b); tenant roots are frozen forks | partial by design |

The single most important observation: **the archive is done.** R19 is the layer DGM builds *on top of* the archive (fitness, selection, a tree-conditioned proposer, an outer loop), plus the goal model that DGM gets for free from a fixed benchmark and we must get from onboarding.

---

## 6. Related work referenced elsewhere in the repo

- **Recursive Harness Self-Improvement** (arXiv:2607.15524) — cited by R16 as the eventual target: a proposer conditioned on the whole revision history with pairwise before/after measurement. R19 is the concrete path to that.
- **The AI Scientist** (arXiv:2408.06292) — named on the slide; relevant for the idea-generation + review loop, not for archive structure.
- **Reflexion / ExpeL** — R2's lessons buffer; same-session vs cross-session reflection.

## References

- Zhang, Hu, Lu, Lange, Clune. *Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents.* arXiv:2505.22954.
- Zhang et al. (Meta). *HyperAgents: Self-Referential Self-Improving Agents.* arXiv:2603.19461. Local checkout: `OpenPhil/old/HyperAgents`.
- Zheng, Xie, Wang, Hooi. *Monte Carlo Tree Search for Comprehensive Exploration in LLM-Based Automatic Heuristic Design.* ICML 2025, arXiv:2501.08603.
- Lee, Xu, Seely, Lee, Zaharia, Tang. *Recursive Harness Self-Improvement.* arXiv:2607.15524.
- Lu et al. *The AI Scientist.* arXiv:2408.06292.
