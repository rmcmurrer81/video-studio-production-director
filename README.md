# Video Studio Storyboard Artist & Production Planner

This All Things Agentic contest edition turns natural chat or an attached story/screenplay into a reviewable **preproduction package**. Its completed cloud path includes:

- a structured creative brief and deterministic, contiguous 24-fps shot plan;
- one privately stored planning illustration for **every** storyboard card;
- visual and detailed storyboard sheets;
- synopsis, character-appearance, location, shot-list, continuity-audit, and source-aware EDL exports; and
- a narrated investor-pitch MP4 assembled from every card, using Google Cloud Text-to-Speech Chirp 3 HD and verified by FFprobe as 1920×1080 H.264 video with AAC audio.

The package is still a **plan and pitch preview**. It is not filmed footage, a final generated scene, lip-synced character video, or an applied raw-footage edit. A job must not report success if even one required visual, narration cue, subtitle cue, or final-media verification is missing.

## Start here

For the shortest owner/judge test, open [docs/START_HERE_TEST.md](docs/START_HERE_TEST.md). It gives one test prompt, one expected result, one pass/fail checklist, and one obvious place to find the MP4 and exports.

For cloud provisioning and deployment, use [docs/ALL_THINGS_AGENTIC_SETUP.md](docs/ALL_THINGS_AGENTIC_SETUP.md). Do not deploy from guesses: the private Cloud Storage bucket, IAM, Cloud Text-to-Speech API, environment variables, and worker resources are required for the corrected media path.

## Try it

- Live application: https://video-studio-agent-api-ajd6ejywsq-uc.a.run.app
- Source repository: https://github.com/rmcmurrer81/video-studio-production-director

The owner/judge access code is provided privately with the submission. It is never committed to this repository or shown in the submission recording.

## What the attachment flow accepts

The **+ Attach** flow reads PDF, TXT, Markdown, Fountain, Screenplay, and Final Draft XML story files in the browser. Ordinary screenplays are included in full up to the displayed 147,000-character source allowance. Larger sources use explicitly labeled beginning/middle/end excerpts with exact extracted and included counts. PDF text extraction uses the vendored PDF.js module locally. Scanned/image-only PDFs are rejected with an OCR-needed message.

The same flow can inventory up to 12 local raw-video files using browser-readable filename, size, MIME type, and duration. Raw video bytes remain in the browser. This contest edition does not claim that it watched, edited, color-matched, or generated bridge footage from those files.

## Corrected cloud media path

The public Cloud Run API creates and reads durable Firestore jobs and dispatches work through Cloud Tasks to an IAM-protected private Cloud Run worker. The worker uses the official Google Gen AI SDK with Vertex AI for the structured brief and planning panels, then:

1. deterministically compiles every scene into ordered storyboard cards;
2. generates and validates one 16:9 JPEG planning panel per card;
3. writes each panel as an immutable, content-addressed object in a private Cloud Storage bucket;
4. builds one narration cue per card, preserving exact screenplay dialogue where the parser can identify it and otherwise labeling prose as planned direction;
5. synthesizes the cues with the configured Google Chirp 3 HD voice;
6. uses FFmpeg to assemble the card images, narration, and SRT subtitles into the pitch video; and
7. uses FFprobe plus manifest/hash checks to require 1920×1080, H.264, AAC, complete card/cue coverage, and job-bound artifact identities before publishing success.

Long plans do not depend on one Cloud Run request. The reviewed configuration generates at most two panels per named Cloud Tasks dispatch and schedules the next visual chunk 75 seconds later, then renders exactly one narrated-pitch card segment per dispatch and concatenates/probes those private segments in one final dispatch. Before every image call, a durable project-wide Firestore FIFO reserves one of at most two request positions in a 75-second window, so concurrent jobs cannot independently oversubscribe the project quota. A quota/429 response checkpoints any validated partial chunk, releases the fenced lease, and uses at most four same-attempt named successors after deterministic 90/180/360/720-second delays; ordinary FIFO contention does not consume that budget, and exhaustion fails without public partial media. Every transition writes an immutable private integrity-validated checkpoint and resumes under the same application attempt with a strictly increasing dispatch sequence. Job creation/retry atomically acquires one of exactly four active-job slots, terminal transitions atomically release it, and slot expiry includes the complete record-retention plus worker-lease margin. Attempt/sequence/lease fencing prevents stale delivery from appending duplicate panels or pitch segments, or from reviving a terminal job; cancellation is checked between panels, pitch-card operations, segment loads, concatenation, and final probing.

The browser never receives a `gs://` URL or a public object URL. It requests a declared artifact through the authenticated same-origin route:

```text
/v1/jobs/{job_id}/artifacts/{artifact_id}
```

The API resolves that identifier only from the exact completed job manifest, verifies the object path is inside that job's prefix, downloads it with its service identity, rechecks byte count and SHA-256, and then returns it with private/no-store response headers. The bucket has Public Access Prevention enforced and Uniform Bucket-Level Access enabled.

## Fail-closed completion rule

