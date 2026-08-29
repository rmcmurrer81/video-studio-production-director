# Video Studio — All Things Agentic deployment and demo

## Submission clock

- **Internal code/deployment freeze:** August 30, 2026 at 5:00 PM EDT.
- **Official submission cutoff:** August 31, 2026 at 5:00 PM PT (8:00 PM EDT), as shown by the organizer's final-call notice.
- Treat the internal freeze as the operating deadline. Use the remaining day only for an already-built demo upload, submission-form checks, and emergency rollback.

Official event sources: [event home](https://allthingsagentichackathon.devpost.com/) and [rules](https://allthingsagentichackathon.devpost.com/rules).

## What this edition actually does

The contest path is intentionally small and testable:

1. A user types a normal creative request into the chat page.
2. The public Cloud Run API validates the request, stores a durable Firestore job, and enqueues an authenticated Cloud Task.
3. Cloud Tasks calls a private Cloud Run worker with an OIDC token.
4. The worker verifies that the configured model is reachable, then uses the official `google-genai` SDK and Vertex AI v1 with `gemini-3.5-flash` to produce an exact creative-plan schema.
5. Local deterministic code expands every ordered scene into establishing, primary-coverage, and continuity-bridge storyboard cards; allocates a contiguous 24-fps planned timeline; then independently audits ordering, duration, coverage, continuity, source-footage guidance, and dialogue/audio coverage.
6. The browser polls the durable job, presents every planned card and audit check, and can download the canonical JSON package without publishing it to a separate URL.

The first ETA is deliberately **unavailable**. An estimate appears only after successful jobs provide measured durations. Cancellation before the worker starts is final. Cancellation during a model call records the request and discards the result when the call returns; it does not claim that an in-flight provider request was preempted. Final success/failure and cancellation serialize in one Firestore transaction, so a cancellation that wins that transaction cannot be overwritten by a late provider result.

Each task is deterministically named from the job ID and application attempt. The body carries both values, so a delayed task from an older retry is acknowledged without running the newer attempt. A worker claim has a 360-second lease and a random fencing token. Active duplicate deliveries receive a retryable non-2xx response; an expired `running`/`cancelling` lease can be reclaimed, while every stage and terminal write must still own the exact attempt and fencing token.

This path produces a structured creative brief plus a deterministic storyboard/edit-decision **plan**. Each package says `media_status = unrendered_plan` and `plan_only = true`: it does not claim that a source clip was found, media was mutated, an edit was applied, or a movie was rendered. It does not claim a live model call, Cloud deployment, or contest receipt until a completed Firestore job contains `execution.evidence_origin = live_google_provider_response` and the matching provider metadata.

## Architecture

```text
browser chat
    |
    v
public Cloud Run API -----> Firestore job <----- browser status polling
    |
    v
Google Cloud Tasks (OIDC)
    |
    v
private Cloud Run worker -----> Vertex AI / Gemini 3.5 Flash creative plan
    |
    +----> deterministic timeline compiler ----> deterministic audit
    |
    +-------------------------------------------> Firestore package + measured duration
```

The API and worker use the same container image but different `KIRA_ALL_THINGS_SERVICE_ROLE` values. Cloud Run IAM, not a shared secret in source, protects the worker endpoint. The public demo page is readable, but every job create/read/cancel/retry route also requires an owner-created access code. The API stores only its SHA-256 digest and compares digests in constant time. Because this is one shared judge gate rather than end-user identity, Firestore also enforces one global admission window: by default 24 new jobs per hour with a three-second cooldown. Each job has at most three **application attempts**. Cloud Tasks delivery retries are a separate crash-recovery mechanism and must not be described as additional user retries or silently omitted from worst-case provider-cost analysis.

## Source map

- `kira_studio/all_things_agentic.py`: strict creative-plan contract, deterministic storyboard compiler/auditor, and durable job state machine.
- `kira_studio/all_things_google.py`: official Google Gen AI, Firestore, and Cloud Tasks adapters.
- `all_things_cloud_app.py`: dependency-light Cloud Run HTTP entry point.
- `web/all-things-agentic.html`: natural-chat demo and job controls.
- `contest_config/all_things_agentic.env.example`: non-secret configuration template.
- `contest_config/all_things_agentic.requirements.txt`: Cloud image dependencies.
- `deploy/all_things_agentic/Dockerfile`: non-root Cloud Run container.
- `cloudbuild.yaml`: clean Cloud Build path that builds and pushes the reviewed image without local Docker.

## Required Google Cloud setup

Use one billing-enabled Google Cloud project. The current candidate project is not written into source or defaults; supply the owner-selected project at deploy time.

Enable these APIs:

```powershell
gcloud services enable run.googleapis.com cloudtasks.googleapis.com firestore.googleapis.com aiplatform.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com iamcredentials.googleapis.com --project $env:AT_PROJECT
```

Create a Firestore database in Native mode, an Artifact Registry Docker repository, and a Cloud Tasks queue in the same operating region as the Cloud Run services. The Vertex AI location remains `global`; the Cloud Tasks location must be a real region such as `us-central1`.

Use separate identities with least privilege:

| Identity | Required use |
| --- | --- |
| API Cloud Run service account | Firestore read/write, Cloud Tasks enqueue, and permission to act as the task-caller identity |
| Worker Cloud Run service account | Firestore read/write and Vertex AI user |
| Cloud Tasks caller service account | Cloud Run Invoker on the private worker only |

Google's Cloud Tasks service agent must be able to mint the OIDC token. Review the exact IAM bindings before deployment; do not use owner/editor as the application runtime identity.

Authoritative setup references:

- [Verified Vertex AI Python sample using `gemini-3.5-flash`](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/samples/googlegenaisdk-textgen-with-local-video)
- [Google Gen AI SDK on PyPI](https://pypi.org/project/google-genai/)
- [Cloud Run container deployment](https://docs.cloud.google.com/run/docs/deploying)
- [Cloud Tasks to a private Cloud Run service](https://docs.cloud.google.com/run/docs/triggering/using-tasks)
- [Firestore server client libraries](https://cloud.google.com/firestore/docs/reference/libraries)

## Build once, deploy twice

Run from the repository root after setting owner-reviewed PowerShell variables:

```powershell
$env:AT_PROJECT = "your-project-id"
$env:AT_REGION = "us-central1"
$env:AT_REPOSITORY = "video-studio"
$env:AT_IMAGE = "$($env:AT_REGION)-docker.pkg.dev/$($env:AT_PROJECT)/$($env:AT_REPOSITORY)/all-things-agentic:submission"
$env:AT_WORKER = "video-studio-agent-worker"
$env:AT_API = "video-studio-agent-api"
$env:AT_WORKER_SA = "video-studio-worker@$($env:AT_PROJECT).iam.gserviceaccount.com"
$env:AT_API_SA = "video-studio-api@$($env:AT_PROJECT).iam.gserviceaccount.com"
$env:AT_TASKS_SA = "video-studio-tasks@$($env:AT_PROJECT).iam.gserviceaccount.com"
$env:AT_ACCESS_SHA256 = py -c "import getpass,hashlib; print(hashlib.sha256(getpass.getpass('New judge access code: ').encode('utf-8')).hexdigest())"
```

The access-code prompt does not echo the plaintext and only the resulting digest enters the Cloud Run environment. Give the plaintext code to judges through an owner-controlled channel; never add it to source, screenshots, logs, shell history, Firestore, or the submission text. Re-run the hash command and deploy a new API revision to rotate it.

Create the three service accounts once, then grant only the runtime roles described above. These commands are required on a fresh project; the later deploy commands do not create identities or runtime IAM for you:

```powershell
gcloud iam service-accounts create video-studio-api --project $env:AT_PROJECT --display-name "Video Studio All Things API"
gcloud iam service-accounts create video-studio-worker --project $env:AT_PROJECT --display-name "Video Studio All Things worker"
gcloud iam service-accounts create video-studio-tasks --project $env:AT_PROJECT --display-name "Video Studio Cloud Tasks caller"

gcloud projects add-iam-policy-binding $env:AT_PROJECT --member "serviceAccount:$($env:AT_API_SA)" --role roles/datastore.user
gcloud projects add-iam-policy-binding $env:AT_PROJECT --member "serviceAccount:$($env:AT_API_SA)" --role roles/cloudtasks.enqueuer
gcloud iam service-accounts add-iam-policy-binding $env:AT_TASKS_SA --project $env:AT_PROJECT --member "serviceAccount:$($env:AT_API_SA)" --role roles/iam.serviceAccountUser

gcloud projects add-iam-policy-binding $env:AT_PROJECT --member "serviceAccount:$($env:AT_WORKER_SA)" --role roles/datastore.user
gcloud projects add-iam-policy-binding $env:AT_PROJECT --member "serviceAccount:$($env:AT_WORKER_SA)" --role roles/aiplatform.user

$env:AT_PROJECT_NUMBER = gcloud projects describe $env:AT_PROJECT --format "value(projectNumber)"
$env:AT_TASKS_AGENT = "service-$($env:AT_PROJECT_NUMBER)@gcp-sa-cloudtasks.iam.gserviceaccount.com"
gcloud iam service-accounts add-iam-policy-binding $env:AT_TASKS_SA --project $env:AT_PROJECT --member "serviceAccount:$($env:AT_TASKS_AGENT)" --role roles/iam.serviceAccountTokenCreator
```

If an identity already exists, skip only its `create` command; still inspect its IAM policy. The final `roles/run.invoker` grant remains service-scoped and is applied after the worker exists.

Build and push the reviewed image locally:

```powershell
gcloud auth configure-docker "$($env:AT_REGION)-docker.pkg.dev"
docker build --file deploy/all_things_agentic/Dockerfile --tag $env:AT_IMAGE .
docker push $env:AT_IMAGE
```

Or build and push from the clean Cloud Build configuration without local Docker. The selected Cloud Build service account must already have permission to write to the Artifact Registry repository:

```powershell
gcloud builds submit --project $env:AT_PROJECT --config cloudbuild.yaml --substitutions "_IMAGE=$($env:AT_IMAGE)" .
```

Deploy the worker first and keep it private:

```powershell
gcloud run deploy $env:AT_WORKER --project $env:AT_PROJECT --region $env:AT_REGION --image $env:AT_IMAGE --service-account $env:AT_WORKER_SA --no-allow-unauthenticated --min-instances 0 --max-instances 1 --cpu 1 --memory 512Mi --concurrency 1 --timeout 300 --set-env-vars "KIRA_ALL_THINGS_SERVICE_ROLE=worker,GOOGLE_CLOUD_PROJECT=$($env:AT_PROJECT),GOOGLE_CLOUD_LOCATION=global,GOOGLE_GENAI_USE_VERTEXAI=True,KIRA_ALL_THINGS_GEMINI_MODEL=gemini-3.5-flash,KIRA_ALL_THINGS_FIRESTORE_DATABASE=(default),KIRA_ALL_THINGS_JOBS_COLLECTION=all_things_agentic_jobs,KIRA_ALL_THINGS_TASKS_LOCATION=$($env:AT_REGION),KIRA_ALL_THINGS_TASKS_QUEUE=video-studio-production-briefs,KIRA_ALL_THINGS_WORKER_LEASE_SECONDS=360"
$env:AT_WORKER_URL = gcloud run services describe $env:AT_WORKER --project $env:AT_PROJECT --region $env:AT_REGION --format "value(status.url)"
gcloud run services add-iam-policy-binding $env:AT_WORKER --project $env:AT_PROJECT --region $env:AT_REGION --member "serviceAccount:$($env:AT_TASKS_SA)" --role roles/run.invoker
```

Create the queue if it does not exist, then deploy the public demo API:

```powershell
gcloud tasks queues create video-studio-production-briefs --project $env:AT_PROJECT --location $env:AT_REGION
gcloud tasks queues update video-studio-production-briefs --project $env:AT_PROJECT --location $env:AT_REGION --max-attempts 30 --max-retry-duration 1800s --min-backoff 5s --max-backoff 60s --max-concurrent-dispatches 1 --max-dispatches-per-second 1
gcloud tasks queues describe video-studio-production-briefs --project $env:AT_PROJECT --location $env:AT_REGION --format "yaml(retryConfig,rateLimits)"
gcloud run deploy $env:AT_API --project $env:AT_PROJECT --region $env:AT_REGION --image $env:AT_IMAGE --service-account $env:AT_API_SA --allow-unauthenticated --min-instances 0 --max-instances 1 --cpu 1 --memory 512Mi --concurrency 20 --timeout 60 --set-env-vars "KIRA_ALL_THINGS_SERVICE_ROLE=api,GOOGLE_CLOUD_PROJECT=$($env:AT_PROJECT),GOOGLE_CLOUD_LOCATION=global,GOOGLE_GENAI_USE_VERTEXAI=True,KIRA_ALL_THINGS_GEMINI_MODEL=gemini-3.5-flash,KIRA_ALL_THINGS_DEMO_ACCESS_SHA256=$($env:AT_ACCESS_SHA256),KIRA_ALL_THINGS_FIRESTORE_DATABASE=(default),KIRA_ALL_THINGS_JOBS_COLLECTION=all_things_agentic_jobs,KIRA_ALL_THINGS_TASKS_LOCATION=$($env:AT_REGION),KIRA_ALL_THINGS_TASKS_QUEUE=video-studio-production-briefs,KIRA_ALL_THINGS_WORKER_URL=$($env:AT_WORKER_URL),KIRA_ALL_THINGS_TASKS_SERVICE_ACCOUNT=$($env:AT_TASKS_SA),KIRA_ALL_THINGS_ADMISSION_COOLDOWN_SECONDS=3,KIRA_ALL_THINGS_ADMISSION_WINDOW_SECONDS=3600,KIRA_ALL_THINGS_ADMISSION_MAX_JOBS=24,KIRA_ALL_THINGS_WORKER_LEASE_SECONDS=360"
```

Both services scale to zero and are capped at one instance for the contest demo. The API keeps modest parallel polling capacity; the worker runs one model job at a time. The queue's delivery policy must outlive the 360-second worker lease: the observed `maxAttempts=3` / `maxRetryDuration=300s` policy is unsafe because a task can exhaust redeliveries before a crashed claim becomes reclaimable. Apply and verify the `30` / `1800s` policy above; keep the separate three-application-attempt cap. Delivery attempts can still repeat a provider call after a crash, so do not claim that 24 admissions imply a hard 72-call provider ceiling. Create project budget alerts before the live test, but remember that Google Cloud budget alerts **monitor and notify; they do not enforce a spending cap**. Keep the instance caps and delete or disable unneeded resources after evidence is captured.

These commands are a deployment path, not a deployment receipt. Capture the immutable image digest, both Cloud Run revision names, queue resource, one completed job ID, and the Vertex response ID only after the real run succeeds. Never replace them with fabricated identifiers.

## Local contract verification

The Google packages are lazy imports, so contract tests run without credentials or network access:

```powershell
py -B -m unittest tests.test_all_things_agentic tests.test_all_things_cloud_app tests.test_all_things_agentic_ui -v
py -B -m py_compile kira_studio/all_things_agentic.py kira_studio/all_things_google.py all_things_cloud_app.py
node --test tests/test_all_things_agentic_ui.js
```

Injected clients in tests are labeled `injected_test_client` or `test_double`. They are not evidence of a live provider call.

For a local container smoke test, provide Application Default Credentials and all configuration values, then run either the API or worker role. A single local process cannot replace Firestore, Cloud Tasks, Cloud Run IAM, or the private-worker deployment.

## Judge demo path

1. Open the public API service root URL. The server returns the chat page. Enter the owner-provided judge access code; the page keeps it only in the password field for this tab and sends it only to same-origin job routes.
2. Enter: `Make a one-minute science-fiction dialogue scene in an orbital repair shop. Two old friends must decide whether to leave Earth. Keep the machinery quiet enough that every line is clear.`
3. Select **Build storyboard package**. Point out the durable job ID, queued/running stage, progress, application attempt, and honest initially-unavailable ETA.
4. Let the page poll through `calling_gemini`, `validating_creative_plan`, `compiling_storyboard_timeline`, and `auditing_coverage_and_continuity`. Show the creative brief, ordered cards with planned 24-fps in/out timecodes, framing/camera/action/audio direction, continuity and source/bridge guidance, deterministic audit, JSON download, and live Vertex execution metadata.
5. Submit a deliberately ambiguous request such as `Make me a show.` Show that Gemini returns concise questions and `ready_for_production: false` instead of pretending it understood.
6. Demonstrate cancel on a queued job and retry on that cancelled job. A retry is capped at three attempts.

Before recording the demo, confirm `/healthz` names the intended project/model but explain that health is configuration evidence only. Then inspect the completed job itself for live-provider evidence.

Access-gate smoke test: a missing code and an incorrect code must both return the same `401` response; the correct code must create a `202` queued job. The server intentionally emits no `Access-Control-Allow-Origin` header, the UI uses only relative same-origin requests, and the HTML content-security policy allows network connections only to its own origin. The access code plus Firestore admission window are a bounded shared-demo control, not a replacement for full end-user identity and production-grade abuse controls.

## Current hold and closeout checklist

As of August 28, 2026, local source and test-double verification exist. A signed-in Google Cloud account, billing screen, or provisioned resource is not by itself end-to-end proof. Treat the deployment as live only after the selected project passes Vertex lookup, Firestore persistence, Cloud Tasks/private-worker execution, package download, and one completed job with the matching provider evidence.

After billing is active:

- Enable the required APIs and create/review the three service accounts.
- Create Firestore, the queue, and Artifact Registry repository.
- Build the clean container and record resolved package versions and image digest.
- Deploy the private worker before the API; confirm unauthenticated worker access returns 401/403.
- Run one clear request, one clarification request, cancel, and retry.
- Verify Firestore durability across an API restart.
- Capture only real resource names, revision IDs, task ID, completed job ID, and Vertex response ID.
- Record the demo and freeze by August 30 at 5:00 PM EDT.
