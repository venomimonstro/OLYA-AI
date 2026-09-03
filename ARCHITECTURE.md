# X1 architecture

## Цель

Максимально качественный локальный AI-продукт при основном сервере 3–4 тыс. ₽/мес., без GPU и без закупки чужого inference.

## MVP topology

```text
Browser / client
      |
      v
FastAPI X1 Backend
  |       |        |
  |       |        +--> Resource Governor --> llama.cpp --> Qwen3.6
  |       |
  |       +--> Context / Project State
  |
  +--> PostgreSQL
       users
       auth_sessions
       projects / project_members
       conversations / messages
       project_memories
       usage_events
```

В MVP PostgreSQL остаётся основным источником истины и позже может обслуживать лёгкую очередь. Redis не добавляется без измеримого bottleneck.

## Trust boundaries

### Клиент не является доверенным

Клиент может присылать только пользовательский/assistant контент. `system` messages в публичном Chat API запрещены. Trusted project instructions и memory строятся сервером после авторизации и проверки membership.

### Server-side sessions

После регистрации/входа клиент получает криптографически случайный bearer token. В БД хранится только SHA-256 digest токена. Сессия может быть отозвана немедленно, имеет срок жизни и не является самостоятельным носителем прав.

### Project access

Owner определяется полем `projects.owner_id`, остальные роли — `project_members`. Проверка производится перед чтением/изменением проекта, истории и памяти. Неавторизованный доступ к существующему проекту маскируется под `404`.

## Inference path

```text
Authenticated request
 -> scope/access check
 -> trusted system context + separate untrusted source excerpts
 -> stored conversation history
 -> incoming turn
 -> deterministic Context Compiler
 -> route fast/work/deep
 -> Resource Governor
 -> llama.cpp
 -> persist assistant message
 -> Usage Ledger
```

Если запрос не получил inference slot, новая неисполненная conversation transaction откатывается.

## Resource rules

- 1 heavy generation slot by default;
- bounded waiting queue;
- context is compacted before inference;
- later file parsers and document renderers must yield to interactive traffic;
- exact host capacity comes from `scripts/benchmark_host.py`, not from advertised vCPU count.

## Data model evolution

Production schema is versioned with Alembic. Application startup does not mutate production schema implicitly. Docker starts with `alembic upgrade head`; local development uses the same command. `create_all` remains only as an explicit test/development escape hatch. Future changes follow expand/migrate/contract so an application rollback does not depend on already-destroyed columns.

## File State (Sprint 2)

Files are tenant/project-scoped source data. `ProjectFile` keeps immutable versions and a current-version marker; `FileChunk` keeps parsed fragments with hashes and optional PDF page numbers. Raw file bytes live under a project/file/version path and are never addressed from user-supplied paths.

Upload path:

1. authorize project role;
2. enforce raw byte limit;
3. normalize logical/original names;
4. calculate SHA-256 and deduplicate unchanged content;
5. enforce replacement permission for an existing logical source;
6. persist raw bytes under a generated project/file/version path;
7. parse with archive/page limits;
8. chunk and hash extracted text;
9. only after successful parsing mark the new version current;
10. a failed parse is recorded as an error and never replaces the previous good source.

Retrieval deliberately starts simple. PostgreSQL narrows candidate chunks using query terms, then X1 scores a small candidate set in application code. This avoids running a second database/search service on the 3–4k RUB node. `pgvector` or PostgreSQL FTS can be introduced behind the same retrieval service when benchmarks justify them.

File excerpts injected into the model are labeled as **untrusted data**. A prompt inside a PDF/DOCX has no authority to change X1 system policy, permissions or tool capabilities.

## Canonical Task State (Sprint 3)

Long work is not reconstructed from the chat transcript. `Task` is a deterministic server-owned state object:

```text
Task
  project_id / created_by
  goal
  constraints[]
  status
  current_step
  state_version
  completed_steps / max_steps
  compute_seconds_used / max_compute_seconds
  acceptance criteria[]
  checkpoints[]
  evidence[]
```

### State machine

```text
created -> running -> waiting -> running
                   -> verifying -> completed
                   -> failed
                   -> cancelled
```

Terminal states are immutable. Invalid transitions are rejected. A task may enter `completed` only from `verifying` and only if every required criterion passes its verification contract.

### Optimistic concurrency

