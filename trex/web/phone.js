/* Helpers shared by the phone apps. A screen redraws only when its data changed, so buttons stay put. */
(function () {
  try { if (localStorage.getItem("trex-theme") === "dark") document.documentElement.dataset.theme = "dark"; } catch (e) {}
  const esc = (s) => String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const rupees = (n) => `Rs ${Number(n || 0).toLocaleString("en-IN")}`;
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const day = (iso) => { const [, m, d] = String(iso).split("-"); return `${Number(d)} ${MONTHS[Number(m) - 1]}`; };
  const STAND_IN = `<span class="standin">Stand-in</span>`;
  const MARK = `<span class="tile-mark" aria-hidden="true"><svg viewBox="0 0 64 64"><path d="M32 10v44M13 21l38 22M13 43l38-22" stroke="#fff" stroke-width="5" stroke-linecap="round"/></svg></span>`;

  function stars(rating) {
    const pct = Math.max(0, Math.min(100, (Number(rating) / 5) * 100));
    return `<span class="rate" aria-label="Rated ${esc(rating)} of 5"><span class="stars" aria-hidden="true">★★★★★<i style="width:${pct}%">★★★★★</i></span>${Number(rating).toFixed(1)}</span>`;
  }

  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.error) throw new Error(j.error || j.detail || `Server replied ${r.status}`);
    return j;
  }

  function poll(url, draw, every) {
    let last = "";
    async function tick() {
      try {
        const r = await fetch(typeof url === "function" ? url() : url, { cache: "no-store" });
        const text = await r.text();
        if (r.ok && text !== last) { last = text; draw(JSON.parse(text)); }
      } catch (e) { /* the next tick tries again */ }
      setTimeout(tick, every || 1200);
    }
    tick();
    return { refresh() { last = ""; } };
  }

  async function getJson(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(`Server replied ${r.status}`);
    return r.json();
  }

  window.Trex = { esc, rupees, day, stars, post, poll, getJson, STAND_IN, MARK };
})();
