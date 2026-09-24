"use strict";

// One narrative session per browser tab; the server owns all story state.
const SESSION_KEY = "pooh-session-id";
const TOKEN_KEY = "pooh-access-token";
const MAX_RECONNECT_DELAY_MS = 10000;

const log = document.getElementById("log");
const statusLine = document.getElementById("status");
const form = document.getElementById("input-form");
const inputText = document.getElementById("input-text");
const sendButton = document.getElementById("send-button");
const endButton = document.getElementById("end-button");
const restartButton = document.getElementById("restart-button");
const showDetails = document.getElementById("show-details");

let socket = null;
let sessionId = null;
let ended = false;
let busy = false;
let reconnectDelay = 1000;
let reconnectTimer = null;

function storageGet(key) {
  try { return sessionStorage.getItem(key); } catch { return null; }
}

function storageSet(key, value) {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch { /* storage may be unavailable; the tab still works until reload */ }
}

function accessToken() {
  const fromUrl = new URLSearchParams(location.search).get("token");
  if (fromUrl) storageSet(TOKEN_KEY, fromUrl);
  return fromUrl || storageGet(TOKEN_KEY) || "";
}

function updateControls() {
  const connected = socket !== null && socket.readyState === WebSocket.OPEN;
  const canType = connected && !ended && !busy;
  inputText.disabled = !canType;
  sendButton.disabled = !canType;
  endButton.disabled = !connected || ended || busy;
  if (ended) statusLine.textContent = "終了しました。「はじめから」で新しく開始できます。";
  else if (!connected) statusLine.textContent = "接続しています…";
  else if (busy) statusLine.textContent = "プーが考えています…";
  else statusLine.textContent = "";
  if (canType) inputText.focus();
}

const sceneIntro = document.getElementById("scene-intro-template");

function clearLog() {
  log.replaceChildren();
  if (sceneIntro && sceneIntro.content.textContent.trim()) {
    log.appendChild(sceneIntro.content.cloneNode(true));
  }
}

function scrollToBottom() {
  log.scrollTop = log.scrollHeight;
}

function appendNotice(text, isError = false) {
  const node = document.createElement("div");
  node.className = isError ? "notice error" : "notice";
  node.textContent = text;
  log.appendChild(node);
  scrollToBottom();
}

function appendUser(text) {
  const node = document.createElement("div");
  node.className = "message user";
  node.textContent = text;
  log.appendChild(node);
  scrollToBottom();
}

function detailsText(output) {
  const lines = [
    `応答モード: ${output.interaction_mode}`,
    `参考場面: ${output.scene_label || "該当なし"}`,
    `物語アクション: ${(output.narrative_actions || []).join(", ") || "なし"}`,
  ];
  if (output.performance_cue) lines.push(`演出: ${output.performance_cue}`);
  if (output.generation_fallback) lines.push("生成失敗のため固定文を使用");
  lines.push(`場面の変化:\n${output.situation_diff}`);
  return lines.join("\n");
}

function appendOutput(output) {
  const node = document.createElement("div");
  node.className = "message pooh";
  const speaker = document.createElement("span");
  speaker.className = "speaker";
  speaker.textContent = "プー";
  const body = document.createElement("div");
  body.textContent = output.bot_response;
  const details = document.createElement("div");
  details.className = "details";
  details.textContent = detailsText(output);
  node.append(speaker, body, details);
  log.appendChild(node);
  scrollToBottom();
}

function renderMessage(message) {
  if (message.type === "output") appendOutput(message.output);
  else if (message.type === "user_input") appendUser(message.text);
}

function handleMessage(message) {
  switch (message.type) {
    case "transcript":
      clearLog();
      message.messages.forEach(renderMessage);
      ended = message.ended;
      busy = message.busy;
      break;
    case "output":
    case "user_input":
      renderMessage(message);
      break;
    case "status":
      busy = message.busy;
      break;
    case "ended":
      ended = true;
      break;
    case "error":
      appendNotice(message.message, true);
      break;
    case "expired":
      storageSet(SESSION_KEY, null);
      sessionId = null;
      ended = true;
      appendNotice("セッションの有効期限が切れました。「はじめから」で新しく開始できます。");
      break;
  }
  updateControls();
}

function connect() {
  clearTimeout(reconnectTimer);
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const token = encodeURIComponent(accessToken());
  const ws = new WebSocket(`${protocol}//${location.host}/ws/${sessionId}?token=${token}`);
  socket = ws;
  ws.onopen = () => {
    reconnectDelay = 1000;
    updateControls();
  };
  ws.onmessage = (event) => handleMessage(JSON.parse(event.data));
  ws.onclose = () => {
    if (socket !== ws) return;
    socket = null;
    updateControls();
    if (sessionId !== null) {
      reconnectTimer = setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY_MS);
    }
  };
}

async function startNewSession() {
  clearTimeout(reconnectTimer);
  if (socket !== null) {
    const old = socket;
    socket = null;
    old.close();
  }
  clearLog();
  ended = false;
  busy = false;
  sessionId = null;
  storageSet(SESSION_KEY, null);
  statusLine.textContent = "準備しています…";
  let response;
  try {
    response = await fetch("/api/sessions", {
      method: "POST",
      headers: { "X-Pooh-Token": accessToken() },
    });
  } catch {
    appendNotice("サーバに接続できませんでした。", true);
    statusLine.textContent = "";
    return;
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    appendNotice(body.error || `開始できませんでした（${response.status}）。`, true);
    statusLine.textContent = "";
    return;
  }
  sessionId = body.session_id;
  storageSet(SESSION_KEY, sessionId);
  connect();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = inputText.value.trim();
  if (!text || socket === null || busy || ended) return;
  socket.send(JSON.stringify({ type: "user_input", text }));
  inputText.value = "";
});

endButton.addEventListener("click", () => {
  if (socket !== null && !ended && confirm("本当に終了しますか？")) {
    socket.send(JSON.stringify({ type: "close" }));
  }
});

async function abandonSession(id) {
  // Frees the server slot; an unfinished story plays its ending once (ROS too).
  try {
    await fetch(`/api/sessions/${id}`, {
      method: "DELETE",
      headers: { "X-Pooh-Token": accessToken() },
    });
  } catch { /* the server discards it at TTL anyway */ }
}

restartButton.addEventListener("click", async () => {
  if (!ended && sessionId !== null && !confirm("現在のセッションを終了して、はじめから始めますか？")) return;
  const previous = sessionId;
  sessionId = null;
  if (previous !== null) await abandonSession(previous);
  startNewSession();
});

showDetails.addEventListener("change", () => {
  document.body.classList.toggle("show-details", showDetails.checked);
});

sessionId = storageGet(SESSION_KEY);
if (sessionId) connect();
else startNewSession();
