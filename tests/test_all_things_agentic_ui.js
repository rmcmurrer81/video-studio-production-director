"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");


class FakeClassList {
  constructor(names = []) { this.names = new Set(names); }
  add(...names) { names.forEach(name => this.names.add(name)); }
  remove(...names) { names.forEach(name => this.names.delete(name)); }
  contains(name) { return this.names.has(name); }
  toggle(name, force) {
    const enabled = force === undefined ? !this.contains(name) : Boolean(force);
    if (enabled) this.add(name); else this.remove(name);
    return enabled;
  }
}


class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.children = [];
    this.dataset = {};
    this.disabled = false;
    this.listeners = new Map();
    this.offsetLeft = 12;
    this.placeholder = "";
    this.style = {left: "", width: "", setProperty() {}};
    this.value = "";
    this.files = [];
    this.attributes = new Map();
    this._className = "";
    this._innerHTML = "";
    this._textContent = "";
    this.classList = new FakeClassList();
  }
  get className() { return this._className; }
  set className(value) {
    this._className = value;
    this.classList = new FakeClassList(String(value).split(/\s+/).filter(Boolean));
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) { this._innerHTML = value; }
  get textContent() { return this._textContent; }
  set textContent(value) {
    this._textContent = String(value);
    if (value === "") this.children = [];
  }
  addEventListener(type, callback) { this.listeners.set(type, callback); }
  append(...children) {
    for (const child of children) {
      child.offsetLeft = 12 + this.children.length * 153;
      this.children.push(child);
    }
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  scrollIntoView() {}
  click() {
    this.clickCount = (this.clickCount || 0) + 1;
    const callback = this.listeners.get("click");
    if (callback) callback();
  }
  remove() { this.removed = true; }
}


function brief({questions = [], ready = false, title = "The Last Repair"} = {}) {
  return {
    title,
    summary: "Two old friends decide whether to leave Earth.",
    format: "dialogue scene",
    genre: "science fiction drama",
    duration_seconds: 60,
    visual_direction: "Grounded practical light.",
    audio_direction: "Quiet room tone beneath clear dialogue.",
    scenes: [{number: 1, setting: "repair shop", purpose: "Make the decision.", dialogue_required: true}],
    clarifying_questions: questions,
    ready_for_production: ready,
  };
}


function job(jobId, {questions = null, ready = false} = {}) {
  const completed = questions !== null;
  return {
    job_id: jobId,
    state: completed ? "succeeded" : "queued",
    stage: completed ? (ready ? "brief_ready" : "clarification_required") : "waiting_for_worker",
    progress: completed ? 100 : 0,
    attempt: 1,
    max_attempts: 3,
    eta: {available: false, high_seconds: null, sample_count: 0},
    ...(completed ? {brief: brief({questions, ready, title: `Brief ${jobId}`})} : {}),
  };
}


function visibleMessages(feed) {
  return feed.children.map(row => ({
    role: row.classList.contains("user") ? "user" : "assistant",
    text: row.children[1].children[1].textContent,
  }));
}


async function settle() {
  for (let index = 0; index < 8; index += 1) {
    await new Promise(resolve => setImmediate(resolve));
  }
}


