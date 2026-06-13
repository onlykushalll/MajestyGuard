# Original User Request

## Initial Request — 2026-06-10T20:57:10Z

Execute a god-level multi-agent code review (Ultrareview) of the MajestyGuard-v2 repository (c:/tmp/MajestyGuard) using the specialized ultrareview agent and reference guidelines.

Working directory: c:/tmp/MajestyGuard
Integrity mode: development

## Requirements

### R1. Orientation and Phase 0 Analysis
Prior to launching the review agents, perform Phase 0 analysis as outlined in C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/SKILL.md:
- Reconstruct the core intent of the MajestyGuard codebase in one sentence.
- Surface the actual observable behavior contract of its critical modules (UI named pipes, daemon tracking/locking, overlay interaction).
- Assess the blast radius of any failures.

### R2. Isolated Specialist Agent Audits (Phase 1)
Run 8 independent specialist passes on the codebase, strictly isolated from one another (no sharing of findings or priming between agents). Follow the guidelines in C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/SKILL.md and their respective markdown configurations in C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/agents/:
- Agent 1 (Logic & Correctness): Run C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/agents/logic.md on logic, arithmetic, and state transitions.
- Agent 2 (Security & Trust): Run C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/agents/security.md on vulnerabilities, name pipe access, and input taint tracking using C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/references/security.md.
- Agent 3 (Concurrency & Async): Run C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/agents/concurrency.md on threading, async event loops, race conditions, and memory ordering using C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/references/async.md.
- Agent 4 (Performance & Scalability): Run C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/agents/performance.md on algorithmic bottlenecks, DWM redrawing, and tail latency.
- Agent 5 (Resilience & Distributed Correctness): Run C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/agents/resilience.md on exception handling, retries, and failure recovery.
- Agent 6 (Architecture & Coupling): Run C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/agents/architecture_agent.md on leaky abstractions, dynamic connascence of timing/execution/value using C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/references/architecture.md.
- Agent 7 (Enhancement & Safe Improvement): Run C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/agents/enhancement.md to identify L0–L2 behavior-preserving improvements.
- Agent 8 (Test Coverage & Dead Code): Run C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/agents/coverage.md to identify untested logic, dead code paths, and generate test anchors.

Use the Python language reference guideline in C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/references/python.md during the audits.

### R3. Cross-Verification & Root Cause Synthesis (Phases 2 & 3)
- For every suspected critical finding, perform an independent execution trace, caller verification, and PoC check. No unverified findings are allowed a Critical severity rating.
- Deduplicate overlapping findings.
- Group issues by bug class and identify any systemic gap where a single architectural change can resolve 3+ instances.

## Acceptance Criteria

### Audit Report Format
- Save the final markdown report to c:/tmp/MajestyGuard/audit_report.md.
- Use the exact markdown schema specified in Phase 4 of C:/Users/Default.L-HCG-9FVVGS3/OneDrive/Desktop/JARVIS/jarvis/skills/ultrareview/SKILL.md, including the 🔍 Ultrareview Report v3 header, critical findings, nits, pre-existing issues, safe enhancements, test anchors, and root cause synthesis table.
- Ensure every Critical finding contains a concrete, reproducible input/sequence and a proposed corrected code block.
