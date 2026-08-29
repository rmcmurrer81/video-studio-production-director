# START HERE — one owner/judge test

Use this page for the first acceptance run. Do not start in an old `TEST-EXAMPLE`, `FULL-SCREENPLAY`, or `SHORT-PITCH-PREVIEW` folder; those may contain historical partial evidence. The corrected build is tested from the installed Video Studio app (preferred) or the current hosted app URL.

## What you need

- the current hosted URL or installed PWA;
- the owner/judge access code supplied privately by the project owner; and
- about 15–30 minutes of uninterrupted test time. Actual time varies with cold starts and provider quota; this is a testing allowance, not an ETA promise.

Do not paste the access code into chat, screenshots, a document, GitHub, or the submission form.

## 1. Open one obvious application

Open **Video Studio — Storyboard Artist & Production Planner** as its own installed app window. If it is not installed, open the hosted URL in Microsoft Edge, choose **⋯ → Apps → Install this site as an app**, and then launch it from the Start menu.

You should see:

- natural-chat/script attachment input;
- an owner/judge access-code field;
- a **Create production plan** button;
- a Production monitor; and
- the storyboard/shot-list area.

Do not open JSON files to begin the test. JSON is a behind-the-scenes audit/export, not the main owner experience.

## 2. Enter the private code

Enter the owner/judge access code in its password field. Keep it off camera. The app keeps it for the current window and sends it only to same-origin protected routes.

## 3. Paste this exact short test

```text
Create a 45-second science-fiction dialogue scene in a quiet orbital repair bay. Two old friends, Mara and Dax, must decide whether to leave a damaged station before an alien signal reaches them. Keep the room tone subtle and the dialogue clear. Mara says, “The signal knows our names.” Dax answers, “Then we leave before it learns our plans.” End with them choosing to work together. Make this as a storyboard production plan and narrated investor-pitch preview, not finished filmed footage.
```

Select **Create production plan** once. Do not repeatedly click it while the job is queued or running.

## 4. Watch the Production monitor

The first ETA should be unavailable because no completed-job timing basis exists yet. The job should move through brief generation, validation, deterministic timeline/audit, all-card visual generation, narration, MP4 rendering, and final verification.

The completed status must be **succeeded**. If it says failed, copy the safe error code and job ID for troubleshooting; do not call it a pass and do not retry blindly more than the owner-approved limit.

## 5. Check the four things that matter

### A. Every card has a visual

- The visual card count equals the detailed card count.
- Every card shows an actual planning illustration.
- There is no `VISUAL PENDING`, `PARTIAL`, blank striped card, or `panel limit reached` message.
- Card order, IDs, and timecodes match the detailed board.

### B. The detailed plan is useful

- The title and synopsis describe this scene.
- Mara and Dax appear in the character/appearance dossier.
- Each card includes framing/camera, scene-specific action, dialogue/audio, and continuity direction.
- The explicit Mara and Dax lines appear in the plan/narration rather than being replaced with generic “protect dialogue” wording.
- Location, shot-list, continuity, and source-aware EDL exports are available.

### C. The MP4 exists and works

- The Production monitor or pitch section provides a video player and **Download pitch video** control.
- Play the full file. It must advance through every visual card in order.
- The narration must be audible and must cover what is happening plus the dialogue beats.
- Download the MP4 and open it full screen in Windows Media Player or VLC.
- The job manifest must report 1920×1080, H.264 video, and AAC audio.
- Narration TXT and SRT subtitle downloads are also available.

### D. The truth label is clear

The app and exports must call this a storyboard, production plan, previsualization, or narrated pitch. They must not claim it is acted/filmed footage, lip-synced character video, or an applied edit of selected raw footage.

## 6. Download one review package

From this same completed job, download:

1. narrated pitch MP4;
2. visual storyboard HTML/PDF;
3. detailed production sheet;
4. character/synopsis dossier;
5. location plan;
6. shot-list and source-aware EDL CSV files;
7. narration TXT and SRT; and
8. canonical package JSON.

Keep them together in one clearly named folder such as:

```text
OWNER-TEST-45-SECOND-ORBITAL-REPAIR-BAY
```

The MP4 and human-readable sheets are what the owner watches/reads. The JSON is retained for hashes, reproducibility, and judging evidence.

## Pass rule

Pass only if all visuals are present, all detailed exports are present, the MP4 plays with audible narration through every card, and the plan-only wording is accurate.

Any pending visual, partial count, absent/inaudible MP4, missing cue, wrong codec/resolution, broken download, or integrity error is a **HOLD**. Preserve the failed job ID and safe error message, fix the cause, and run a new short test. Never relabel an incomplete historical package as the corrected result.

## After the short test passes

Run one attached short screenplay or television scene to verify file import and exact dialogue extraction. A long episode creates many image and TTS calls and can exceed quota, task, cost, or pitch-duration limits. Do not use a feature-length script as the first proof, and do not promise that a completed short test proves hour-long throughput.
