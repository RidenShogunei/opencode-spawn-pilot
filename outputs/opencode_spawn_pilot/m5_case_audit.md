# M5 Case Audit — Stage 1B

**Date**: 2026-04-30
**Source**: 7 M5-flagged runs from 40-run Stage 1B experiment (MuSiQue 10 tasks × 4 systems)
**Definition**: M5 = Explore found the gold evidence (M1 recall > 0) but Build answered incorrectly

---

## Summary Table

| Case ID | System | Gold Answer | Predicted | M1 | M5 Root Cause |
|---------|--------|------------|-----------|----|---------------|
| 4hop_009 | S2 Explore→Build | Rio Linda | FINDINGS_COMPLETE | 0.75 | **Context overflow** — Build saw truncated/incomplete Explore output |
| 3hop_005 | S3 Explore→General→Build | January 2015 | 2014 | 1.0 | **General polluted chain** — wrong "correction" misled Build |
| 3hop_006 | S3 Explore→General→Build | Warner Music Group | James Conkling | 1.0 | **General confirmed wrong chain** — Build didn't verify |
| 4hop_009 | S3 Explore→General→Build | Rio Linda | "not available in documents" | 0.75 | **General dismissed valid evidence** — Build gave up |
| 3hop_005 | S4 Explore→Table→Build | January 2015 | "Prior to the 1954 elections" | 0.67 | **Table lost critical reasoning** — "take control" became "already had control" |
| 3hop_006 | S4 Explore→Table→Build | Warner Music Group | James Conkling | 1.0 | **Incomplete table** — Build trusted table without verifying full chain |
| 4hop_009 | S4 Explore→Table→Build | Rio Linda | "do not contain information" | 1.0 | **Table truncated at hop 3** — hop 4 evidence not in table, Build gave up |

---

## Case 1: musique_4hop_009 — S2 (Explore→Build)

**Gold Answer**: Rio Linda
**Predicted**: FINDINGS_COMPLETE
**M1**: 0.75 (found 3/4 gold paragraphs)

### Explore Findings
Explore correctly traced hops 1–3:
- **Hop 1**: Planes bound for California (Para 10)
- **Hop 2**: Samuel Brannan, California Gold Rush figure (Para 18)
- **Hop 3**: Samuel Brannan worked in Sacramento/ San Francisco (Para 18)

**Hop 4 missing**: What shares a border with that city — Explore was mid-trace when context overflowed.

### What Build Received
`prompt_build.txt` shows the Explore output was **concatenated in a confusing blob** mixing two incomplete reasoning sessions (the second session restarted due to the overflow). Build saw:
- The original incomplete trace (ending mid-hop)
- A restart that re-summarized hop 1–2
- A final attempt at hop 4 that said Oak Lawn "shares borders with Chicago"

### Root Cause Classification
**Context overflow — Build saw incomplete evidence**

Build output `FINDINGS_COMPLETE` (the Explore termination signal) as its final answer, indicating it received the concatenated blob and could not form a coherent answer.

### Verdict
Explore found 3/4 gold paragraphs. Build output `FINDINGS_COMPLETE` — the termination signal was misinterpreted as an answer. This is an **integration protocol failure** (M3-adjacent): the information was present but the *format* of the handoff caused Build to output the wrong thing.

---

## Case 2: musique_3hop_005 — S3 (Explore→General→Build)

**Gold Answer**: January 2015
**Predicted**: 2014
**M1**: 1.0

### Explore Findings
- Para 8: Republicans had majorities prior to 1954
- Para 6: Congress determines rules (Article One, Section 5)
- Para 5/11: Republicans took control of both chambers in 2014 (114th Congress) and maintained it in 2016 (115th)

### General Agent Review
General raised a **false "correction"**: claimed Explore incorrectly identified "Congress" as the rule-determining organization (it said each chamber sets its own rules), called Explore's paragraph numbering "completely off," and dismissed the 2016 date as wrong.

General's actual conclusion: "The answer is **2014**."

### What Build Received
Build was told by General: 2014 is correct. Build re-read paragraphs and confirmed 2014.

### Gold Answer Analysis
The question asks: *"When did the party that had majorities PRIOR TO 1954 take CONTROL of the organization?"*

- Republicans had majorities heading INTO 1954 (not "took control" then)
- They **lost** control in 1954
- They **regained** control in January 2015 (when the 114th Congress convened)

So the correct answer is **January 2015**, not 2014.

### Root Cause Classification
**General polluted the correct chain**

Explore's answer (2014/2016) was off by 1 year. General's "correction" confirmed 2014 as correct and dismissed the correct date. Build trusted General and output 2014.