Every canonical mutation carries `expected_version`. In addition, SQLAlchemy maps `tasks.state_version` as a database version column, so the final `UPDATE` includes the previously observed version. If two workers read version 1 concurrently, only one may commit version 2; the other gets a stale-write conflict and cannot silently overwrite newer progress. API stale writes return `409`. This is required before multiple workers/agents are introduced.

### Evidence boundary

Public clients may submit evidence, but it is stored as `submitted`. They cannot set `verified`. Only trusted internal verifiers may create verified evidence via the service boundary. This prevents a client or LLM from manufacturing a successful completion simply by claiming that a test/source passed.

Manual criteria are intentionally different: the task owner/authorized manager can explicitly attest them. Evidence-based criteria require an actual verified Evidence Ledger entry.

### Checkpoints and resume

Checkpoints store a sequence number, canonical state version, current step and small structured working state. They do not store hidden model reasoning. Safe checkpoints are created on pause, cancellation, failure/completion boundaries and budget exhaustion.

### Runtime budget

Before an expensive agent/runtime step, the executor reserves bounded task budget. If the reservation would exceed `max_steps` or `max_compute_seconds`, the task is moved to `waiting` and checkpointed before additional local CPU is consumed.

### Chat integration

When a chat is attached to `task_id`, X1 injects the task goal, constraints, state version, current step, criteria and verified evidence into the trusted server context. A viewer may read task state but cannot invoke task chat/inference; compute requires at least project `member` role.

## Production persistence and local compute quotas (Sprint 4)

### Usage is real CPU time

`usage_events` now records total request time, queue time and actual local inference milliseconds. Failed llama.cpp attempts are also recorded because they consumed the owner's CPU even when no answer was produced. Monthly quota calculations therefore use measured inference time, not character/token estimates.

A `user_quotas` row defines plan, monthly local-compute seconds, per-user inference concurrency and background-job concurrency. The API checks the month-to-date ledger before calling llama.cpp. A user may not occupy all heavy model slots simply by opening many parallel HTTP connections.

When a chat request is attached to `task_id`, remaining `Task.max_compute_seconds` is also checked before inference and measured llama.cpp time is added to `Task.compute_seconds_used` afterwards. This makes the canonical task CPU budget enforceable rather than advisory.

### Durable jobs without Redis/Celery

`background_jobs` is a PostgreSQL-backed durable queue foundation. Jobs are deduplicated per owner with `idempotency_key`, have bounded retry counts and are claimed with an atomic lease token and expiry. Completion, heartbeat and failure operations require the active lease token. Expired leases may be recovered by another worker; repeated timeouts eventually exhaust attempts instead of producing immortal jobs.

One user's active job count is bounded. This prevents a single tenant from filling all background capacity. No permanently running worker container is required yet; `scripts/job_worker.py` is an on-demand generic runner for when real background handlers appear.

### Account lifecycle

Users can export a server-produced snapshot of their own account, projects they own, conversations, messages and usage events. Deactivation immediately revokes active sessions and prevents future login. Physical deletion is intentionally not an unsafe one-shot cascade: ownership transfer/retention rules must be explicit before deleting shared project data.

## Perfect Answer Engine (Sprint 5)

Answer verification is a separate deterministic layer around local inference, not another model provider. A chat request may run with `verification=off|auto|strict` and bounded formal requirements.

### Deterministic gates first

Cheap checks run before any second model pass:

- non-empty answer;
- unfinished placeholder detection (`TODO/TBD/FIXME`, templated gaps);
- required/forbidden literal text;
- minimum/maximum character length;
- valid-JSON requirement;
- URL provenance: an external URL is not considered trusted merely because the model printed it.

If deterministic checks fail, X1 may perform **one** focused repair pass and re-run the checks. It does not regenerate a good answer just to create a second opinion.

### Critic is not evidence

Strict mode may run one small critic pass using the same local llama.cpp model. The critic can lower confidence by identifying contradictions, missing requirements or unsupported claims. It **cannot** upgrade model output to `verified`. Real verification still requires a deterministic tool/source/evidence boundary.

### Failure degradation

Once a primary answer exists, failure of the optional repair/critic pass does not discard it. The answer is returned with downgraded quality status and a warning. All actual model time, including optional passes, is billed to the same local compute/task budget.

### Persistent audit

`answer_audits` stores deterministic check results, warnings, critic findings and request ownership. Quality audits are private; knowing an audit UUID does not grant another user access.

## Research grounding (Sprint 6)

Research sources are immutable content snapshots fetched by X1 itself. The source fetcher is an **outbound untrusted-data** capability, not a browser with access to the X1 host.

