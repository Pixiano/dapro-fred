<!-- Vatsal's Mark II plan, shared in-session 2026-09-27; saved verbatim 2026-09-29. Candidate order agreed 2026-09-29: 1 (done) -> 3 -> 5 -> 4 -> 2. -->

# FRED Multimodal Agent Architecture & Model Evaluation

## Full Technical Report

**Status:** Concept / Research Plan
**Purpose:** Consolidate the architecture and evaluation ideas developed during this conversation for the next-generation FRED system.

---

# 1. Executive Summary

The discussion began with Realtime-Venus and the possibility of using its architecture as a reference for building a smaller, FRED-specific multimodal agent system.

The central idea evolved into:

> **Do not simply replace Realtime-Venus with MiniCPM5-2B. Study how Venus gives a language model multimodal, realtime, and agentic capabilities, then use those principles to build a FRED-specific system around a much smaller model.**

The proposed system should be experimentally validated rather than assuming MiniCPM5-2B is automatically the best backbone.

Five candidate systems were identified for a controlled bake-off:

1. **Base Realtime-Venus**
2. **Realtime-Venus-style system with MiniCPM5-2B as the backbone**
3. **Base Qwen2.5-Omni**
4. **Qwen2.5-Omni fine-tuned on FRED's tool ecosystem**
5. **Base MiniCPM-o 2.6**

The evaluation should focus on actual FRED usage rather than conventional LLM benchmarks.

The eventual FRED architecture is envisioned as:

* A **realtime multimodal FRED Core**
* A **Researcher agent**
* A **CFO agent**
* An **Execution agent**
* A **General MiniCPM5-2B worker**
* A separate **scheduled consolidation system**

Coding is intentionally excluded as an FRED specialist because Claude Code already handles that role.

---

# 2. Core Architectural Idea

## 2.1 Realtime-Venus as a Reference

Realtime-Venus is not necessarily intended to become the deployed FRED backbone.

Instead, its architecture and training methodology can serve as a reference for answering:

> How can a pretrained language model be trained or adapted to understand continuous audio/video inputs, operate in realtime, interact naturally, and delegate tasks?

The intended approach is therefore:

```text
Realtime-Venus
      │
      ├── multimodal architecture
      ├── modality alignment
      ├── realtime interaction
      ├── agent/delegation behavior
      └── training methodology
              │
              ▼
      Study / reproduce principles
              │
              ▼
        FRED-specific system
```

The target is not a direct clone.

---

# 3. MiniCPM5-2B Hypothesis

The main proposed experiment is to investigate whether MiniCPM5-2B can serve as the core model after being adapted for FRED's requirements.

The hypothesis is:

> A small model heavily specialized for FRED's environment may provide a better practical local assistant than a much larger general-purpose model, particularly when combined with specialized agents and external tools.

MiniCPM5-2B is attractive because:

* It is substantially smaller than an 8B–9B backbone.
* It is already intended for agentic/tool-oriented workloads.
* Its small size makes local multi-agent deployment more realistic.
* FRED's environment is considerably narrower than the full universe of tasks expected from a general-purpose frontier model.

The goal is therefore not simply parameter reduction.

It is **specialization**.

---

# 4. Multimodal Adaptation Strategy

MiniCPM5-2B is fundamentally different from simply taking a prebuilt multimodal model.

If used as the backbone, FRED would need a multimodal interface that allows information from:

* microphone/audio
* screen
* face camera
* potentially other video streams

to become usable by the language model.

A conceptual architecture is:

```text
              Screen ──────────┐
                               │
              Face Camera ─────┤
                               │
              Microphone ──────┤
                               ▼
                       Multimodal Encoders
                               │
                               ▼
                       Multimodal Adapter
                               │
                               ▼
                        MiniCPM5-2B
                               │
                 ┌─────────────┼─────────────┐
                 ▼             ▼             ▼
               Tools        Agents       Response
```

The exact implementation should be informed by studying Realtime-Venus rather than being invented blindly.

Areas to investigate include:

* visual representation alignment
* audio representation alignment
* temporal information
* audio/video-to-language projection
* realtime streaming
* interruption handling
* modality synchronization
* delegation representation
* asynchronous result injection
* frozen vs trainable components
* training data structure
* multimodal trajectory construction

---

# 5. FRED's Multimodal Inputs

The initial target environment assumes simultaneous:

* **screen video**
* **face-camera video**
* **audio**

This makes the system more like an ambient assistant than a conventional chatbot.

