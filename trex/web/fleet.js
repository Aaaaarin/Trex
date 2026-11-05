/* Drivers, dispatcher and the retailer desk. The main page calls TrexFleet.view() as one of its views. */
(function () {
  let F = null;
  let note = "";

  const esc = (s) => String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const rupees = (n) => `Rs ${Number(n || 0).toLocaleString("en-IN")}`;

  document.head.insertAdjacentHTML("beforeend", `<style>
    .fl-job { padding: 16px 18px; margin-bottom: 12px; }
    .fl-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .fl-pay { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 14px; align-items: center; margin-top: 12px; }
    .fl-pay strong { font-size: 22px; font-weight: 500; font-variant-numeric: tabular-nums; }
    .fl-scale { display: flex; justify-content: space-between; margin-top: 3px; }
    .fl-replies { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 12px; }
    .fl-gate { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--rule); }
    .fl-gate .grow { flex: 1; min-width: 12px; }
    .fl-picks { margin-top: 10px; }
    .fl-picks summary { cursor: pointer; color: var(--muted); font-size: 13px; }
    .fl-picks table { margin-top: 8px; }
    .fl-desk { display: grid; grid-template-columns: 110px minmax(0, 1fr) auto; gap: 8px; margin-bottom: 12px; }
    .fl-job .chip { white-space: normal; }
    .fl-answer { padding: 10px 0; border-top: 1px solid var(--rule); }
    .fl-answer .ev { margin-left: 4px; }
    @media (max-width: 640px) { .fl-desk { grid-template-columns: 1fr; } .fl-pay { grid-template-columns: 1fr; } }
  </style>`);

  async function poll() {
    try {
      const r = await fetch("/api/fleet", { cache: "no-store" });
      F = r.ok ? await r.json() : { jobs: [], drivers: [], desk: { answers: [] } };
    } catch (e) { /* the main page already shows that it is reconnecting */ }
    setTimeout(poll, 1000);
  }

  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.error) throw new Error(j.error || j.detail || `Server replied ${r.status}`);
    return j;
  }

  function jobChip(j) {
    if (j.status === "open") return `<span class="chip or">Offering, round ${esc(j.round)}</span>`;
    if (j.status === "no_driver") return `<span class="chip or">No driver free</span>`;
    if (j.how === "default") return `<span class="chip am">${esc(j.driver)}, assigned by default</span>`;
    return `<span class="chip gr">${esc(j.driver)} accepted</span>`;
  }

  function replyChip(r) {
    const label = { yes: "yes", no: "no", silent: "no reply", unclear: "unclear" }[r.answer] || r.answer;
    const tone = { yes: "gr", no: "mu", silent: "mu", unclear: "am" }[r.answer] || "mu";
    return `<span class="chip ${tone}" title="Round ${esc(r.round)} at ${esc(rupees(r.pay))}${r.text ? `: ${esc(r.text)}` : ""}">${esc(r.driver)} ${esc(label)}${r.simulated ? "" : " · live"}</span>`;
  }

  function gateChip(name, g) {
    if (!g) return `<span class="chip mu">${name} not logged</span>`;
    const late = name === "In" && g.late_minutes > 0;
    const when = late ? `${g.clock}, ${g.late_minutes} min late` : name === "In" ? `${g.clock}, on time` : g.clock;
    return `<span class="chip ${late ? "am" : "gr"}">${name} ${esc(when)}${g.photo ? ", photo" : ""}${g.simulated ? " · simulated" : ""}</span>`;
  }

  function jobCard(j) {
    const span = Math.max(j.ceiling - j.base_pay, 1);
    const share = Math.min(100, Math.max(4, ((j.pay - j.base_pay) / span) * 100));
    const assigned = j.status === "assigned";
    const gate = assigned ? `<div class="fl-gate">
        <span class="small muted">Report ${esc(j.report_time)}</span>
        ${gateChip("In", j.gate.in)}${gateChip("Out", j.gate.out)}
        <span class="grow"></span>
        ${j.gate.in ? "" : `<button class="btn" data-fleet="gate" data-truck="${esc(j.truck)}" data-event="in">Log in</button><button class="btn" data-fleet="noshow" data-truck="${esc(j.truck)}">No-show</button>`}
        ${j.gate.out ? "" : `<button class="btn" data-fleet="gate" data-truck="${esc(j.truck)}" data-event="out">Log out</button>`}
      </div>` : "";
    const picks = (j.pick_list || []).map((p) => `<tr><td>${esc(p.product_name)}</td><td class="mono">${esc(p.batch)}</td><td class="num">${esc(p.pieces)}</td></tr>`).join("");
    return `<section class="card fl-job" data-key="job-${esc(j.truck)}-${esc(j.version)}">
      <div class="fl-head"><h2><a class="link" href="#/truck/${encodeURIComponent(j.truck)}">${esc(j.truck)}</a></h2>
        <span class="small muted">v${esc(j.version)} · ${esc(j.stops)} stops · ${esc(j.kg)} kg</span>${jobChip(j)}</div>
      <div class="fl-pay"><strong>${esc(rupees(j.pay))}</strong>
        <div><div class="bar"><i class="${j.how === "default" ? "fill-or" : "ink"}" style="width:${share}%"></i></div>
        <div class="fl-scale small muted"><span>${esc(rupees(j.base_pay))} start</span><span>${esc(rupees(j.ceiling))} limit</span></div></div></div>
      <div class="fl-replies">${(j.replies || []).map(replyChip).join("")}</div>
      ${gate}
      ${picks ? `<details class="fl-picks" data-key="picks-${esc(j.truck)}"><summary>What the dispatcher hands over</summary><table class="otable"><thead><tr><th>Product</th><th>Batch</th><th>Pieces</th></tr></thead><tbody>${picks}</tbody></table></details>` : ""}
    </section>`;
  }

  function answerRow(a, i) {
    const evs = (a.evidence || []).map((id) => `<button class="ev" data-ev="${esc(id)}" title="Open evidence">${esc(String(id).slice(String(id).lastIndexOf(":") + 1))}</button>`).join("");
    return `<div class="fl-answer" data-key="ans-${i}">
      <div class="small"><span class="muted">${esc(a.retailer)}${a.simulated === false ? " · live" : " · simulated"}:</span> ${esc(a.text)}</div>
      <div class="small" style="margin-top:3px">${esc(a.answer || "")} ${evs}</div></div>`;
  }

  function view() {
    if (!F) return `<div data-key="v-fleet"><h1>Drivers</h1><p class="quiet">Loading</p></div>`;
    const jobs = F.jobs || [];
    const answers = (F.desk && F.desk.answers) || [];
    return `<div data-key="v-fleet">
      <div class="fl-head" style="justify-content:space-between"><div><h1>Drivers</h1><p class="sub">Drivers, pay and gate times are stand-ins. The invoices hold none of them.</p></div>
        <button class="btn" data-fleet="phone" title="One driver and the dispatcher go to the team phone">${F.dispatcher_live ? "Dispatcher on phone" : "Use my phone"}</button></div>
      <h2 class="sec-title">Tomorrow's jobs</h2>
      ${jobs.length ? jobs.map(jobCard).join("") : `<p class="quiet">Jobs open once a load sheet is issued.</p>`}
      <h2 class="sec-title">Retailer desk</h2>
      <form class="fl-desk" id="fl-desk-form" data-key="desk-form">
        <input id="fl-shop" placeholder="R-120" autocomplete="off" aria-label="Retailer">
        <input id="fl-msg" placeholder="Gaadi kahan hai?" autocomplete="off" aria-label="Message from the retailer">
        <button class="btn ink">Send</button>
      </form>
      ${note ? `<p class="small or" role="status">${esc(note)}</p>` : ""}
      <div data-key="desk-list">${answers.slice().reverse().slice(0, 12).map((a, i) => answerRow(a, answers.length - i)).join("")}</div>
    </div>`;
  }

  async function act(fn) {
    note = "";
    try { await fn(); } catch (e) { note = e.message; }
  }

  document.addEventListener("click", (event) => {
    const el = event.target.closest("[data-fleet]");
    if (!el) return;
    const truck = el.dataset.truck;
    if (el.dataset.fleet === "gate") act(() => post("/api/fleet/gate", { truck, event: el.dataset.event }));
    if (el.dataset.fleet === "noshow") act(() => post(`/api/fleet/no_show/${encodeURIComponent(truck)}`));
    if (el.dataset.fleet === "phone") act(() => post("/api/fleet/live", { driver: "D03", dispatcher: true }));
  });

  document.addEventListener("submit", (event) => {
    if (event.target.id !== "fl-desk-form") return;
    event.preventDefault();
    const shop = document.getElementById("fl-shop");
    const msg = document.getElementById("fl-msg");
    if (!shop.value.trim() || !msg.value.trim()) { note = "Enter a retailer and a message"; return; }
    const body = { retailer: shop.value.trim(), text: msg.value.trim() };
    msg.value = "";
    act(() => post("/api/desk", body));
  });

  window.TrexFleet = { view };
  poll();
})();
