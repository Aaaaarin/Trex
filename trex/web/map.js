/* The fleet map. A schematic: positions are invented, only the state painted on them is real. Main page calls TrexMap.view(). */
(function () {
  let M = null;
  let failed = false;

  const esc = (s) => String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const kg = (n) => `${Math.round(Number(n || 0)).toLocaleString("en-IN")} kg`;
  const f1 = (n) => Math.round(n * 10) / 10;

  document.head.insertAdjacentHTML("beforeend", `<style>
    .mp { --mp-good: #0ca30c; --mp-warn: #fab219; --mp-bad: #d03b3b; --mp-off: var(--muted); }
    .mp-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px 16px; flex-wrap: wrap; }
    .mp-summary { font-size: 17px; font-weight: 500; line-height: 1.35; margin: 14px 0 14px; }
    .mp-layout { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-start; }
    .mp-mapcard { flex: 3 1 460px; min-width: 0; padding: 8px; }
    .mp-side { flex: 2 1 320px; min-width: 0; display: flex; flex-direction: column; gap: 14px; }
    .mp-side .card { padding: 14px 16px; }
    .mp svg.mp-map { display: block; width: 100%; height: auto; max-width: 720px; margin: 0 auto; }
    .mp-map text { font-family: var(--sans); fill: var(--text); }
    .mp-map .mp-ring { fill: none; stroke: var(--rule); stroke-width: 1; }
    .mp-map .mp-wedge { fill: var(--hover); opacity: 0.75; }
    .mp-map .mp-spoke { stroke: var(--rule); stroke-width: 1; }
    .mp-map .mp-spoke.on { stroke: var(--quiet-bar); stroke-width: 1.5; }
    .mp-map .mp-rlabel { font-size: 10px; fill: var(--muted); text-anchor: middle; dominant-baseline: central; }
    .mp-map .mp-rlabel.on { font-size: 12px; font-weight: 600; fill: var(--text); }
    .mp-map .mp-stop circle, .mp-map .mp-stop path { transition: fill 0.4s, stroke 0.4s; }
    .mp-map .hit { fill: transparent; stroke: none; }
    .mp-map .mp-yard { fill: var(--surface); stroke: var(--ink); stroke-width: 1.5; }
    .mp-map .mp-bay { fill: var(--bg); stroke: var(--rule); stroke-width: 1; stroke-dasharray: 3 3; }
    .mp-map .mp-yardname { font-size: 12px; font-weight: 600; text-anchor: middle; }
    .mp-truck, .mp-pill { transition: transform 0.9s cubic-bezier(.2, .7, .2, 1); }
    .mp-truck rect { fill: var(--surface); stroke-width: 2; transition: stroke 0.4s; }
    .mp-truck .t1 { font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .mp-truck .t2 { font-size: 11px; fill: var(--muted); font-variant-numeric: tabular-nums; }
    .mp-note { margin: 6px 8px 8px; }
    .mp-legend { display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 4px 8px 8px; font-size: 12px; color: var(--muted); }
    .mp-legend > div { display: flex; align-items: center; gap: 5px; }
    .mp-legend svg { width: 14px; height: 14px; flex: none; }
    .mp-legend b { font-weight: 600; color: var(--text); margin-right: -6px; }
    .mp-legend .brk { flex-basis: 100%; height: 0; }
    .mp-cardhead { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
    .mp-board { width: 100%; border-collapse: collapse; font-size: 13px; }
    .mp-board th { text-align: left; font-weight: 400; font-size: 12px; color: var(--muted); padding: 4px 6px 4px 0; }
    .mp-board td { padding: 6px 6px 6px 0; border-top: 1px solid var(--rule); vertical-align: middle; }
    .mp-board td:last-child, .mp-board th:last-child { padding-right: 0; }
    .mp-board .nw { white-space: nowrap; }
    .mp-board .num { text-align: right; padding-right: 12px; }
    .mp-stars { position: relative; display: inline-block; letter-spacing: -0.5px; color: var(--muted); }
    .mp-stars i { position: absolute; left: 0; top: 0; overflow: hidden; white-space: nowrap; font-style: normal; color: var(--text); }
    .mp-dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; vertical-align: 0.5px; }
    .mp-dot.good { background: var(--mp-good); } .mp-dot.warn { background: var(--mp-warn); } .mp-dot.bad { background: var(--mp-bad); } .mp-dot.mu { background: var(--mp-off); }
    .mp-trow { display: grid; grid-template-columns: 22px minmax(0, 1fr) auto; gap: 2px 8px; align-items: center; padding: 8px 0; border-top: 1px solid var(--rule); }
    .mp-trow:first-of-type { border-top: 0; }
    .mp-trow svg { width: 18px; height: 18px; display: block; }
    .mp-trow .lab { grid-column: 2 / -1; font-size: 13px; color: var(--muted); }
    .mp-trow .bar { grid-column: 2 / -1; height: 6px; margin-top: 4px; }
    .mp-trow .bar > i { background: var(--ink); }
    @media (prefers-reduced-motion: reduce) { .mp-truck, .mp-pill { transition: none; } }
  </style>`);

  const isMap = () => /^#\/?map\b/.test(location.hash);

  async function fetchMap() {
    try {
      const r = await fetch("/api/fleetmap", { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      M = await r.json();
      failed = false;
    } catch (e) { failed = !M; }
  }
  async function loop() {
    if (isMap()) await fetchMap();
    setTimeout(loop, 2000);
  }
  window.addEventListener("hashchange", () => { if (isMap() && !M) fetchMap(); });

  const TONE = { good: "--mp-good", warn: "--mp-warn", bad: "--mp-bad", mu: "--mp-off" };
  /* Colour never works alone: each tone has its own glyph, so red and green stay apart for colourblind readers. */
  function glyph(tone, cx, cy, r) {
    const ink = tone === "warn" ? "#191917" : "#FFFFFF";
    const s = r / 8;
    const p = (d) => `<path transform="translate(${cx} ${cy}) scale(${s})" d="${d}" fill="none" stroke="${ink}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>`;
    const mark = { good: p("M-3.6 0.2 L-1 2.8 L3.8 -2.6"), warn: p("M0 -3.6 L0 0.8 M0 3.6 L0 3.7"), bad: p("M-3 -3 L3 3 M3 -3 L-3 3"), mu: p("M-3.4 0 L3.4 0") }[tone];
    return `<circle cx="${cx}" cy="${cy}" r="${r}" style="fill:var(${TONE[tone]})"/>${mark}`;
  }

  function stopMark(state) {
    if (state === "delivered") return `<circle r="4.5" style="fill:var(--mp-good);stroke:var(--surface);stroke-width:1.5"/>`;
    if (state === "failed") return `<circle r="6" style="fill:var(--mp-bad);stroke:var(--surface);stroke-width:1.5"/><path d="M-2.2 -2.2 L2.2 2.2 M2.2 -2.2 L-2.2 2.2" style="fill:none;stroke:#FFFFFF;stroke-width:1.6;stroke-linecap:round"/>`;
    if (state === "out") return `<circle r="4" style="fill:var(--mp-warn);stroke:var(--surface);stroke-width:1.5"/>`;
    return `<circle r="3.5" style="fill:var(--surface);stroke:var(--mp-off);stroke-width:1.5"/>`;
  }
  const STOP_WORD = { delivered: "delivered", failed: "failed", out: "not closed yet, truck is out", planned: "not yet" };

  const TW = 104, TH = 36;

  function drawMap(R, narrow) {
    const trucks = M.trucks;
    const cols = trucks.length > 4 ? 2 : 1;
    const rows = Math.max(1, Math.ceil(trucks.length / cols));
    const yardW = cols * TW + (cols - 1) * 6 + 16;
    const yardH = rows * (TH + 4) + 30;
    const r0 = Math.hypot(yardW, yardH) / 2 + 2;
    const rEnd = R - (narrow ? 18 : 24);
    const n = Math.max(M.routes.length, 1);
    const half = Math.PI / n;
    const rad = (deg) => ((deg - 90) * Math.PI) / 180;

    const place = (angle, rf, lane) => {
      const a = rad(angle);
      const r = r0 + 8 + rf * (rEnd - r0 - 16);
      const side = lane * Math.min(r * Math.tan(half) * 0.62, 10);
      return [f1(r * Math.cos(a) - side * Math.sin(a)), f1(r * Math.sin(a) + side * Math.cos(a))];
    };
    const onPolar = (angle, r) => [f1(r * Math.cos(rad(angle))), f1(r * Math.sin(rad(angle)))];

    const wedges = M.routes.filter((x) => x.trucks.length).map((x) => {
      const a = rad(x.angle);
      const pts = [[r0, a - half], [rEnd, a - half], [rEnd, a + half], [r0, a + half]].map(([r, t]) => [f1(r * Math.cos(t)), f1(r * Math.sin(t))]);
      return `<path class="mp-wedge" data-key="w-${esc(x.route)}" d="M${pts[0]} L${pts[1]} A${rEnd} ${rEnd} 0 0 1 ${pts[2]} L${pts[3]} A${r0} ${r0} 0 0 0 ${pts[0]} Z"/>`;
    }).join("");
    const spokes = M.routes.map((x) => {
      const on = x.trucks.length > 0;
      const [x1, y1] = onPolar(x.angle, r0);
      const [x2, y2] = onPolar(x.angle, rEnd);
      const line = `<line class="mp-spoke ${on ? "on" : ""}" data-key="sp-${esc(x.route)}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"><title>Route ${esc(x.route)}${on ? `, truck ${esc(x.trucks.join(" and "))} tomorrow` : ", no truck tomorrow"}</title></line>`;
      if (!on && narrow) return line;
      const [lx, ly] = onPolar(x.angle, rEnd + (on ? 11 : 9));
      return `${line}<text class="mp-rlabel ${on ? "on" : ""}" data-key="lb-${esc(x.route)}" x="${lx}" y="${ly}">${esc(x.route)}</text>`;
    }).join("");

    const spot = {};
    const stops = M.stops.map((s) => {
      const [x, y] = place(s.angle, s.rf, s.lane);
      spot[s.key] = [x, y, s.angle];
      const sim = s.simulated === true ? ", simulated" : "";
      return `<g class="mp-stop" data-key="st-${esc(s.key)}" transform="translate(${x} ${y})"><title>${esc(s.retailer)} on ${esc(s.truck)}, route ${esc(s.route)}: ${esc(STOP_WORD[s.state])}${sim}</title><circle class="hit" r="8"/>${stopMark(s.state)}</g>`;
    }).join("");

    const parked = (i) => {
      const col = i % cols;
      const row = Math.floor(i / cols);
      return [f1(-yardW / 2 + 8 + TW / 2 + col * (TW + 6)), f1(-yardH / 2 + 24 + TH / 2 + row * (TH + 4))];
    };
    const bays = trucks.map((t, i) => {
      const [x, y] = parked(i);
      return `<rect class="mp-bay" data-key="bay-${esc(t.truck)}" x="${x - TW / 2}" y="${y - TH / 2}" width="${TW}" height="${TH}" rx="8"/>`;
    }).join("");
    const markers = trucks.map((t, i) => {
      const stop = t.at ? spot[t.at] : null;
      const [x, y] = stop ? [stop[0], stop[1]] : parked(i);
      /* A pin marks the stop, and the label sits beside it so it never hides a dot. Parked trucks need neither. */
      let ox = 0, oy = 0;
      if (stop) {
        const idx = M.routes.findIndex((r) => r.angle === stop[2]);
        const next = M.routes[(idx + 1) % n], prev = M.routes[(idx - 1 + n) % n];
        const side = next.trucks.length && !prev.trucks.length ? -1 : 1;
        const a = rad(stop[2]);
        const nx = -Math.sin(a) * side, ny = Math.cos(a) * side;
        const gap = Math.abs(nx) * (TW / 2) + Math.abs(ny) * (TH / 2) + 9;
        let cx = Math.max(-R + TW / 2 + 2, Math.min(R - TW / 2 - 2, x + nx * gap));
        let cy = Math.max(-R + TH / 2 + 2, Math.min(R - TH / 2 - 2, y + ny * gap));
        const dx = yardW / 2 + TW / 2 + 4, dy = yardH / 2 + TH / 2 + 4;
        if (Math.abs(cx) < dx && Math.abs(cy) < dy) {
          if (dx - Math.abs(cx) < dy - Math.abs(cy)) cx = (cx < 0 ? -1 : 1) * dx;
          else cy = (cy < 0 ? -1 : 1) * dy;
        }
        ox = f1(cx - x); oy = f1(cy - y);
      }
      const who = t.driver || (t.held ? "held" : "no driver");
      return `<a href="#/truck/${encodeURIComponent(t.truck)}" class="mp-truck" data-key="tr-${esc(t.truck)}" style="transform:translate(${x}px,${y}px)">
        <title>${esc(t.truck)}, ${esc(who)}, ${esc(kg(t.kg))}: ${esc(t.label)}</title>
        <line x1="0" y1="0" x2="${ox}" y2="${oy}" style="stroke:var(${TONE[t.tone]});stroke-width:1.5"/>
        <circle r="${stop ? 6 : 0}" style="fill:var(${TONE[t.tone]});stroke:var(--surface);stroke-width:2"/>
        <g class="mp-pill" style="transform:translate(${ox}px,${oy}px)">
          <rect x="${-TW / 2}" y="${-TH / 2}" width="${TW}" height="${TH}" rx="8" style="stroke:var(${TONE[t.tone]})"/>
          ${glyph(t.tone, -TW / 2 + 13, 0, 8)}
          <text class="t1" x="${-TW / 2 + 25}" y="-3">${esc(t.truck)} · ${esc(who)}</text>
          <text class="t2" x="${-TW / 2 + 25}" y="12">${esc(kg(t.kg))}</text>
        </g>
      </a>`;
    }).join("");

    return `<svg class="mp-map" data-key="map-svg" viewBox="${-R} ${-R} ${2 * R} ${2 * R}" role="img" aria-label="Schematic map of tomorrow's trucks and stops. ${esc(M.summary)}">
      <circle class="mp-ring" cx="0" cy="0" r="${f1(rEnd)}"/>
      <circle class="mp-ring" cx="0" cy="0" r="${f1((r0 + rEnd) / 2)}"/>
      <g data-key="wedges">${wedges}</g>
      <g data-key="spokes">${spokes}</g>
      <g data-key="stops">${stops}</g>
      <g data-key="yard">
        <rect class="mp-yard" x="${-yardW / 2}" y="${-yardH / 2}" width="${yardW}" height="${yardH}" rx="12"/>
        <text class="mp-yardname" x="0" y="${-yardH / 2 + 16}">Depot</text>
        ${bays}
      </g>
      <g data-key="trucks">${markers}</g>
    </svg>`;
  }

  const legendDot = (inner) => `<svg viewBox="-7 -7 14 14" aria-hidden="true">${inner}</svg>`;
  function legend() {
    const item = (mark, text) => `<div>${mark}<span>${text}</span></div>`;
    const truck = (tone) => legendDot(glyph(tone, 0, 0, 6.5));
    return `<div class="mp-legend" data-key="legend">
      ${item(legendDot(stopMark("delivered")), "Delivered")}
      ${item(legendDot(stopMark("failed")), "Failed")}
      ${item(legendDot(stopMark("out")), "Truck out, not closed")}
      ${item(legendDot(stopMark("planned")), "Not yet")}
      ${item(`<svg viewBox="0 0 14 14" aria-hidden="true"><rect x="1" y="2" width="12" height="10" rx="2" style="fill:var(--hover)"/><line x1="1" y1="7" x2="13" y2="7" style="stroke:var(--quiet-bar);stroke-width:1.5"/></svg>`, "Route with a truck tomorrow")}
      <span class="brk"></span>
      ${item(truck("good"), "Truck on track")}
      ${item(truck("warn"), "Waiting for a driver or under target")}
      ${item(truck("bad"), "No driver, no-show or failures")}
      ${item(truck("mu"), "Held")}
    </div>`;
  }

  function stars(rating) {
    if (!rating) return `<span class="muted">No trips</span>`;
    const share = Math.round((rating / 5) * 100);
    return `<span class="mp-stars" aria-hidden="true">☆☆☆☆☆<i style="width:${share}%">★★★★★</i></span> <span class="num">${Number(rating).toFixed(1)}</span>`;
  }

  function driverBoard() {
    const rows = M.drivers.map((d) => `<tr data-key="d-${esc(d.id)}">
      <td class="nw"><span class="mp-dot ${esc(d.tone)}"></span><a class="link" href="/driver?id=${encodeURIComponent(d.id)}">${esc(d.id)}</a></td>
      <td class="nw" title="Average of invented trip stars">${stars(d.rating)}</td>
      <td class="nw num">${esc(d.km_to_depot)} km</td>
      <td>${esc(d.status)}${d.truck ? ` <span class="muted nw">${esc(d.truck)}</span>` : ""}</td>
    </tr>`).join("");
    return `<section class="card" data-key="drivers">
      <div class="mp-cardhead"><h2>Drivers</h2><span class="chip am" title="${esc(M.standin)}">Stand-in</span></div>
      <table class="mp-board"><thead><tr><th>Driver</th><th>Rating</th><th class="num">To depot</th><th>Now</th></tr></thead><tbody>${rows}</tbody></table>
    </section>`;
  }

  function truckList() {
    const rows = M.trucks.map((t) => {
      const share = t.stops ? Math.round((t.closed / t.stops) * 100) : 0;
      return `<div class="mp-trow" data-key="row-${esc(t.truck)}">
        <svg viewBox="-9 -9 18 18" aria-hidden="true">${glyph(t.tone, 0, 0, 8)}</svg>
        <div class="small"><a class="link" href="#/truck/${encodeURIComponent(t.truck)}"><strong>${esc(t.truck)}</strong></a> ${esc(t.driver || (t.held ? "" : "no driver"))}</div>
        <div class="small muted num">${esc(kg(t.kg))}</div>
        <div class="lab">${esc(t.label)}</div>
        <div class="bar" role="img" aria-label="${esc(t.closed)} of ${esc(t.stops)} stops closed"><i style="width:${share}%"></i></div>
      </div>`;
    }).join("");
    const merged = (M.merged || []).map((m) => `<p class="small muted" data-key="mg-${esc(m.truck)}">${esc(m.truck)} merged into ${esc(m.into)}</p>`).join("");
    return `<section class="card" data-key="trucklist"><div class="mp-cardhead"><h2>Trucks tomorrow</h2><span class="small muted">${esc(M.plan_date)}</span></div>${rows}${merged}</section>`;
  }

  function view() {
    if (!M) return `<div data-key="v-map"><h1>Fleet map</h1><p class="quiet">${failed ? "Reconnecting" : "Loading"}</p></div>`;
    const main = document.getElementById("main");
    const narrow = main ? main.clientWidth < 620 : false;
    const R = narrow ? 240 : 310;
    return `<div class="mp" data-key="v-map">
      <div class="mp-head"><div><h1>Fleet map</h1><p class="sub">${esc(M.schematic)}</p></div>
        <span class="chip am" title="${esc(M.standin)}">Stand-in positions and drivers</span></div>
      <p class="mp-summary" role="status">${esc(M.summary)}</p>
      <div class="mp-layout">
        <section class="card mp-mapcard" data-key="mapcard">
          ${drawMap(R, narrow)}
          ${legend()}
          <p class="small muted mp-note">${esc(M.schematic)} A truck moves only when its stops close.</p>
        </section>
        <div class="mp-side" data-key="side">
          ${truckList()}
          ${driverBoard()}
        </div>
      </div>
    </div>`;
  }

  window.TrexMap = { view };
  loop();
})();
