# Flyweave MVP Product Spec

## 1. Purpose

Flyweave v0.1 is a thin enterprise AI service workflow demo that proves one thing clearly:

> An AI agent can move through enterprise knowledge, business data, tools, approval, execution, and audit to complete a real service task instead of only answering questions.

The first business scenario is **after-sales replacement handling**.

This spec is intentionally narrow. It is a product and execution contract for the MVP, not a long-term platform blueprint.

## 2. Target User

Primary user:

- after-sales / customer service operator

Secondary user:

- team lead or operations manager who needs visibility into Agent Runs, approvals, automation rate, failures, and cost

## 3. Core Problem

Traditional service systems digitize tickets and records, but the real work still requires humans to:

1. understand the request;
2. search service policy;
3. query order information;
4. check inventory;
5. decide whether the request is eligible;
6. execute a replacement action;
7. write the result back;
8. leave an auditable record.

Flyweave v0.1 demonstrates how an Agent can participate in this workflow while preserving deterministic business rules and human control.

## 4. Golden Path

The MVP must support exactly one complete business path:

```text
Customer ticket
  ↓
Understand request
  ↓
Retrieve after-sales policy
  ↓
Query order
  ↓
Check inventory
  ↓
Evaluate replacement eligibility
  ↓
Risk / confidence gate
  ↓
Human approval when required
  ↓
Create replacement order
  ↓
Update ticket
  ↓
Audit + Eval
```

Reference demo case:

> The customer reports that the right earbud is broken. The order was purchased recently and the customer asks whether it can be replaced.

## 5. Functional Requirements

### FR-001 Ticket intake

The system must expose a seeded demo ticket with enough context to start an Agent Run.

### FR-002 Intent understanding

The Agent must classify the ticket into a structured service intent. The initial supported intent is:

- quality issue / replacement

The output must be structured and persisted as part of the Agent Run.

### FR-003 Policy retrieval

The system must retrieve the relevant after-sales policy and preserve source metadata so the final recommendation can show its basis.

### FR-004 Order query tool

The Agent must use a real application Tool to query order data.

The LLM must never simulate a successful order lookup in natural language.

### FR-005 Inventory query tool

The Agent must use a real application Tool to check replacement inventory.

### FR-006 Constrained decision

Replacement eligibility must combine:

- retrieved policy;
- order facts;
- inventory facts;
- deterministic business constraints.

The model may assist reasoning, but final execution eligibility must not depend on unconstrained free-form text alone.

### FR-007 Human approval

At least one demo case must enter `waiting_for_approval` before an irreversible or high-risk action.

An operator must be able to approve or reject the action.

### FR-008 Business execution tool

After required approval, the system must call a real application Tool to create a replacement record.

Failure must remain failure. The model must not report success when the business action did not complete.

### FR-009 Ticket write-back

The completed result must be written back to the ticket through an application Tool or service boundary.

### FR-010 Agent Run timeline

The UI must show the major steps of a run, including:

- step name;
- status;
- relevant structured input / output summary;
- tool calls;
- approval state;
- failure state when applicable.

### FR-011 Audit trail

The system must record key Agent and human actions, including decisions, tool calls, approvals, rejections, and execution outcomes.

### FR-012 Eval baseline

The project must include repeatable evaluation cases covering at minimum:

- intent correctness;
- policy grounding;
- tool selection;
- unsafe action blocked;
- business execution outcome.

## 6. Product Surfaces

MVP scope includes only these primary surfaces:

1. **Service Operations** — demo operational overview and tickets
2. **Ticket Detail** — ticket context and business facts
3. **Agent Runs** — step-by-step execution timeline
4. **Approval Inbox** — actions waiting for human approval

A chatbot-first UI is explicitly not the primary product surface.

## 7. Non-Functional Requirements

### NFR-001 Observable execution

Every meaningful Agent step must have an inspectable state.

### NFR-002 Structured boundaries

Tool inputs and outputs must use explicit schemas.

### NFR-003 Honest failure

Tool, model, retrieval, or approval failure must never be converted into fake success.

### NFR-004 Human control

High-risk actions must be pausable and reviewable.

### NFR-005 Demo truthfulness

All seeded metrics, costs, tickets, orders, policies, and operational numbers must be clearly marked as demo or simulation data where they could be mistaken for production data.

### NFR-006 Thin architecture

The MVP must prefer mature components and direct application code over new generic infrastructure.

## 8. Explicit Non-Goals

Flyweave v0.1 must not implement the following unless a later approved requirement explicitly requires them:

- generic Agent platform;
- multi-agent framework;
- custom model router;
- generic provider abstraction layer;
- generic plugin system;
- dynamic workflow DSL;
- custom MCP gateway;
- custom eval platform;
- microservice decomposition;
- Kubernetes deployment architecture;
- generalized CRM / ERP product;
- multiple unrelated business scenarios.

## 9. Acceptance Criteria

The MVP is complete when all of the following are true:

1. a user can open a seeded after-sales ticket;
2. an Agent Run can start from that ticket;
3. policy, order, and inventory data are obtained through explicit retrieval / tool boundaries;
4. the system produces a constrained replacement decision;
5. at least one scenario requires human approval;
6. approval resumes the same Agent Run rather than creating an unrelated new flow;
7. an approved replacement is executed through real application code and persisted;
8. the ticket is updated with the result;
9. the complete run can be inspected in a timeline;
10. key steps appear in an audit trail;
11. repeatable eval cases exist;
12. no required step is faked by model-generated success text.

## 10. Product Principle

When a trade-off is unclear, prefer the choice that best supports this statement:

> **Outcome over chat. Thin vertical slice over platform breadth. Real execution over impressive simulation.**
