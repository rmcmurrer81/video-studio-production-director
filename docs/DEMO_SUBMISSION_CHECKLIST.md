# Demo and submission checklist

This checklist separates local readiness from evidence that can exist only after a real billing-enabled Google Cloud deployment. At repository staging time on August 28, 2026, live cloud proof is not present.

## Repository freeze

- [ ] Confirm this repository contains only the focused Production Director source, UI, deployment files, documentation, and tests.
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

- [ ] Confirm the three direct Google SDK versions and Python base image digest match the reviewed pins.
- [ ] Build the container from a clean checkout (local Docker or `cloudbuild.yaml`) and record resolved transitive package versions plus the immutable image digest.

## Live Google Cloud proof

Do not check these boxes based on mocks, configuration text, `/healthz`, or deployment commands alone.

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

## Demo recording

- [ ] Start with the product problem and the one-sentence architecture, then show the live URL.
- [ ] Enter the owner-provided access code without exposing it in the recording, logs, source, or submission text.
- [ ] Submit a specific creative request and show job ID, stage, progress, application attempt, and the honestly unavailable first-run ETA.
- [ ] Show the final title, summary, ordered storyboard cards, planned in/out timecodes, framing/camera/action/audio direction, deterministic audit, manifest digest, and live execution metadata.
- [ ] Download the package JSON and call it an unrendered storyboard/edit-decision plan—not a video, applied edit, or located source footage.
- [ ] Show one clarification round, then cancel/retry behavior if time permits.
- [ ] Keep claims narrow: the system produces a structured production brief, not a rendered movie.
- [ ] Remove or blur unrelated account, billing, email, project, tab, and notification details from screenshots and video.

## Submission form

- [ ] Public source URL resolves from a signed-out browser and points to the frozen revision.
- [ ] Demo video is accessible to judges, uses permitted media, and matches the deployed revision.
- [ ] Description names the Google services actually demonstrated and distinguishes live evidence from local tests.
- [ ] Setup instructions, architecture diagram, test command, license, and access instructions are easy to find.
- [ ] Judge access is delivered through an owner-controlled channel; no plaintext secret appears in the repository or form.
- [ ] Re-open every submitted link and re-read the final text before the organizer's cutoff.

If any live-proof item remains unchecked, describe the project as locally verified and deployment-ready—not as live-deployed or provider-proven.
