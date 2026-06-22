/* Shared helpers for the ITGC Controls Toolkit. */
const API = {
  async req(path, opts = {}) {
    const r = await fetch("/api/" + path, { credentials: "include", ...opts });
    if (!r.ok) {
      let msg = "Error " + r.status;
      try { msg = (await r.json()).error || msg; } catch {}
      const e = new Error(msg); e.status = r.status; throw e;
    }
    return r.json();
  },
  get(p) { return this.req(p); },
  post(p, body) {
    return this.req(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  },
  postForm(p, formData) { return this.req(p, { method: "POST", body: formData }); },
};

let ME = null;
async function loadMe() {
  try { const d = await API.get("me"); ME = d.user === null ? null : d; } catch { ME = null; }
  renderUserChip();
  if (!ME && !location.pathname.includes("login")) {
    // not logged in -> show login overlay
    showLogin();
  }
  return ME;
}
function atLeast(role) {
  const rank = { viewer: 0, auditor: 1, admin: 2 };
  return ME && rank[ME.role] >= rank[role];
}
function renderUserChip() {
  const el = document.getElementById("userChip");
  if (!el) return;
  if (ME) {
    el.innerHTML = `<span class="role-tag">${ME.role.toUpperCase()}</span> <strong>${ME.username}</strong> <a href="#" id="logoutLink">log out</a>`;
    document.getElementById("logoutLink").onclick = async (e) => { e.preventDefault(); await API.post("logout"); location.reload(); };
  } else {
    el.innerHTML = `<a href="#" id="loginLink">log in</a>`;
    document.getElementById("loginLink").onclick = (e) => { e.preventDefault(); showLogin(); };
  }
}
function showLogin() {
  if (document.getElementById("loginOverlay")) return;
  const ov = document.createElement("div");
  ov.id = "loginOverlay";
  ov.style.cssText = "position:fixed;inset:0;background:rgba(10,10,14,.85);display:flex;align-items:center;justify-content:center;z-index:999;";
  ov.innerHTML = `
    <div class="card" style="padding:2rem;width:min(380px,90%);opacity:1;transform:none;">
      <h2 style="margin:0 0 .3rem;font-size:1.3rem;">ITGC Controls Toolkit</h2>
      <p style="color:var(--muted);font-size:.85rem;margin:0 0 1.2rem;">Sign in to continue</p>
      <input id="luser" placeholder="username" style="width:100%;padding:.6rem;margin-bottom:.6rem;background:var(--panel-soft);border:1px solid var(--border);border-radius:8px;color:var(--ink);">
      <input id="lpass" type="password" placeholder="password" style="width:100%;padding:.6rem;margin-bottom:1rem;background:var(--panel-soft);border:1px solid var(--border);border-radius:8px;color:var(--ink);">
      <button class="run-btn" id="lbtn" style="width:100%;">Sign in</button>
      <p id="lerr" style="color:var(--red);font-size:.8rem;margin:.7rem 0 0;min-height:1rem;"></p>
      <p style="color:var(--muted);font-size:.74rem;margin:.8rem 0 0;line-height:1.5;">
        Demo logins: <code>admin / admin123</code> (full), <code>auditor / auditor123</code>, <code>viewer / viewer123</code> (read-only)</p>
    </div>`;
  document.body.appendChild(ov);
  const submit = async () => {
    const u = document.getElementById("luser").value.trim();
    const p = document.getElementById("lpass").value;
    try { await API.post("login", { username: u, password: p }); location.reload(); }
    catch (e) { document.getElementById("lerr").textContent = e.message; }
  };
  document.getElementById("lbtn").onclick = submit;
  document.getElementById("lpass").addEventListener("keydown", e => { if (e.key === "Enter") submit(); });
}

function navBar(active) {
  const items = [["", "Home"], ["ccm", "CCM Monitor"], ["sod", "Access · SoD"], ["jml", "Access · JML"],
    ["change", "Change Mgmt"], ["recert", "Recertification"], ["framework", "Framework Map"],
    ["gam", "GAM · GITC"], ["fait", "FAIT"], ["governance", "Audit Trail"]];
  return `<header class="topbar card">
    <div class="brand"><span class="brand-name">ITGC Controls Toolkit</span>
      <span class="brand-tag">Technology Risk · ITGC</span></div>
    <nav class="main-nav">${items.map(([h, l]) =>
      `<a href="/${h}" class="${active === h ? 'active' : ''}">${l}</a>`).join("")}</nav>
    <div class="user-wrap"><div id="userChip"></div></div>
  </header>`;
}

function fileDrop(zoneId, inputId, onFile) {
  const zone = document.getElementById(zoneId), input = document.getElementById(inputId);
  if (!zone) return;
  zone.onclick = () => input.click();
  input.onchange = () => { if (input.files[0]) onFile(input.files[0]); };
  ["dragover", "dragenter"].forEach(ev => zone.addEventListener(ev, e => { e.preventDefault(); zone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach(ev => zone.addEventListener(ev, e => { e.preventDefault(); zone.classList.remove("drag"); }));
  zone.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) onFile(f); });
}
