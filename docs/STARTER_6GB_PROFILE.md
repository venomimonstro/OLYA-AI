# OLYA AI — starter_6gb

Target: **4 CPU cores around 3.3 GHz, 6 GiB RAM, 80 GB disk**. The purpose of this profile is a commercially usable first node that stays alive under bursts instead of pretending it is a 32–64 GiB production server.

## Model and quality policy

Primary local model: **Qwen3-4B Q4_K_M**, pinned by repository/revision/SHA-256 in `model-manifest.json` (about 2.5 GB GGUF). The node uses a 4096-token server context, q4 K/V cache, one llama.cpp parallel slot and three inference threads by default. This leaves one CPU core available for the API, PostgreSQL, search and the host.

The product still exposes Fast / Work / Deep. On the starter node the response envelopes are intentionally bounded: Fast up to 448 output tokens, Work up to 640–768, Deep up to 1024. Deterministic quality checks remain enabled. Semantic critic/repair is limited to at most one extra model pass, so `strict` cannot silently triple CPU cost.

This is a quality/cost tradeoff, not a claim that 4B equals the previous 35B model. Fresh public facts should use the self-hosted SearXNG research path. A larger model can be introduced later as a separately accepted server profile without changing user/project data.

## Always-on memory envelope

| Service | hard/default limit |
|---|---:|
| llama.cpp + Qwen3-4B | 3200 MB |
| X1 app | 768 MB |
| PostgreSQL | 384 MB |
| SearXNG | 256 MB |
| Total container ceilings | ~4608 MB |

That deliberately leaves roughly 1.4 GiB from a nominal 6 GiB host for Linux, Docker, filesystem cache and short-lived overhead. The starter installer attempts to create a **2 GiB emergency swapfile** only when the host has no swap. Swap is a crash cushion, not inference memory; swappiness is set low.

PostgreSQL is tuned for the node (`shared_buffers=64MB`, `work_mem=2MB`, `maintenance_work_mem=32MB`, `max_connections=30`). The SQLAlchemy pool is 3 + 1 overflow. The app uses one Uvicorn worker and bounded HTTP admission.

## Features kept on the starter node

- Chat: Fast / Work / Deep / streaming.
- Projects, conversations, memory and files.
- Local RAG/file context with smaller parsing/storage envelopes.
- Self-hosted SearXNG research.
- API keys/API chat with lower rate limits.
- Billing/subscriptions and plan/resource accounting.
- Development/code generation that does not require local sandbox execution.
- Admin, analytics, recovery and backups.

Intentionally disabled on `starter_6gb`:

- local image generation/editing;
- sandbox execution/live previews;
- LibreOffice/Poppler document rendering and visual document QA.

The data model and APIs remain in the project. After a RAM/compute upgrade those components can be switched to remote workers or a larger node; starter mode does not delete them.

## Queue and overload behavior

The server performs **one LLM generation at a time**. This is deliberate: two simultaneous 4B decodes on four CPU cores usually increase latency for both and create memory pressure without doubling useful throughput.

Bursts are handled in layers:

1. HTTP chat admission allows only a small number of active expensive requests and keeps a bounded pre-DB queue.
2. The inference scheduler has a bounded queue and only one active local generation.
3. One principal/account may occupy only **one inference waiting slot**. One user cannot fill the queue.
4. Fast requests have the best base priority, Work follows, Deep/background are lower. Paid plans receive only a small priority boost.
5. Aging continually improves the priority of older work, so Free/Deep requests cannot starve forever.
6. When capacity is genuinely full or a deadline expires, the server fails closed with retry semantics instead of allocating more RAM and falling into OOM.

The result is controlled waiting rather than a server crash.

## Commercial request units

Request units make tariffs understandable. They are separate from measured CPU seconds/resource budgets, which remain the hard cost ceiling.

- Fast = **1 unit**
- Work = **2 units**
- Deep = **4 units**
- API and UI use the same units for the same selected complexity.
- Failed/cancelled answers do **not** consume request units. Actual inference time can still consume compute budget, preventing cancellation abuse.

Default starter plan limits:

| Plan | Price/month | Monthly units | Daily fair-use units | Approx Work answers/month* |
|---|---:|---:|---:|---:|
| Free | 0 ₽ | 30 | 6 | up to 15 |
| X1 | 300 ₽ | 240 | 24 | up to 120 |
| Pro | 700 ₽ | 720 | 60 | up to 360 |
| Max | 1,500 ₽ | 1,800 | 120 | up to 900 |
| Business | 4,000 ₽ | 4,800 | 300 | up to 2,400 |

`*` Request units are not a promise that every request will be cheap enough to reach this count. The existing measured compute/resource quota may stop unusually long workloads earlier.

Daily fair-use limits are important on one small node: a subscriber cannot buy one month and consume the whole month’s theoretical capacity in one afternoon.

## Basic economics at 4,000 ₽/month server cost

Ignoring acquiring/payment/tax costs, server rent alone is covered by approximately any one of:

- 14 × X1 = 4,200 ₽ MRR;
- 6 × Pro = 4,200 ₽ MRR;
- 3 × Max = 4,500 ₽ MRR;
- 1 × Business = 4,000 ₽ MRR.

The request-unit limits are intentionally conservative relative to the fixed server month. For example, X1 exposes up to 120 Work-equivalent answers but still has the existing compute budget underneath it. This makes early oversubscription economically possible without promising unbounded CPU time.

Do **not** raise plan limits from theoretical token/s numbers. First run real traffic/load acceptance on the actual rented CPU and calibrate from p95 latency, queue time, average inference seconds per successful answer and paid conversion.

## Disk policy for 80 GB

- Qwen3-4B GGUF: about 2.5 GB.
- Keep at least **10 GB free**; file/image storage guards use this floor.
- User file quota defaults to 512 MB; images are disabled.
- The starter app image omits LibreOffice/Poppler.
- Do not keep the old ~20 GB 35B rollback GGUF on this node unless there is plenty of free disk; rollback identity remains in the manifest, but it is not automatically downloaded.
- Backups and retention should be monitored as traffic grows.

## Installation

```bash
bash scripts/install_starter_6gb.sh
```

The installer validates RAM/disk, generates secrets, configures starter limits, downloads and SHA-verifies the pinned Qwen3-4B model, builds the lightweight app image, starts PostgreSQL/SearXNG/llama/app, runs the starter contract audit and creates/restores an initial backup.

## Acceptance

For technical starter acceptance:

```bash
python3 scripts/starter_6gb_acceptance.py
```

For a commercial launch, first configure `X1_BILLING_CHECKOUT_URL_TEMPLATE` to the real HTTPS payment/checkout flow and then run:

```bash
python3 scripts/starter_6gb_acceptance.py --require-billing
```

The separate Sprint 80 real-load harness should then be run with at least 10 distinct test accounts before scaling advertising or lifting limits. No capacity claim is considered proven until it is measured on the actual rented CPU.

## Upgrade triggers

Upgrade the node/profile when one or more of these persist rather than occur as an isolated spike:

- p95 inference queue time > 20–30 s during normal paid traffic;
- queue timeouts/rejections become common despite fair-use limits;
- llama.cpp is busy most of the useful day;
- swap is actively used during normal requests;
- paid demand needs sandbox/document/image workers on the same node;
- 4B answer quality materially limits retention/conversion even with research/RAG;
- disk free space approaches the 10 GB guard.

The first upgrade should normally be **RAM (12–16 GB) and CPU**, then a separately benchmarked larger model/profile. Do not simply raise concurrency on the 6 GB node.