test("short clarification answers compose complete one-field context across repeated rounds and reset when ready", async () => {
  const ids = [
    "access", "message", "submit", "cancel", "retry", "error", "state", "stage",
    "progress", "progressBar", "bar", "eta", "job", "brief", "monitor",
    "conversationFeed", "conversationContext", "timelineTrack", "timelineRuler",
    "timelineStatus", "timelineEmpty", "timelineTimecode", "timelinePlayhead",
    "timelineSelection", "timelineFirst", "timelinePrevious", "timelineNext", "timelineLast",
    "downloadPackage", "downloadVisualStoryboard", "printVisualStoryboard", "downloadDetailedSheet",
    "downloadLocationPlan", "downloadLocationCsv", "downloadLocationJson", "downloadCharacterHtml", "downloadCharacterText", "downloadCharacterJson", "downloadCharacterCsv", "downloadShotList", "downloadEdl", "accessHelp", "sourceSummary", "attachmentButton",
    "attachmentMenu", "attachStory", "attachFootage", "scriptFile", "footageFiles",
    "scriptStatus", "footageStatus", "animatic", "animaticPlay", "animaticStop", "pitchPlay", "pitchStop", "pitchVoiceStatus", "downloadPitchScript",
    "animaticImage", "animaticPlaceholder", "animaticOverlay", "animaticShot",
    "animaticAction", "animaticBar", "animaticTime", "animaticTruth", "installApp",
  ];
  const elements = new Map(ids.map(id => [id, new FakeElement(id)]));
  elements.get("access").value = "private-judge-code";

  const documentObject = {
    getElementById(id) { return elements.get(id); },
    createElement() { return new FakeElement(); },
    querySelectorAll(selector) {
      if (selector !== ".timeline-clip") return [];
      return elements.get("timelineTrack").children.filter(child => child.classList.contains("timeline-clip"));
    },
  };

  const questionOne = "Should the ending feel hopeful or uncertain?";
  const questionTwo = "Should the final decision happen in private or before the crew?";
  const completedByJob = new Map([
    ["job-1", job("job-1", {questions: [questionOne]})],
    ["job-2", job("job-2", {questions: [questionTwo]})],
    ["job-3", job("job-3", {questions: [], ready: true})],
    ["job-4", job("job-4", {questions: [], ready: true})],
  ]);
  let jobNumber = 0;
  const fetchCalls = [];
  async function fetchObject(url, options = {}) {
    fetchCalls.push({url, options});
    if (url === "/v1/jobs") {
      jobNumber += 1;
      return {ok: true, status: 202, async json() { return job(`job-${jobNumber}`); }};
    }
    return {ok: true, status: 200, async json() { return completedByJob.get(url.split("/").at(-1)); }};
  }

  const html = fs.readFileSync(path.join(__dirname, "..", "web", "all-things-agentic.html"), "utf8");
  const script = html.match(/<script>\s*([\s\S]*?)\s*<\/script>/)[1];
  vm.runInNewContext(script, {
    clearTimeout() {},
    console,
    document: documentObject,
    fetch: fetchObject,
    setTimeout(callback) { Promise.resolve().then(callback); return 1; },
  }, {filename: "all-things-agentic.html"});

  const submit = elements.get("submit").listeners.get("click");
  const original = "Make a one-minute science-fiction dialogue scene in a repair shop.";
  elements.get("message").value = original;
  await submit();
  await settle();

  let posts = fetchCalls.filter(call => call.url === "/v1/jobs");
  assert.equal(JSON.parse(posts[0].options.body).message, original);
  assert.match(visibleMessages(elements.get("conversationFeed")).at(-1).text, new RegExp(questionOne));

  elements.get("message").value = "Hopeful, but understated.";
  await submit();
  await settle();
  posts = fetchCalls.filter(call => call.url === "/v1/jobs");
  const secondMessage = JSON.parse(posts[1].options.body).message;
  assert.match(secondMessage, /Original user request:/);
  assert.match(secondMessage, new RegExp(original.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(secondMessage, new RegExp(questionOne.replace("?", "\\?")));
  assert.match(secondMessage, /User's current short answer:\n\nHopeful, but understated\./);
  assert.doesNotMatch(secondMessage, /private-judge-code/);
  assert.equal(visibleMessages(elements.get("conversationFeed")).filter(item => item.role === "user").at(-1).text, "Hopeful, but understated.");

  elements.get("message").value = "In private, beside the damaged engine.";
  await submit();
  await settle();
  posts = fetchCalls.filter(call => call.url === "/v1/jobs");
  const thirdMessage = JSON.parse(posts[2].options.body).message;
  assert.match(thirdMessage, /Clarification round 1 questions:/);
  assert.match(thirdMessage, new RegExp(questionOne.replace("?", "\\?")));
  assert.match(thirdMessage, /User answer for round 1:\nHopeful, but understated\./);
  assert.match(thirdMessage, new RegExp(questionTwo.replace("?", "\\?")));
  assert.match(thirdMessage, /User's current short answer:\n\nIn private, beside the damaged engine\./);
  assert.equal(Object.keys(JSON.parse(posts[2].options.body)).join(","), "message");

  const unrelated = "Make a completely different mystery short.";
  elements.get("message").value = unrelated;
  await submit();
  await settle();
  posts = fetchCalls.filter(call => call.url === "/v1/jobs");
  assert.equal(JSON.parse(posts[3].options.body).message, unrelated);
  assert.equal(posts[3].options.headers["X-Video-Studio-Access"], "private-judge-code");
  assert.equal(visibleMessages(elements.get("conversationFeed")).filter(item => item.role === "user").at(-1).text, unrelated);
});


test("visual and detailed HTML downloads stay separate, escaped, scriptless, and printable", async () => {
  const ids = [
    "access", "message", "submit", "cancel", "retry", "error", "state", "stage",
    "progress", "progressBar", "bar", "eta", "job", "brief", "monitor",
    "conversationFeed", "conversationContext", "timelineTrack", "timelineRuler",
    "timelineStatus", "timelineEmpty", "timelineTimecode", "timelinePlayhead",
    "timelineSelection", "timelineFirst", "timelinePrevious", "timelineNext", "timelineLast",
    "downloadPackage", "downloadVisualStoryboard", "printVisualStoryboard", "downloadDetailedSheet",
    "downloadLocationPlan", "downloadLocationCsv", "downloadLocationJson", "downloadCharacterHtml", "downloadCharacterText", "downloadCharacterJson", "downloadCharacterCsv", "downloadShotList", "downloadEdl", "accessHelp", "sourceSummary", "attachmentButton",
    "attachmentMenu", "attachStory", "attachFootage", "scriptFile", "footageFiles",
    "scriptStatus", "footageStatus", "animatic", "animaticPlay", "animaticStop", "pitchPlay", "pitchStop", "pitchVoiceStatus", "downloadPitchScript",
    "animaticImage", "animaticPlaceholder", "animaticOverlay", "animaticShot",
    "animaticAction", "animaticBar", "animaticTime", "animaticTruth", "installApp",
  ];
  const elements = new Map(ids.map(id => [id, new FakeElement(id)]));
  elements.get("access").value = "private-judge-code";
  const appended = [];
  const documentObject = {
    body: {append(node) { appended.push(node); }},
    getElementById(id) { return elements.get(id); },
    createElement() { return new FakeElement(); },
    querySelectorAll(selector) {
      if (selector !== ".timeline-clip") return [];
      return elements.get("timelineTrack").children.filter(child => child.classList.contains("timeline-clip"));
    },
  };
  const storyboardPackage = {
    package_id: "storyboard-safe-test",
    media_status: "unrendered_plan",
    manifest_sha256: "abc123",
    access_code: "package-secret-that-must-not-export",
    secret: "top-level-secret-that-must-not-export",
    production_brief: {
      title: "A <Director> & \"Friend\"",
      summary: "A summary with <script>alert(1)</script>.",
      format: "short scene",
      target_audience: "Independent filmmakers & investors",
      genre: "science fiction & drama",
      duration_seconds: 12,
      tone: ["warm", "<b>untrusted tone</b>"],
      visual_direction: "Close </style><script>alert(2)</script> light.",
      audio_direction: "Room tone & dialogue.",
      deliverables: ["review <copy>"],
      scenes: [{
        number: 1,
        setting: "Repair <shop>",
        purpose: "Choose & commit.",
        characters: ["Alex <Lead>"],
        dialogue_required: true,
      }, {
        number: 2,
        setting: "=Dock",
        purpose: "Inspect the departure route.",
        characters: ["Alex <Lead>", "Morgan", "=HYPERLINK(\"bad\")", "+COMMAND", "-DANGER", "@SUM"],
        dialogue_required: false,
      }, {
        number: 3,
        setting: "  repair   <SHOP> ",
        purpose: "Return and decide.",
        characters: ["Morgan", "  alex <lead>  "],
        dialogue_required: true,
      }],
      secret: "nested-secret-that-must-not-export",
    },
    timeline: {
      shot_count: 2,
      timecode_basis: "planned_non_drop_24fps",
      frame_rate: 24,
      start_timecode: "00:00:00:00",
      end_timecode: "00:00:12:00",
      shots: [{
        shot_id: "SC01-SH01",
        sequence: 1,
        scene_number: 1,
        role: "primary_coverage",
        planned_in_timecode: "00:00:00:00",
        planned_out_timecode: "00:00:06:00",
        planned_duration_seconds: 6,
        storyboard_card: {
          framing: "Medium <two-shot>",
          camera: "Locked & level",
          action: "Repair <engine> together.",
          dialogue_or_audio: "Protect dialogue <clearly>.",
          continuity_requirements: ["Match <wardrobe> & props."],
          source_footage_guidance: "Use verified <source> only.",
          bridge_shot_guidance: "Flag <missing> coverage.",
        },
      }, {
        shot_id: "SC01-SH02",
        sequence: 2,
        scene_number: 3,
        role: "reaction_coverage",
        planned_in_timecode: "00:00:06:00",
        planned_out_timecode: "00:00:12:00",
        planned_duration_seconds: 6,
        storyboard_card: {
          framing: "Close reaction",
          camera: "Locked & level",
          action: "Alex listens before answering.",
          dialogue_or_audio: "Hold room tone.",
          continuity_requirements: ["Match eyeline."],
          source_footage_guidance: "Use verified reaction coverage.",
          bridge_shot_guidance: "Flag a missing reaction.",
        },
      }],
    },
    audit: {
      structurally_valid: true,
      ready_for_editorial: true,
      passed: true,
      checks: [{id: "coverage_check", status: "pass", evidence: "No <img src=x onerror=alert(3)> gaps."}],
      issue_codes: [],
      hold_reasons: [],
    },
  };
  const visualStoryboard = {
    schema: "video-studio.visual-storyboard/v1",
    status: "partial",
    verification_scope: "technical_asset_integrity_only",
    required_panel_count: 2,
    available_panel_count: 1,
    missing_panel_count: 1,
    representation: "inline_base64",
    renderer: {
      provider: "google_vertex_ai",
      framework: "google_genai",
      model: "gemini-image-test",
      location: "global",
      evidence_origin: "provider_response",
    },
    panels: [{
      shot_id: "SC01-SH01",
      status: "available",
      alt_text: "Alex <Lead> repairs the engine; no <script> is trusted.",
      prompt_sha256: "1".repeat(64),
      mime_type: "image/jpeg",
      width: 768,
      height: 432,
      byte_length: 4,
      content_sha256: "2".repeat(64),
      data_base64: "/9j/2Q==",
      missing_reason: null,
    }, {
      shot_id: "SC01-SH02",
      status: "missing",
      alt_text: "Reaction planning panel pending.",
      prompt_sha256: "3".repeat(64),
      mime_type: null,
      width: null,
      height: null,
      byte_length: null,
      content_sha256: null,
      data_base64: null,
      missing_reason: "provider_blocked",
    }],
    secret: "visual-secret-that-must-not-export",
  };
  const queued = job("sheet-job");
  const completed = {
    ...job("sheet-job", {questions: [], ready: true}),
    storyboard_package: storyboardPackage,
    visual_storyboard: visualStoryboard,
  };
  async function fetchObject(url) {
    const payload = url === "/v1/jobs" ? queued : completed;
    return {ok: true, status: url === "/v1/jobs" ? 202 : 200, async json() { return payload; }};
  }
  const blobs = [];
  const revoked = [];
  class FakeBlob {
    constructor(parts, options) { this.parts = parts; this.type = options.type; blobs.push(this); }
  }
  const urlObject = {
    createObjectURL(blob) { return `blob:storyboard-${blobs.indexOf(blob) + 1}`; },
    revokeObjectURL(url) { revoked.push(url); },
  };
  let printedSheet = "";
  let printCount = 0;
  const printWindow = {
    opener: "source-window",
    document: {
      open() {},
      write(value) { printedSheet = value; },
      close() {},
    },
    focus() {},
    print() { printCount += 1; },
  };
  const spoken = [];
  let speechCancels = 0;
  const voiceListeners = new Map();
  const naturalVoice = {name: "Microsoft Aria Online (Natural) - English (United States)", lang: "en-US"};
  let currentVoices = [
    {name: "Microsoft David Desktop", lang: "en-US", default: true},
    {name: "Microsoft Sonia Premium", lang: "en-GB"},
    {name: "Microsoft Mark Online", lang: "en-US"},
    {name: "Microsoft Katja Online (Natural)", lang: "de-DE"},
    naturalVoice,
  ];
  class FakeUtterance {
    constructor(textValue) { this.text = textValue; this.onend = null; this.onerror = null; }
  }
  const speechSynthesis = {
    getVoices() { return currentVoices; },
    addEventListener(type, callback) { voiceListeners.set(type, callback); },
    speak(utterance) { spoken.push(utterance); },
    cancel() { speechCancels += 1; },
  };

  const html = fs.readFileSync(path.join(__dirname, "..", "web", "all-things-agentic.html"), "utf8");
  const script = html.match(/<script>\s*([\s\S]*?)\s*<\/script>/)[1];
  vm.runInNewContext(script, {
    Blob: FakeBlob,
    URL: urlObject,
    clearTimeout() {},
    console,
    document: documentObject,
    fetch: fetchObject,
    setTimeout(callback) { Promise.resolve().then(callback); return 1; },
    window: {addEventListener() {}, open() { return printWindow; }, speechSynthesis, SpeechSynthesisUtterance: FakeUtterance},
  }, {filename: "all-things-agentic.html"});

  elements.get("message").value = "Build the safe storyboard sheet.";
  await elements.get("submit").listeners.get("click")();
  await settle();
  assert.equal(elements.get("animaticPlay").disabled, false);
  assert.equal(elements.get("pitchPlay").disabled, false);
  assert.equal(elements.get("downloadPitchScript").disabled, false);
  assert.match(elements.get("pitchVoiceStatus").textContent, /Natural pitch voice ready: Microsoft Aria Online \(Natural\)/);
  assert.equal(elements.get("animaticImage").src, "data:image/jpeg;base64,/9j/2Q==");
  assert.match(elements.get("animaticShot").textContent, /SC01-SH01/);
  assert.equal(elements.get("animaticTime").textContent, "00:00 / 00:12");
  elements.get("pitchPlay").click();
  assert.equal(spoken.length, 1);
  assert.match(spoken[0].text, /^Card 1, primary coverage\./);
  assert.equal(spoken[0].voice, naturalVoice, "only the highest-ranked clearly labeled English natural voice is assigned");
  assert.equal(spoken[0].lang, "en-US");
  spoken[0].onend();
  await settle();
  assert.equal(spoken.length, 2);
  assert.match(spoken[1].text, /^Card 2, reaction coverage\./);
  elements.get("pitchStop").click();
  spoken[1].onend();
  await settle();
  assert.equal(spoken.length, 2, "stale speech callbacks cannot advance after Stop");
  assert.ok(speechCancels >= 1);
  currentVoices = [
    {name: "Microsoft David Desktop", lang: "en-US", default: true},
    {name: "Microsoft Sonia Premium", lang: "en-GB"},
    {name: "Microsoft Mark Online", lang: "en-US"},
  ];
  voiceListeners.get("voiceschanged")();
  assert.equal(elements.get("pitchPlay").disabled, true, "robotic/default-only voice inventory cannot enable narration");
  assert.equal(elements.get("pitchPlay").textContent, "Natural voice unavailable");
  assert.equal(elements.get("downloadPitchScript").disabled, false, "the narration script remains available without a qualified voice");
  assert.match(elements.get("pitchVoiceStatus").textContent, /Natural pitch voice unavailable.*download the pitch narration script instead/);
  elements.get("downloadDetailedSheet").click();
  elements.get("downloadVisualStoryboard").click();
  elements.get("downloadLocationPlan").click();
  elements.get("downloadLocationCsv").click();
  elements.get("downloadLocationJson").click();
  elements.get("downloadCharacterHtml").click();
  elements.get("downloadCharacterText").click();
  elements.get("downloadCharacterJson").click();
  elements.get("downloadCharacterCsv").click();
  elements.get("printVisualStoryboard").click();
  elements.get("downloadShotList").click();
  elements.get("downloadEdl").click();
  elements.get("downloadPitchScript").click();
  await settle();

  assert.equal(blobs.length, 12);
  assert.equal(blobs[0].type, "text/html;charset=utf-8");
  assert.equal(blobs[1].type, "text/html;charset=utf-8");
  assert.equal(blobs[2].type, "text/html;charset=utf-8");
  assert.equal(blobs[3].type, "text/csv;charset=utf-8");
  assert.equal(blobs[4].type, "application/json;charset=utf-8");
  assert.equal(blobs[5].type, "text/html;charset=utf-8");
  assert.equal(blobs[6].type, "text/plain;charset=utf-8");
  assert.equal(blobs[7].type, "application/json;charset=utf-8");
  assert.equal(blobs[8].type, "text/csv;charset=utf-8");
  assert.equal(blobs[9].type, "text/csv;charset=utf-8");
  assert.equal(blobs[10].type, "text/csv;charset=utf-8");
  assert.equal(blobs[11].type, "text/plain;charset=utf-8");
  const detailed = blobs[0].parts.join("");
  const visual = blobs[1].parts.join("");
  const locationHtml = blobs[2].parts.join("");
  const locationCsv = blobs[3].parts.join("");
  const locationJson = blobs[4].parts.join("");
  const characterHtml = blobs[5].parts.join("");
  const characterText = blobs[6].parts.join("");
  const characterJson = blobs[7].parts.join("");
  const characterCsv = blobs[8].parts.join("");
  const shotList = blobs[9].parts.join("");
  const edl = blobs[10].parts.join("");
  const pitchScript = blobs[11].parts.join("");
  assert.match(detailed, /^<!doctype html>/);
  assert.match(detailed, /Content-Security-Policy/);
  assert.match(detailed, /default-src 'none'/);
  assert.match(detailed, /PLAN ONLY · NO RENDERED MEDIA/);
  assert.match(detailed, /Target Final Deliverables — not generated by this plan-only build\./);
  assert.match(detailed, /A &lt;Director&gt; &amp; &quot;Friend&quot;/);
  assert.match(detailed, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(detailed, /Medium &lt;two-shot&gt;/);
  assert.match(detailed, /Protect dialogue &lt;clearly&gt;/);
  assert.match(detailed, /Match &lt;wardrobe&gt; &amp; props\./);
  assert.match(detailed, /Use verified &lt;source&gt; only\./);
  assert.match(detailed, /Flag &lt;missing&gt; coverage\./);
  assert.match(detailed, /No &lt;img src=x onerror=alert\(3\)&gt; gaps\./);
  assert.doesNotMatch(detailed, /<script\b|<img\b/i);
  assert.match(visual, /^<!doctype html>/);
  assert.match(visual, /img-src data:/);
  assert.match(visual, /PLAN ONLY · GENERATED PLANNING ILLUSTRATIONS/);
  assert.match(visual, /Human visual review required/);
  assert.match(visual, /<img src="data:image\/jpeg;base64,\/9j\/2Q=="/);
  assert.match(visual, /Alex &lt;Lead&gt; repairs the engine; no &lt;script&gt; is trusted\./);
  assert.match(visual, /Repair &lt;engine&gt; together\./);
  assert.match(visual, /Visual pending/);
  assert.match(visual, /provider blocked/);
  assert.match(visual, /Page 1 of 1/);
  assert.doesNotMatch(visual, /<script\b/i);
  assert.match(shotList, /EDITORIAL INSTRUCTION ONLY - NO FOOTAGE SELECTED CHANGED OR RENDERED/);
  assert.match(shotList, /SC01-SH01/);
  assert.match(edl, /ROUGH-CUT INSTRUCTION ONLY - NOT AN APPLIED EDIT OR RENDERED VIDEO/);
  assert.match(edl, /no local source selected; planned editorial event only/);
  assert.match(edl, /source_duration_deficit_seconds/);
  assert.match(edl, /NO SOURCE ASSIGNED/);
  assert.match(locationHtml, /derived location production plan/i);
  assert.match(locationHtml, /potential changes avoided/i);
  assert.match(locationHtml, /Props and wardrobe are not itemized/i);
  assert.match(locationHtml, /Repair &lt;shop&gt;/);
  assert.doesNotMatch(locationHtml, /<script\b/i);
  assert.equal((locationCsv.match(/\r\n/g) || []).length, 2, "location CSV has header plus two exact-location rows");
  assert.match(locationCsv, /not_itemized_in_current_brief_schema/);
  assert.match(locationCsv, /"'=Dock"/, "spreadsheet formula-leading cells are neutralized");
  const parsedLocation = JSON.parse(locationJson);
  assert.equal(parsedLocation.summary.uniqueLocationCount, 2);
  assert.equal(parsedLocation.summary.narrativeLocationChangeCount, 2);
  assert.equal(parsedLocation.summary.suggestedLocationChangeCount, 1);
  assert.equal(parsedLocation.summary.potentialLocationChangesAvoided, 1);
  assert.deepEqual(parsedLocation.locations[0].sceneNumbers, [1, 3]);
  assert.deepEqual(parsedLocation.locations[0].characters, ["Alex <Lead>", "Morgan"]);
  assert.match(characterHtml, /^<!doctype html>/);
  assert.match(characterHtml, /Content-Security-Policy/);
  assert.match(characterHtml, /Production brief synopsis/);
  assert.match(characterHtml, /A &lt;Director&gt; &amp; &quot;Friend&quot;/);
  assert.match(characterHtml, /A summary with &lt;script&gt;alert\(1\)&lt;\/script&gt;\./);
  assert.match(characterHtml, /Role:<\/b> Not itemized/);
  assert.match(characterHtml, /does not identify this character as a speaker/);
  assert.doesNotMatch(characterHtml, /<script\b|<img\b/i);
  assert.match(characterText, /CHARACTER LIST \+ SYNOPSIS/);
  assert.match(characterText, /Appears in scenes: 1, 2, 3/);
  assert.match(characterText, /does not identify who speaks/);
  assert.match(characterText, /no role inferred/i);
  const parsedCharacter = JSON.parse(characterJson);
  assert.equal(parsedCharacter.schema, "video-studio.character-synopsis-dossier/v1");
  assert.equal(parsedCharacter.summary.namedCharacterCount, 6);
  assert.equal(parsedCharacter.summary.roleCount, 0);
  assert.equal(parsedCharacter.derivation.modelCalls, 0);
  assert.equal(parsedCharacter.derivation.packageMutated, false);
  assert.equal(parsedCharacter.characters[0].displayName, "Alex <Lead>");
  assert.deepEqual(parsedCharacter.characters[0].appearanceSceneNumbers, [1, 2, 3]);
  assert.deepEqual(parsedCharacter.characters[0].dialogueContextSceneNumbers, [1, 3]);
  assert.equal(parsedCharacter.characters[0].role, null);
  assert.match(characterCsv, /scene_level_only_not_attributed_to_character/);
  assert.match(characterCsv, /"'=HYPERLINK\(""bad""\)"/);
  assert.match(characterCsv, /"'\+COMMAND"/);
  assert.match(characterCsv, /"'-DANGER"/);
  assert.match(characterCsv, /"'@SUM"/);
  assert.doesNotMatch(characterCsv, /,"=HYPERLINK|,"\+COMMAND|,"-DANGER|,"@SUM/);
  assert.match(pitchScript, /INVESTOR PITCH NARRATION \/ CUE SCRIPT/);
  assert.match(pitchScript, /Dialogue and audio direction: Protect dialogue <clearly>\./);
  assert.match(pitchScript, /CARD 2 — SC01-SH02/);
  assert.doesNotMatch(detailed + visual + locationHtml + locationCsv + locationJson + characterHtml + characterText + characterJson + characterCsv + shotList + edl + pitchScript, /private-judge-code|package-secret|top-level-secret|nested-secret|visual-secret/);
  assert.doesNotMatch(detailed + visual, /https?:\/\//i);
  assert.equal(appended.length, 12);
  assert.equal(appended[0].download, "a-director-friend-detailed-production-sheet.html");
  assert.equal(appended[1].download, "a-director-friend-visual-storyboard.html");
  assert.equal(appended[2].download, "a-director-friend-location-production-plan.html");
  assert.equal(appended[3].download, "a-director-friend-location-schedule.csv");
  assert.equal(appended[4].download, "a-director-friend-location-production-plan.json");
  assert.equal(appended[5].download, "a-director-friend-character-synopsis.html");
  assert.equal(appended[6].download, "a-director-friend-character-synopsis.txt");
  assert.equal(appended[7].download, "a-director-friend-character-synopsis.json");
  assert.equal(appended[8].download, "a-director-friend-character-appearances.csv");
  assert.equal(appended[9].download, "a-director-friend-shot-list.csv");
  assert.equal(appended[10].download, "a-director-friend-source-aware-rough-cut-edl.csv");
  assert.equal(appended[11].download, "a-director-friend-investor-pitch-narration.txt");
  assert.equal(appended[0].clickCount, 1);
  assert.equal(appended[1].clickCount, 1);
  assert.equal(appended[2].clickCount, 1);
  assert.equal(appended[3].clickCount, 1);
  assert.equal(appended[4].clickCount, 1);
  assert.equal(appended[5].clickCount, 1);
  assert.equal(appended[6].clickCount, 1);
  assert.equal(appended[7].clickCount, 1);
  assert.equal(appended[8].clickCount, 1);
  assert.equal(appended[9].clickCount, 1);
  assert.equal(appended[10].clickCount, 1);
  assert.equal(appended[11].clickCount, 1);
  assert.equal(appended[0].removed, true);
  assert.equal(appended[1].removed, true);
  assert.equal(appended[2].removed, true);
  assert.equal(appended[3].removed, true);
  assert.equal(appended[4].removed, true);
  assert.equal(appended[5].removed, true);
  assert.equal(appended[6].removed, true);
  assert.equal(appended[7].removed, true);
  assert.equal(appended[8].removed, true);
  assert.equal(appended[9].removed, true);
  assert.equal(appended[10].removed, true);
  assert.equal(appended[11].removed, true);
  assert.deepEqual(revoked, ["blob:storyboard-1", "blob:storyboard-2", "blob:storyboard-3", "blob:storyboard-4", "blob:storyboard-5", "blob:storyboard-6", "blob:storyboard-7", "blob:storyboard-8", "blob:storyboard-9", "blob:storyboard-10", "blob:storyboard-11", "blob:storyboard-12"]);
  assert.equal(printWindow.opener, null);
  assert.equal(printCount, 1);
  assert.equal(printedSheet, visual);
});


test("owner v2 blocks blank access, imports whole or bounded scripts locally, and wires desktop install", async () => {
  const ids = [
    "access", "message", "submit", "cancel", "retry", "error", "state", "stage",
    "progress", "progressBar", "bar", "eta", "job", "brief", "monitor",
    "conversationFeed", "conversationContext", "timelineTrack", "timelineRuler",
    "timelineStatus", "timelineEmpty", "timelineTimecode", "timelinePlayhead",
    "timelineSelection", "timelineFirst", "timelinePrevious", "timelineNext", "timelineLast",
    "downloadPackage", "downloadVisualStoryboard", "printVisualStoryboard", "downloadDetailedSheet",
    "downloadLocationPlan", "downloadLocationCsv", "downloadLocationJson", "downloadCharacterHtml", "downloadCharacterText", "downloadCharacterJson", "downloadCharacterCsv", "downloadShotList", "downloadEdl", "accessHelp", "sourceSummary", "attachmentButton",
    "attachmentMenu", "attachStory", "attachFootage", "scriptFile", "footageFiles",
    "scriptStatus", "footageStatus", "animatic", "animaticPlay", "animaticStop", "pitchPlay", "pitchStop", "pitchVoiceStatus", "downloadPitchScript",
    "animaticImage", "animaticPlaceholder", "animaticOverlay", "animaticShot",
    "animaticAction", "animaticBar", "animaticTime", "animaticTruth", "installApp",
  ];
  const elements = new Map(ids.map(id => [id, new FakeElement(id)]));
  class FakeVideo extends FakeElement {
    constructor() { super(); this.duration = 9.375; }
    set src(value) {
      this._src = value;
      Promise.resolve().then(() => this.onloadedmetadata?.());
    }
    get src() { return this._src; }
  }
  const documentObject = {
    getElementById(id) { return elements.get(id); },
    createElement(tagName) { return tagName === "video" ? new FakeVideo() : new FakeElement(); },
    querySelectorAll() { return []; },
  };
  const windowListeners = new Map();
  const windowObject = {
    addEventListener(type, callback) { windowListeners.set(type, callback); },
  };
  const serviceWorkerRegistrations = [];
  const fetchCalls = [];
  const revokedLocalUrls = [];
  const urlObject = {
    createObjectURL() { return "blob:local-footage-metadata"; },
    revokeObjectURL(value) { revokedLocalUrls.push(value); },
  };
  async function fetchObject(url, options = {}) {
    fetchCalls.push({url, options});
    return {ok: true, status: 202, async json() { return job("owner-v2-job"); }};
  }

  const html = fs.readFileSync(path.join(__dirname, "..", "web", "all-things-agentic.html"), "utf8");
  assert.match(html, /aria-label="Production planning workflow"/);
  assert.match(html, /aria-label="Included Video Studio planning capabilities"/);
  assert.match(html, /class="capability-chip"/);
  assert.doesNotMatch(html, /aria-label="Jump to a Video Studio capability"/);
  assert.match(html, /Judge \/ owner access code — provided with submission/);
  assert.match(html, /id="submit" class="primary" type="button" disabled>Create production plan<\/button>/);
  const script = html.match(/<script>\s*([\s\S]*?)\s*<\/script>/)[1];
  const sandbox = {
    clearTimeout() {},
    console,
    document: documentObject,
    fetch: fetchObject,
    navigator: {serviceWorker: {async register(pathValue) { serviceWorkerRegistrations.push(pathValue); }}},
    setTimeout() { return 1; },
    URL: urlObject,
    window: windowObject,
  };
  vm.createContext(sandbox);
  vm.runInContext(script, sandbox, {filename: "all-things-agentic.html"});

  assert.deepEqual(serviceWorkerRegistrations, ["/sw.js"]);
  assert.equal(elements.get("submit").disabled, true);
  elements.get("message").value = "Plan a complete short scene.";
  elements.get("message").listeners.get("input")();
  assert.equal(elements.get("submit").disabled, true);
  await elements.get("submit").listeners.get("click")();
  assert.equal(fetchCalls.length, 0);
  assert.match(elements.get("error").textContent, /provided with the submission/);
  assert.match(elements.get("error").textContent, /before creating a production plan/);
  assert.match(elements.get("accessHelp").textContent, /Provided with the submission/);

  await elements.get("installApp").listeners.get("click")();
  assert.match(elements.get("error").textContent, /Microsoft Edge/);
  assert.match(elements.get("error").textContent, /app-window launcher/);
  let prevented = 0;
  let prompted = 0;
  const installEvent = {
    preventDefault() { prevented += 1; },
    async prompt() { prompted += 1; },
    userChoice: Promise.resolve({outcome: "accepted"}),
  };
  windowListeners.get("beforeinstallprompt")(installEvent);
  assert.equal(prevented, 1);
  await elements.get("installApp").listeners.get("click")();
  assert.equal(prompted, 1);
  windowListeners.get("appinstalled")();
  assert.equal(elements.get("installApp").disabled, true);
  assert.equal(elements.get("installApp").textContent, "Desktop app installed");

  const fullText = "INT. REPAIR SHOP - NIGHT\n\nALEX\nWe should leave before dawn.";
  await elements.get("scriptFile").listeners.get("change")({
    target: {files: [{name: "complete.fountain", size: fullText.length, async text() { return fullText; }}]},
  });
  assert.match(elements.get("scriptStatus").textContent, /full text/);
  assert.match(elements.get("sourceSummary").innerHTML, /FULL TEXT/);
  assert.match(elements.get("sourceSummary").innerHTML, new RegExp(`${fullText.length} of ${fullText.length}`));

  const longText = "😀".repeat(100000);
  await elements.get("scriptFile").listeners.get("change")({
    target: {files: [{name: "feature.screenplay", size: longText.length, async text() { return longText; }}]},
  });
  assert.match(elements.get("scriptStatus").textContent, /146,736 of 200,000/);
  assert.match(elements.get("scriptStatus").textContent, /beginning\/middle\/end excerpts/);
  assert.match(elements.get("sourceSummary").innerHTML, /BEGINNING \/ MIDDLE \/ END EXCERPTS/);

  await elements.get("footageFiles").listeners.get("change")({
    target: {files: [{
      name: "repair-shop-wide.mov",
      size: 123456,
      type: "video/quicktime",
      bytesThatMustStayLocal: "RAW-VIDEO-SECRET-BYTES",
    }]},
  });
  await settle();
  assert.match(elements.get("footageStatus").textContent, /1 file ready · metadata only · no upload/);
  assert.deepEqual(revokedLocalUrls, ["blob:local-footage-metadata"]);

  elements.get("access").value = "private-code-never-in-body";
  elements.get("access").listeners.get("input")();
  assert.equal(elements.get("submit").disabled, false);
  assert.match(elements.get("accessHelp").textContent, /window only/);
  assert.match(elements.get("accessHelp").textContent, /never included in exports/);
  await elements.get("submit").listeners.get("click")();
  const posts = fetchCalls.filter(call => call.url === "/v1/jobs");
  assert.equal(posts.length, 1);
  const posted = JSON.parse(posts[0].options.body).message;
  assert.match(posted, /CLIENT-IMPORTED SCRIPT SOURCE/);
  assert.match(posted, /coverage: beginning_middle_end_excerpt/);
  assert.match(posted, /Included source characters: 146736 of 200000/);
  assert.match(posted, /\[BEGINNING EXCERPT/);
  assert.match(posted, /\[MIDDLE EXCERPT/);
  assert.match(posted, /\[ENDING EXCERPT/);
  assert.match(posted, /repair-shop-wide\.mov \| video\/quicktime \| 123456 bytes \| browser-readable duration 9\.375s/);
  assert.match(posted, /source assignments as recommendations based only on filenames\/metadata/);
  assert.doesNotMatch(posted, /RAW-VIDEO-SECRET-BYTES/);
  assert.doesNotMatch(posted, /private-code-never-in-body/);
  assert.equal(Buffer.from(posted, "utf8").toString("utf8"), posted);
  assert.equal(posts[0].options.headers["X-Video-Studio-Access"], "private-code-never-in-body");
  assert.equal(Object.keys(JSON.parse(posts[0].options.body)).join(","), "message");
});


test("character dossier fails closed on missing evidence and never mutates its source package", () => {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  };
  const documentObject = {
    body: {append() {}},
    getElementById(id) { return element(id); },
    createElement() { return new FakeElement(); },
    querySelectorAll() { return []; },
  };
  const html = fs.readFileSync(path.join(__dirname, "..", "web", "all-things-agentic.html"), "utf8");
  const sourceScript = html.match(/<script>\s*([\s\S]*?)\s*<\/script>/)[1];
  const instrumentedScript = sourceScript.replace(/\}\)\(\);\s*$/, `
    globalThis.__characterDossierTestApi = {
      deriveCharacterSynopsisDossier,
      characterSynopsisHtml,
      characterSynopsisText,
      characterAppearancesCsv,
    };
  })();`);
  const context = {
    clearTimeout() {},
    console,
    document: documentObject,
    fetch() { throw new Error("network should not be used"); },
    setTimeout() { return 1; },
    window: {addEventListener() {}},
  };
  vm.runInNewContext(instrumentedScript, context, {filename: "all-things-agentic-character-test.html"});
  const api = context.__characterDossierTestApi;
  assert.ok(api);
  assert.equal(api.deriveCharacterSynopsisDossier({}), null);

  const sourcePackage = {
    package_id: "storyboard-missing-evidence",
    manifest_sha256: "f".repeat(64),
    production_brief: {
      title: "",
      summary: "",
      scenes: [null, {number: 0}, {number: 1, setting: "", purpose: "", characters: [], dialogue_required: false}],
    },
  };
  const before = JSON.stringify(sourcePackage);
  const dossier = api.deriveCharacterSynopsisDossier(sourcePackage);
  assert.equal(JSON.stringify(sourcePackage), before);
  assert.equal(dossier.production.title, "Untitled production");
  assert.equal(dossier.production.synopsis, "");
  assert.equal(dossier.summary.validSceneCount, 1);
  assert.equal(dossier.summary.namedCharacterCount, 0);
  assert.equal(dossier.summary.roleCount, 0);
  assert.ok(dossier.reviewHolds.some(value => value.includes("did not supply a synopsis")));
  assert.ok(dossier.reviewHolds.some(value => value.includes("No named characters")));
  assert.ok(dossier.reviewHolds.some(value => value.includes("Scene entry 1 was omitted")));
  assert.ok(dossier.reviewHolds.some(value => value.includes("were not inferred")));
  assert.match(api.characterSynopsisText(dossier), /No synopsis was supplied/);
  assert.match(api.characterSynopsisHtml(dossier), /No named characters were itemized/);
  assert.equal(api.characterAppearancesCsv(dossier).split("\r\n").length, 1);
});
