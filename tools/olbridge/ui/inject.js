// Injected into every page of the local Overleaf by the proxy. No secrets here:
// the proxy adds the API token server-side and only forwards four endpoints.
(() => {
  if (window.__olbridge) return;
  window.__olbridge = true;
  const API = "/olbridge/api";
  const LOADED_AT = Date.now();
  const projectId = (location.pathname.match(/^\/project\/([0-9a-f]{24})/) || [])[1] || "";
  const deferred = new Set();

  const call = async (path, body) => {
    const res = await fetch(API + path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-OLB-Page": "1" }, body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.status);
    return data;
  };

  // Everything lives in a shadow root so Overleaf's CSS cannot touch it and vice versa.
  const host = document.createElement("div");
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `<style>
    .bar{position:fixed;z-index:2147483000;left:50%;top:10px;transform:translateX(-50%);display:none;max-width:90vw;gap:10px;align-items:center;
      background:#1d2125;color:#e8eaed;border:1px solid #3fb950;border-radius:8px;padding:8px 14px;font:14px/1.4 system-ui,sans-serif;box-shadow:0 4px 18px rgba(0,0,0,.35)}
    .bar.show{display:flex}.bar.warn{border-color:#e3a008}
    button{font:inherit;padding:4px 10px;border-radius:6px;border:1px solid #555;background:#2d333a;color:#e8eaed;cursor:pointer}
    button.primary{background:#138a36;border-color:#138a36;color:#fff}
    dialog{border:1px solid #444;border-radius:12px;background:#1d2125;color:#e8eaed;width:min(960px,94vw);padding:18px;font:14px/1.45 system-ui,sans-serif}
    dialog::backdrop{background:rgba(0,0,0,.55)}
    h2{margin:0;font-size:17px}.sub{color:#9aa0a6;font-size:13px}
    .panes{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0}
    h3{font-size:13px;margin:0 0 6px}
    pre{margin:0;padding:10px;background:#14171a;border:1px solid #2d333a;border-radius:8px;max-height:340px;overflow:auto;font-size:12px;white-space:pre-wrap;word-break:break-word}
    .actions{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap}
  </style>
  <div class="bar" id="bar"><span id="msg"></span><button id="reload" hidden>Reload</button><button id="close">Dismiss</button></div>
  <dialog id="dlg"><h2 id="title"></h2><div class="sub" id="sub"></div>
    <div class="panes"><div><h3>overleaf.com</h3><pre id="com"></pre></div><div><h3>Local (this server)</h3><pre id="local"></pre></div></div>
    <div class="actions"><button id="later">Decide later</button><button id="keep-local">Keep local</button><button class="primary" id="keep-com">Keep overleaf.com</button></div>
  </dialog>`;
  const $ = (id) => root.getElementById(id);
  document.documentElement.append(host);

  let hideTimer;
  const banner = (text, { warn = false, reload = false, sticky = false } = {}) => {
    $("msg").textContent = text;
    $("bar").className = "bar show" + (warn ? " warn" : "");
    $("reload").hidden = !reload;
    clearTimeout(hideTimer);
    if (!sticky) hideTimer = setTimeout(() => $("bar").classList.remove("show"), 6000);
  };
  $("close").onclick = () => $("bar").classList.remove("show");
  $("reload").onclick = () => location.reload();

  const showText = (side) => side.text ?? (side.missing ? "(file does not exist)" : `(binary file, ${side.size} bytes)`);
  async function checkConflicts() {
    const dlg = $("dlg");
    if (dlg.open) return;
    let page;
    try { page = await call("/page?id=" + projectId); } catch { return; }
    const c = page.conflicts.find((x) => !deferred.has(x.link + "/" + x.path));
    if (!c) return;
    $("title").textContent = "Conflict in " + c.path;
    $("sub").textContent = c.project + ": changed on both sides since the last sync. Nothing is overwritten until you choose.";
    $("com").textContent = showText(c.com);
    $("local").textContent = showText(c.local);
    const pick = async (choice) => {
      dlg.close();
      if (choice === "skip") deferred.add(c.link + "/" + c.path);
      else try { await call("/resolve", { id: c.link, path: c.path, choice }); banner("Resolved " + c.path + (projectId ? ". Reload to see it." : "."), { reload: !!projectId }); }
      catch (e) { banner("Could not resolve: " + e.message, { warn: true }); }
      checkConflicts();
    };
    $("keep-com").onclick = () => pick("com");
    $("keep-local").onclick = () => pick("local");
    $("later").onclick = () => pick("skip");
    dlg.showModal();
  }

  async function pullOnOpen() {
    if (!projectId) return;
    banner("Checking overleaf.com for the latest version...", { sticky: true });
    try {
      const r = await call("/pull", { local_id: projectId });
      if (r.pulled.length) banner(`Pulled ${r.pulled.length} file(s) from overleaf.com: ${r.pulled.join(", ")}`, { reload: true, sticky: true });
      else if (r.conflicts.length) banner("Conflicts need a decision.", { warn: true });
      else banner("Up to date with overleaf.com.");
    } catch (e) {
      if (String(e.message).includes("not linked")) $("bar").classList.remove("show");
      else banner("Could not check overleaf.com: " + e.message, { warn: true });
    }
  }

  function listen() {
    const source = new EventSource(API + "/events?after=0");
    source.onmessage = (m) => {
      const e = JSON.parse(m.data);
      if (new Date(e.ts).getTime() < LOADED_AT - 1000) return;   // ignore history
      if (e.kind === "edit") banner(e.text, {});
      else if (e.kind === "pull") banner(e.text, { reload: !!projectId, sticky: !!projectId });
      else if (e.kind === "warn") banner(e.text, { warn: true });
      else if (e.kind === "conflict") checkConflicts();
    };
  }

  pullOnOpen().finally(checkConflicts);
  listen();
  setInterval(checkConflicts, 15000);
})();
