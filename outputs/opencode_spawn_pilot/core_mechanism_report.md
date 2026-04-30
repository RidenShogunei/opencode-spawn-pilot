# Core Mechanism Report — Forced Subagent Spawning on MuSiQue

**Version**: v1.0
**Source**: Stage 1B experiment (40 runs, 10 MuSiQue tasks × 4 systems)
**Date**: 2026-04-30
**Commit**: `f0c5b0b` (tagged `stage1b-musique-mechanism`)

---

## 1. Core Claim and Falsification Conditions

### 1.1 Central Thesis

> **Forced subagent spawning on multi-hop QA reveals that the bottleneck is not evidence discovery but evidence integration.** Explore agents reliably find gold-supporting paragraphs, but the primary Build agent does not reliably convert that evidence into correct answers. Adding a General review agent or a structured evidence table does not automatically fix this integration bottleneck.

### 1.2 Falsification Conditions

This claim is falsified if **any** of the following hold:

| # | Falsification Condition | Expected Result | Why It Would Refute |
|----|------------------------|----------------|---------------------|
| F1 | Explore fails to find gold paragraphs | M1 < 0.3 overall | If subagent can't find evidence, the bottleneck is discovery, not integration |
| F2 | Build integrates Explore evidence without error | M3 = 0 or M5 = 0 | If evidence is found and no integration failure occurs, bottleneck is elsewhere |
| F3 | S3 General improves over S2 | S3 accuracy > S2 accuracy | If General review reliably corrects integration errors, bottleneck is in evidence review |
| F4 | S4 Table improves over S2 | S4 M3 < S2 M3 | If structured evidence representation reliably fixes integration, representation is the bottleneck |

**Stage 1B results against falsification conditions**:

- F1: **REJECTED** — M1 = 64–67% across S2/S3/S4, Explore reliably finds evidence
- F2: **REJECTED** — M5 = 7 cases (17.5% of all runs), integration failures exist
- F3: **CONFIRMED** — S3 (60%) < S2 (70%), General is net-negative
- F4: **CONFIRMED** — S4 M3 = 30% > S2 M3 = 10%, Table does not reduce integration error

### 1.3 What This Proves

The pattern F1-rejected + F2-rejected + F3/F4-confirmed establishes:

```
Evidence Discovery: EXPLORE works (M1 = 64–67%)
Evidence Integration: BUILD fails (M5 = 7 cases, M3 = 10–30%)
Ablation interventions: GENERAL and TABLE do not fix integration
```

This confirms the bottleneck is at **Build-level reasoning fidelity**, not at the subagent level.

---

## 2. System-Level Results

### 2.1 Primary Metrics

| System | Accuracy | M1 (Evidence Recall) | M3 (Integration Error) | M5 (Explore Found, Build Failed) | Tokens/Correct |
|--------|:--------:|:--------------------:|:---------------------:|:--------------------------------:|:--------------:|
| **S1** Build-only | 40% | — | — | 0 | 167k |
| **S2** Explore→Build | **70%** | 67% | 10% | 1 | 130k |
| **S3** Explore→General→Build | 60% | 64% | 30% | 3 | 126k |
| **S4** Explore→Table→Build | 60% | 63% | 30% | 3 | 113k |

**Key observations**:
- S2 achieves the highest accuracy (70%) with the simplest architecture
- Adding General (S3) drops accuracy by 10 percentage points
- Adding a structured Table (S4) also drops accuracy by 10 percentage points
- Tokens per correct are lowest for S4 (most token-efficient), but this efficiency is irrelevant if accuracy is lower

### 2.2 Per-Hop Breakdown

| Hop Count | S1 | S2 | S3 | S4 |
|-----------|:--:|:--:|:--:|:--:|
| 2-hop | 33% | 67% | 67% | 67% |
| 3-hop | 33% | 67% | 33% | 33% |
| 4-hop | 50% | 75% | 75% | 75% |

- **2-hop tasks**: S2/S3/S4 all achieve 67%, all superior to S1's 33%
- **3-hop tasks**: S2 maintains 67%, S3/S4 collapse to 33%
- **4-hop tasks**: All systems perform better than 2-hop, suggesting 4-hop tasks have more extractable evidence

### 2.3 M1 Recall Details

| System | M1 Avg | 2-hop | 3-hop | 4-hop |
|--------|:------:|:-----:|:-----:|:-----:|
| S2 | 67% | 67% | 67% | 67% |
| S3 | 64% | 67% | 50% | 75% |
| S4 | 63% | 67% | 50% | 75% |

M1 is consistently above 60% across systems, confirming **Explore finds evidence**.

---

## 3. Evidence Discovery: Explore Hits Gold Paragraphs

### 3.1 M1 Summary

M1 (Evidence Recall) measures what fraction of gold-supporting paragraphs Explore identifies. Across all S2/S3/S4 runs:

- **Overall M1**: 63–67%
- **Per-hop M1**: 50–75%
- **No system shows systematic failure to find evidence**

This refutes the hypothesis that the bottleneck is evidence discovery. If Explore were failing to find evidence, M1 would be near zero. Instead, M1 is consistently in the 60s.

### 3.2 What This Means

```
M1 = 63–67%
↓
Explore finds gold paragraphs at reasonable rate
↓
Evidence discovery is not the primary bottleneck
↓
The problem is what happens AFTER evidence is found
```

---

## 4. Integration Bottleneck: M5 = 7 Cases

### 4.1 M5 Definition

M5 (Explore Found, Build Failed) counts runs where:
1. Explore found at least one gold-supporting paragraph (M1 > 0)
2. Build was given Explore's output
3. Build still answered incorrectly

These are the most informative cases because they isolate the integration failure from evidence discovery failure.

### 4.2 M5 Count by System

| System | M5 Count | Total Runs | M5 Rate |
|--------|:--------:|:----------:|:-------:|
| S1 Build-only | 0 | 10 | 0% |
| S2 Explore→Build | 1 | 10 | 10% |
| S3 Explore→General→Build | 3 | 10 | 30% |
| S4 Explore→Table→Build | 3 | 10 | 30% |

**7 out of 30 S2/S3/S4 runs (23%) exhibit M5 failure.**

---

## 5. M5 Case Classification

### 5.1 Summary Table

| Case | Task | System | Gold | Predicted | Root Cause Class |
|------|------|--------|------|-----------|-----------------|
| 1 | 4hop_009 | S2 | Rio Linda | FINDINGS_COMPLETE | Context overflow — truncated evidence delivery |
| 2 | 3hop_005 | S3 | January 2015 | 2014 | General polluted — wrong "correction" |
| 3 | 3hop_006 | S3 | Warner Music Group | James Conkling | General confirmed — wrong chain accepted |
| 4 | 4hop_009 | S3 | Rio Linda | "not available" | General dismissed — Build gave up |
| 5 | 3hop_005 | S4 | January 2015 | "Prior to 1954" | Table distorted — reasoning collapsed |
| 6 | 3hop_006 | S4 | Warner Music Group | James Conkling | Table replicated — wrong chain looked authoritative |
| 7 | 4hop_009 | S4 | Rio Linda | "not available" | Table truncated — hop 4 missing, Build gave up |

### 5.2 Failure Mode Taxonomy

| Failure Mode | Count | Cases | Description |
|-------------|:-----:|-------|-------------|
| **Context overflow** | 1 | Case 1 | Build received concatenated truncated output; terminated with FINDINGS_COMPLETE |
| **General polluted** | 1 | Case 2 | General's "correction" changed correct date to incorrect date |
| **General confirmed wrong** | 1 | Case 3 | General confirmed Explore's wrong chain; Build trusted and repeated error |
| **General dismissed** | 1 | Case 4 | General said evidence insufficient; Build abandoned valid evidence |
| **Table distorted** | 1 | Case 5 | Table collapsed temporal distinction ("take control" → "already had control") |
| **Table replicated** | 1 | Case 6 | Table made wrong chain look authoritative; Build trusted it |
| **Table truncated** | 1 | Case 7 | Table explicitly stated hop 4 missing; Build accepted and gave up |

### 5.3 Key Observation: M1 = 1.0 Does Not Imply Answerability

Cases 2, 3, and 6 all have M1 = 1.0 (all gold paragraphs found) yet still failed. This is a critical finding:

```
M1 = 1.0
≠
Question is answerable from found evidence
```

The gold paragraphs were found, but the **causal or temporal reasoning chain** across those paragraphs was broken:
- Case 2: Found Para 8 (Republicans pre-1954), Para 6 (Congress rules), Para 5/11 (Republican control dates) — but couldn't distinguish "had majorities" from "took control"
- Case 3: Found Para 0 (Terry Dexter), Para 7 (Warner Bros. Records), Para 19 (James Conkling founder) — but missed the corporate parent (Warner Music Group) relationship
- Case 6: Same as Case 3

**This means M1 is a necessary but not sufficient condition for success.**

---

## 6. S3 and S4 as Diagnostic Ablation, Not Mitigation

### 6.1 What S3 (General) Demonstrates

S3 adds a General review agent between Explore and Build. Its purpose in this experiment is **diagnostic**: to test whether a second reasoning pass can catch and correct integration errors.

**Result**: S3 accuracy (60%) < S2 accuracy (70%), M3 = 30% > S2 M3 = 10%.

**What this tells us**:
- General does not reliably correct integration errors
- General can actively introduce errors (Cases 2, 3, 4)
- A second reasoning pass without grounded verification is insufficient

General is a **negative control**. It confirms that adding a reasoning layer without grounding does not solve the integration problem.

### 6.2 What S4 (Table) Demonstrates

S4 replaces Explore's free-text output with a structured evidence table. Its purpose is **diagnostic**: to test whether structured representation reduces integration errors.