### SSRF boundary

Before every hop X1 validates scheme, port, hostname and resolved addresses. Loopback, private, link-local, multicast, reserved, unspecified and non-global addresses are rejected, as are localhost/`.local` names and URLs with credentials. Redirects are followed manually and revalidated. `httpx` is created with `trust_env=False` so an operator proxy cannot silently bypass the URL policy. When the transport exposes the peer socket, X1 also validates the connected peer address as a defense-in-depth DNS-rebinding check.

Only bounded `text/html`, `text/plain` and JSON responses are accepted. Download bytes, redirect count, extracted text size and timeout are capped before source text reaches the model.

### Snapshot and provenance

`ResearchSource` stores final URL, title, cleaned text, content SHA-256, HTTP/content metadata and `fetched_at`. Unchanged content is reused rather than duplicated. `SourceEvidence` can mark an **exact excerpt** as present in a stored snapshot; the excerpt must literally exist in that snapshot at creation time.

This deliberately proves provenance, not truth: `verified_excerpt` means "these bytes existed in the fetched source", not "the source's claim is correct".

Research snippets are added as a separate `user` role block labeled `UNTRUSTED RESEARCH SOURCE EXCERPTS`. They never enter the trusted X1/project system role. Perfect Answer URL checks recognize fetched source URLs, but URLs invented by the model remain unverified.

## Research Planner & Search Discovery (Sprint 7)

A `ResearchRun` is a resumable, project-scoped research job above the source snapshot layer. Its plan is deterministic and does not require a second model call merely to invent search queries. For `local_business`, X1 creates bounded queries for broad discovery, reviews, prices/official sites, ratings and services.

Search providers implement a replaceable `SearchProvider` interface. The first adapter targets Brave **web search**, not Brave Answers/AI, and is disabled unless the operator explicitly configures the provider and API key. Discovery results are normalized, deduplicated, classified (`official_candidate`, maps/catalog, reviews, generic web), and bounded before collection. Tracking-only URL differences do not create duplicate sources.

`ResearchRun.visited_urls` checkpoints already collected pages, so restart/resume does not crawl them again. `collect` reuses the Sprint 6 SSRF-safe fetcher and persists the resulting source snapshots. If search is not configured, the run remains `waiting_search` and can continue after operator configuration rather than being lost.

## Local Business Intelligence (Sprint 8)

`BusinessIntelligenceService` resolves search results into candidate real-world organisations before comparing them. Entity resolution is deliberately conservative: titles are normalized for common rating/review suffixes and tracking noise, but unrelated businesses are not merged merely because URLs look similar. A candidate records official-site, maps/catalog, review and independent-web source sets plus identity conflicts.

Two scores are deliberately separate:

- `evidence_score` estimates confidence/diversity/freshness of the assembled company record;
- `comparison_score` is an early consumer-quality signal derived only from independent public rating observations and review counts.

A self-published rating on the company's own site does not enter the independent rating aggregate. Review counts receive logarithmic weight so 900 reviews count more than 12 without allowing one giant platform to dominate every other source.

Company state is `insufficient_data`, `preliminary`, `conflicted` or `comparable`. `comparable` currently requires at least two independent rating sources plus sufficiently strong source confidence and no identity conflict. A "winner" is exposed only when the leading comparable candidate has strong evidence and a meaningful margin over the runner-up; otherwise X1 explicitly returns no confident leader. This prevents the product from converting "first search result" into a fabricated "best clinic" verdict.

The complete analysis is persisted in `ResearchRun.business_analysis`, so it can be revisited/recomputed as new snapshots are collected rather than existing only in an LLM answer.

## Search Provider Pool (Sprint 9)

Search discovery is now routed through `SearchRouter`. The core no longer assumes one provider: configured adapters expose a common `SearchProvider` interface, and the router scores them by locale fit, measured success/failure history and latency.

Two execution policies keep quality and cost separate:

- `economy`: try providers in priority order and stop once one yields results; continue only on failure/empty output;
- `quality`: query all currently healthy providers, interleave their ranked results and deduplicate canonical URLs across indexes.

Local-business research uses `quality`; ordinary research uses `economy`. Provider errors are partial failures rather than automatic research failure as long as another provider succeeds. Search telemetry persists request/success/failure counts, last error and latency in `search_provider_stats`. Repeated severe failure degrades routing priority, but if every adapter is degraded X1 still attempts the pool instead of deadlocking permanently.