The model should not require explicit commands for every observation.

For example:

```text
User works
    ↓
Screen changes
    ↓
Application throws error
    ↓
User reacts verbally
    ↓
FRED perceives both
    ↓
FRED determines whether intervention is warranted
```

The crucial capability is not merely seeing.

It is deciding:

> **Does this matter enough to act on?**

---

# 6. VRAM / Multi-Agent Architecture Hypothesis

A working estimate discussed during the conversation was:

* approximately **4.5–5 GB VRAM** for the full multimodal FRED core under high simultaneous usage with screen + face-camera input
* approximately **2.5 GB VRAM** for a base MiniCPM5-2B agent

On a 16 GB GPU this gives a rough theoretical allocation:

```text
FRED Core       ~5 GB
Agent 1         ~2.5 GB
Agent 2         ~2.5 GB
Agent 3         ~2.5 GB
Agent 4         ~2.5 GB
Agent 5         ~2.5 GB
```

However, this does **not** mean five agents should always be resident.

Actual VRAM consumption also depends on:

* KV cache
* context length
* multimodal encoders
* CUDA allocations
* framework overhead
* concurrent inference
* temporary tensors
* operating-system GPU usage
* model quantization

Therefore:

> **Five concurrent agents is a theoretical ceiling, not the intended normal operating configuration.**

A more realistic design is dynamic agent spawning/loading.

```text
                    FRED Core
                        │
                  Agent Manager
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
       Agent A       Agent B       Agent C
       loaded         loaded         loaded
       as needed      as needed      as needed
```

If only one specialist is needed, only one needs to consume GPU memory.

---

# 7. Candidate Model Bake-Off

Five candidate systems were selected.

## Candidate 1 — Base Realtime-Venus

Use the released Venus system as the reference/control.

Purpose:

* establish the capability baseline
* determine what the Venus architecture achieves out of the box
* measure realtime multimodal behavior
* establish performance and resource requirements

---

## Candidate 2 — Venus-Inspired MiniCPM5-2B

Use MiniCPM5-2B as the language backbone while reproducing/adapting the relevant Venus multimodal and agent methodology.

Potential training:

* multimodal alignment
* FRED-specific tool use
* realtime interaction
* delegation
* FRED-specific conversational behavior

This is the key experimental hypothesis.

---

## Candidate 3 — Base Qwen2.5-Omni

Use Qwen2.5-Omni without FRED-specific fine-tuning.

Purpose:

* establish a strong existing multimodal baseline
* determine what can be achieved without specialization

---

## Candidate 4 — Qwen2.5-Omni + FRED Tool Fine-Tuning

Take Qwen2.5-Omni and fine-tune it specifically for FRED's tool ecosystem.

This isolates an important question:

> How much improvement comes from simply teaching an already-capable multimodal model how FRED's tools work?

This candidate should receive the same FRED tool definitions and representative trajectories used for the other systems where applicable.

---

## Candidate 5 — Base MiniCPM-o 2.6

Use MiniCPM-o 2.6 as another compact multimodal baseline.

This is deliberately kept separate from MiniCPM5-2B.

The experiment therefore compares both:

* a small text backbone adapted into FRED's multimodal architecture
* an existing compact multimodal model

---

# 8. Evaluation Philosophy

The evaluation should not primarily be:

> Which model scores highest on generic benchmarks?

Instead:

> Which system behaves most effectively as FRED under identical real-world workloads?

The evaluation should therefore emphasize:

* multimodal grounding
* temporal understanding
* realtime interaction
* interruption handling
* tool selection
* delegation
* memory interaction
* hallucination rate
* unnecessary interventions
* latency
* VRAM
* throughput
* reliability

---

# 9. Core Multimodal Tests

## Test 1 — Passive Video Awareness

Provide a prerecorded 5–10 minute video showing ordinary computer activity.

Example:

```text
User codes
↓
Encounter error
↓
Looks at error
↓
Opens documentation
↓
Fixes problem
↓
Continues working
```

The model receives the same video/audio input in every trial.

It is **not explicitly prompted to analyze the event**.

Measure:

* whether the important event was detected
* whether it was understood correctly
* whether FRED decided intervention was warranted
* timing of intervention
* usefulness of intervention
* hallucination
* unnecessary interruption

This tests whether FRED can behave as an ambient assistant.

---

# 10. Visual Cue Recognition

Construct a scenario where the screen contains important information while the user speaks naturally.

Example:

```text
Terminal:
CUDA out of memory

User:
"Oh come on..."
```