**If General had been absent**: Build might have traced the same off-by-1 error, but the failure mode would be different — Explore's raw evidence was not precise enough for this question.

### Verdict
M1 = 1.0 means Explore found the evidence. But the chain from "Republicans held majorities heading into 1954" to "they took control in January 2015" requires understanding that losing = losing control, not "already had control." **General reinforced the wrong interpretation.**

---

## Case 3: musique_3hop_006 — S3 (Explore→General→Build)

**Gold Answer**: Warner Music Group
**Predicted**: James Conkling
**M1**: 1.0

### Explore Findings
- Para 0: "Better Than Me" performed by Terry Dexter
- Para 7: Terry Dexter signed with **Warner Bros. Records**
- Para 19: Warner Bros. Records founder is **James Conkling**

Chain: Terry Dexter → Warner Bros. Records → James Conkling

### General Agent Review
General "verified" the chain: "CONFIRMED — all three steps accurate."

But the **gold answer is Warner Music Group**, not James Conkling. The question asks for the **label owner**, not the label founder. Warner Music Group (WMG) is the parent/owner of Warner Bros. Records. The documents likely contain this relationship (Para 19 mentions James Conkling as founder, but Para 22 might establish the WMG relationship).

### What Build Received
Build was told by General the chain was correct. Build re-read documents and output "James Conkling."

### Root Cause Classification
**Build trusted General's "verification" without re-checking the actual question**

The chain Explore found was: performer → label → founder. But the question asks for the **owner of the record label** — which is Warner Music Group (a corporate parent), not the founder. Explore didn't find the corporate relationship. General confirmed the wrong chain was correct.

### Verdict
**Incomplete evidence + General added false confidence.** M1 = 1.0 because Explore found relevant paragraphs, but those paragraphs didn't contain the complete answer (corporate ownership hierarchy). General plastering "CONFIRMED" on an incomplete chain is the integration failure.

---

## Case 4: musique_4hop_009 — S3 (Explore→General→Build)

**Gold Answer**: Rio Linda
**Predicted**: "The information is not available in the documents"
**M1**: 0.75

### Explore Findings
Same as Case 1: Hop 1–3 traced correctly, hop 4 missing.

### General Agent Review
General **dismissed** the entire chain as "not sufficient to answer the question" and concluded the documents don't contain the answer.

### What Build Received
Build was told by General the information is not available. Build gave up.

### Root Cause Classification
**General caused Build to abandon valid evidence**

Explore found 3/4 gold paragraphs. General said "this isn't enough." Build believed General and output that the information is not available.

### Verdict
**General is harmful here.** It incorrectly assessed that the evidence was insufficient and caused Build to give up on a question that was answerable (M1 = 0.75, only hop 4 missing). The 4-hop was hard but not impossible.

---

## Case 5: musique_3hop_005 — S4 (Explore→Table→Build)

**Gold Answer**: January 2015
**Predicted**: "Prior to the 1954 elections"
**M1**: 0.67

### Explore Findings
Same as S3: Para 8 (Republicans pre-1954 majorities), Para 6 (rules by Congress), Para 5/11 (Republican control 2014/2016).

### Evidence Table
```
| Para ID | Key Fact | Connects To |
|---------|----------|-------------|
| 8 | Republicans had majorities prior to 1954 | hop 1 |
| 6 | Congress determines rules | hop 2 |
| 6 | The organization above is where Republicans held control PRIOR TO 1954 → ANSWER | hop 3 → ANSWER |
```

### What Build Received
The table **rewrote the question**: "take control" became "held control prior to 1954." The table collapsed hop 3 into "already had control before 1954" rather than "took control AFTER losing."

Build read the table and answered: "Prior to the 1954 elections" — directly from the table.

### Root Cause Classification
**Table representation distorted the reasoning chain**

The TABLE_PROMPT forced all evidence into a flat 3-column format. The subtle but critical distinction between:
- "The party had majorities heading into 1954" (static state)
- "When did they take control" (dynamic event after a loss)

...was flattened into one row. Build read the table and extracted the static-state phrase as the answer.

### S4 vs S3 Comparison
S3 also got this wrong (predicted 2014), but for different reasons:
- S3: General's "correction" steered Build to 2014
- S4: Table collapsed "take control" into "already had control" → Build output the phrase from the table

### Verdict
**Table representation lost the temporal/causal reasoning.** "Take control" requires understanding that the party lost majority in 1954, then regained it in January 2015. The table presented this as a static property.

---

## Case 6: musique_3hop_006 — S4 (Explore→Table→Build)

**Gold Answer**: Warner Music Group
**Predicted**: James Conkling
**M1**: 1.0

