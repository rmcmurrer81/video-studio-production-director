# Program and submission checklist

This checklist separates local contract verification from evidence that exists only after a real Google Cloud run. Do not pass or submit the corrected build while visuals are pending or the narrated MP4 is absent.

## 1. Repository freeze

- [ ] Confirm the repository contains only the contest Storyboard Artist & Production Planner source, UI, deployment files, documentation, tests, and properly licensed vendored assets.
- [ ] Review every diff. Confirm no plaintext owner/judge code, API key, token, credential file, email, billing data, or private project identifier is committed.
- [ ] Confirm the copyright attribution in `LICENSE`.
- [ ] Run:

  ```powershell
  py -B -m unittest discover -s tests -p "test_all_things*.py" -v
  py -B -m py_compile kira_studio/all_things_agentic.py kira_studio/all_things_google.py kira_studio/all_things_media.py kira_studio/all_things_cloud_media.py all_things_cloud_app.py
  node --test tests/test_all_things_agentic_ui.js
  ```

- [ ] Confirm the pinned Python base image and Google SDK/Pillow dependencies are reviewed.
- [ ] Confirm the container installs FFmpeg/FFprobe and runs as a non-root user.
- [ ] Build from a clean checkout and record the immutable container digest.
- [ ] Remember: mocks and a clean build prove contracts, not a live cloud job.

## 2. Private cloud prerequisites

- [ ] Billing is active on the exact project; a cost alert is configured.
- [ ] Required APIs are enabled: Cloud Run, Cloud Tasks, Firestore, Vertex AI, Cloud Text-to-Speech, Cloud Storage, Cloud Build, Artifact Registry, and IAM Credentials.
- [ ] Firestore Native mode, Artifact Registry, and the Cloud Tasks queue exist in owner-reviewed locations.
- [ ] The artifact bucket was created with Uniform Bucket-Level Access enabled and Public Access Prevention `enforced`.
- [ ] Bucket output from `gcloud storage buckets describe` was inspected; no `allUsers` or `allAuthenticatedUsers` grant exists.
- [ ] The API service identity has only bucket metadata/object read permissions required by the adapter.
- [ ] The worker service identity has only bucket metadata/object create/read permissions required by the adapter.
- [ ] API, worker, and task caller are three distinct service identities; none has Owner or Editor.
- [ ] Worker has Firestore, Vertex AI, and enabled-service consumption access. API has Firestore and Cloud Tasks enqueue access.
- [ ] The Cloud Tasks service agent can mint the task caller's OIDC token, and only that caller has Cloud Run Invoker on the worker.
- [ ] The worker is private; an unauthenticated request is rejected by Cloud Run IAM.
- [ ] Both roles have the exact non-secret bucket/voice configuration. Only the API has the access-code digest.
- [ ] Cloud Tasks dispatch deadline and Cloud Run worker timeout are 1,740 seconds; fenced lease is 1,800 seconds.
- [ ] Queue recovery is `maxAttempts=250`, `maxRetryDuration=21600s`, concurrency 1, and rate 1/s.
- [ ] Before upgrading a v1 admission ledger, the queue is paused/empty, no Firestore job is `queued`, `running`, or `cancelling`, and the private visual-capacity FIFO has no live entry/reservation; otherwise deployment remains on hold.
- [ ] Queue concurrency and worker max instances are both one; worker `/health` reports `continuation_dispatch_configured: true`.
- [ ] API and worker use the same reviewed pacing target: `KIRA_ALL_THINGS_VISUAL_PANELS_PER_DISPATCH=2`, `KIRA_ALL_THINGS_VISUAL_SUCCESSOR_DELAY_SECONDS=75`, `KIRA_ALL_THINGS_VISUAL_QUOTA_MAX_DEFERRALS=4`, `KIRA_ALL_THINGS_VISUAL_QUOTA_BASE_DEFERRAL_SECONDS=90`, and `KIRA_ALL_THINGS_VISUAL_QUOTA_MAX_DEFERRAL_SECONDS=720`; worker also has its own canonical URL and the task-caller service account configured for successor dispatches.
- [ ] Worker starts at 2 CPU, 4 GiB, concurrency 1, max instances 1 for the first media acceptance run.

## 3. Access and boundary smoke tests

- [ ] Public application root loads from a signed-out browser.
- [ ] Missing and incorrect access codes return the same `401`; a correct code can create a `202` queued job.
- [ ] The page sends only same-origin job/artifact requests and never receives a public bucket or signed URL.
- [ ] The install button opens the PWA install path or the exact Edge Apps fallback; test it as a dedicated app window, not only an ordinary browser tab.
- [ ] Raw-video attachment displays only local metadata. Network inspection confirms video bytes are not uploaded.
- [ ] Script attachment clearly shows full text versus labeled excerpt mode and exact extracted/included counts.

## 4. One clear live acceptance job

Follow [START_HERE_TEST.md](START_HERE_TEST.md). Do not use a full television episode as the first run.

- [ ] A specific one-minute dialogue request is accepted and has a durable job ID.
- [ ] The first ETA says unavailable rather than inventing a time.
- [ ] The job progresses through brief, validation, timeline compilation, audit, all-card visual generation, narration, media render, and final verification stages.
- [ ] For a multi-dispatch test, dispatch sequences advance contiguously within one application attempt; every checkpoint is private/hash-bound and no stale or duplicate task changes the panel count.
- [ ] Completed job records `execution.evidence_origin = live_google_provider_response` plus actual provider metadata.
- [ ] `state == succeeded` only after all technical media gates pass; the visible app still says `technical package ready · owner visual review HOLD` until human visual approval.

