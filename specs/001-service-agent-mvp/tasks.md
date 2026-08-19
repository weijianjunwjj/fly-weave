# Flyweave MVP Tasks

## Execution Rules

Each task is intentionally small and bounded.

For every task:

1. read `spec.md` and `plan.md` first;
2. do not broaden product scope;
3. do not introduce generic infrastructure unless the task acceptance criteria require it;
4. preserve explicit Tool / business execution boundaries;
5. do not claim success without verification evidence;
6. stop when the task acceptance criteria are met.

The task list maps to GitHub Issues #1–#5 but is more granular for AI-assisted development.

---

# Phase A — Project Shell

## T001 — Bootstrap Vue application

**Parent:** #1

**Goal**

Create the minimal Vue 3 + TypeScript frontend application.

**Allowed area**

- `apps/web/**`
- root workspace files only when required to run the frontend

**Acceptance criteria**

- app starts locally;
- TypeScript is enabled;
- a minimal Flyweave application shell renders;
- no business feature implementation yet.

**Do not**

- add a custom design system;
- add microfrontend architecture;
- add unnecessary state libraries before a concrete need appears.

---

## T002 — Bootstrap FastAPI application

**Parent:** #1

**Goal**

Create the minimal FastAPI backend.

**Allowed area**

- `apps/api/**`
- root workspace files only when required to run the API

**Acceptance criteria**

- API starts locally;
- `GET /health` returns structured healthy status;
- configuration is environment-driven;
- basic test structure exists.

**Do not**

- create microservices;
- create a generic provider layer;
- create an Agent framework.

---

## T003 — Add PostgreSQL baseline

**Parent:** #1

**Depends on:** T002

**Goal**

Establish the minimum persistent data layer.

**Acceptance criteria**

- FastAPI can connect to PostgreSQL;
- development configuration is documented;
- schema / migration approach is explicit;
- one integration smoke test can confirm database connectivity.

**Do not**

- add pgvector yet unless needed by a later retrieval task;
- optimize database architecture prematurely.

---

## T004 — Connect web health status to API

**Parent:** #1

**Depends on:** T001, T002

**Goal**

Prove frontend-backend integration.

**Acceptance criteria**

- frontend calls the backend health endpoint;
- UI shows a clear connected / failed state;
- network failure is not displayed as success.

---

# Phase B — Product Shell

## T005 — Define seeded demo domain data

**Parent:** #2

**Depends on:** T003

**Goal**

Create a small, repeatable demo dataset for the first business scenario.

**Required entities**

- customer / customer reference;
- ticket;
- order;
- inventory;
- after-sales policy;
- at least one low-risk case;
- at least one approval-required case;
- at least one failure / rejection case.

**Acceptance criteria**

- data can be recreated deterministically;
- all business ids are stable enough for demo / tests;
- simulated content is clearly distinguishable from production data.

---

## T006 — Build Service Operations dashboard

**Parent:** #2

**Depends on:** T005

**Goal**

Make the product understandable within 30 seconds without requiring a chatbot interaction.

**Acceptance criteria**

- dashboard shows seeded tickets;
- dashboard displays clearly labeled demo metrics such as automation rate, human takeover, processing time, and cost;
- user can navigate to ticket detail;
- UI reads as enterprise operations software rather than a generic chat app.

---

## T007 — Build Ticket Detail

**Parent:** #2

**Depends on:** T005

**Goal**

Show the business context an Agent needs to process a service case.

**Acceptance criteria**

- ticket request is visible;
- related order / product context is visible;
- current resolution state is visible;
- user can start or inspect an Agent Run entry point.

---

## T008 — Build Agent Run timeline shell

**Parent:** #2

**Goal**

Create the UI structure for inspectable Agent execution before full Agent behavior exists.

**Acceptance criteria**

- timeline supports step name and status;
- states include pending / running / completed / failed / skipped;
- placeholders exist for Tool calls, approval, and trace metadata;
- no fake execution data is presented as real model output.

