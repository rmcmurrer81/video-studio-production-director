# Video Studio Storyboard Artist & Production Planner

Video Studio Storyboard Artist & Production Planner is a focused All Things Agentic submission that turns natural chat or an attached story/screenplay into a validated creative brief, a deterministic audited scene package, and human-usable preproduction artifacts. The Google Cloud design uses a public Cloud Run API, Firestore-backed job state, an OIDC-authenticated Cloud Tasks handoff, a private Cloud Run worker, and Vertex AI through the official Google Gen AI SDK.

The familiar **+ Attach** menu accepts locally read PDF, TXT, Markdown, Fountain, Screenplay, and Final Draft XML story files. Ordinary screenplays are included in full up to the tested 147,000-character source allowance; larger sources use explicitly labeled beginning/middle/end excerpts with exact included and extracted character counts. PDF text extraction uses a vendored PDF.js module entirely in the browser. Scanned/image-only PDFs are rejected with an honest OCR-needed message. The same menu can inventory up to 12 selected raw-video files using browser-readable filename, size, MIME type, and duration only; raw bytes never leave the browser and no visual-content analysis is claimed.

The downloadable JSON package contains ordered storyboard cards, contiguous planned 24-fps timecodes, framing, camera, scene-specific action, dialogue/audio, continuity, and source/bridge guidance. When Gemini image generation is available, a separate cryptographically checked sidecar supplies studio-style planning illustrations; unavailable panels remain visibly marked instead of being fabricated. The UI provides complementary visual and detailed boards, a timed in-app animatic assembled from planning panels and compiled shot durations, and—only when the browser exposes a clearly labeled English Natural or Neural voice—a browser-voice investor-pitch preview. The downloadable narration TXT remains available when natural playback is unavailable. It also derives an exact-setting location plan (HTML, schedule CSV, and JSON) and an evidence-limited character/synopsis dossier (HTML, TXT, JSON, and character-appearance CSV), then offers a shot-list CSV and metadata-aware rough-cut EDL CSV with explicit source-duration deficits/holds. The HTML sheets can be printed or saved as PDF.

The location and character handoffs are deterministic browser-side derivations over the completed package and make no additional model call. The character dossier includes the production-brief synopsis, exact supported appearances/settings, dialogue-context scenes, and chronological outline. Because the current brief does not itemize roles, biographies, relationships, arcs, pronouns, casting, or speaker attribution, those remain explicit review holds instead of invented facts. Pitch playback is enabled only for an English browser or operating-system voice whose advertised name explicitly contains `Natural` or `Neural`; otherwise it stays disabled and the complete narration TXT remains downloadable. Browser speech is a preview only and is not embedded in an MP4, MOV, or generated audio file. Every artifact remains explicitly marked `media_status = unrendered_plan` and `plan_only = true`: storyboard stills and the animatic are previsualization, not source footage, an applied edit, or a rendered movie.

The hosted page is installable as a standalone PWA. Use **Install desktop app** when the browser exposes its install prompt; Microsoft Edge users can also choose **⋯ → Apps → Install Video Studio Storyboard Artist & Production Planner**. The private owner-test packet includes an Edge `--app=` launcher template for a dedicated window without normal browser tabs. It contains no access code or credential.

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

The suites cover deterministic package compilation/auditing, durable Firestore-size bounds, visual-sidecar integrity and failure fallbacks, Gemini image normalization, the brief schema and fenced job state machine, admission control, Google adapter request contracts with injected clients, Cloud Tasks OIDC binding, HTTP routes and access-code behavior, clarification follow-ups, local script coverage bounds, PWA install wiring, the visual/detailed/location/character/pitch/editorial exports, print behavior, and the browser UI. They do not contact Google Cloud. The JavaScript check requires a current Node.js runtime.

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
- `web/all-things-agentic.html` — same-origin natural-chat, attachment, visual/detailed boards, timed animatic, qualified Natural/Neural browser-voice pitch preview with narration-script fallback, deterministic location/character handoffs, and editorial-export UI.
- `web/manifest.webmanifest`, `web/sw.js`, and `web/video-studio-icon-*` — standalone PWA shell with Chromium-ready PNG install icons and an SVG favicon.
- `web/vendor/pdfjs/` — vendored browser-local PDF text extractor and its Apache 2.0 license.
- `contest_config/` — reviewed dependency and non-secret environment templates.
- `deploy/all_things_agentic/` — non-root Cloud Run container definition.
- `cloudbuild.yaml` — clean remote container build and Artifact Registry push path.
- `tests/` — offline focused Python and Node contract suites.
- `docs/ARCHITECTURE.md` — component and trust-boundary diagram.
- `docs/ALL_THINGS_AGENTIC_SETUP.md` — deployment and judge-demo procedure.
- `docs/DEMO_SUBMISSION_CHECKLIST.md` — live-evidence and submission closeout checklist.

## License

An MIT license draft is included in `LICENSE`. Confirm the copyright attribution before publishing the repository. Vendored PDF.js is distributed under Apache License 2.0; see `web/vendor/pdfjs/LICENSE` and `THIRD_PARTY_NOTICES.md`.