### Explore Findings
Same as S3: Terry Dexter → Warner Bros. Records → James Conkling.

### Evidence Table
The table was short — same 3-paragraph chain as S3. No additional corporate ownership information was included.

### What Build Received
Table: performer → label → founder. Build trusted the table and answered James Conkling.

### Root Cause Classification
**Incomplete table + Build trusted table without verification**

Same as S3's Case 3, but worse: in S3, Build re-read documents before answering. In S4, Build directly trusted the table output. The table made the wrong chain look authoritative.

### Verdict
**Table did not add value** over S3's Explore→General. The additional step (label founder vs. label owner) was never in the evidence, so the table could not transmit it. Build trusting the table as ground truth is an integration failure.

---

## Case 7: musique_4hop_009 — S4 (Explore→Table→Build)

**Gold Answer**: Rio Linda
**Predicted**: "The available documents do not contain information about what shares a border with San Francisco"
**M1**: 1.0

### Explore Findings
Hop 1: California. Hop 2: Samuel Brannan. Hop 3: Sacramento/San Francisco.

### Evidence Table
```
| Para ID | Key Fact | Connects To |
|---------|----------|-------------|
| 10 | Four airliners on 9/11 bound for California | hop 1 → ANSWER |
| 18 | Samuel Brannan, California Gold Rush figure, San Francisco newspaper publisher | hop 2 |
| 18 | Gold confirmed in March 1848 by Samuel Brannan | hop 2 |
```

The table **stopped at hop 3** and explicitly stated hop 4 information is missing. Build received this and output: "The available documents do not contain information about what shares a border with San Francisco."

### Root Cause Classification
**Table truncated at hop 3 — no hop 4 row was generated**

Unlike S2 where context overflow caused incomplete delivery, S4's Table agent explicitly concluded hop 4 evidence was missing. Build correctly read the table and correctly reported the limitation. But the **gold answer (Rio Linda) was actually findable** — the Table agent should have continued exploring or the table should have included a row noting "no border information found for Sacramento/San Francisco in available paragraphs."

### Verdict
**Systematic hop-count truncation in table representation.** The table format has no mechanism to represent "I couldn't find hop 4" as a meaningful signal to Build. Build took this at face value.

---

## Cross-Case Patterns

### 1. Context overflow kills 4-hop (Cases 1, 4)
S2 and S3 both hit context overflow on 4hop_009, causing truncated evidence delivery. The concatenate-and-restart behavior produces confusing input for Build.

### 2. General is net-negative on hard questions (Cases 2, 3, 4)
General's "error detection" has no calibration — it dismisses correct evidence (Case 4) and confirms wrong chains (Cases 2, 3). On questions where Explore's chain is slightly wrong, General makes it worse. On questions where Explore is right, General either pollutes or doesn't help.

### 3. Evidence table doesn't solve incomplete evidence (Cases 5, 6, 7)
The table faithfully reproduces what Explore found. If Explore found an incomplete chain (missing hop 4, or wrong intermediate step), the table amplifies the incompleteness by making it look structured and authoritative.

### 4. Build trusts its inputs too much
Across all 7 cases, Build accepted the input from the previous agent (Explore+General, or Explore+Table) without independent verification when the input seemed confident. The only self-correction events happened in S2's `build_explore.py` where Build re-read documents — but even then it sometimes missed.

### 5. M1 = 1.0 does not guarantee answerability
Cases 2, 3, 6 have M1 = 1.0 (all gold paragraphs found) but still fail. This means **M1 is a necessary but not sufficient condition** for success. The missing ingredient is the *reasoning chain* — correct paragraph selection does not imply correct causal/temporal reasoning across paragraphs.

---

## Implications for S5 and S6 Design

### S5 (Explore → Table + Rationale → Build)
Must address:
- Rationale must include *why* a paragraph was selected (causal/temporal reasoning), not just what was found
- Table must signal "hop N evidence missing" as a structured field, not absence
- Build must still independently verify, not trust the table

### S6 (Explore → Build must cite evidence IDs)
Must address:
- Build citing IDs forces explicit grounding — reduces hallucination risk
- But if Explore gives wrong IDs or incomplete IDs, Build still fails (Case 6)
- Needs to be combined with a verification loop or confidence signal

### Key Design Requirements
1. **Survivable context overflow**: 4-hop questions exceed 64k context easily. Need a way to compress or checkpoint intermediate reasoning.
2. **Downstream agent independence**: Build must re-read and verify, not trust the prior agent's framing.
3. **No "confidence inflation"**: A General or Table agent that says "CONFIRMED" or produces a clean table should not increase Build's confidence — it should trigger more scrutiny, not less.
