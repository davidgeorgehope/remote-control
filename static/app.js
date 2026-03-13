/* ── State ────────────────────────────────────────────────────────────────── */

let host = null;  // IP of the connected TV
let statusInterval = null;

/* ── Helpers ──────────────────────────────────────────────────────────────── */

async function api(path, body) {
  const opts = body !== undefined
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { method: "GET" };
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "Request failed");
  return data;
}

function flash(el) {
  el.classList.remove("flash");
  void el.offsetWidth;
  el.classList.add("flash");
}

function msg(text, type) {
  const el = document.getElementById("status-msg");
  el.textContent = text;
  el.className = "status-msg" + (type ? " " + type : "");
}

/* ── Discovery ────────────────────────────────────────────────────────────── */

async function discover() {
  const container = document.getElementById("devices");
  container.innerHTML = "";
  msg("Searching for TVs...");

  try {
    const data = await api("/api/discover", {});
    if (data.devices.length === 0) {
      msg("No TVs found. Enter IP manually.", "error");
      return;
    }
    msg(`Found ${data.devices.length} device(s)`, "success");
    data.devices.forEach(d => {
      const div = document.createElement("div");
      div.className = "device-item";
      div.innerHTML = `<span>${d.name}<br><small>${d.model} &middot; ${d.host}</small></span>
        <button onclick="connectToHost('${d.host}')">Connect</button>`;
      container.appendChild(div);
    });
  } catch (e) {
    msg(e.message, "error");
  }
}

/* ── Connect (handles pairing automatically) ──────────────────────────────── */

async function connectToHost(ip) {
  if (!ip) return;
  msg(`Connecting to ${ip}...`);

  try {
    const data = await api("/api/connect", { host: ip });

    if (data.status === "needs_pairing") {
      // Start pairing
      msg("Starting pairing...");
      await api("/api/pair/start", { host: ip });
      host = ip;
      document.getElementById("pair-dialog").classList.remove("hidden");
      msg("Look at your TV and enter the code shown on screen", "success");
      document.getElementById("pair-code").focus();
      return;
    }

    // Connected!
    host = ip;
    msg("Connected!", "success");
    showRemote(data.device_info);
  } catch (e) {
    msg(e.message, "error");
  }
}

async function finishPairing() {
  const code = document.getElementById("pair-code").value.trim();
  if (!code || !host) return;

  msg("Pairing...");
  try {
    const data = await api("/api/pair/finish", { host, code });
    document.getElementById("pair-dialog").classList.add("hidden");
    document.getElementById("pair-code").value = "";
    msg("Paired and connected!", "success");
    showRemote(data.device_info);
  } catch (e) {
    msg(e.message, "error");
  }
}

/* ── Remote Panel ─────────────────────────────────────────────────────────── */

function showRemote(deviceInfo) {
  document.getElementById("setup-panel").classList.add("hidden");
  document.getElementById("remote-panel").classList.remove("hidden");

  if (deviceInfo) {
    document.getElementById("device-name").textContent =
      `${deviceInfo.manufacturer || ""} ${deviceInfo.model || "TV"}`.trim();
  }

  startStatusPolling();
}

function showSetup() {
  document.getElementById("setup-panel").classList.remove("hidden");
  document.getElementById("remote-panel").classList.add("hidden");
  if (statusInterval) clearInterval(statusInterval);
}

function switchTab(btn) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(t => t.classList.remove("active"));
  btn.classList.add("active");
  document.getElementById(btn.dataset.tab).classList.add("active");
}

/* ── Commands ─────────────────────────────────────────────────────────────── */

async function sendKey(key, direction) {
  if (!host) return;
  const btn = event?.target;
  if (btn) flash(btn);
  try {
    await api("/api/key", { host, key, direction: direction || "SHORT" });
  } catch (e) {
    console.error("Key failed:", e);
  }
}