`search_query_cache` persists normalized result sets in PostgreSQL, avoiding a permanent Redis service on the cheap node. Cache identity includes query, locale, configured provider set, requested result count and execution mode, so an earlier one-provider economy hit cannot incorrectly satisfy a later quality request. The router never silently turns external search into external AI inference.

## Admin Control Plane (Sprint 10)

Administration is a separate server-side authorization boundary. `users.is_admin` is checked on every `/v1/admin/*` route; knowing the URL or possessing an ordinary authenticated session does not grant control-plane access.

### First administrator

The first admin is promoted only through `POST /v1/admin/bootstrap` with a dedicated operator secret. Bootstrap is refused when the secret is empty/default and is permanently unavailable after any administrator exists. There is no public self-promotion endpoint.

### Operations

The admin API can list/update users, revoke all sessions, change plan/compute/concurrency/job quotas, view aggregate inference/queue statistics, inspect search-provider health and problem AnswerAudits, manage non-secret persistent `SystemSetting` values, and read `AdminAuditLog`.

Critical mutations append an audit row with actor, action, target and bounded structured details. Secrets (API keys, database passwords, session tokens) are not valid persistent `SystemSetting` values; they stay in operator environment/secret storage.

`/admin` is a lightweight same-process HTML/JS console using the protected APIs. It adds no Node/React service or additional long-running container on the small VPS. The page is `no-store`, frame-denied and uses a restrictive CSP; its bearer token lives only in `sessionStorage` for the tab.

## Safety / Abuse / Legal Review Center (Sprint 11)

Safety is split into three different trust levels:

```text
RiskEvent (signal) -> SafetyCase (human investigation)
                  -> UserRestriction (bounded enforcement)
                  -> LegalReview (separate legal/disclosure decision)
```

`RiskEvent` is only a signal and is never a legal conclusion. Automatic detectors or administrators may create a signal with bounded structured evidence. A human administrator opens a `SafetyCase`, verifies/dismisses signals and records the decision. Temporary restrictions are explicit capabilities (`chat`, `research`, `tools`, `images`, `all`) with expiry/revocation rather than an implicit global ban.

The chat inference path enforces active restrictions before reserving model compute. Administration does not have a silent chat-reader endpoint: case-linked conversation inspection is a break-glass operation requiring a concrete reason, correct case/user ownership and an immutable `AdminAuditLog` entry.

Legal review is deliberately separated from moderation. A case may create a `LegalReview` only after review. The requester records the asserted legal basis and exact data scope. Approval requires a **different administrator**, preventing one operator from opening and approving their own disclosure. Preparation only builds a minimized manifest from an allowlist (`user_id`, `email`, `risk_events`, `case_summary`) and stores a SHA-256 for integrity; unsupported sensitive fields are discarded. Sprint 11 does not transmit that bundle anywhere. Any later external disclosure requires a separate connector/integration and its own explicit human/legal authorization.

## Diagnostics & Frustration Observatory (Sprint 12)

`FrustrationEvent` is a privacy-minimized signal for product failures that may lose a user even when the HTTP service itself is alive. Server-side detectors reuse already-recorded `UsageEvent` metrics, so they do not add a second LLM pass. Current automatic signals include inference failure, slow queue, slow total response, exact normalized repeated query and abnormal context expansion. The future streaming client can additionally emit cancellation, regeneration, stream-error and UI-error signals through a strict allowlist.

Events store IDs, kind/severity/source, bounded technical metrics, fingerprint and resolution state. They deliberately do **not** duplicate full user messages. Client telemetry keys are allowlisted and arbitrary text/secrets are dropped. One request/kind/fingerprint is deduplicated in the current transaction and against persisted events.

Admin `/v1/admin/frustration/*` endpoints expose recent events and 24h aggregate counts; resolving an event is audited through `AdminAuditLog`. `/admin` shows open frustration volume and recent signals alongside safety/quality/search health. This is diagnostic evidence, not permission to bypass the case-linked content review boundary from Sprint 11.

## Performance & Resource Optimization Lab (Sprint 13)

`PerformanceSnapshot` is a persisted aggregate over real `UsageEvent` rows plus Quality/Frustration outcomes for a bounded time window. It records response/queue/inference P50/P95/P99 where the underlying data exists, CPU-seconds per successful request, context efficiency, success rate, quality failure rate and frustration rate. On Linux it also records an **instantaneous** process RSS, total/available memory and load average; those fields are not mislabeled as a sampled high-water mark.