FRED should connect:

* visual information
* spoken reaction
* context

without requiring:

> "FRED, read the terminal."

---

# 11. Temporal Reasoning

Use a prerecorded sequence:

```text
00:00  Application works
00:30  User changes code
01:00  Application crashes
01:30  User reverts change
02:00  Application works
```

Ask:

> What caused the problem?

The system must reason over the sequence rather than only the current frame.

This is particularly useful for distinguishing genuine multimodal/temporal understanding from static image recognition.

---

# 12. Audio-Visual Conflict

Create deliberately conflicting signals.

Example:

```text
Screen:
Application appears functional

Audio:
User says "It crashed again."

Later:
Screen displays actual failure.
```

Test whether the system:

* considers both modalities
* resolves the conflict
* updates its interpretation as new evidence arrives

---

# 13. Realtime Interruption

This should be a live test.

FRED begins speaking:

> "The reason this happens is—"

User interrupts:

> "Wait, I already fixed that."

Expected behavior:

```text
detect interruption
      ↓
stop speech
      ↓
process new information
      ↓
update state
      ↓
continue appropriately
```

Measure:

* interruption latency
* speech cutoff latency
* whether the correction is actually incorporated
* conversational coherence afterward

---

# 14. Multimodal Tool-Use Test

Give FRED a screen state that requires action.

Example:

```text
Screen:
application showing an error

User:
"Check what's wrong."
```

Expected sequence:

```text
Perceive
  ↓
Understand
  ↓
Choose tool
  ↓
Inspect relevant information
  ↓
Reason
  ↓
Respond
```

Repeat with misleading visual information to ensure the model isn't merely matching obvious cues to predetermined tools.

---

# 15. Delegation Test

Give FRED a problem that should be delegated.

Example:

> "Investigate why this project is crashing and figure out what needs changing."

The core may delegate:

```text
FRED Core
   │
   ├── Researcher
   ├── Execution Agent
   └── General Worker
```

Measure:

* whether delegation occurred
* whether the correct agent was selected
* quality of delegated instructions
* quality of returned result
* whether FRED correctly interpreted the result
* unnecessary delegation

---

# 16. Memory + Multimodal Test

Day 1:

Show FRED a project and discuss an architectural decision.

Day 5:

Show it a new version and ask:

> "Why did we make this section like this?"

Correct behavior requires:

```text
Current visual state
        +
Historical memory
        ↓
Correct explanation
```

This tests FRED as a persistent assistant rather than a stateless multimodal chatbot.

---

# 17. Long-Video Retrieval

Provide a 30–60 minute recording containing several important events.

Example:

```text
05:20  Architecture discussion
17:43  Bug discovered
29:11  Database changed
41:06  Approach rejected
```

Later ask:

> "When did we decide not to use X, and why?"

Measure:

* retrieval accuracy
* timestamp accuracy
* reason accuracy
* irrelevant retrieval
* hallucination

---

# 18. Do-Nothing Test

Give FRED long stretches of boring activity:

```text
typing
reading
scrolling
coding
switching tabs
thinking
```

Nothing requires intervention.

The correct behavior is often:

> **silence.**

Measure:

**Unsolicited interventions per hour.**

This is important because an assistant that comments on everything it notices is not necessarily more intelligent.

---

# 19. Full Real-World Usage Simulation

After isolated tests, run complete simulated sessions.

## Session A — Coding

```text
Open project
↓
Code
↓
Encounter error
↓
Ask FRED
↓
FRED observes screen
↓
Tool use
↓
Interruption
↓
Continue work
```

Coding itself remains with Claude Code where appropriate.

---

## Session B — Research

```text
Ask FRED a research question
↓
Researcher delegated
↓
Web / papers / GitHub / HF / Zenodo
↓
Evidence gathered
↓
Result returned
↓
FRED explains result
```

---

## Session C — Building

A long continuous work session where FRED is mostly silent and only intervenes when useful.

This tests whether the system understands **when not to act**.

---

## Session D — Multitasking

The user moves between:

* projects
* browsers
* applications
* conversations
* research
* development

FRED must maintain context without confusing projects.

---

## Session E — Full Day

The most important eventual test:

```text
Morning
  ↓
Work
  ↓
Project switching
  ↓
Errors
  ↓
Research
  ↓
Agent delegation
  ↓
Background work
  ↓
Evening
  ↓
Daily consolidation
```

This tests FRED as an actual persistent assistant.

---

