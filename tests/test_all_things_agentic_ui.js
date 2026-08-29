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
        scene_number: 1,
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
    window: {open() { return printWindow; }},
  }, {filename: "all-things-agentic.html"});

  elements.get("message").value = "Build the safe storyboard sheet.";
  await elements.get("submit").listeners.get("click")();
  await settle();
  elements.get("downloadDetailedSheet").click();
  elements.get("downloadVisualStoryboard").click();
  elements.get("printVisualStoryboard").click();
  await settle();

  assert.equal(blobs.length, 2);
  assert.equal(blobs[0].type, "text/html;charset=utf-8");
  assert.equal(blobs[1].type, "text/html;charset=utf-8");
  const detailed = blobs[0].parts.join("");
  const visual = blobs[1].parts.join("");
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
  assert.doesNotMatch(detailed + visual, /private-judge-code|package-secret|top-level-secret|nested-secret|visual-secret/);
  assert.doesNotMatch(detailed + visual, /https?:\/\//i);
  assert.equal(appended.length, 2);
  assert.equal(appended[0].download, "a-director-friend-detailed-production-sheet.html");
  assert.equal(appended[1].download, "a-director-friend-visual-storyboard.html");
  assert.equal(appended[0].clickCount, 1);
  assert.equal(appended[1].clickCount, 1);
  assert.equal(appended[0].removed, true);
  assert.equal(appended[1].removed, true);
  assert.deepEqual(revoked, ["blob:storyboard-1", "blob:storyboard-2"]);
  assert.equal(printWindow.opener, null);
  assert.equal(printCount, 1);
  assert.equal(printedSheet, visual);
});