Current chat inference is non-streaming, so X1 deliberately reports `ttft_available=false`. A later streaming runtime must record first-token timestamps before TTFT can become a real metric. The same rule applies to prefill/decode split, page faults and swap pressure: missing telemetry is explicit instead of fabricated.

`OptimizationExperiment` compares a baseline with a candidate through deterministic gates. A candidate is rejected if its quality pass rate regresses beyond tolerance, its frustration rate worsens, or it fails to improve either P95 latency or CPU-seconds per successful task. Therefore faster-but-worse settings cannot be promoted merely because throughput increased. The admin console can create/list performance snapshots and experiments without adding a permanent metrics service on the small node.

## Perfect Document Engine (Sprint 14)

Documents are explicit versioned artifacts rather than one-off files. `DocumentArtifact` is the user/project logical document, `DocumentRevision` is an immutable authored revision, and `DocumentQAEvent` records every release gate run.

Creation writes a semantic DOCX from a bounded structured specification (headings, paragraphs, numbered/bulleted lists, tables and page breaks) using Word styles rather than drawing text. The revision stores a source-spec SHA and DOCX SHA-256. A failed file write is transactional: the incomplete database revision is rolled back and partial revision directory is removed.

Release path:

```text
structured spec
 -> DOCX
 -> structural XML/package checks
 -> LibreOffice headless render in isolated profile
 -> PDF integrity/text checks for every page
 -> PNG raster for every page
 -> deterministic page geometry/blankness/edge checks
 -> QA event
 -> explicit release
```

No revision can be released without a passing QA event. Release re-hashes both DOCX and rendered PDF and rejects any post-QA modification. A later draft therefore never replaces the last good release until it independently passes the full gate. Project members may create/QA their own artifacts, while shared project release requires manager/owner authority.

Current visual QA is deterministic: every page must have a valid raster, page geometry, meaningful ink and no obvious content touching the crop boundary. Semantic vision analysis is explicitly reported as `not_configured`; the same stored page PNGs are the hook for a later Qwen3.6/mmproj pass rather than pretending raster heuristics can understand layout semantics.

## Code Workspace & Single Agent foundation (Sprint 15)

Code workspaces live only under an X1-owned storage root and are scoped to a user/project. ZIP imports are extracted into a temporary directory and reject absolute/traversal names, symlinks, `.git`, oversized expanded archives and file-count explosions before replacing the existing repository tree.

`CodeAgentRun` persists plan, checkpoint, approved path scope, changed files, command results and command budget. File writes require a preimage SHA-256, so a user edit made after the agent planned a change causes a conflict rather than a silent overwrite.

Tool execution is intentionally narrower than a normal shell. X1 uses `subprocess.run(..., shell=False)` with an executable allowlist, timeout, output cap, cleaned proxy environment and workspace-only CWD. This is **not** treated as a complete network/filesystem sandbox: Python/test runners can themselves open sockets or read accessible host files. Therefore arbitrary build/test execution is disabled by default (`X1_CODE_ALLOW_UNSAFE_COMMANDS=false`); only static-safe checks may run until the deployment provides a real container/namespace sandbox backend.

## Planned image generation / self-improvement path

Image support is split so a cheap VPS does not pay the RAM/storage cost permanently:

1. local on-demand image runtime with resource queue and generation limits;
2. Perfect Image QA with targeted retry plus AVIF/WebP/thumbnails/content-addressed storage, retention and disk watermarks;
3. Admin Media Center with versioned server-side safety superprompt/policy and regression tests before policy rollout;
4. curated self-improvement datasets from QA-passed, consent-eligible positive examples and separate regression/negative examples. Training/re-ranking candidates must beat a frozen benchmark without unacceptable RAM/latency regression before promotion.

The model is never allowed to learn indiscriminately from all of its own outputs: that would amplify artifacts and create a self-reinforcing quality collapse.

## Image Generation Runtime (Sprint 16)

Image generation is a durable background capability, not part of the always-resident chat process. `ImageGeneration` is the logical/user-visible job and `ImageBlob` is content-addressed immutable binary storage. An image request is rejected before queueing if requested dimensions, pixel count, inference steps or per-user active-generation count exceed configured limits. The API creates a typed `image.generate` BackgroundJob and returns immediately.

`scripts/image_worker.py` is an **on-demand** worker. It leases only `image.generate` jobs, so it cannot steal ordinary background tasks, loads the configured local backend only in the worker process, validates the produced image by actually decoding it, persists a SHA-256 blob atomically, and exits after a bounded idle window. A supervisor may start it when image work exists, but X1 does not add a permanent image process/container to the base topology.

