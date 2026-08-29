# Architecture

Video Studio Storyboard Artist & Production Planner separates the public job-control surface from private model execution. The API and worker run the same pinned container image with different service-role configuration. A deterministic local compiler transforms the validated creative brief into an audited, downloadable plan-only package. An optional Gemini image stage adds bounded planning illustrations as a separately validated sidecar; it does not render or modify source media.

```mermaid
flowchart LR
    User[Judge or creator] -->|HTTPS / installed PWA| UI[Same-origin standalone-capable UI]
    User -->|Local + Attach| LocalSources[PDF or text story<br/>raw-video metadata]
    LocalSources -->|Extracted text + bounded inventory<br/>raw bytes stay local| UI
    UI -->|Same-origin job routes<br/>access code header| API[Public Cloud Run API<br/>role = api]
    API -->|Create/read/update job| DB[(Cloud Firestore)]
    API -->|Enqueue job ID| Queue[Google Cloud Tasks]
    Queue -->|OIDC token<br/>Cloud Run audience| Worker[Private Cloud Run worker<br/>role = worker]
    Worker -->|Lease-fenced claim<br/>transactional result| DB
    Worker -->|Structured brief request<br/>official google-genai SDK| Vertex[Vertex AI<br/>Gemini 3.5+ brief model]
    Worker -->|Bounded 16:9 panel requests| ImageModel[Vertex AI<br/>Gemini 3.1 Image]
    Vertex -->|Schema-constrained response<br/>provider metadata| Worker
    Worker -->|Validated creative brief| Compiler[Deterministic storyboard<br/>timeline compiler + auditor]
    Compiler -->|Plan-only JSON package<br/>manifest digest| Worker
    UI -->|Poll status| API
    UI -->|Local deterministic derivation| Handoffs[Exact-setting location plan<br/>character + synopsis dossier]
    UI -->|Local downloads| Package[Package JSON + visual/detailed HTML<br/>location HTML/CSV/JSON<br/>character HTML/TXT/JSON/CSV<br/>shot-list CSV + rough-cut EDL CSV]
    UI -->|Compiled durations + panels| Animatic[Timed in-app animatic<br/>previsualization only]
    UI -->|Bounded cue text| Pitch[Qualified English Natural/Neural browser speech when available<br/>narration TXT always]
```

## Responsibilities

| Component | Responsibility |
| --- | --- |
| Browser UI | Collect natural chat; locally extract supported story files; inventory raw-video metadata without uploading bytes; display durable status; compose clarification context in window memory; render visual/detailed boards and a timed animatic; derive exact-setting location and evidence-limited character/synopsis handoffs; preview bounded cue text only with a qualified English Natural/Neural browser or operating-system voice; export JSON, HTML, TXT, CSV, and source-aware rough-cut EDL files; and open the visual sheet for Print / Save PDF. |
| Browser-side plan derivations | Group exact normalized settings and compute scheduling metrics; combine the brief synopsis with exact supported character appearances and scene outline. These derivations make no additional model call, do not mutate the source package, do not merge merely similar locations, and do not infer unsupported character roles, biographies, relationships, arcs, casting, pronouns, or speaker attribution. |
| PWA shell | Provide a same-origin manifest, static shell service worker, install prompt integration, and standalone display. API/job routes are never cached. |
| Local source boundary | Read PDF/text story content and raw-video metadata in the browser. Script text is sent only as creative source inside the same-origin job request; selected video bytes are never sent. No footage-content analysis is claimed. |
| Visual sidecar | Keep one ordered record per planned shot, validate hashes/base64/JPEG dimensions and byte limits, show missing panels explicitly, and never weaken or rewrite the deterministic JSON plan. |
| Public API | Validate the access code and exact request body, enforce shared Firestore admission limits, create/read/cancel/retry jobs, and enqueue work. |
| Firestore | Persist admission state, the lease-fenced job state machine, cancellation intent, attempts, structured result, execution metadata, and measured durations. |
| Cloud Tasks | Deliver the job ID plus application attempt to the private worker with an OIDC identity, worker-specific audience, and deterministic task name. |
| Private worker | Transactionally claim or reclaim work, call the configured model, validate the exact brief schema, compile/audit the package, and finalize only while holding the current lease. |
| Vertex AI adapter | Verify model lookup and request schema-constrained JSON through the official Google Gen AI SDK. |
| Deterministic compiler/auditor | Expand ordered scenes into planned shot cards, allocate a contiguous non-drop 24-fps timeline, verify coverage/continuity and source guidance, and hash the canonical package. |

## Trust boundaries

- The page uses relative, same-origin requests. The server does not emit a permissive CORS header, and the page content-security policy limits connections to its own origin.
- Every job create/read/cancel/retry request requires the owner-created demo access code. Only its SHA-256 digest is configured server-side, and comparisons are constant-time. This is demo cost control, not a general user-account system or rate limiter.
- A global Firestore admission record defaults to 24 new jobs per hour with a three-second cooldown. It bounds shared-demo admission but is not a hard provider-call ceiling because crash redeliveries are separate from the three application attempts.
- The worker is not public. Cloud Run IAM validates the Cloud Tasks caller's OIDC token before the internal route is reached.
- Worker leases fence stale deliveries. Expired work can be reclaimed; a late worker cannot publish after losing its lease, and cancellation wins when it reaches transactional finalization first.
- The API, worker, and task caller use separate least-privilege service accounts.
- User text is creative input. The model system instruction requires the exact JSON contract and treats text inside the request as content rather than authority to change that contract.
- The plaintext access code remains only in the password input for the current window. It is never put in a URL, localStorage, sessionStorage, durable job body, download, or log. Only the same-origin authentication header carries it to protected job routes.
- Successful jobs discard the raw submitted message after provider use. The durable result is additionally bounded below the Firestore document limit; optional visual panels are shed before the audited plan if necessary.

## Durable job lifecycle

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> running: worker claims
    running --> running: expired lease reclaimed
    queued --> cancelled: cancel before claim
    running --> cancelling: cancel requested
    running --> succeeded: valid brief stored
    running --> failed: provider or validation failure
    cancelling --> cancelled: provider result discarded
    failed --> queued: bounded retry
    cancelled --> queued: bounded retry
```

The first ETA is unavailable. ETA ranges appear only after successful jobs provide measured duration samples. Cancellation during a provider request records intent and discards the eventual result; it does not claim to preempt an in-flight external call. Retries are bounded to three application attempts, while Cloud Tasks delivery retry is a separate crash-recovery path whose window must outlive the worker lease.

## Evidence boundary

Local tests inject model, task, repository, or runtime doubles. Their evidence labels (`injected_test_client` and `test_double`) are deliberately different from `live_google_provider_response`. Configuration health, a successful container build, deterministic audit success, and mocked tests are useful verification, but none proves a live Google Cloud execution. Likewise, a valid package proves an internally consistent editorial plan—not rendered media. Browser/device pitch speech is not generated audio and is not embedded in a media file; character/synopsis outputs prove only the synopsis and exact appearance evidence present in the current brief.
