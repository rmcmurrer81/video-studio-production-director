# Video Studio All Things Agentic deployment and demo

This is the operator document for the corrected contest build. Start with [START_HERE_TEST.md](START_HERE_TEST.md) if the services are already deployed.

## What a successful job must contain

A production-ready request is complete only when the same durable job contains:

1. a schema-validated creative brief;
2. the deterministic detailed card/timeline package and passing continuity audit;
3. a private planning JPEG for every card—no `VISUAL PENDING` entries;
4. a synopsis and evidence-limited character, location, shot-list, and EDL exports;
5. one narration and subtitle cue for every card; and
6. a complete narrated-pitch manifest whose MP4 has been probed as 1920×1080 H.264 video with AAC audio.

This is still **preproduction and investor-pitch media**. The planning illustrations and narrated card sequence are not filmed footage, an acted or lip-synced scene, or a completed raw-footage edit.

## Architecture

```text
installed PWA / browser
    |
    | same-origin job API + owner/judge access header
    v
public Cloud Run API <----------> Firestore durable job
    |                                  ^
    | named Cloud Task                 | lease-fenced updates
    v                                  |
Cloud Tasks --OIDC--> private Cloud Run worker
                              |
                              +--> Vertex AI Gemini 3.5+ brief
                              +--> Vertex AI image model: every card
                              +--> Cloud TTS Chirp 3 HD: every cue
                              +--> FFmpeg + FFprobe: 1080p H.264/AAC MP4
                              +--> private Cloud Storage artifacts

PWA --authenticated job-bound artifact route--> API --service identity--> bucket
```

The bucket is never public. The page receives neither a `gs://` URL nor a signed/public URL. The API serves only artifact IDs declared by that exact completed job and verifies the object prefix, byte count, and SHA-256 before returning bytes.

## Before running commands

Use an owner-selected billing-enabled project. The commands below are PowerShell examples. Review every substituted value before pressing Enter. They create billable resources.

```powershell
$env:AT_PROJECT = "your-project-id"
$env:AT_REGION = "us-central1"
$env:AT_BUCKET = "globally-unique-video-studio-artifacts-name"
$env:AT_REPOSITORY = "video-studio"
$env:AT_IMAGE = "$($env:AT_REGION)-docker.pkg.dev/$($env:AT_PROJECT)/$($env:AT_REPOSITORY)/all-things-agentic:submission"
$env:AT_WORKER = "video-studio-agent-worker"
$env:AT_API = "video-studio-agent-api"
$env:AT_WORKER_SA = "video-studio-worker@$($env:AT_PROJECT).iam.gserviceaccount.com"
$env:AT_API_SA = "video-studio-api@$($env:AT_PROJECT).iam.gserviceaccount.com"
$env:AT_TASKS_SA = "video-studio-tasks@$($env:AT_PROJECT).iam.gserviceaccount.com"
$env:AT_ACCESS_SHA256 = py -c "import getpass,hashlib; print(hashlib.sha256(getpass.getpass('New judge access code: ').encode('utf-8')).hexdigest())"
```

Choose a globally unique bucket name that contains no email address, access code, or other sensitive information. Bucket names are globally visible even when their contents are private.

The plaintext owner/judge code must never enter source, a committed `.env`, a URL, logs, screenshots, Firestore, or the submission form. Only its SHA-256 digest is deployed. Give the plaintext to judges through an owner-controlled channel.

## 1. Enable the required APIs

```powershell
gcloud services enable run.googleapis.com cloudtasks.googleapis.com firestore.googleapis.com aiplatform.googleapis.com texttospeech.googleapis.com storage.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com iamcredentials.googleapis.com --project $env:AT_PROJECT
```

The corrected build adds `texttospeech.googleapis.com` and `storage.googleapis.com`; omitting either prevents the full-media path from completing.

## 2. Create the private artifact bucket exactly once

Create it with Uniform Bucket-Level Access and Public Access Prevention enabled from the beginning:

```powershell
gcloud storage buckets create "gs://$($env:AT_BUCKET)" --project $env:AT_PROJECT --location $env:AT_REGION --default-storage-class STANDARD --uniform-bucket-level-access --public-access-prevention --soft-delete-duration=0
```

`--soft-delete-duration=0` is a deliberate short-lived contest-cost choice: accidentally deleted objects are not recoverable. If recovery matters more than temporary storage cost, use an owner-reviewed soft-delete period instead.

Verify both required privacy controls before deployment:

```powershell
gcloud storage buckets describe "gs://$($env:AT_BUCKET)" --project $env:AT_PROJECT --format="yaml(name,location,public_access_prevention,uniform_bucket_level_access)"
```

The output must show Public Access Prevention as `enforced` and Uniform Bucket-Level Access as enabled/true. The application also reloads bucket metadata and refuses to start when either condition is absent.

Official references: [bucket creation](https://docs.cloud.google.com/storage/docs/creating-buckets), [Public Access Prevention](https://docs.cloud.google.com/storage/docs/public-access-prevention), and [Uniform Bucket-Level Access](https://docs.cloud.google.com/storage/docs/using-uniform-bucket-level-access).

## 3. Create service accounts and least-privilege storage roles

Create three runtime identities:

```powershell
gcloud iam service-accounts create video-studio-api --project $env:AT_PROJECT --display-name "Video Studio API"
gcloud iam service-accounts create video-studio-worker --project $env:AT_PROJECT --display-name "Video Studio worker"
gcloud iam service-accounts create video-studio-tasks --project $env:AT_PROJECT --display-name "Video Studio Cloud Tasks caller"
```

If an identity already exists, skip only that `create` command and inspect its policy. Do not grant Owner or Editor to an application identity.

The storage adapter checks bucket metadata as well as object bytes. Create two project custom-role definitions, then grant them only on this bucket:

```powershell
gcloud iam roles create videoStudioArtifactApi --project $env:AT_PROJECT --title "Video Studio artifact API" --description "Read declared job artifacts and verify bucket privacy" --permissions storage.buckets.get,storage.objects.get --stage GA
gcloud iam roles create videoStudioArtifactWorker --project $env:AT_PROJECT --title "Video Studio artifact worker" --description "Create/read immutable job artifacts and verify bucket privacy" --permissions storage.buckets.get,storage.objects.create,storage.objects.get --stage GA

gcloud storage buckets add-iam-policy-binding "gs://$($env:AT_BUCKET)" --member "serviceAccount:$($env:AT_API_SA)" --role "projects/$($env:AT_PROJECT)/roles/videoStudioArtifactApi"
gcloud storage buckets add-iam-policy-binding "gs://$($env:AT_BUCKET)" --member "serviceAccount:$($env:AT_WORKER_SA)" --role "projects/$($env:AT_PROJECT)/roles/videoStudioArtifactWorker"
```

The worker creates content-addressed objects with a create-only precondition and then reads them for MP4 assembly and integrity checks. The API reads only the object named in a completed job manifest. Neither identity needs permission to make an object public or change bucket policy.

If a custom role already exists, use `gcloud iam roles describe ...` and compare its permissions rather than blindly recreating it. See [Cloud Storage IAM roles](https://docs.cloud.google.com/storage/docs/access-control/iam-roles).

## 4. Grant Firestore, Tasks, Vertex AI, and Text-to-Speech access

```powershell
gcloud projects add-iam-policy-binding $env:AT_PROJECT --member "serviceAccount:$($env:AT_API_SA)" --role roles/datastore.user
gcloud projects add-iam-policy-binding $env:AT_PROJECT --member "serviceAccount:$($env:AT_API_SA)" --role roles/cloudtasks.enqueuer
gcloud iam service-accounts add-iam-policy-binding $env:AT_TASKS_SA --project $env:AT_PROJECT --member "serviceAccount:$($env:AT_API_SA)" --role roles/iam.serviceAccountUser

gcloud projects add-iam-policy-binding $env:AT_PROJECT --member "serviceAccount:$($env:AT_WORKER_SA)" --role roles/datastore.user
gcloud projects add-iam-policy-binding $env:AT_PROJECT --member "serviceAccount:$($env:AT_WORKER_SA)" --role roles/aiplatform.user
gcloud projects add-iam-policy-binding $env:AT_PROJECT --member "serviceAccount:$($env:AT_WORKER_SA)" --role roles/serviceusage.serviceUsageConsumer

$env:AT_PROJECT_NUMBER = gcloud projects describe $env:AT_PROJECT --format "value(projectNumber)"
$env:AT_TASKS_AGENT = "service-$($env:AT_PROJECT_NUMBER)@gcp-sa-cloudtasks.iam.gserviceaccount.com"
gcloud iam service-accounts add-iam-policy-binding $env:AT_TASKS_SA --project $env:AT_PROJECT --member "serviceAccount:$($env:AT_TASKS_AGENT)" --role roles/iam.serviceAccountTokenCreator
```

Cloud Text-to-Speech uses the worker's attached service-account Application Default Credentials. The synchronous synthesis method uses the Cloud Platform authorization scope; this path does not use Speech-to-Text roles. The API must be enabled and the worker must be allowed to consume enabled project services. See [Cloud TTS authentication](https://docs.cloud.google.com/text-to-speech/docs/authentication) and [Chirp 3 HD voices](https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd).

## 5. Create Firestore, Artifact Registry, and the queue

Create Firestore in Native mode in an owner-selected supported location. Do this once; the database location cannot be casually changed. Then create Artifact Registry and Cloud Tasks resources:

```powershell
gcloud artifacts repositories create $env:AT_REPOSITORY --project $env:AT_PROJECT --location $env:AT_REGION --repository-format docker --description "Video Studio contest images"
gcloud tasks queues create video-studio-production-briefs --project $env:AT_PROJECT --location $env:AT_REGION
gcloud tasks queues update video-studio-production-briefs --project $env:AT_PROJECT --location $env:AT_REGION --max-attempts 30 --max-retry-duration 3600s --min-backoff 5s --max-backoff 60s --max-concurrent-dispatches 1 --max-dispatches-per-second 1
```

Cloud Tasks delivery retry is crash recovery, not an extra owner-visible application retry. The application separately caps a job at three **application attempts**.

The previously observed `maxAttempts=3` / `maxRetryDuration=300s` policy is unsafe because it can exhaust crash-recovery delivery before the 1,800-second fenced lease is reclaimable. The queue command above deliberately uses a longer retry window and more delivery attempts; those delivery attempts do not increase the owner-visible application-attempt cap.

## 6. Build the reviewed container

Run the local contract checks first:

```powershell
py -B -m unittest discover -s tests -p "test_all_things*.py" -v
py -B -m py_compile kira_studio/all_things_agentic.py kira_studio/all_things_google.py kira_studio/all_things_media.py kira_studio/all_things_cloud_media.py all_things_cloud_app.py
node --test tests/test_all_things_agentic_ui.js
```

Then use the clean Cloud Build path:

```powershell
gcloud builds submit --project $env:AT_PROJECT --config cloudbuild.yaml --substitutions "_IMAGE=$($env:AT_IMAGE)" .
```

Record the immutable image digest after the build. A successful build is not a live job or media proof.

## 7. Deploy the private worker first

For the all-card image and FFmpeg path, start with 2 CPU, 4 GiB, concurrency 1, and one maximum instance. This is a contest baseline, not a universal performance guarantee.

```powershell
gcloud run deploy $env:AT_WORKER --project $env:AT_PROJECT --region $env:AT_REGION --image $env:AT_IMAGE --service-account $env:AT_WORKER_SA --no-allow-unauthenticated --min-instances 0 --max-instances 1 --cpu 2 --memory 4Gi --concurrency 1 --timeout 1740 --set-env-vars "KIRA_ALL_THINGS_SERVICE_ROLE=worker,GOOGLE_CLOUD_PROJECT=$($env:AT_PROJECT),GOOGLE_CLOUD_LOCATION=global,GOOGLE_GENAI_USE_VERTEXAI=True,KIRA_ALL_THINGS_GEMINI_MODEL=gemini-3.5-flash,KIRA_ALL_THINGS_IMAGE_MODEL=gemini-3.1-flash-image,KIRA_ALL_THINGS_ARTIFACTS_BUCKET=$($env:AT_BUCKET),KIRA_ALL_THINGS_TTS_VOICE=en-US-Chirp3-HD-Aoede,KIRA_ALL_THINGS_FIRESTORE_DATABASE=(default),KIRA_ALL_THINGS_JOBS_COLLECTION=all_things_agentic_jobs,KIRA_ALL_THINGS_TASKS_LOCATION=$($env:AT_REGION),KIRA_ALL_THINGS_TASKS_QUEUE=video-studio-production-briefs,KIRA_ALL_THINGS_WORKER_LEASE_SECONDS=1800"

$env:AT_WORKER_URL = gcloud run services describe $env:AT_WORKER --project $env:AT_PROJECT --region $env:AT_REGION --format "value(status.url)"
gcloud run services add-iam-policy-binding $env:AT_WORKER --project $env:AT_PROJECT --region $env:AT_REGION --member "serviceAccount:$($env:AT_TASKS_SA)" --role roles/run.invoker
```

Confirm an unauthenticated worker request is rejected by Cloud Run IAM. The corrected dispatcher sets a 1,740-second per-task HTTP deadline, the Cloud Run worker timeout is 1,740 seconds, and the fenced worker lease is 1,800 seconds. Keep those three values aligned in that order. A queue retry window alone does not extend an individual HTTP request.

## 8. Deploy the public API

```powershell
gcloud run deploy $env:AT_API --project $env:AT_PROJECT --region $env:AT_REGION --image $env:AT_IMAGE --service-account $env:AT_API_SA --allow-unauthenticated --min-instances 0 --max-instances 1 --cpu 1 --memory 1Gi --concurrency 20 --timeout 60 --set-env-vars "KIRA_ALL_THINGS_SERVICE_ROLE=api,GOOGLE_CLOUD_PROJECT=$($env:AT_PROJECT),GOOGLE_CLOUD_LOCATION=global,GOOGLE_GENAI_USE_VERTEXAI=True,KIRA_ALL_THINGS_GEMINI_MODEL=gemini-3.5-flash,KIRA_ALL_THINGS_IMAGE_MODEL=gemini-3.1-flash-image,KIRA_ALL_THINGS_ARTIFACTS_BUCKET=$($env:AT_BUCKET),KIRA_ALL_THINGS_TTS_VOICE=en-US-Chirp3-HD-Aoede,KIRA_ALL_THINGS_DEMO_ACCESS_SHA256=$($env:AT_ACCESS_SHA256),KIRA_ALL_THINGS_FIRESTORE_DATABASE=(default),KIRA_ALL_THINGS_JOBS_COLLECTION=all_things_agentic_jobs,KIRA_ALL_THINGS_TASKS_LOCATION=$($env:AT_REGION),KIRA_ALL_THINGS_TASKS_QUEUE=video-studio-production-briefs,KIRA_ALL_THINGS_WORKER_URL=$($env:AT_WORKER_URL),KIRA_ALL_THINGS_TASKS_SERVICE_ACCOUNT=$($env:AT_TASKS_SA),KIRA_ALL_THINGS_ADMISSION_COOLDOWN_SECONDS=3,KIRA_ALL_THINGS_ADMISSION_WINDOW_SECONDS=3600,KIRA_ALL_THINGS_ADMISSION_MAX_JOBS=24,KIRA_ALL_THINGS_WORKER_LEASE_SECONDS=1800"
```

The API is publicly reachable so judges can load the application, but all job and artifact routes still require the owner/judge code. The private worker remains IAM protected.

## Required non-secret environment values

Both roles require:

| Variable | Required value/use |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT` | Selected billing-enabled project ID. |
| `GOOGLE_CLOUD_LOCATION` | Vertex AI location; this build uses `global`. |
| `GOOGLE_GENAI_USE_VERTEXAI` | `True`. |
| `KIRA_ALL_THINGS_GEMINI_MODEL` | Reviewed Gemini 3.5+ brief model. |
| `KIRA_ALL_THINGS_IMAGE_MODEL` | Reviewed Vertex image model. |
| `KIRA_ALL_THINGS_ARTIFACTS_BUCKET` | Exact private bucket name, without `gs://`. |
| `KIRA_ALL_THINGS_TTS_VOICE` | A valid Chirp 3 HD voice such as `en-US-Chirp3-HD-Aoede`. |
| `KIRA_ALL_THINGS_FIRESTORE_DATABASE` | Normally `(default)`. |
| `KIRA_ALL_THINGS_JOBS_COLLECTION` | Durable jobs collection. |
| `KIRA_ALL_THINGS_WORKER_LEASE_SECONDS` | Longer than worker request timeout; valid code range is 60–1800 seconds. |

The API additionally requires the access-code digest and Cloud Tasks dispatch values. The worker never needs the access-code digest. Do not put plaintext secrets or downloaded service-account keys in environment files; Cloud Run uses attached service identities.

## Realistic time, resource, and cost cautions

- One planning image is requested per card. Three cards per scene means a 12-scene script can require 36 image generations before TTS and encoding.
- Provider quota, safety/provider rejection, timeout, or malformed image output must fail the complete-media gate; the system must not silently substitute blank cards and call the job complete.
- Chirp 3 HD synthesis is billable and runs once per card cue. Vertex text/image calls, Cloud Run CPU/memory time, Cloud Storage bytes/operations, Firestore, Cloud Tasks, Cloud Build, and Artifact Registry can also incur charges.
- Google Cloud budget alerts notify; they do not stop spending. Keep worker concurrency and max instances at one during judging, watch Billing reports, and remove old artifacts/resources after the evidence window according to an owner-approved retention policy.
- Cold starts, quota, card count, narration length, and FFmpeg work make elapsed time variable. The first job has no evidence-based ETA. Do not promise a completion time until measured live jobs exist.
- The pitch renderer is bounded to 60 minutes and 2 GiB. The task/worker request ceiling can be lower. Use the one-minute START HERE test before a TV episode; do not use a feature-length screenplay as the first live proof.
- 4 GiB is a reasonable contest starting point for 1080p FFmpeg work, not proof that every long script fits. Monitor worker memory and CPU before reducing it.

## Live evidence boundary

`GET /health`, a billing page, enabled APIs, a deployed revision, passing mock tests, or a generated fixture is not end-to-end proof. A real acceptance run must show:

- a completed Firestore job with `execution.evidence_origin = live_google_provider_response`;
- full `generated_panel_count == required_panel_count` coverage and no missing/pending panel;
- private artifact manifests bound to the completed job;
- one cue per card;
- a narrated pitch manifest marked complete;
- FFprobe verification of 1920×1080 H.264/AAC media;
- successful authenticated panel and MP4 retrieval; and
- truthful plan-only labels in the UI and exports.

Only after that run should the operator capture real job IDs, provider metadata, revision names, container digest, hashes, timing, and cost. Never replace missing proof with a fabricated receipt.

## Primary Google references

- [Deploying containers to Cloud Run](https://docs.cloud.google.com/run/docs/deploying)
- [Using Cloud Tasks with private Cloud Run services](https://docs.cloud.google.com/run/docs/triggering/using-tasks)
- [Cloud Storage bucket creation](https://docs.cloud.google.com/storage/docs/creating-buckets)
- [Public Access Prevention](https://docs.cloud.google.com/storage/docs/public-access-prevention)
- [Uniform Bucket-Level Access](https://docs.cloud.google.com/storage/docs/using-uniform-bucket-level-access)
- [Cloud Storage IAM roles](https://docs.cloud.google.com/storage/docs/access-control/iam-roles)
- [Cloud Text-to-Speech authentication](https://docs.cloud.google.com/text-to-speech/docs/authentication)
- [Chirp 3 HD voices](https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd)
- [Firestore server client libraries](https://cloud.google.com/firestore/docs/reference/libraries)