The default backend is `disabled`, which fails before job creation so an operator cannot accumulate impossible work. `mock` exists only for deterministic tests/smoke. The optional `diffusers` adapter accepts only an **existing local directory** and loads with `local_files_only=True`; model-hub identifiers are rejected rather than downloaded. No external image-generation API fallback exists.

One physical blob may back multiple logical generations if bytes are identical. Authorization always resolves through `ImageGeneration`, never through a raw global blob ID. The manifest stores requested dimensions/steps/seed plus backend/model identity for reproducibility. Queue-time cancellation is supported; in-flight cooperative cancellation remains backend-specific and is not falsely claimed.

## Perfect Image QA & Storage Optimization (Sprint 17)

A generated source blob is not automatically deliverable. `ImageQAEvent` records each deterministic QA attempt and defect codes. Current deterministic gates verify decodeability, exact requested dimensions, non-empty/non-transparent content, useful dynamic range and obvious border activity. These checks are deliberately not described as semantic detection of hands/faces/text; semantic VLM QA remains a separate future layer.

A repairable deterministic defect may trigger at most `X1_IMAGE_QA_MAX_REPAIRS` (default one) targeted regeneration pass. Failure after the bounded repair becomes `qa_failed` and the asset is not downloadable. A successful generation is `ready` only after QA.

Delivery variants are immutable `ImageVariant` rows. X1 encodes full-size WebP/AVIF (where Pillow supports AVIF) and a WebP preview. It evaluates several quality levels and keeps the smallest variant whose mean normalized RGB error remains below `X1_IMAGE_PERCEPTUAL_ERROR_MAX`; it does not blindly choose a fixed compression quality. `preferred_blob_id` points to the smallest acceptable full-resolution asset, while the source master remains available for reproducibility/editing. All variant bytes use the existing SHA-256 content-addressed blob store.

Before enqueue, X1 enforces both a filesystem free-space watermark and a per-user logical storage quota. User quota accounts for source blobs plus delivery variants associated with their source assets. A maintenance command can detach expired `failed`, `qa_failed` and `cancelled` generations after retention; physical blobs are deleted only after reference checks prove no generation, preferred link or variant still needs them.

## Admin Media Center, Image Policy & Semantic QA (Sprint 18)

Media administration is a separate control plane at `/admin/media` and `/v1/admin/media/*`. Admins see storage totals, generation/QA status, user usage and policy versions without automatically receiving full private prompts. Reading a user's prompt or media through the admin inspection endpoint is a break-glass action requiring `X-Admin-Access-Reason`; the reason and target are appended to `AdminAuditLog`.

`ImageSafetyPolicy` is immutable-versioned policy data with `draft/staging/published/archived` state. The policy combines structured server-enforced deny phrases with an administrator superprompt/negative-prompt additions. Structured deny checks run **before** an expensive image job is created. Publishing is impossible unless the policy has both allow and block `ImagePolicyRegressionCase`s and every stored expectation passes; rollback therefore means publishing a previously tested version, not silently mutating production text.

Every `ImageGeneration` records the policy version used and its safety/delivery status. Admin quarantine immediately blocks user delivery while preserving bytes for investigation; restore is allowed only from quarantine, while revoke is terminal for normal delivery. Policy violation detection may automatically quarantine a finished generation if a policy changed while it was queued.

Optional `LocalVisionQA` provides semantic post-generation review for malformed anatomy, duplicated objects, broken text or other visual artifacts. It is allowed only against an explicit loopback HTTP endpoint (`127.0.0.1/localhost/::1`) and has no external fallback. Deterministic QA remains the cheap first gate; semantic VLM cost is therefore an operator-controlled extension rather than a mandatory second model pass.

## Curated Image Self-Improvement (Sprint 19)

Self-improvement is an explicit data-governance pipeline, not automatic recursive training on every generated image. A user may submit one `ImageFeedback` row per generation with `allow_training` defaulting to false. Positive training candidates require consent, delivered/ready state, passed QA and a high rating; low-rated consented outputs can only become `regression` examples. This prevents model artifacts from becoming positive targets merely because X1 created them.

`ImageTrainingExample` stores immutable generation/blob/prompt/backend/model/policy/QA provenance, deterministic dedupe key and a perceptual hash. Identical samples are deduplicated exactly; near perceptual duplicates are not admitted as new approved positives, limiting repeated-style/user bias. Admin decisions (`approved/rejected`) are audited implicitly through the curated state; users cannot approve their own examples.

