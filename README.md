# Video Studio Production Director

Video Studio Production Director is a focused All Things Agentic submission that turns a natural-language creative request into a validated creative brief, a deterministic audited storyboard/edit-decision package, and a bounded visual-storyboard review sheet. The Google Cloud design uses a public Cloud Run API, Firestore-backed job state, an OIDC-authenticated Cloud Tasks handoff, a private Cloud Run worker, and Vertex AI through the official Google Gen AI SDK.

The downloadable JSON package contains ordered storyboard cards, contiguous planned 24-fps timecodes, framing, camera, scene-specific action, dialogue/audio, continuity, and source/bridge guidance. When Gemini image generation is available, a separate cryptographically checked sidecar supplies studio-style planning illustrations; unavailable panels remain visibly marked instead of being fabricated. Separate scriptless HTML exports provide a visual board and a detailed production sheet, and the visual board can be printed or saved as PDF. The package remains explicitly marked `media_status = unrendered_plan` and `plan_only = true`: storyboard stills are planning art, not source footage, an applied edit, or a rendered movie.

## Evidence status

As of August 28, 2026, the repository has local contract tests and injected test-double coverage only. It does **not** contain proof of a live Vertex AI call, Firestore transaction, Cloud Tasks execution, Cloud Run revision, or public demo deployment.

`GET /health` proves configuration parsing only. A provider call is live evidence only when a completed durable job records `execution.evidence_origin = live_google_provider_response` together with the matching provider metadata. Do not describe local test results as cloud execution proof. The route intentionally avoids Cloud Run paths ending in `z`, which Google reserves in some deployments.

## Run the focused checks

Python 3.12 is the container target. The local checks need no Google credentials and no third-party packages because the Google SDK imports are lazy.

```powershell
py -B -m unittest discover -s tests -p "test_all_things*.py" -v
py -B -m py_compile kira_studio/all_things_agentic.py kira_studio/all_things_google.py all_things_cloud_app.py
node --test tests/test_all_things_agentic_ui.js
```

The suites cover deterministic package compilation/auditing, visual-sidecar integrity and failure fallbacks, Gemini image normalization, the brief schema and fenced job state machine, admission control, Google adapter request contracts with injected clients, Cloud Tasks OIDC binding, HTTP routes and access-code behavior, clarification follow-ups, all three downloads, print behavior, and the self-contained browser UI. They do not contact Google Cloud. The JavaScript check requires a current Node.js runtime.

## Container build

The same image is deployed twice with `KIRA_ALL_THINGS_SERVICE_ROLE=api` or `worker`. The Dockerfile pins Python 3.12.11 slim-bookworm by digest; the Google SDKs and Pillow image normalizer are pinned exactly.

Local Docker build:

```powershell
docker build --file deploy/all_things_agentic/Dockerfile --tag all-things-agentic:local .
```

Cloud Build path, using an owner-selected Artifact Registry image URL:

```powershell
gcloud builds submit --project $env:AT_PROJECT --config cloudbuild.yaml --substitutions "_IMAGE=$($env:AT_IMAGE)" .
```

For a real deployment, copy the environment template without committing secrets and follow `docs/ALL_THINGS_AGENTIC_SETUP.md`. The API role requires an owner-created access code represented in configuration only by its SHA-256 digest. Firestore also enforces a shared admission window, while lease fencing and attempt-bound task names make redelivery and crash recovery explicit.

## Repository map

- `all_things_cloud_app.py` — dependency-light API/worker HTTP entry point.
- `kira_studio/all_things_agentic.py` — strict brief contract, deterministic package compiler/auditor, fenced job lifecycle, retry/cancel rules, and evidence-aware ETA.
- `kira_studio/all_things_google.py` — Vertex AI, Firestore, and Cloud Tasks adapters.
- `web/all-things-agentic.html` — self-contained, same-origin production-director UI.
- `contest_config/` — reviewed dependency and non-secret environment templates.
- `deploy/all_things_agentic/` — non-root Cloud Run container definition.
- `cloudbuild.yaml` — clean remote container build and Artifact Registry push path.
- `tests/` — offline focused Python and Node contract suites.
- `docs/ARCHITECTURE.md` — component and trust-boundary diagram.
- `docs/ALL_THINGS_AGENTIC_SETUP.md` — deployment and judge-demo procedure.
- `docs/DEMO_SUBMISSION_CHECKLIST.md` — live-evidence and submission closeout checklist.

## License

An MIT license draft is included in `LICENSE`. Confirm the copyright attribution before publishing the repository.
