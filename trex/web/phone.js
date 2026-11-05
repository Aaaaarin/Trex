/* Helpers shared by the phone screens. A screen redraws only when its data changed, so buttons stay put. */
(function () {
  try { if (localStorage.getItem("trex-theme") === "dark") document.documentElement.dataset.theme = "dark"; } catch (e) {}
  const esc = (s) => String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const rupees = (n) => `Rs ${Number(n || 0).toLocaleString("en-IN")}`;

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

  window.Trex = { esc, rupees, post, poll };
})();
