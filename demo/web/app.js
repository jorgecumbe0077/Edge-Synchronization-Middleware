let running = false;

const EVENT_MAIN = "MAZ-MVP01-REMOTE-WH-OUT-001";
const EVENT_CRASH = "MAZ-MVP01B-REMOTE-WH-CRASH-001";

const REAL = {
  main: {
    event: EVENT_MAIN,
    picking: "#91",
    move: "#88",
    delta: "-3",
    result: "DONE",
    note: "Remote Warehouse continuity validated."
  },

  crash: {
    event: EVENT_CRASH,
    picking: "#92",
    move: "#89",
    result: "DONE",
    note: "Crash + ACK loss recovered without a second business effect under the tested scenario."
  },

  race: {
    event: "MAZ-R8.11.2-OUTBOX-CONCURRENT-001",
    result: "DUPLICATED",
    pickings: "2 Pickings",
    moves: "2 Moves",
    note: "Real Outbox V1 concurrency failure: two independent workers consumed the same PENDING item."
  },

  v2: {
    event: "MAZ-R8.11.17-STALE-FENCING-001",
    generationA: "1",
    generationB: "2",
    staleHeartbeat: "0",
    staleFinalize: "0",
    currentFinalize: "1",
    note: "Experimental ownership/fencing model. Stale Worker A was blocked after Worker B reclaimed generation 2."
  }
};

const state = {
  stock: 10,
  event: "—",
  edge: "READY",
  edgeDetail: "Waiting for scenario",
  outbox: "EMPTY",
  outboxDetail: "No pending work",
  timeline: [],
  evidence: []
};

function $(id) {
  return document.getElementById(id);
}

function beginScenario() {
  if (running) return false;
  running = true;
  return true;
}

function endScenario() {
  running = false;
}

function addStep(text) {
  state.timeline.push(text);
}

function render() {
  $("edgeState").textContent = state.edge;
  $("edgeDetail").textContent = state.edgeDetail;
  $("stock").textContent = state.stock;
  $("event").textContent = state.event;
  $("eventStatus").textContent =
    state.event === "—" ? "No event loaded" : "Event identity loaded";
  $("outbox").textContent = state.outbox;
  $("outboxDetail").textContent = state.outboxDetail;

  $("timeline").innerHTML = state.timeline
    .map((x, i) => `<div class="timeline-row"><span>${String(i + 1).padStart(2, "0")}</span><div>${x}</div></div>`)
    .join("");

  $("realEvidence").innerHTML = state.evidence
    .map(x => `<div class="evidence-row">${x}</div>`)
    .join("");
}

function resetState() {
  state.stock = 10;
  state.event = "—";
  state.edge = "READY";
  state.edgeDetail = "Waiting for scenario";
  state.outbox = "EMPTY";
  state.outboxDetail = "No pending work";
  state.timeline = [];
  state.evidence = [];
}

function runOfflineDemo() {
  if (!beginScenario()) return;

  resetState();

  state.event = EVENT_MAIN;
  state.stock = 7;
  state.edge = "ACCEPTED";
  state.edgeDetail = "Local operation persisted at Edge";
  state.outbox = "PENDING";
  state.outboxDetail = "Waiting for synchronization";

  addStep("Remote Warehouse: OUT -3");
  addStep("Stock: 10 → 7");
  addStep("Event ACCEPTED locally");
  addStep("Event persisted");
  addStep("Outbox: PENDING");

  state.evidence.push(
    `<strong>REAL:</strong> ${REAL.main.event} · Picking ${REAL.main.picking} · Move ${REAL.main.move} · delta ${REAL.main.delta} · ${REAL.main.result}`
  );

  render();
  endScenario();
}

function networkFailure() {
  if (!beginScenario()) return;

  state.event = EVENT_MAIN;
  state.stock = 7;
  state.edge = "NETWORK_ERROR";
  state.edgeDetail = "Remote synchronization unavailable";
  state.outbox = "PENDING";
  state.outboxDetail = "Event remains persisted for retry";

  addStep("Network failure");
  addStep("Remote synchronization failed");
  addStep("Event remains persisted");
  addStep("Outbox remains PENDING");

  render();
  endScenario();
}

