# Sprint 64 — Files & RAG UX

## Result

Sprint 64 makes the complete file lifecycle visible and recoverable. A document is available to RAG only while its database version is both `ready` and current.

## Lifecycle

Upload creates a durable `processing` row before isolated parsing. Successful parsing replaces chunks and promotes the newest ready version to current. Unsupported, encrypted, empty, unreadable, timed-out and capacity-blocked inputs get explicit user-safe errors; no failed version is marked current.

`GET /v1/projects/{project_id}/files` reconciles stale processing rows and keeps non-ready rows visible in the default view. The maintenance loop uses the same recovery primitive after application restart.

Managers can:

- `POST .../files/{file_id}/retry` to reprocess the stored upload without creating a duplicate version;
- `POST .../files/{file_id}/make-current` to restore a ready historical version;
- `DELETE .../files/{file_id}` to remove a version and its stored content.

Retry is state-safe: ready is idempotently returned, processing conflicts, and only an error row can start another parser attempt.

## Citation trust boundary

`FileContextBuilder` continues to give local Qwen exact `FILE_REF` locators. The new resolver parses locators from the final answer, then ignores every model-authored name/version/page field and resolves only `(file_id, chunk ordinal)` against ready files in the authorized project.

`ChatResponse.file_citations` contains canonical filename, version, page and a bounded source fragment. Forged, cross-project, missing and non-ready references are omitted. The browser renders citations with DOM nodes and authenticated downloads.

## Product UI

The Files surface now exposes:

- Russian processing/ready/error labels and parser detail;
- current version and full history navigation;
- retry, select-current, authenticated download and manager-only delete actions;
- file citation fragments below the Chat answer.

## Regression coverage

`tests/test_sprint64_files_rag_ux.py` covers version selection/deletion, failed-file retry, empty input, stale state recovery, database-validated citations, cross-project rejection and safe UI wiring. The release surface audit treats the lifecycle endpoints as essential.

## Target-node acceptance

Run the full release gate on the production 32-GiB+ node, then verify a text file, encrypted PDF, unsupported file, parser timeout/restart, historical restore and Chat citation download with owner, manager and viewer accounts.
