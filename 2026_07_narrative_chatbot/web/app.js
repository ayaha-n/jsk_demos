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
const logButton = document.getElementById("log-button");
const logDialog = document.getElementById("log-dialog");
const logCloseButton = document.getElementById("log-close-button");
const logRefreshButton = document.getElementById("log-refresh-button");
const logSelect = document.getElementById("log-select");
const logViewerStatus = document.getElementById("log-viewer-status");
const logViewer = document.getElementById("log-viewer");

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

function authenticatedHeaders() {
  return { "X-Pooh-Token": accessToken() };
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

function formatLogTime(value) {
  if (!value) return "時刻不明";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("ja-JP");
}

function appendLogRecord(record, index) {
  const item = document.createElement("article");
  item.className = "log-record";

  const meta = document.createElement("div");
  meta.className = "log-record-meta";
  meta.textContent = `Turn ${index + 1} · ${formatLogTime(record.timestamp)}`;
  item.appendChild(meta);

  if (record.user_action) {
    const user = document.createElement("div");
    user.className = "log-record-user";
    user.textContent = `参加者: ${record.user_action}`;
    item.appendChild(user);
  }

  const pooh = document.createElement("div");
  pooh.className = "log-record-pooh";
  pooh.textContent = `プー: ${record.bot_response || ""}`;
  item.appendChild(pooh);

  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "記録の詳細";
  const body = document.createElement("pre");
  body.textContent = JSON.stringify({
    source: record.source,
    world_event: record.world_event,
    interaction_mode: record.interaction_mode,
    scene_id: record.scene_id,
    narrative_actions: record.narrative_actions || [],
    latency_ms: record.latency_ms,
    updated_situation: record.updated_situation,
  }, null, 2);
  details.append(summary, body);
  item.appendChild(details);
  logViewer.appendChild(item);
}

async function loadSelectedLog() {
  const name = logSelect.value;
  if (!name) {
    logViewer.replaceChildren();
    logViewerStatus.textContent = "確認できるログはありません。";
    return;
  }
  logViewerStatus.textContent = "読み込んでいます…";
  logViewer.replaceChildren();
  try {
    const response = await fetch(`/api/logs/${encodeURIComponent(name)}`, {
      headers: authenticatedHeaders(),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    body.records.forEach(appendLogRecord);
    const notes = [`${body.records.length}件`];
    if (body.invalid_lines) notes.push(`読めない行: ${body.invalid_lines}`);
    if (body.truncated) notes.push("表示上限以降は省略");
    logViewerStatus.textContent = notes.join(" / ");
  } catch (error) {
    logViewerStatus.textContent = `ログを読み込めませんでした: ${error.message}`;
  }
}

async function refreshLogList() {
  logViewerStatus.textContent = "ログ一覧を取得しています…";
  try {
    const response = await fetch("/api/logs", { headers: authenticatedHeaders() });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    const previous = logSelect.value;
    logSelect.replaceChildren();
    for (const item of body.logs) {
      const option = document.createElement("option");
      option.value = item.name;
      option.textContent = `${formatLogTime(item.modified_at * 1000)} · ${item.name}`;
      logSelect.appendChild(option);
    }
    if ([...logSelect.options].some((option) => option.value === previous)) {
      logSelect.value = previous;
    }
    await loadSelectedLog();
  } catch (error) {
    logSelect.replaceChildren();
    logViewer.replaceChildren();
    logViewerStatus.textContent = `ログ一覧を取得できませんでした: ${error.message}`;
  }
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
      headers: authenticatedHeaders(),
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
      headers: authenticatedHeaders(),
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

logButton.addEventListener("click", () => {
  logDialog.showModal();
  refreshLogList();
});

logCloseButton.addEventListener("click", () => logDialog.close());
logRefreshButton.addEventListener("click", refreshLogList);
logSelect.addEventListener("change", loadSelectedLog);

sessionId = storageGet(SESSION_KEY);
if (sessionId) connect();
else startNewSession();