`ImageDatasetSnapshot` freezes an immutable manifest of currently approved examples and its SHA-256. Consent withdrawal, generation quarantine/revoke or other source invalidation tombstones the related example and invalidates any dataset snapshot that references it. An invalidated snapshot therefore cannot be treated as a legal/quality-authorized basis for a new training run.

`ImageImprovementRun` is a holdout evaluation record for a locally produced prompt adapter, LoRA or QA/ranker candidate. It compares baseline/candidate quality, artifact failure and compute cost. Quality regression, artifact regression or excessive compute growth causes `rejected`; passing the gate yields `accepted` but **does not auto-promote** the candidate into production. Runtime promotion remains a separate controlled release decision.

## Isolated Project Development Runtime (Sprint 20)

`ProjectRuntime` is the trust boundary for a long-lived software project. A runtime is bound one-to-one with an X1 Project and Code Workspace, and its persisted manifest records resource ceilings, network policy, repository size and the host isolation capability. Runtime roots and snapshot archives live under a separate configured storage root and are addressed only by server-generated IDs.

`ProjectRuntimeSnapshot` freezes a repository manifest and tar archive before risky changes; identical manifests reuse the same snapshot. `ProjectRuntimeSecret` stores only AES-GCM ciphertext in PostgreSQL. The encryption key is an operator secret (`X1_PROJECT_RUNTIME_SECRET_KEY`) and the API never returns plaintext/ciphertext in normal reads.

Security invariant: filesystem separation alone is not called a sandbox. The runtime probes Linux user+network namespaces. If the host cannot provide them, arbitrary untrusted project execution remains prohibited until a container/namespace runner with enforceable CPU/RAM/PID/disk/network limits is available. X1 source, Docker socket, host SSH credentials and other tenant roots are never project mounts.

## Project Architect & Sprint Orchestrator (Sprint 21)

`DevelopmentPlan` is the canonical product-development state above individual `Task` rows. It stores the user brief, prioritized requirements, architecture summary, constraints, current sprint and an optimistic `state_version`. `DevelopmentSprint` and `DevelopmentWorkItem` preserve roadmap order and dependencies; architecture decisions are immutable-key ADR-style records.

A project may be bootstrapped from a structured API payload or from a plain-language brief using the same local llama.cpp runtime. Model output is treated only as a draft: X1 requires strict JSON, validates unique ordinals, backward-only dependencies, bounded sizes and acceptance criteria before any plan is persisted. No external inference provider is involved.

Activating a sprint materializes every work item into the existing Canonical Task/Evidence system rather than inventing a second execution engine. Sprint-level acceptance criteria not already covered by work items become a dedicated verification work item, so the sprint cannot be marked complete until all Definition-of-Done checks have passed through Task completion gates.

Replanning is history-preserving. Active/completed sprints and their Tasks are frozen; only future `planned` sprints may be replaced. X1 creates a deterministic `DevelopmentCheckpoint` before replacement. The compact current-sprint state and active architecture decisions are injected into trusted project context so a user can say “continue the sprint” without replaying the project specification.

## Multi-Agent Engineering Team (Sprint 22)

Engineering roles are logical capabilities, not concurrent model processes. One `EngineeringRun` is bound to one materialized `DevelopmentWorkItem`/Canonical `Task`. Server code owns transitions and permissions; model text cannot change `current_role`, task completion, project scope or tool authority.

The default transition is `coordinator -> architect -> developer -> tester -> reviewer`. Reviewer `revise` starts a bounded `developer -> tester -> reviewer` cycle. `max_cycles` prevents agent debate loops. Every successful role produces an immutable `EngineeringRoleTurn` with role/cycle/sequence, input state SHA-256, validated output, model identity and inference time. Invalid model JSON consumes compute but does not advance the workflow.

All role inference shares the existing global and per-user resource governors and records compute in both `UsageEvent` and the linked Canonical Task budget. Role context is reconstructed from PostgreSQL state plus a bounded repository map; no hidden free-form agent memory is authoritative. Proposed paths are normalized as workspace-relative paths. Sprint 22 intentionally grants no file-write, command-execution, network or completion capability to role turns. Those capabilities remain behind the isolated execution gates planned for Sprint 23.

## Sprint 23 — Autonomous Build / Test / Debug execution loop

