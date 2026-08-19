# Flyweave MVP Implementation Plan

## 1. Objective

Implement the product contract in `spec.md` with the smallest architecture that can support a credible enterprise Agent workflow demo.

The plan favors explicit boundaries, inspectable state, and real business execution over abstraction depth.

## 2. Repository Shape

Initial target structure:

```text
apps/
  web/        # Vue 3 + TypeScript
  api/        # FastAPI

docs/
  ARCHITECTURE.md
  MVP_SCOPE.md
  ROADMAP.md

specs/
  001-service-agent-mvp/
    spec.md
    plan.md
    tasks.md
```

Additional directories should only be introduced when concrete implementation needs appear.

## 3. Planned Stack

### Frontend

- Vue 3
- TypeScript
- Vite
- a mature UI component library if needed

### Backend

- Python
- FastAPI
- Pydantic
- SQLAlchemy or equivalent mature ORM

### Data

- PostgreSQL
- pgvector only when policy retrieval requires vector search

### Agent / AI

Use mature SDKs or orchestration components where they reduce implementation risk.

Do not build a generic Agent runtime, provider layer, model router, plugin framework, or MCP gateway for v0.1.

### Eval / tracing

Prefer an existing tracing / eval solution or a minimal application-owned event model. Do not build an eval platform.

## 4. Domain Model

Minimum domain concepts:

```text
Ticket
Order
InventoryItem
PolicyDocument
AgentRun
AgentStep
ToolCall
ApprovalRequest
AuditEvent
ReplacementOrder
EvalCase
EvalResult
```

The exact database schema may evolve, but these concepts should remain explicit rather than hidden inside arbitrary JSON blobs.

## 5. Critical Architecture Boundary

The most important rule in the implementation is:

```text
LLM reasoning
    ↓
structured decision / tool request
    ↓
application service / Tool
    ↓
business state mutation
```

The LLM is not the business executor.

Example of invalid behavior:

```text
Assistant: "Replacement created successfully."
```

when no application service has actually persisted a replacement.

Example of valid behavior:

```text
Agent decides replacement is eligible
    ↓
create_replacement tool called
    ↓
application service validates preconditions
    ↓
transaction persists replacement
    ↓
ToolResult(success=true, replacement_id=...)
    ↓
Agent summarizes confirmed result
```

## 6. Agent Run State

Minimum run states:

```text
queued
running
waiting_for_approval
completed
failed
cancelled
```

Minimum step states:

```text
pending
running
completed
failed
skipped
```

The same `AgentRun` must survive a human approval pause and resume.

## 7. Tool Contracts

Initial Tool set:

### `get_order`

Input:

- order identifier or resolved customer/order reference

Output:

- order id
- purchase date
- product
- order status
- customer reference

### `check_inventory`

Input:

- product / SKU

Output:

- available quantity
- warehouse or fulfillment context where needed

### `create_replacement`

Input:

- order id
- product / SKU
- reason
- approval reference when required

Output:

- replacement id
- persisted status

### `update_ticket`

Input:

- ticket id
- resolution status
- summary / structured result

Output:

- persisted ticket state

All Tool contracts must be typed and validated.

## 8. Policy Retrieval

Start simple.

Phase 1 may use a deterministic policy fixture or direct lookup to prove the full workflow.

Phase 2 should add actual retrieval with:

- policy documents;
- chunk metadata;
- source ids;
- retrieved passages;
- citations or source references shown in the UI.

Do not optimize retrieval before the Golden Path works end-to-end.

## 9. Decision Boundary

The replacement decision should combine:

- model-extracted intent;
- retrieved policy;
- factual order data;
- inventory result;
- deterministic rules.

High-risk or ambiguous decisions must not execute automatically.

Initial approval triggers may include:

- low model confidence;
- policy ambiguity;
- high-value order;
- conflicting business facts;
- explicitly configured risky action.

At least one trigger should be deterministic and easy to test.

## 10. Human Approval

Approval flow:

```text
Agent reaches guarded action
  ↓
ApprovalRequest persisted
  ↓
AgentRun = waiting_for_approval
  ↓
operator approves / rejects
  ↓
AuditEvent recorded
  ↓
Run resumes or terminates safely
```

A rejected action must not call the protected business Tool.

## 11. Audit Model

Audit events should capture enough information to answer:

- what happened;
- who or what initiated it;
- when it happened;
- what business object it affected;
- whether it succeeded;
- what approval or Tool result justified the next step.

Do not store secrets or unnecessary sensitive content in audit records.

## 12. UI Priority

Build product comprehension before visual complexity.

Priority order:

1. Service Operations dashboard
2. Ticket detail
3. Agent Run timeline
4. Approval Inbox
5. policy source / trace details

The first screen should look like enterprise operations software, not a generic chat interface.

## 13. Delivery Phases

### Phase A — Project shell

- web + api bootstrapped
- database baseline
- health integration

### Phase B — Product shell

- seeded demo data
- dashboard
- ticket detail
- run timeline placeholder
- approval placeholder

### Phase C — Golden Path

- run model
- intent
- baseline policy lookup
- order / inventory Tools
- constrained decision
- replacement execution
- ticket write-back

### Phase D — Enterprise control

- risk gate
- pause / resume
- approval
- audit
- failure states

### Phase E — AI grounding and evaluation

- policy RAG
- source display
- tracing
- eval cases
- cost / latency metadata where available

### Phase F — Demo polish

- seed scenarios
- README screenshots / architecture
- truthful simulation labels
- concise case study

## 14. Architecture Guardrails

Do not introduce any of the following without a concrete acceptance criterion that requires it:

- microservices;
- event bus infrastructure;
- Kubernetes;
- generic workflow engine;
- custom MCP gateway;
- multi-agent orchestration;
- custom provider abstraction;
- custom model router;
- generic plugin system;
- custom eval platform.

If a mature external component can solve a generic problem reliably, prefer integration over invention.

## 15. Verification Philosophy

Every task should leave behind evidence that the requested behavior actually works.

Prefer:

- typed schemas;
- automated tests for deterministic rules and Tools;
- integration tests for business execution;
- repeatable seeded demo cases;
- explicit failure scenarios;
- inspectable Agent Run state.

The MVP is not complete merely because a model-generated demo looks plausible.
