/* What is going up and what is going down. The main page calls TrexAnalysis.view() as one of its views.
   The page polls every second, so the charts are built once per answer and the same string is handed back:
   patch() sees no change and leaves the DOM alone. Every figure and line of text comes from /api/analysis. */
(function () {
  let D = null;
  let html = "";
  let loading = false;
  let failed = false;
  let retryAt = 0;

  const esc = (s) => String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const group = (n) => Number(n || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  const inr = (n) => {
    const a = Math.abs(Number(n || 0));
    const sign = Number(n || 0) < 0 ? "-" : "";
    if (a < 100000) return `${sign}₹${group(a)}`;
    return `${sign}₹${(a / 100000).toFixed(2).replace(/\.?0+$/, "")} lakh`;
  };
  const signed = (n) => (Number(n || 0) > 0 ? "+" : "") + inr(n);
  const share = (v) => Math.max(0, Math.min(100, Number(v) || 0)).toFixed(2);

  document.head.insertAdjacentHTML("beforeend", `<style>
    .an { container-type: inline-size; --an-up: var(--gr-fg); --an-down: #C62828; }
    [data-theme="dark"] .an { --an-down: #E5534B; }
    .an-up { color: var(--an-up); }
    .an-down { color: var(--an-down); }
    .an-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; margin-top: 16px; }
    .an-card { padding: 15px 16px 14px; min-width: 0; }
    .an-top { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 4px 10px; }
    .an-top .chip { white-space: normal; font-size: 12px; }
    .an-take { margin-top: 6px; font-size: 15px; line-height: 1.35; overflow-wrap: anywhere; }
    .an-note { margin-top: 4px; font-size: 13px; color: var(--muted); overflow-wrap: anywhere; }
    .an-tiles { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 16px; }
    .an-tile { padding: 12px 14px; min-width: 0; }
    .an-tile span { display: block; font-size: 13px; color: var(--muted); }
    .an-tile b { display: block; font-size: 24px; font-weight: 500; letter-spacing: -0.01em; line-height: 1.25; margin-top: 2px; }
    .an-tile .an-move { font-size: 13px; margin-top: 2px; }
    .an-tile .an-move small { color: var(--muted); font-size: 12px; display: block; }
    .an-chart { position: relative; margin: 20px 8px 0 54px; height: 176px; touch-action: pan-y; }
    .an-chart svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; display: block; }
    .an-grid-line { stroke: var(--rule); stroke-width: 1; vector-effect: non-scaling-stroke; }
    .an-line { fill: none; stroke: var(--ink); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
    .an-area { fill: var(--ink); opacity: 0.07; }
    .an-avg { stroke: var(--accent); stroke-width: 2; vector-effect: non-scaling-stroke; }
    .an-dot { fill: var(--ink); stroke: var(--surface); stroke-width: 2; }
    .an-y { position: absolute; left: -54px; width: 48px; text-align: right; font-size: 12px; color: var(--muted); transform: translateY(-50%); white-space: nowrap; font-variant-numeric: tabular-nums; }
    .an-mark { position: absolute; font-size: 12px; white-space: nowrap; pointer-events: none; }
    .an-win { position: absolute; top: -18px; font-size: 12px; color: var(--muted); white-space: nowrap; }
    .an-x { position: relative; height: 18px; margin: 5px 8px 0 54px; font-size: 12px; color: var(--muted); }
    .an-x span { position: absolute; white-space: nowrap; }
    .an-cross { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--muted); pointer-events: none; }
    .an-tip { position: absolute; top: 2px; z-index: 3; padding: 5px 8px; border-radius: 8px; font-size: 12px; background: var(--surface); border: 1px solid var(--rule); box-shadow: 0 4px 14px rgba(25, 25, 23, 0.12); white-space: nowrap; pointer-events: none; }
    .an-tip b { font-weight: 600; display: block; }
    .an-hdot { position: absolute; width: 10px; height: 10px; margin: -5px 0 0 -5px; border-radius: 50%; background: var(--ink); box-shadow: 0 0 0 2px var(--surface); pointer-events: none; }
    .an-key { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-top: 8px; font-size: 12px; color: var(--muted); }
    .an-key i { display: inline-block; width: 14px; height: 2px; margin-right: 6px; vertical-align: 3px; }
    .an-rows { margin-top: 10px; }
    .an-row { display: grid; grid-template-columns: 62px minmax(0, 1fr) 62px; gap: 8px; align-items: center; min-height: 25px; font-size: 13px; }
    .an-row small { color: var(--muted); font-size: 11px; margin-left: 3px; }
    .an-row .an-bar { height: 12px; }
    .an-bar svg { display: block; width: 100%; height: 100%; overflow: visible; }
    .an-move { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
    .an-move.none { color: var(--muted); font-size: 12px; white-space: normal; line-height: 1.15; }
    .an-mix { grid-template-columns: 72px minmax(0, 1fr) 76px 52px; }
    .an-mix .an-amt { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
    .an-pair { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; margin-top: 10px; }
    .an-pair h3 { font-size: 13px; font-weight: 500; color: var(--muted); margin-bottom: 2px; }
    .an-prod { padding: 7px 0; border-bottom: 1px solid var(--rule); font-size: 13px; }
    .an-prod:last-child { border-bottom: 0; }
    .an-prod-name { overflow-wrap: anywhere; line-height: 1.3; }
    .an-prod-line { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: center; margin-top: 3px; }
    .an-prod-line .an-bar { height: 10px; }
    .an-more { margin-top: 10px; font-size: 13px; }
    .an-more summary { cursor: pointer; color: var(--muted); }
    .an-table { width: 100%; border-collapse: collapse; margin-top: 6px; font-size: 12px; font-variant-numeric: tabular-nums; }
    .an-table th { font-weight: 400; color: var(--muted); text-align: right; padding: 3px 0 3px 8px; border-bottom: 1px solid var(--rule); }
    .an-table td { text-align: right; padding: 4px 0 4px 8px; border-bottom: 1px solid var(--rule); }
    .an-table th:first-child, .an-table td:first-child { text-align: left; padding-left: 0; }
    .an-shops { margin-top: 10px; }
    .an-shops td:first-child { white-space: normal; }
    .an-truck { display: grid; grid-template-columns: 34px minmax(0, 1fr) 40px; gap: 8px; align-items: center; min-height: 30px; font-size: 13px; }
    .an-truck .an-bar { height: 14px; }
    .an-truck span:last-child { text-align: right; font-variant-numeric: tabular-nums; }
    .an-cols { position: relative; height: 96px; margin-top: 18px; }
    .an-cols svg { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
    .an-sub { padding-top: 14px; margin-top: 14px; border-top: 1px solid var(--rule); }
    .an-track { fill: var(--track); }
    .an-mid { stroke: var(--muted); stroke-width: 1; }
    .an-fill-up { fill: var(--an-up); }
    .an-fill-down { fill: var(--an-down); }
    .an-fill-ink { fill: var(--ink); }
    .an-fill-quiet { fill: var(--quiet-bar); }
    .an-fill-or { fill: var(--accent); }
    .an-band { fill: var(--or-bg); }
    @media (max-width: 640px) {
      .an-shops td:not(:first-child) { display: inline-block; margin-right: 12px; font-size: 12px; }
      .an-shops td[data-l="Days with a bill"] { display: none; }
    }
    @container (min-width: 620px) {
      .an-tiles { grid-template-columns: repeat(4, minmax(0, 1fr)); }
      .an-row { grid-template-columns: 76px minmax(0, 1fr) 62px; }
    }
    @container (min-width: 860px) {
      .an-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; }
      .an-wide { grid-column: 1 / -1; }
      .an-chart { height: 210px; }
      .an-mix { grid-template-columns: 84px minmax(0, 1fr) 84px 56px; }
    }
    @container (min-width: 480px) {
      .an-pair.an-duo { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }
    }
    @container (max-width: 340px) {
      .an-mix { grid-template-columns: 66px minmax(0, 1fr) 70px 46px; gap: 5px; }
    }
  </style>`);

  const REAL = `<span class="chip mu">Real invoices, masked</span>`;
  const standin = (text) => `<span class="chip am">${esc(text || "Stand-in")}</span>`;

  /* Direction is never colour alone: an arrow, a word in the alt text and the side of the bar all say it too. */
  function move(row) {
    if (row.pct === null || row.pct === undefined) return `<div class="an-move none">Not enough data</div>`;
    if (row.direction === "flat") return `<div class="an-move muted">Flat</div>`;
    const up = row.direction === "up";
    return `<div class="an-move ${up ? "an-up" : "an-down"}" aria-label="${up ? "Rising" : "Falling"} ${Math.abs(Math.round(row.pct))}%">${up ? "▲" : "▼"} ${Math.abs(Math.round(row.pct))}%</div>`;
  }

  function card(key, title, chips, take, body, wide) {
    return `<section class="card an-card ${wide ? "an-wide" : ""}" data-key="an-${key}">
      <div class="an-top"><h2>${esc(title)}</h2><span>${chips}</span></div>
      <p class="an-take">${esc(take)}</p>
      ${body}
    </section>`;
  }

  /* ---------- Tiles ---------- */
  function tiles(d) {
    const size = d.windows.size;
    return `<div class="an-tiles">${d.tiles.map((t) => {
      const value = t.money ? inr(t.now) : group(t.now);
      const was = t.money ? inr(t.before) : group(t.before);
      return `<div class="card an-tile" data-key="an-tile-${esc(t.key)}"><span>${esc(t.label)}</span><b>${esc(value)}</b>
        ${move(t).replace("</div>", ` <small>against ${esc(was)} in the ${esc(size)} before</small></div>`).replace('class="an-move', 'style="text-align:left;white-space:normal" class="an-move')}</div>`;
    }).join("")}</div>`;
  }

  /* ---------- Daily sales line ---------- */
  function niceTicks(max) {
    if (max <= 0) return { top: 1, ticks: [0] };
    const rough = max / 4;
    const power = Math.pow(10, Math.floor(Math.log10(rough)));
    const step = [1, 2, 2.5, 5, 10].map((s) => s * power).find((s) => s >= rough);
    const top = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = 0; v <= top + step / 2; v += step) ticks.push(v);
    return { top, ticks };
  }

  function trendChart(t, windows) {
    const days = t.days;
    const n = days.length;
    if (!n) return `<p class="quiet">No bills on record</p>`;
    const { top, ticks } = niceTicks(Math.max(...days.map((x) => x.sales)));
    const px = (i) => (n > 1 ? (i / (n - 1)) * 100 : 50);
    const py = (v) => 100 - (v / top) * 100;
    const line = days.map((x, i) => `${px(i).toFixed(2)},${py(x.sales).toFixed(2)}`).join(" ");
    const area = `0,100 ${line} 100,100`;
    const grid = ticks.map((v) => `<line class="an-grid-line" x1="0" x2="100" y1="${py(v).toFixed(2)}" y2="${py(v).toFixed(2)}"/>`).join("");
    const idxOf = (w) => days.map((x, i) => (x.window === w ? i : -1)).filter((i) => i >= 0);
    const seg = (idx, avg) => (idx.length && avg ? `<line class="an-avg" x1="${px(idx[0]).toFixed(2)}" x2="${px(idx[idx.length - 1]).toFixed(2)}" y1="${py(avg).toFixed(2)}" y2="${py(avg).toFixed(2)}"/>` : "");
    const prev = idxOf("previous");
    const last = idxOf("last");
    const end = days[n - 1];
    const lowIdx = (t.low_days || []).map((d) => days.findIndex((x) => x.date === d)).filter((i) => i >= 0);
    const summary = `Daily sales for ${n} selling days. ${t.takeaway}`;
    const dots = [n - 1, ...lowIdx].map((i) => `<circle class="an-dot" cx="${px(i).toFixed(2)}%" cy="${py(days[i].sales).toFixed(2)}%" r="4"/>`).join("");
    const lowLabels = lowIdx.map((i) => `<span class="an-mark" style="left:${px(i).toFixed(2)}%;top:calc(${py(days[i].sales).toFixed(2)}% + 7px);transform:translateX(-50%)">${esc(inr(days[i].sales))}</span>`).join("");
    const xLabels = [0, last.length ? last[0] : -1, n - 1].filter((i, at, all) => i >= 0 && all.indexOf(i) === at).map((i) => {
      const align = i === 0 ? "left:0" : i === n - 1 ? "right:0" : `left:${px(i).toFixed(2)}%;transform:translateX(-50%)`;
      return `<span style="${align}">${esc(days[i].label.replace(/^\w+ /, ""))}</span>`;
    }).join("");
    return `<div class="an-chart" id="an-trend" data-key="an-trend-plot" role="img" aria-label="${esc(summary)}">
        ${ticks.map((v) => `<span class="an-y" style="top:${py(v).toFixed(2)}%">${esc(inr(v).replace(" lakh", "L"))}</span>`).join("")}
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">${grid}<polygon class="an-area" points="${area}"/>${seg(prev, t.avg_previous)}${seg(last, t.avg_last)}<polyline class="an-line" points="${line}"/></svg>
        <svg aria-hidden="true">${dots}</svg>
        ${prev.length ? `<span class="an-win" style="left:0">${esc(windows.size)} before</span>` : ""}
        ${last.length && prev.length ? `<span class="an-win" style="left:${px(last[0]).toFixed(2)}%">Last ${esc(windows.size)}</span>` : ""}
        ${lowLabels}
        <span class="an-mark" style="right:0;top:calc(${py(end.sales).toFixed(2)}% + 8px)">${esc(inr(end.sales))}</span>
        <i class="an-cross" hidden></i><i class="an-hdot" hidden></i><div class="an-tip" hidden></div>
      </div>
      <div class="an-x">${xLabels}</div>
      <div class="an-key"><span><i style="background:var(--ink)"></i>Sales each selling day</span>${t.avg_previous ? `<span><i style="background:var(--accent)"></i>Average a day: ${esc(inr(t.avg_previous))}, then ${esc(inr(t.avg_last))}</span>` : ""}</div>
      <details class="an-more" data-key="an-trend-table"><summary>See the numbers</summary>
        <table class="an-table"><thead><tr><th>Day</th><th>Sales</th><th>Bills</th></tr></thead><tbody>${days.map((x) => `<tr><td>${esc(x.label)}</td><td>${esc(inr(x.sales))}</td><td>${esc(group(x.bills))}</td></tr>`).join("")}</tbody></table>
      </details>`;
  }

  /* ---------- Bars ---------- */
  function divergeBar(change, biggest) {
    const part = (Math.abs(change) / (biggest || 1)) * 50;
    const up = change >= 0;
    return `<div class="an-bar"><svg aria-hidden="true">
      <rect class="${up ? "an-fill-up" : "an-fill-down"}" x="${up ? 50 : share(50 - part)}%" y="0" width="${share(part)}%" height="100%" rx="2"/>
      <line class="an-mid" x1="50%" x2="50%" y1="-2" y2="14"/></svg></div>`;
  }

  function plainBar(part, cls) {
    return `<div class="an-bar"><svg aria-hidden="true"><rect class="an-track" width="100%" height="100%" rx="2"/><rect class="${cls}" width="${share(part)}%" height="100%" rx="2"/></svg></div>`;
  }

  function routesCard(r) {
    const biggest = Math.max(1, ...r.rows.map((x) => Math.abs(x.change)));
    const rows = r.rows.map((x) => `<div class="an-row" data-key="an-route-${esc(x.key)}" title="${esc(`${x.key}: ${inr(x.now)} last 7, ${inr(x.before)} the 7 before`)}">
      <span>${esc(x.label)}${x.counter ? `<small>counter</small>` : ""}</span>${divergeBar(x.change, biggest)}${move(x)}</div>`).join("");
    const thin = r.thin.length ? `<p class="an-note">Not enough data: ${esc(r.thin.map((x) => x.key).join(", "))}. Under ${esc(inr(r.min_base))} in the 7 before.</p>` : "";
    const table = `<details class="an-more" data-key="an-routes-table"><summary>See the rupees</summary>
      <table class="an-table"><thead><tr><th>Route</th><th>Last 7</th><th>7 before</th><th>Change</th></tr></thead><tbody>${r.rows.concat(r.thin).map((x) => `<tr><td>${esc(x.key)}</td><td>${esc(inr(x.now))}</td><td>${esc(inr(x.before))}</td><td>${esc(signed(x.change))}</td></tr>`).join("")}</tbody></table></details>`;
    return card("routes", "Routes", REAL, r.takeaway, `<p class="an-note">Routes are the only areas in the data, because locations are masked. Green rises, red falls, and a longer bar means more rupees.</p>
      <div class="an-rows">${rows}</div>${thin}${table}`);
  }

  function productRows(list, cls, biggest, empty) {
    if (!list.length) return `<p class="muted small">${esc(empty)}</p>`;
    return list.map((x) => `<div class="an-prod" data-key="an-prod-${esc(x.key)}" title="${esc(`${inr(x.now)} last 7, ${inr(x.before)} the 7 before`)}">
      <div class="an-prod-name">${esc(x.label)}</div>
      <div class="an-prod-line">${plainBar((Math.abs(x.change) / biggest) * 100, cls)}<span class="muted num" style="font-size:12px">${esc(signed(x.change))}</span></div>
      ${move(x).replace('class="an-move', 'style="text-align:left" class="an-move')}</div>`).join("");
  }

  function productsCard(p) {
    const biggest = Math.max(1, ...p.rising.concat(p.falling).map((x) => Math.abs(x.change)));
    const body = `<div class="an-pair an-duo">
      <div><h3>Top rising</h3>${productRows(p.rising, "an-fill-up", biggest, "Nothing rising")}</div>
      <div><h3>Top falling</h3>${productRows(p.falling, "an-fill-down", biggest, "Nothing falling")}</div>
    </div>
    ${p.left_out ? `<p class="an-note">Not enough data for ${esc(group(p.left_out))} smaller products, so they are left out: each needs ${esc(inr(p.min_base))} on ${esc(p.min_days)} or more days in the 7 before.</p>` : ""}`;
    return card("products", "Products", REAL, p.takeaway, body);
  }

  function mixRows(rows) {
    const biggest = Math.max(1, ...rows.map((x) => x.now));
    return `<div class="an-rows">${rows.map((x) => `<div class="an-row an-mix" data-key="an-mix-${esc(x.key)}" title="${esc(`${x.label}: ${inr(x.now)} last 7, ${inr(x.before)} the 7 before`)}">
      <span>${esc(x.label)}</span>${plainBar((x.now / biggest) * 100, "an-fill-quiet")}<span class="an-amt">${esc(inr(x.now))}</span>${move(x)}</div>`).join("")}</div>`;
  }

  /* ---------- Shops that stopped ---------- */
  function quietCard(q) {
    const shown = q.rows.slice(0, 10);
    const rows = shown.map((r) => `<tr data-key="an-shop-${esc(r.shop)}">
      <td><a class="link" href="#/retailers/${encodeURIComponent(r.shop)}">${esc(r.type)} ${esc(r.shop)}</a></td>
      <td data-l="Route">${esc(r.route)}</td><td data-l="Last bill">${esc(r.last_bill_label)}</td><td data-l="Days with a bill">${esc(r.bill_days)}</td>
      <td class="r" data-l="A week, estimate">${esc(inr(r.weekly_inr))}</td></tr>`).join("");
    const body = q.rows.length ? `<p class="an-note">Lost sales start here. Regular means bills on ${esc(q.regular_days)} or more days. The value is what each shop bought in an average ${esc(q.window)} selling days, not a forecast.</p>
      <table class="otable an-shops"><thead><tr><th>Shop</th><th>Route</th><th>Last bill</th><th>Days with a bill</th><th class="r">A week, estimate</th></tr></thead><tbody>${rows}</tbody></table>
      ${q.rows.length > shown.length ? `<p class="an-note">The ${esc(q.rows.length - shown.length)} smaller shops are in the total above.</p>` : ""}` : "";
    return card("quiet", "Shops that stopped ordering", `${REAL} ${standin("Value is an estimate")}`, q.takeaway, body, true);
  }

  /* ---------- Truck load and the product gap ---------- */
  function trucksBlock(t) {
    const target = t.target * 100;
    return t.rows.map((r) => `<div class="an-truck" data-key="an-truck-${esc(r.truck)}" title="${esc(`${group(r.kg)} kg, ${r.stops} stops`)}">
      <span>${esc(r.truck)}</span>
      <div class="an-bar"><svg aria-hidden="true"><rect class="an-track" width="100%" height="100%" rx="2"/><rect class="${r.at_target ? "an-fill-ink" : "an-fill-or"}" width="${share(r.fill * 100)}%" height="100%" rx="2"/><line class="an-mid" x1="${share(target)}%" x2="${share(target)}%" y1="-3" y2="17" style="stroke:var(--text);stroke-width:1.5"/></svg></div>
      <span>${esc(r.fill_label)}</span></div>`).join("");
  }

  function gapBlock(g) {
    const n = g.days.length;
    if (!n) return "";
    const top = Math.max(1, ...g.days.map((x) => x.billed_inr));
    const slot = 100 / n;
    const band = g.silent_days ? `<rect class="an-band" x="${share(slot * (n - g.silent_days))}%" y="0" width="${share(slot * g.silent_days)}%" height="100%"/>` : "";
    const cols = g.days.map((x, i) => {
      const h = (x.billed_inr / top) * 100;
      return x.billed_inr > 0 ? `<rect class="an-fill-ink" x="${(slot * i + slot * 0.25).toFixed(2)}%" y="${(100 - h).toFixed(2)}%" width="${(slot * 0.5).toFixed(2)}%" height="${h.toFixed(2)}%" rx="2"><title>${esc(`${x.label}: ${inr(x.billed_inr)}`)}</title></rect>` : "";
    }).join("");
    const lastIdx = n - g.silent_days - 1;
    return `<div class="an-sub" data-key="an-gap">
      <div class="an-top"><h2>${esc(g.label)}</h2><span>${REAL}</span></div>
      <p class="an-take">${esc(g.takeaway)}</p>
      <div class="an-cols" role="img" aria-label="${esc(g.takeaway)}"><svg aria-hidden="true">${band}${cols}<line class="an-grid-line" x1="0" x2="100%" y1="100%" y2="100%"/></svg>
        ${g.silent_days ? `<span class="an-mark or" style="right:6px;top:calc(50% - 8px)">No bills</span>` : ""}</div>
      <div class="an-x" style="margin:5px 0 0"><span style="left:0">${esc(g.days[0].label.replace(/^\w+ /, ""))}</span>
        ${g.silent_days && lastIdx > 1 && lastIdx < n - 2 ? `<span style="left:${share(slot * (lastIdx + 0.5))}%;transform:translateX(-50%)">${esc(g.days[lastIdx].label.replace(/^\w+ /, ""))}</span>` : ""}
        <span style="right:0">${esc(g.days[n - 1].label.replace(/^\w+ /, ""))}</span></div>
      <p class="an-note"><a class="link" href="#/misses">See where it is stuck</a></p>
    </div>`;
  }

  function build(d) {
    const w = d.windows;
    const period = w.previous.label ? `Last ${w.size} selling days (${w.last.label}) against the ${w.size} before (${w.previous.label})` : `${d.selling_days} selling days on record`;
    return `<div data-key="v-analysis" class="an">
      <h1>Analysis</h1>
      <p class="sub">Distributor ${esc(d.distributor)} · Records to ${esc(d.records_to_label)} · ${esc(period)}</p>
      ${tiles(d)}
      <div class="an-grid">
        ${card("trend", "Daily sales", REAL, d.trend.takeaway, `${d.trend.note ? `<p class="an-note">${esc(d.trend.note)}</p>` : ""}${trendChart(d.trend, w)}`, true)}
        ${routesCard(d.routes)}
        ${productsCard(d.products)}
        ${card("categories", "Sales by category", REAL, d.categories.takeaway, `<p class="an-note">Categories are grouped from product names. Bars show the last ${esc(w.size)} selling days.</p>${mixRows(d.categories.rows)}`)}
        ${card("types", "Sales by retailer type", standin("Stand-in types"), d.retailer_types.takeaway, `<p class="an-note">Sales are real. Types are stand-ins, inferred from names and packs, not recorded.</p>${mixRows(d.retailer_types.rows)}`)}
        ${quietCard(d.quiet_shops)}
        ${card("trucks", "Truck load for tomorrow", standin(), d.trucks.takeaway, `<p class="an-note">Capacity is a stand-in, and tonight's orders are next day bills standing in. The line marks ${esc(d.trucks.target_label)}.</p><div class="an-rows">${trucksBlock(d.trucks)}</div>${gapBlock(d.gap)}`, true)}
      </div>
    </div>`;
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const r = await fetch("/api/analysis", { cache: "no-store" });
      if (!r.ok) throw new Error(`Server replied ${r.status}`);
      D = await r.json();
      html = build(D);
      failed = false;
    } catch (e) {
      failed = true;
      retryAt = Date.now() + 5000;
    }
    loading = false;
    if (typeof window.render === "function") window.render();
  }

  function view() {
    if (!D && !loading && (!failed || Date.now() >= retryAt)) load();
    if (!D) return `<div data-key="v-analysis"><h1>Analysis</h1><p class="quiet">${failed ? "Could not load the analysis. Trying again." : "Loading"}</p></div>`;
    return html;
  }

  /* Reopening the tab picks up a changed truck load. The invoice figures do not move, so nothing else redraws. */
  window.addEventListener("hashchange", () => { if (D && location.hash.startsWith("#/analysis")) load(); });

  /* Crosshair and tooltip for the daily line. These touch only the hover marks, never the chart itself. */
  function hover(event) {
    const plot = event.target.closest && event.target.closest("#an-trend");
    if (!plot || !D) return;
    const days = D.trend.days;
    const box = plot.getBoundingClientRect();
    const i = Math.max(0, Math.min(days.length - 1, Math.round(((event.clientX - box.left) / box.width) * (days.length - 1))));
    const day = days[i];
    const { top } = niceTicks(Math.max(...days.map((x) => x.sales)));
    const x = days.length > 1 ? (i / (days.length - 1)) * 100 : 50;
    const cross = plot.querySelector(".an-cross");
    const dot = plot.querySelector(".an-hdot");
    const tip = plot.querySelector(".an-tip");
    cross.hidden = dot.hidden = tip.hidden = false;
    cross.style.left = `${x}%`;
    dot.style.left = `${x}%`;
    dot.style.top = `${100 - (day.sales / top) * 100}%`;
    tip.innerHTML = `<b>${esc(day.label)}</b>${esc(inr(day.sales))}, ${esc(group(day.bills))} bills`;
    const room = box.width - tip.offsetWidth;
    tip.style.left = `${Math.max(0, Math.min(room, (x / 100) * box.width - tip.offsetWidth / 2))}px`;
  }
  function unhover(event) {
    const plot = event.target.closest && event.target.closest("#an-trend");
    if (!plot) return;
    plot.querySelectorAll(".an-cross, .an-hdot, .an-tip").forEach((el) => { el.hidden = true; });
  }
  document.addEventListener("pointermove", hover);
  document.addEventListener("pointerdown", hover);
  document.addEventListener("pointerout", (event) => {
    const plot = event.target.closest && event.target.closest("#an-trend");
    if (plot && !plot.contains(event.relatedTarget)) unhover(event);
  });

  window.TrexAnalysis = { view };
})();