---

## T009 — Build Approval Inbox shell

**Parent:** #2

**Goal**

Create the product surface for future human-in-the-loop actions.

**Acceptance criteria**

- pending approval cards / rows can be rendered from seeded data;
- action reason and affected business object are visible;
- approve / reject controls may remain non-functional until Phase D.

---

# Phase C — Golden Path

## T010 — Implement AgentRun persistence model

**Parent:** #3

**Depends on:** T003

**Goal**

Represent Agent execution as persistent application state.

**Required run states**

- queued
- running
- waiting_for_approval
- completed
- failed
- cancelled

**Acceptance criteria**

- runs persist independently from HTTP request lifetime;
- run steps can be recorded and queried;
- failed runs remain visibly failed.

---

## T011 — Implement structured intent extraction

**Parent:** #3

**Depends on:** T010

**Goal**

Convert a service ticket into a validated structured intent.

**Initial supported intent**

- quality issue / replacement

**Acceptance criteria**

- output uses an explicit schema;
- invalid model output fails validation or enters a safe fallback path;
- intent result is recorded in the Agent Run.

---

## T012 — Implement baseline policy lookup

**Parent:** #3

**Goal**

Provide a deterministic policy basis before advanced RAG is introduced.

**Acceptance criteria**

- relevant policy content can be retrieved for the seeded case;
- source identity / metadata is preserved;
- decision logic can reference the returned policy.

**Do not**

- block Golden Path completion on vector retrieval sophistication.

---

## T013 — Implement `get_order` Tool

**Parent:** #3

**Goal**

Query order facts through an explicit Tool boundary.

**Acceptance criteria**

- typed input and output schemas;
- Tool reads actual seeded / persisted application data;
- missing orders return structured failure;
- Agent cannot invent a successful order lookup.

---

## T014 — Implement `check_inventory` Tool

**Parent:** #3

**Goal**

Query replacement availability through an explicit Tool boundary.

**Acceptance criteria**

- typed input and output schemas;
- Tool reads actual seeded / persisted inventory data;
- unavailable inventory is represented explicitly;
- Tool call is recorded in the Agent Run.

---

## T015 — Implement constrained replacement decision

**Parent:** #3

**Depends on:** T011, T012, T013, T014

**Goal**

Determine whether a replacement may proceed using both AI-derived and deterministic evidence.

**Acceptance criteria**

- decision references intent, policy, order facts, and inventory facts;
- deterministic rules can block execution;
- output is structured;
- ambiguity is representable rather than forced into approval.

---

## T016 — Implement `create_replacement` Tool

**Parent:** #3

**Depends on:** T015

**Goal**

Execute the real business mutation for an eligible low-risk Golden Path case.

**Acceptance criteria**

- typed input / output;
- application service validates preconditions;
- replacement record is persisted;
- duplicate / invalid execution is safely rejected;
- Tool result determines success, not model text.

---

## T017 — Implement `update_ticket` Tool

**Parent:** #3

**Depends on:** T016

**Goal**

Write the confirmed execution result back to the service ticket.

**Acceptance criteria**

- ticket state is persisted;
- result references the executed replacement where applicable;
- Tool failure prevents false completed status.

---

## T018 — Wire first end-to-end happy path

**Parent:** #3

**Depends on:** T010–T017

**Goal**

Run one seeded ticket from intake through successful replacement and ticket update.

**Acceptance criteria**

- one click / action can initiate the complete flow;
- Agent Run timeline reflects real steps;
- actual Tool outputs drive the final result;
- successful replacement is persisted;
- failed execution cannot be displayed as success.

---

# Phase D — Enterprise Control

## T019 — Add deterministic risk gate

**Parent:** #4

**Goal**

Ensure at least one seeded scenario requires human approval.

**Acceptance criteria**

- risk rule is explicit and testable;
- protected action cannot bypass the gate;
- decision reason can be shown in UI.

---

## T020 — Implement ApprovalRequest persistence

**Parent:** #4