function networkRecovery() {
  if (!beginScenario()) return;

  state.event = EVENT_MAIN;
  state.stock = 7;
  state.edge = "SYNCED";
  state.edgeDetail = "Remote effect recovered";
  state.outbox = "SYNCED";
  state.outboxDetail = "Remote effect confirmed";

  addStep("Connectivity restored");
  addStep("Event sent to Odoo 19");
  addStep(`Picking ${REAL.main.picking} → Move ${REAL.main.move}`);
  addStep("Move DONE");
  addStep("Remote delta: -3");
  addStep("Replay converges under tested conditions");

  state.evidence.push(
    `<strong>CAPTURED REAL EVIDENCE:</strong> Odoo 19 · Picking ${REAL.main.picking} · Move ${REAL.main.move} · DONE · delta ${REAL.main.delta}`
  );

  render();
  endScenario();
}

function crashRecovery() {
  if (!beginScenario()) return;

  state.event = EVENT_CRASH;
  state.stock = 7;
  state.edge = "RECOVERED";
  state.edgeDetail = "Remote effect found after Edge restart";
  state.outbox = "SYNCED";
  state.outboxDetail = "Existing remote effect reused";

  addStep("Edge accepts event");
  addStep("Odoo completes remote effect");
  addStep("Edge crashes before ACK");
  addStep("After restart: Outbox still PENDING");
  addStep(`Odoo: Picking ${REAL.crash.picking} · Move ${REAL.crash.move} · DONE`);
  addStep("Existing remote effect recovered");
  addStep("Outbox marked SYNCED");

  state.evidence.push(
    `<strong>CAPTURED REAL EVIDENCE:</strong> ${REAL.crash.event} · Picking ${REAL.crash.picking} · Move ${REAL.crash.move} · DONE`
  );

  render();
  endScenario();
}

function partialRecovery() {
  if (!beginScenario()) return;

  state.event = EVENT_CRASH;
  state.stock = 7;
  state.edge = "PARTIAL RECOVERY";
  state.edgeDetail = "Existing remote state recovered";

  addStep(`Picking ${REAL.crash.picking} exists`);
  addStep("Move not yet created");
  addStep(`Recover Picking ${REAL.crash.picking}`);
  addStep(`Create Move ${REAL.crash.move}`);
  addStep(`Picking ${REAL.crash.picking} → Move ${REAL.crash.move} → DONE`);

  state.evidence.push(
    `<strong>CAPTURED REAL EVIDENCE:</strong> Picking ${REAL.crash.picking} existed; Move ${REAL.crash.move} was missing; recovery completed the missing remote state.`
  );

  render();
  endScenario();
}

function raceScenario() {
  if (!beginScenario()) return;

  resetState();

  state.event = REAL.race.event;
  state.edge = "V1 FAILURE";
  state.edgeDetail = "Concurrent ownership was not enforced";
  state.outbox = "PENDING";
  state.outboxDetail = "Same work consumed by two independent processes";

  addStep("Worker A reads PENDING");
  addStep("Worker B reads same PENDING");
  addStep("Both enter remote execution path");
  addStep("Result: 2 Pickings · 2 Moves");
  addStep("Both remote effects reach DONE");

  state.evidence.push(
    `<strong>CAPTURED REAL FAILURE:</strong> ${REAL.race.event} · ${REAL.race.pickings} · ${REAL.race.moves} · ${REAL.race.result}`
  );

  render();
  endScenario();
}

function v2Scenario() {
  if (!beginScenario()) return;

  resetState();

  state.event = REAL.v2.event;
  state.edge = "V2 EXPERIMENTAL";
  state.edgeDetail = "Ownership + lease generation + fencing";
  state.outbox = "GENERATION 2";
  state.outboxDetail = "Worker B is current owner";

  addStep("Worker A claims generation 1");
  addStep("Worker B reclaims expired lease");
  addStep("Generation advances: 1 → 2");
  addStep("Worker A stale heartbeat → rowcount 0");
  addStep("Worker A stale finalize → rowcount 0");
  addStep("Worker B current finalize → rowcount 1");

  state.evidence.push(
    `<strong>CAPTURED REAL EVIDENCE:</strong> ${REAL.v2.event} · stale heartbeat 0 · stale finalize 0 · current finalize 1`
  );

  state.evidence.push(
    `<strong>STATUS:</strong> V2 ownership/fencing is experimental and is not presented as production-ready.`
  );

  render();
  endScenario();
}

function resetDemo() {
  if (!beginScenario()) return;
  resetState();
  render();
  endScenario();
}

render();
