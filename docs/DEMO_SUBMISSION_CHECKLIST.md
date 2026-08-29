# Demo and submission checklist

This checklist separates local readiness from evidence that can exist only after a real billing-enabled Google Cloud deployment. At repository staging time on August 28, 2026, live cloud proof is not present.

## Repository freeze

- [ ] Confirm this repository contains only the focused Storyboard Artist & Production Planner source, UI, deployment files, documentation, and tests.
- [ ] Review every diff and confirm no access code, credential, token, project-private identifier, or personal billing data is committed.
- [ ] Confirm the copyright attribution in `LICENSE`.
- [ ] Run the focused unit tests from the repository root:

  ```powershell
  py -B -m unittest discover -s tests -p "test_all_things*.py" -v
  ```

- [ ] Compile the three Python entry modules:

  ```powershell
  py -B -m py_compile kira_studio/all_things_agentic.py kira_studio/all_things_google.py all_things_cloud_app.py
  ```

- [ ] Run the browser behavior harness:

  ```powershell
  node --test tests/test_all_things_agentic_ui.js
  ```

- [ ] Confirm the three direct Google SDK versions, Pillow version, Python base image digest, and vendored PDF.js license/notices match the reviewed pins.
- [ ] Build the container from a clean checkout (local Docker or `cloudbuild.yaml`) and record resolved transitive package versions plus the immutable image digest.

## Live Google Cloud proof

Do not check these boxes based on mocks, configuration text, `/health`, or deployment commands alone.

- [ ] Owner confirms an active billing account is attached to the intended project and creates a budget alert.
- [ ] Required APIs, Firestore Native mode, Artifact Registry, Cloud Tasks queue, and three least-privilege service accounts exist.
- [ ] Private worker deploys first; an unauthenticated request is rejected by Cloud Run IAM.
- [ ] Public API deploys with only the access-code digest, never the plaintext code.
- [ ] One clear request completes after a real model lookup and structured generation.
- [ ] The completed Firestore job records `execution.evidence_origin = live_google_provider_response`.
- [ ] The completed job contains a package with `plan_only = true`, `media_status = unrendered_plan`, a valid manifest digest, contiguous planned 24-fps timecodes, and a passing deterministic audit.
- [ ] Download the JSON package and verify it is local-only, contains no access code, and matches the job manifest digest.
- [ ] Capture the real project, region, immutable image digest, API and worker revision names, queue/task resource, completed job ID, configured model, and provider response ID when present.
- [ ] Restart or redeploy the API and verify the completed job remains readable from Firestore.
- [ ] Run at least one Firestore emulator or live smoke test for transactional admission, lease reclamation, finalization, and late-cancellation behavior; unit doubles alone are insufficient proof of Firestore semantics.
- [ ] Exercise an ambiguous request and show `ready_for_production: false` with concise questions.
- [ ] Exercise queued cancellation and one permitted retry; do not claim an in-flight provider call was preempted.
- [ ] Confirm missing and incorrect access codes return the same `401`, the correct code can create a `202` job, and the browser sends only same-origin requests.
- [ ] Confirm **Build scene package** remains disabled until a nonblank access code and creative source are present, and the blank-code guidance points to `OWNER-TEST-INSTRUCTIONS`.
- [ ] Confirm the install button either opens the browser PWA prompt or provides the exact Edge Apps / private launcher fallback; test the installed or Edge `--app=` window separately from ordinary tabs.

## Demo recording

- [ ] Start with the product problem and the one-sentence architecture, then show the live URL.
- [ ] Enter the owner-provided access code without exposing it in the recording, logs, source, or submission text.
- [ ] Submit a specific creative request or use **+ Attach** to add a supported story/screenplay. Show whether full text or labeled excerpts were used, with included/extracted character counts.
- [ ] Optionally attach a few raw-video files and state explicitly that only filename, MIME type, size, and browser-readable duration are inventoried; raw bytes remain local.
- [ ] Show job ID, stage, progress, application attempt, and the honestly unavailable first-run ETA.
- [ ] Show the final title, summary, generated planning illustrations or explicit pending frames, ordered detailed cards, planned in/out timecodes, framing/camera/action/audio direction, deterministic audit, both manifest digests, and live execution metadata.
- [ ] Play the timed animatic and call it previsualization assembled from planning panels—not a rendered scene or finished video.
- [ ] Run **Narrate investor pitch** for one or two cues, stop it, and download the narration TXT. State that browser/device speech is not generated audio and is not embedded in an MP4/MOV/audio file.
- [ ] Show exact-setting location grouping and download its HTML, schedule CSV, and JSON. State that similar-looking settings are not silently merged and props/wardrobe are not itemized by the current brief.
- [ ] Show the character/synopsis dossier and download its HTML, TXT, JSON, and character-appearance CSV. State that it derives supported synopsis/appearance evidence without another model call and does not infer roles, biographies, relationships, arcs, casting, pronouns, or speaker attribution.
- [ ] Download the package JSON, visual storyboard HTML, detailed production sheet, shot-list CSV, and source-aware rough-cut EDL CSV; demonstrate Print / Save PDF. Call them editorial planning artifacts—not a video, applied edit, footage analysis, or located source footage.
- [ ] Show one clarification round, then cancel/retry behavior if time permits.
- [ ] Keep claims narrow: the contest edition produces a brief, visual/detailed boards, timed animatic, device-voice pitch preview plus narration TXT, exact-setting location exports, evidence-limited character/synopsis exports, continuity audit, and editorial instructions. It does not render the Local edition's final video, generate/attach an audio track, create lip sync, or apply a raw-footage edit.
- [ ] Remove or blur unrelated account, billing, email, project, tab, and notification details from screenshots and video.

## Submission form

- [ ] Public source URL resolves from a signed-out browser and points to the frozen revision.
- [ ] Demo video is accessible to judges, uses permitted media, and matches the deployed revision.
- [ ] Description names the Google services actually demonstrated and distinguishes live evidence from local tests.
- [ ] Setup instructions, architecture diagram, test command, license, and access instructions are easy to find.
- [ ] Judge access is delivered through an owner-controlled channel; no plaintext secret appears in the repository or form.
- [ ] Re-open every submitted link and re-read the final text before the organizer's cutoff.

If any live-proof item remains unchecked, describe the project as locally verified and deployment-ready—not as live-deployed or provider-proven.