# 20. Proposed FRED Agent Architecture

The discussion initially considered several specialist types.

Several were rejected because they duplicate capabilities already available in FRED or existing software.

### Rejected as permanent specialist: Observer

The main multimodal FRED system already handles:

* screen
* face camera
* audio
* ordinary perception

A separate observer is therefore unnecessary unless testing proves the core cannot handle a specific perception workload.

---

### Rejected as permanent specialist: Coding Agent

Claude Code already fills this role.

There is no reason for FRED to duplicate it unless a future requirement emerges.

---

### Consolidation is separate

Deep analysis of historical data belongs to the scheduled consolidation system.

It should not consume one of the always-available specialist slots.

---

# 21. Final Proposed Specialist Set

## Agent 1 — Researcher

Primary role:

> **Acquire external knowledge.**

Tools:

* web search
* GitHub
* Hugging Face
* ArXiv
* Zenodo
* documentation
* other relevant sources

Responsibilities:

* research
* source discovery
* evidence gathering
* technical comparisons
* literature review
* current-information retrieval
* source synthesis

---

# 22. Agent 2 — CFO

Primary role:

> **Financial decision-support and financial state analysis.**

Responsibilities:

* track structured financial state
* analyze spending
* evaluate purchases
* compare opportunity costs
* assess project economics
* estimate runway
* analyze recurring income/expenses
* perform forecasts
* conduct financial reviews
* identify spending patterns
* evaluate timing of purchases/investments
* maintain historical financial decisions

The CFO should have a deterministic financial layer for arithmetic and state.

```text
                 CFO
                  │
       ┌──────────┴──────────┐
       │                     │
Financial Engine          MiniCPM5
deterministic             reasoning
calculations
       │                     │
       └──────────┬──────────┘
                  ▼
            CFO conclusion
```

The model should not be trusted to invent numerical financial state.

The CFO advises; actual financial actions remain explicitly controlled by the user.

---

# 23. Agent 3 — Execution Agent

Primary role:

> **Carry out long or operational multi-step tasks using FRED's tools.**

This is deliberately different from coding.

Possible responsibilities:

* filesystem operations
* application interaction
* repetitive workflows
* command execution
* log collection
* file organization
* data transformation
* API interaction
* verifying that requested operations actually succeeded

Conceptually:

```text
FRED Core
    │
    │ "This needs 30 operational steps."
    ▼
Execution Agent
    │
    ├── Tool 1
    ├── Tool 2
    ├── Tool 3
    ├── Tool 4
    └── verification
    │
    ▼
Result → FRED
```

FRED Core decides **what should happen**.

The Execution Agent handles **the long operational procedure**.

---

# 24. General MiniCPM5-2B Worker

In addition to the three specialists, the architecture can maintain one general-purpose MiniCPM5-2B worker.

Its purpose is cheap miscellaneous work that does not justify invoking a specialist.

Example:

```text
FRED
 │
 ├── Research question → Researcher
 ├── Financial question → CFO
 ├── Long operational task → Executor
 └── Simple miscellaneous task → General MiniCPM5
```

This keeps specialist agents from becoming unnecessarily overloaded.

---

# 25. Delegation Hierarchy

The overall decision process becomes:

```text
                     FRED CORE
                         │
                 Understand request
                         │
            ┌────────────┴────────────┐
            │                         │
       Can solve directly?          No
            │                         │
           YES                       ▼
            │                Determine workload
            ▼                         │
         Execute             ┌────────┼────────┐
                             │        │        │
                          Research   Finance  Execution
                             │        │        │
                             ▼        ▼        ▼
                         Researcher   CFO   Executor
                                     
                         Otherwise:
                              │
                              ▼
                       General MiniCPM5
```

FRED should not delegate simply because an agent exists.

Delegation itself is a decision.

---

# 26. Dynamic Agent Loading

The system should avoid keeping every specialist permanently resident if memory pressure makes that inefficient.

Possible design:

```text
                  FRED Core
                      │
                 Agent Manager
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
      Researcher      CFO      Executor
       load on      load on     load on
        demand       demand      demand
```

If research is running:

```text
FRED Core + Researcher
```

If financial analysis is running:

```text
FRED Core + CFO
```

If multiple tasks genuinely require simultaneous execution:

```text
FRED Core + Researcher + CFO
```

The theoretical five-agent VRAM ceiling should therefore not dictate the architecture.

---

# 27. Consolidation Architecture

The nightly/weekly consolidation system remains separate.