async function sendText() {
  const input = document.getElementById("text-input");
  const text = input.value;
  if (!text || !host) return;
  try {
    await api("/api/text", { host, text });
    input.value = "";
  } catch (e) {
    console.error("Text failed:", e);
  }
}

async function launchApp(appId) {
  if (!host) return;
  const btn = event?.target;
  if (btn) flash(btn);
  try {
    await api("/api/launch", { host, app: appId });
  } catch (e) {
    console.error("Launch failed:", e);
  }
}

function launchCustomApp() {
  const input = document.getElementById("custom-app");
  const app = input.value.trim();
  if (app) { launchApp(app); input.value = ""; }
}

/* ── Status Polling ──────────────────────────────────────────────────────── */

function startStatusPolling() {
  if (statusInterval) clearInterval(statusInterval);
  updateStatus();
  statusInterval = setInterval(updateStatus, 3000);
}

async function updateStatus() {
  if (!host) return;
  try {
    const s = await api(`/api/state/${host}`);
    const indicator = document.getElementById("power-indicator");
    indicator.className = "indicator " + (s.is_on ? "on" : "off");
    document.getElementById("conn-status").textContent = s.is_on ? "On" : "Standby";
    document.getElementById("current-app").textContent = s.current_app
      ? s.current_app.split(".").pop()
      : "";
    if (s.volume_info) {
      document.getElementById("volume-display").textContent =
        `Vol: ${s.volume_info.level}/${s.volume_info.max}${s.volume_info.muted ? " (Muted)" : ""}`;
    }
  } catch {
    document.getElementById("conn-status").textContent = "Disconnected";
    document.getElementById("power-indicator").className = "indicator off";
  }
}

/* ── Keyboard shortcuts ──────────────────────────────────────────────────── */

document.addEventListener("keydown", (e) => {
  if (document.activeElement?.tagName === "INPUT") return;
  if (!host) return;

  const keyMap = {
    ArrowUp: "DPAD_UP", ArrowDown: "DPAD_DOWN",
    ArrowLeft: "DPAD_LEFT", ArrowRight: "DPAD_RIGHT",
    Enter: "DPAD_CENTER", Backspace: "BACK", Escape: "HOME",
    " ": "MEDIA_PLAY_PAUSE", "m": "VOLUME_MUTE",
    "+": "VOLUME_UP", "=": "VOLUME_UP", "-": "VOLUME_DOWN",
  };

  const key = keyMap[e.key];
  if (key) { e.preventDefault(); sendKey(key); }
});

/* ── Saved Devices ────────────────────────────────────────────────────────── */

async function loadSavedDevices() {
  try {
    const data = await api("/api/devices");
    const container = document.getElementById("devices");
    if (data.devices.length === 0) return;

    container.innerHTML = "";
    data.devices.forEach(d => {
      const div = document.createElement("div");
      div.className = "device-item saved-device";
      div.innerHTML = `<span>${d.name}<br><small>${d.model} &middot; ${d.host}</small></span>
        <div class="device-actions">
          <button onclick="connectToHost('${d.host}')">Connect</button>
          <button onclick="removeDevice('${d.host}')" class="btn-remove" title="Remove">&times;</button>
        </div>`;
      container.appendChild(div);
    });
  } catch (e) {
    console.error("Failed to load saved devices:", e);
  }
}

async function removeDevice(ip) {
  try {
    await api("/api/devices/remove", { host: ip });
    loadSavedDevices();
  } catch (e) {
    console.error("Failed to remove device:", e);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadSavedDevices();
  document.getElementById("text-input")?.addEventListener("keydown", e => {
    if (e.key === "Enter") sendText();
  });
  document.getElementById("pair-code")?.addEventListener("keydown", e => {
    if (e.key === "Enter") finishPairing();
  });
  document.getElementById("manual-host")?.addEventListener("keydown", e => {
    if (e.key === "Enter") connectToHost(e.target.value.trim());
  });
});