**Depends on:** T010, T019

**Acceptance criteria**

- pending approval is persisted;
- approval references the Agent Run and protected action;
- Agent Run moves to `waiting_for_approval`.

---

## T021 — Implement approve / reject flow

**Parent:** #4

**Depends on:** T009, T020

**Acceptance criteria**

- operator can approve or reject;
- actor and timestamp are recorded;
- rejection prevents protected Tool execution;
- approval makes the run eligible to resume.

---

## T022 — Resume the same Agent Run after approval

**Parent:** #4

**Depends on:** T021

**Goal**

Prove durable human-in-the-loop behavior.

**Acceptance criteria**

- the same `AgentRun` id resumes;
- already completed steps are not blindly replayed;
- approved action executes once;
- resume failure is visible and recoverable.

---

## T023 — Add audit trail

**Parent:** #4

**Goal**

Create an inspectable record of meaningful AI and human actions.

**Acceptance criteria**

- decisions, Tool calls, approvals, rejections, and execution outcomes create audit events;
- audit events reference relevant business objects;
- secrets are not written to audit records.

---

# Phase E — Grounding, Eval, Tracing

## T024 — Add policy document ingestion baseline

**Parent:** #5

**Goal**

Move policy lookup from fixture-style retrieval to an explicit knowledge ingestion flow.

**Acceptance criteria**

- policy documents can be loaded deterministically;
- chunks preserve source metadata;
- ingestion is repeatable.

---

## T025 — Add policy retrieval / RAG

**Parent:** #5

**Depends on:** T024

**Acceptance criteria**

- relevant passages can be retrieved for the seeded case;
- source references reach the decision layer;
- UI can show policy basis;
- retrieval failure is represented explicitly.

---

## T026 — Add Agent tracing metadata

**Parent:** #5

**Goal**

Make Agent behavior inspectable without building a tracing platform.

**Acceptance criteria**

- run captures model / step timing where available;
- Tool calls are linked to run steps;
- token / cost metadata is stored when the selected model/provider exposes it;
- unavailable metrics are not fabricated.

---

## T027 — Create repeatable eval dataset

**Parent:** #5

**Acceptance criteria**

Dataset includes at least:

- correct replacement case;
- ineligible policy case;
- unavailable inventory case;
- approval-required case;
- Tool failure case;
- unsafe action must be blocked case.

Expected outcomes must be machine-checkable where practical.

---

## T028 — Implement MVP eval runner

**Parent:** #5

**Depends on:** T027

**Goal**

Evaluate product behavior, not general model intelligence.

**Minimum metrics / checks**

- intent correctness;
- policy grounding;
- expected Tool selection;
- unsafe action blocked;
- final business outcome correctness.

**Acceptance criteria**

- eval cases can run repeatedly;
- failures identify the case and failed criterion;
- no custom generic eval platform is introduced.

---

# Phase F — Demo Finish

## T029 — Add failure and recovery demo scenarios

**Goal**

Show that Flyweave handles failure honestly.

**Acceptance criteria**

- at least one Tool failure is visible in Agent Runs;
- at least one rejected approval terminates safely;
- UI does not collapse failures into a green success state.

---

## T030 — Polish demo narrative and README evidence

**Goal**

Turn the working MVP into a job-facing case study.

**Acceptance criteria**

- README explains the Golden Path concisely;
- screenshots or demo assets show Service Operations, Agent Run, and Approval Inbox;
- architecture diagram matches implementation;
- all simulated metrics are labeled;
- project can be explained in a five-minute interview walkthrough.

---

# Scope Stop Rule

Before adding any task beyond T030, ask:

> Does this directly make the single service Golden Path more credible, safe, observable, or interview-ready?

If the answer is no, defer it.

Examples that should normally be deferred:

- multi-agent collaboration;
- custom MCP gateway;
- workflow DSL;
- generalized plugin marketplace;
- model routing platform;
- custom Agent runtime;
- custom eval product;
- unrelated second business vertical.