### Required visual gate

- [ ] `required_panel_count` equals the number of detailed cards.
- [ ] `generated_panel_count` equals `required_panel_count`.
- [ ] Every visual card contains a real planning image; none says `VISUAL PENDING`, `partial`, `panel limit reached`, or equivalent.
- [ ] Each private panel manifest has a safe artifact ID, this job's object prefix, content type, byte count, and SHA-256.
- [ ] Visual sheet and on-screen grid preserve card order and show matching card IDs/timecodes.
- [ ] Owner review explicitly passes story match, anatomy/proportions (including attached, proportionate hands and natural digits), identity, continuity, and composition for every panel. Any defect is a HOLD and requires a new generated package; technical JPEG/hash completion is not a visual-quality pass.

### Required narrated-pitch gate

- [ ] Narration cue count equals card count.
- [ ] Subtitle cue count equals card count.
- [ ] Exact screenplay lines appear where the parser identified screenplay dialogue; prose direction is labeled as planned direction rather than falsely attributed dialogue.
- [ ] `pitch_preview.status == complete` and its manifest hash validates.
- [ ] FFprobe verification records 1920×1080, H.264 video, AAC audio, positive bounded duration, and complete coverage.
- [ ] The in-app video player opens the authenticated MP4 and audio is present.
- [ ] Downloaded MP4 plays in Windows Media Player or VLC at full-screen size.
- [ ] Downloaded narration TXT and SRT are present and correspond to card order.

### Required planning exports

- [ ] Canonical package JSON and manifest hash validate.
- [ ] Detailed production sheet contains scene-specific action, framing/camera, dialogue/audio, continuity, and source/bridge direction.
- [ ] Visual storyboard HTML/PDF contains every visual card without excessive blank pages.
- [ ] Character/synopsis HTML, TXT, JSON, and character-appearance CSV download successfully.
- [ ] Exact-setting location HTML, schedule CSV, and JSON download successfully.
- [ ] Shot-list CSV and source-aware rough-cut EDL CSV download successfully.
- [ ] Unsupported character biographies, casting, relationships, pronouns, or speaker claims remain explicit review holds.
- [ ] Every export says the material is plan-only/previsualization rather than finished filmed footage.

## 5. Fail cases that must not be called a pass

- [ ] Force or observe a visual provider failure in a non-submission test. Confirm the production-ready job fails instead of succeeding with blank/pending panels.
- [ ] Confirm a missing TTS cue, missing subtitle, FFmpeg error, wrong codec/resolution, or hash mismatch fails finalization.
- [ ] Confirm an arbitrary artifact ID, another job's artifact ID, missing code, or wrong code cannot download bucket bytes.
- [ ] Confirm direct bucket/object anonymous access fails.
- [ ] Exercise an ambiguous request and show `ready_for_production: false` with concise questions and no fake finished package.
- [ ] Exercise queued cancellation and one bounded retry. Do not claim an in-flight external provider call was preempted.
- [ ] Cancel during a multi-panel run and confirm work stops at a panel boundary without enqueuing another continuation. Repeat during a narrated-pitch card and confirm no successor or finalizer is enqueued. Retry must start a new application attempt at dispatch sequence zero rather than reusing a terminal/failed checkpoint.

If any required panel or MP4 item fails, the release remains on hold. A historical partial test is useful debugging evidence but is not owner-review media.

## 6. Program walkthrough recording

- [ ] Open with the problem: independent filmmakers need affordable, consistent visual planning and pitch materials before expensive shooting.
- [ ] State the narrow product truth: natural chat/script → structured brief → detailed + visual cards → character/synopsis/location/editorial exports → narrated storyboard pitch MP4.
- [ ] State explicitly that it is not final filmed footage, lip sync, or applied raw-footage editing.
- [ ] Show the installed/dedicated app window and enter the access code off camera.
- [ ] Attach a short script or enter the START HERE prompt.
- [ ] Show job ID, stages, progress, and honest ETA behavior.
- [ ] Show all visuals—not three samples and not pending placeholders.
- [ ] Open detailed cards and character/synopsis/location/shot-list/EDL exports.
- [ ] Play the cloud-rendered MP4 with audible Chirp 3 HD narration; show that it advances through every card and includes the dialogue beats.
- [ ] Download the MP4, TXT, SRT, visual board, detailed board, and JSON package.
- [ ] Briefly show authenticated artifact architecture and the private-bucket controls.
- [ ] Show live execution metadata, but do not expose codes, emails, billing, unrelated tabs, or personal notifications.
- [ ] If demonstrating an ambiguous request or cancel/retry, keep it after the successful core path.

## 7. Submission form

- [ ] Public source URL resolves from a signed-out browser and points to the frozen reviewed revision.
- [ ] Hosted program URL resolves and owner/judge code instructions are clear.
- [ ] Submission video is accessible to judges and matches the deployed revision.
- [ ] Description names only Google services actually demonstrated.
- [ ] Upload the current job-independent `docs/all-things-agentic-architecture.png` only as an architecture diagram. Package any live result separately and make a media-acceptance claim only after sequential card/cue, full-resolution visual, technical, and owner review.
- [ ] The repository is public only when the owner is ready; no secret was added during the visibility change.
- [ ] Every submitted link was reopened and the final text reread before the organizer cutoff.

If live-proof items remain unchecked, describe the repository as locally verified and deployment-ready—not as live-proven. If visual or MP4 gates remain unchecked, do not describe the corrected media feature as complete.