Approved engineering plans are now executable through a separate server-owned `EngineeringExecution` state machine. An execution is allowed only after the corresponding `EngineeringRun` reached `approved` and is bound to a project runtime/workspace.

Execution sequence:

1. Persist an immutable runtime snapshot before touching files.
2. Build a bounded implementation context from the approved Coordinator/Developer/Tester/Reviewer handoffs and only the explicitly approved files.
3. Ask the local model for minimal full-file replacements. Each replacement must echo the preimage SHA-256 observed in the execution context.
4. Re-check project scope and preimage SHA immediately before each atomic write. A user edit made while inference was running aborts the patch instead of being overwritten.
5. Run only server-approved verification commands. Changed Python files also receive deterministic `python -m py_compile` checks.
6. If verification fails, restore only the changed paths from the pre-execution snapshot. At most `max_repairs` targeted repair cycles may run.
7. A successful verification stores verified task evidence and a complete execution event trail. It does not automatically mark unrelated acceptance criteria satisfied.

### Sandbox truth boundary

`linux_namespace` currently proves user/network namespace availability, not a complete filesystem jail. Therefore arbitrary project commands (`pytest`, npm scripts, Composer, user programs) remain blocked unless the operator has configured a stronger backend reported as `container` or `filesystem_jail` and explicitly enabled unsafe commands. This prevents X1 from treating network isolation as full host isolation.

`EngineeringExecutionEvent` records snapshot, implementation, patch application, verification, rollback/repair and completion decisions. The execution itself uses SQLAlchemy optimistic versioning to reject concurrent state overwrites.

Preview is represented in the execution completion evidence as `not_configured` until a read-only preview sandbox is implemented. X1 does not expose workspace files as a web application from the main backend merely to claim preview support.

## Sprint 24 — Container sandbox and internal preview

Arbitrary project build/test commands no longer need an unsafe host subprocess. The sandbox layer discovers Docker or Podman and requires a preinstalled local image; it never pulls an image automatically. A project command runs with `--pull=never`, a read-only container root, `cap-drop=ALL`, `no-new-privileges`, PID/RAM/CPU limits, a bounded tmpfs, network `none` for deny-policy runtimes, and only the current project workspace plus a runtime scratch directory mounted. Docker socket, host SSH keys, X1 source and other project roots are never mounted.

`EngineeringExecution` continues to use direct host execution only for deterministic static-safe commands. Any broader approved verification command is delegated to the container sandbox. If the runtime/image is unavailable, verification fails closed and the existing snapshot rollback path is used; there is no unsafe fallback. `ProjectSandboxRun` persists the capability snapshot and command results for diagnostics and later admin visibility.

Preview uses a separate approved developer contract (`preview_command`, `preview_port`, `preview_health_command`). X1 can start the service in the isolated container and run the health command inside that same container. The container is intentionally not published to an external/user-facing port yet, because ordinary bridge publishing would also grant uncontrolled egress. A future restricted-egress preview proxy must mediate user access before `public_url` can be populated.

## Git trust boundary (Sprint 25)

Local Git is part of every `ProjectRuntime` and is the canonical change-history layer beneath chat-native development. The project has one `GitRepositoryBinding`; it begins local-only and may be upgraded to one normalized GitHub repository. Remote credentials are references to encrypted `ProjectRuntimeSecret` records, never URL/userinfo fields. Git is invoked with `shell=False`, system/global configuration disabled where possible, hooks disabled, `protocol.file.allow=never`, terminal prompting disabled and bounded stdout/stderr.

External Git transport is a capability: clone/fetch require explicit confirmation; push additionally requires a stored `push_enabled` permission and manager authority. Before push, X1 resolves the current remote branch head, scans the complete outbound commit range for secrets, checks optional expected remote/local heads, fetches the remote branch and requires it to be an ancestor of local HEAD. No force push is exposed. Branch workflow uses a separate stable `x1/<project>` branch instead of silently targeting the default branch. Persistent `GitOperation` audit rows contain heads and summaries but no credential values.

## Chat-native Development Controller

`DevelopmentChatSession` is the conversation-scoped pointer into canonical software-development state. It never replaces `DevelopmentPlan`, `Task`, `EngineeringRun` or `EngineeringExecution`; it only resolves the next safe action and exposes a compact user-facing state. Exact control intents are routed from `/v1/chat` to the controller, while ordinary project questions remain ordinary inference requests. One user message advances at most one bounded engineering phase. Verified executions can be committed locally, while remote writes remain behind the existing GitHub external-action capability gate.