Its role is to process accumulated FRED history and produce higher-level information.

Conceptually:

```text
Raw interactions
      │
      ▼
Daily log
      │
      ▼
Nightly consolidation
      │
      ├── important events
      ├── decisions
      ├── project state
      ├── preferences
      ├── unresolved issues
      └── patterns
      │
      ▼
Long-term memory
```

A larger model can be used for this process because:

* it is not realtime
* latency matters less
* it can process large context
* it can perform deeper meta-analysis
* it does not need to remain resident during ordinary interaction

This keeps the realtime FRED model small and responsive.

---

# 28. Daily Log Concept

FRED should maintain structured daily records with at least:

```text
Year
Month
Day

Summary
Important events
Projects worked on
Decisions made
Tasks completed
Tasks left unfinished
New preferences/facts
Problems encountered
Potential long-term memories
```

These daily records become inputs to larger periodic consolidation runs.

---

# 29. Long-Term Architecture

The eventual architecture therefore resembles:

```text
                         ┌──────────────────────┐
                         │      FRED CORE       │
                         │ MiniCPM5-based       │
                         │ realtime multimodal  │
                         └──────────┬───────────┘
                                    │
                         conversation + perception
                                    │
                              delegation
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
              ▼                     ▼                     ▼
        RESEARCHER                CFO                EXECUTOR
        external info       financial reasoning      operations
              │                     │                     │
              └─────────────────────┼─────────────────────┘
                                    │
                             GENERAL MINI-2B
                                    │
                                    ▼
                               FRED response


                    ┌─────────────────────────┐
                    │ Scheduled Consolidation │
                    │ larger/deeper model     │
                    └────────────┬────────────┘
                                 │
                         historical analysis
                                 │
                                 ▼
                         Long-term memory
```

Claude Code remains external to this architecture as the primary coding agent.

---

# 30. Final Experimental Principle

The project should not begin with the assumption:

> "MiniCPM5-2B will win."

Nor:

> "Realtime-Venus is the best architecture."

Nor:

> "The largest model is automatically best."

The experiment should determine:

**Which combination of:**

* backbone
* multimodal synthesis
* fine-tuning
* tool training
* delegation
* agent specialization
* latency
* VRAM
* reliability

produces the most useful FRED system under actual local hardware constraints.

The final decision should therefore be driven by measurements from the FRED-specific benchmark and real-world sessions.

---

# 31. Immediate Research Questions

Before implementation, the following should be investigated:

1. Exactly how Realtime-Venus performs multimodal alignment.
2. Which parts of its architecture can be transferred to MiniCPM5-2B.
3. Which components need to be retrained versus frozen.
4. How audio and video streams are represented.
5. How realtime interruption is trained.
6. How asynchronous delegation is represented.
7. How Venus trains tool/agent trajectories.
8. How MiniCPM5-2B's existing agent/tool capabilities can be preserved during multimodal adaptation.
9. What quantization level provides the best FRED latency/quality tradeoff.
10. Actual VRAM consumption under simultaneous screen + face camera + audio.
11. Whether specialist agents need separate model instances or can be dynamically loaded.
12. What FRED-specific training data can be generated from existing project/tool trajectories.
13. How to construct a reproducible FRED benchmark.
14. How to evaluate unsolicited intervention quality.
15. How to evaluate long-term memory correctness rather than merely retrieval similarity.

---

# 32. Working Final Architecture

The current strongest architecture emerging from the conversation is:

```text
                           FRED
                            │
                ┌───────────┴───────────┐
                │                       │
          Multimodal Core         Persistent Memory
                │                       │
         MiniCPM5-based           Daily/long-term
         realtime model               state
                │
        ┌───────┼────────┐
        │       │        │
        ▼       ▼        ▼
   Research    CFO    Execution
    Agent     Agent     Agent
        │       │        │
        └───────┼────────┘
                │
        General MiniCPM5
          worker/fallback


             Scheduled:
                 │
                 ▼
       Large-model consolidation
                 │
                 ▼
          Long-term meta-memory
```

The system is deliberately divided into:

**perception + conversation + immediate reasoning**
→ FRED Core

**external knowledge acquisition**
→ Researcher

**financial reasoning/state**
→ CFO

**long operational procedures**
→ Execution Agent

**cheap miscellaneous work**
→ General MiniCPM5

**historical/meta-level reasoning**
→ scheduled consolidation model

**coding**
→ Claude Code

This gives each component a distinct purpose rather than creating agents merely because additional VRAM is available.