**Result**: S4 M3 = 30% > S2 M3 = 10%, S4 accuracy (60%) < S2 accuracy (70%).

**What this tells us**:
- Structured representation does not reduce integration errors
- A clean table amplifies whatever errors exist in Explore's reasoning (Cases 5, 6)
- The problem is not evidence representation; it is evidence reasoning fidelity

S4 is a **negative control**. It confirms that formatting evidence more cleanly does not solve the integration problem.

### 6.3 Why These Are Ablations, Not Mitigations

A mitigation would be an intervention designed to reduce integration errors. S3 and S4 are designed to **measure** whether a specific intervention helps. They failed to help, which is a valid experimental outcome that tells us:

```
Integration bottleneck is NOT fixed by:
- Adding a reasoning review layer (S3)
- Changing evidence representation format (S4)

Integration bottleneck IS characterized by:
- Build-level reasoning fidelity (M5 cases)
- Context-dependent comprehension (overflow)
- Trust calibration (Build over-trusts prior agents)
```

---

## 7. Conclusion

### 7.1 The Bottleneck Is Not Subagent Utilization

S2 (Explore→Build) achieves 70% accuracy — the highest among all systems. This proves that **subagent spawning is not inherently harmful**. The question is not whether to spawn subagents, but how to ensure their output is faithfully integrated.

### 7.2 The Bottleneck Is Build-Level Reasoning Fidelity

The 7 M5 cases reveal a consistent pattern:

```
Explore finds evidence
↓
Something goes wrong in the handoff or Build reasoning
↓
Build produces wrong answer or no answer
```

The failures are not random. They cluster around:
1. **Context overflow** (Case 1): Evidence delivery mechanism breaks down
2. **Trust calibration** (Cases 2, 3, 4, 6): Build trusts prior agent framing without independent verification
3. **Reasoning collapse** (Case 5): Temporal/causal distinctions lost in representation
4. **Premature abandonment** (Cases 4, 7): Build accepts "evidence not found" without retry

### 7.3 The Optimization Target

The results support the following refinement of the research question:

```
BEFORE Stage 1B:
"Does spawning subagents improve multi-hop QA?"

AFTER Stage 1B:
"What integration protocol ensures that Explore's evidence
is faithfully converted into Build's reasoning chain?"
```

This shifts the optimization target from **whether to spawn** (which is binary and already validated) to **how to integrate** (which is the actual bottleneck).

---

## 8. Future Work: S5 and S6 as Mitigation Protocols

Stage 1B establishes the mechanism. S5 and S6 (drafted in `SPEC.stage1c.md`) propose specific mitigation interventions targeting the integration bottleneck:

### S5: Explore → Table + Rationale → Build

- **Idea**: Add natural-language rationale to each table row, forcing Explore/Table to articulate *why* an evidence piece connects to the next hop
- **Targeted failure modes**: Reasoning collapse (Case 5), Table replicated wrong chain (Case 6)
- **Risk**: Rationale can encode wrong reasoning just as easily; adds noise without grounding

### S6: Explore → Build with Mandatory Evidence Citation

- **Idea**: Force Build to cite specific paragraph IDs before answering, requiring explicit grounding
- **Targeted failure modes**: Trust calibration (Cases 2, 3, 4, 6), premature abandonment (Cases 4, 7)
- **Risk**: If Explore's paragraph IDs are incomplete or wrong, faithful Build citing them still fails

**Status**: S5 and S6 are **designed but not executed**. They represent the next phase of research (mitigation testing) and are out of scope for the current mechanism-proving stage.

---

## Appendix: M5 Case Details

Full M5 case audit available at: `outputs/opencode_spawn_pilot/m5_case_audit.md`

Key details per case:

| Case | Explore Evidence | Build Output | Gold Answer | What Happened |
|------|-----------------|-------------|-------------|---------------|
| 1 | Found 3/4 gold paras; truncated at hop 4 | FINDINGS_COMPLETE | Rio Linda | Context overflow; termination signal treated as answer |
| 2 | Found all paras; chain: pre-1954 → rules → control 2014/2016 | 2014 | January 2015 | General "corrected" 2015→2014; Build accepted |
| 3 | Found all paras; chain: performer → label → founder | James Conkling | Warner Music Group | General confirmed wrong chain (founder vs. owner) |
| 4 | Found 3/4 paras; hop 4 missing | "not available" | Rio Linda | General dismissed evidence; Build gave up |
| 5 | Found all paras; temporal reasoning required | "Prior to 1954" | January 2015 | Table collapsed "take control" into "already had control" |
| 6 | Found all paras; missing corporate relationship | James Conkling | Warner Music Group | Table made wrong chain authoritative; Build trusted it |
| 7 | Found 3/4 paras; hop 4 not in table | "not available" | Rio Linda | Table explicitly said hop 4 missing; Build accepted and gave up |