`succeeded` means the corrected full-media path completed its technical checks; it is not an owner visual-quality approval. The visible app and visual export remain on **owner visual review HOLD** until a person reviews story match, anatomy/proportions, identity, continuity, and composition. A production-ready job is not acceptable when it contains `VISUAL PENDING`, a partial panel count, a missing MP4, an absent cue, a codec/resolution/integrity mismatch, or any owner-rejected visual. Partial work can be retained in logs or internal state for diagnosis, but it is not a submission pass and must not be presented as the finished feature.

The first ETA is intentionally unavailable. Later estimates can use measured completed-job durations. Panel generation, Text-to-Speech, FFmpeg encoding, Cloud Run, Cloud Storage, Firestore, and Cloud Tasks can all consume time and billable resources. A long screenplay creates many cards and model calls; test with a short dialogue scene before attempting a television episode. The narrated pitch implementation is bounded to 60 minutes and 2 GiB. It is not rendered in one long request: each card segment and the final concat/probe have separate bounded dispatches under the Cloud Run/Cloud Tasks request ceiling.

## Evidence status

Repository tests, injected clients, generated fixtures, and `/health` verify contracts or configuration only. They do **not** prove that a live Vertex image request, Chirp synthesis, Cloud Storage write, Cloud Tasks delivery, Cloud Run revision, or MP4 render succeeded.

Job `48ed0927-ac40-4450-9f15-a3f98dfdd383` is **owner rejected and on HOLD; do not submit or use it as submission media**. Although it recorded live Google-provider evidence and 9/9 technical assets, owner review found repeated location narration, card/cue timing problems, an excessive silent video tail, and a panel with a disconnected/duplicated lower body. Its earlier Codex visual/narration pass was incorrect and has been withdrawn. A replacement run must pass sequential Card 1/Cue 1 through Card 9/Cue 9 review, full-resolution anatomy/continuity review, and complete owner listening before this repository can name a final release candidate. Never invent, pre-fill, or extend technical evidence into human media acceptance.

## Run the local contract checks

The tests use injected doubles and do not require Google credentials:

```powershell
py -B -m unittest discover -s tests -p "test_all_things*.py" -v
py -B -m py_compile kira_studio/all_things_agentic.py kira_studio/all_things_google.py kira_studio/all_things_media.py kira_studio/all_things_cloud_media.py all_things_cloud_app.py
node --test tests/test_all_things_agentic_ui.js
```

Python 3.12 is the container target. The production container installs FFmpeg and pins the Google client libraries used by the cloud path.

## Repository map

- `all_things_cloud_app.py` — API/worker HTTP entry point and authenticated job-bound artifact route.
- `kira_studio/all_things_agentic.py` — strict brief contract, deterministic compiler/auditor, visual coverage gate, fenced job lifecycle, and ETA.
- `kira_studio/all_things_google.py` — Vertex AI, Firestore, and Cloud Tasks adapters.
- `kira_studio/all_things_media.py` — screenplay dialogue extraction and one-cue-per-card narration plan.
- `kira_studio/all_things_cloud_media.py` — private Cloud Storage adapter, Chirp 3 HD synthesis, FFmpeg assembly, and FFprobe/integrity gates.
- `web/all-things-agentic.html` — natural chat, attachment flow, job monitor, hydrated private panels, cloud MP4 playback/download, and editorial exports.
- `web/manifest.webmanifest`, `web/sw.js`, and icons — standalone PWA shell. Job/API/artifact routes are never cached.
- `web/vendor/pdfjs/` — vendored browser-local PDF text extraction assets and license.
- `contest_config/` — pinned dependencies and a non-secret environment template.
- `deploy/all_things_agentic/` — non-root Cloud Run container with FFmpeg.
- `docs/ARCHITECTURE.md` — trust boundaries and end-to-end component diagram.
- `docs/all-things-agentic-architecture.svg` and `.png` — upload-ready 3:2 architecture diagram source and rendered submission asset.
- `docs/ALL_THINGS_AGENTIC_SETUP.md` — exact Cloud Storage, API, IAM, build, and deploy procedure.
- `docs/PROGRAM_SUBMISSION_CHECKLIST.md` — fail-closed live-proof and recording checklist.
- `docs/START_HERE_TEST.md` — the single owner/judge acceptance path.

## Primary Google references

- [Cloud Storage bucket creation](https://docs.cloud.google.com/storage/docs/creating-buckets)
- [Public Access Prevention](https://docs.cloud.google.com/storage/docs/public-access-prevention)
- [Uniform Bucket-Level Access](https://docs.cloud.google.com/storage/docs/using-uniform-bucket-level-access)
- [Cloud Storage IAM roles](https://docs.cloud.google.com/storage/docs/access-control/iam-roles)
- [Chirp 3 HD voices](https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd)
- [Cloud Text-to-Speech authentication](https://docs.cloud.google.com/text-to-speech/docs/authentication)
- [Cloud Tasks to private Cloud Run](https://docs.cloud.google.com/run/docs/triggering/using-tasks)

## License

The repository is distributed under the completed MIT License in `LICENSE`, copyright (c) 2026 Robert McMurrer. Vendored PDF.js is distributed under Apache License 2.0; see `web/vendor/pdfjs/LICENSE` and `THIRD_PARTY_NOTICES.md`.
