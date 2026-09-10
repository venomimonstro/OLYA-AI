# Sprint 63 — Projects as Product Core

## Goal

Sprint 63 turns a project from a selector into the central product workspace:

```text
Project
 ├─ trusted instructions and description
 ├─ project-scoped conversations
 ├─ current files used by RAG
 ├─ explicit project memory
 ├─ tasks and their current state
 └─ development plan
```

The project page is an overview and navigation surface. It does not duplicate chat, file parsing, RAG, task or development business logic.

## Bounded workspace snapshot

`GET /v1/projects/{project_id}/workspace` returns a bounded summary assembled by `app/services/project_workspace.py`:

- the canonical project and the caller's effective role;
- counts for conversations, current files, project-memory items and tasks;
- up to eight recent conversations, files and tasks;
- up to fifty explicit project-memory items;
- a compact development-plan status when a plan exists.

Message bodies, file contents, chunks, task evidence and full development plans are deliberately excluded. Their existing controllers remain authoritative.

Every request first passes `require_project_role(..., "viewer")`. Cross-project and cross-tenant access remains masked as `404` by the canonical access service.

## Project-centric UI

`/app` now opens a real project workspace with:

- project counts and current activity;
- direct entry into a new project chat;
- project-scoped recent chat history;
- direct entry into the existing Files surface;
- description and trusted instruction editing for owner/manager only;
- read-only summaries of memory, tasks and development state.

Selecting a project in Chat resets the current conversation before changing scope. This prevents an existing conversation from being submitted with a different `project_id`.

The UI uses DOM nodes and `textContent`; project-controlled text is never inserted as executable HTML.

## Context and isolation contract

Sprint 63 preserves the existing canonical pipeline:

```text
project chat
 → Conversation.project_id
 → project RBAC
 → ProjectContextBuilder
 → trusted Project.instructions
 → bounded conversation history / ConversationMemory
 → ProjectMemory
 → current ready ProjectFile chunks through RAG
 → local Qwen
```

Project state is never copied into a global user context and one project's summary never queries another project's rows.

## Regression coverage

`tests/test_sprint63_project_workspace.py` covers aggregation, cross-tenant access masking, bounded previews, role-aware editing, project-scoped history, DOM-only rendering and product-route registration.

## Scope boundary

Sprint 64 owns full Files/RAG UX: processing/error/ready states, retry/delete, version history, citations and recovery. Sprint 63 only shows compact current-file status and links to the existing Files controller.

Development execution, task proof and agent restart semantics remain owned by their existing services and Sprint 76 product lock. The project workspace only exposes their bounded current state.

## Target-node acceptance

The real deployment must prove:

1. create a project and save instructions;
2. create two chats in that project and one personal chat;
3. project selection shows only the two project chats;
4. both project chats receive the project's instructions/memory/RAG context;
5. a second user without membership receives `404` for the workspace and its resources;
6. a viewer cannot edit settings while an owner/manager can;
7. mobile layout remains usable with populated project sections.
