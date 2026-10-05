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
const logStorybookToggle = document.getElementById("log-storybook");
const logStorybookLayout = document.getElementById("log-storybook-layout");
const logStorybookPdf = document.getElementById("log-storybook-pdf");
const storybookButton = document.getElementById("storybook-button");
const storybookDialog = document.getElementById("storybook-dialog");
const storybookCloseButton = document.getElementById("storybook-close-button");
const storybookLayout = document.getElementById("storybook-layout");
const storybookPdf = document.getElementById("storybook-pdf");
const storybookStatus = document.getElementById("storybook-status");
const storybookViewer = document.getElementById("storybook-viewer");
// Yomogi's ascender + descender in ems (storybook.YOMOGI_HEIGHT_EM): the
// tablet's text px is that whole height.
const YOMOGI_HEIGHT_EM = 1.448;

let socket = null;
let sessionId = null;
let ended = false;
let endedSessionId = null;  // the session whose book 絵本を見る opens
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

// Body parts named in config/fixed_utterances.json motions.
const MOTION_PART_LABELS = { eyebrow: "眉", arm: "腕", neck: "首", nose: "鼻" };

function detailsText(output) {
  const lines = [
    `応答モード: ${output.interaction_mode}`,
    `参考場面: ${output.scene_label || "該当なし"}`,
    `物語アクション: ${(output.narrative_actions || []).join(", ") || "なし"}`,
  ];
  const motion = Object.entries(output.motion || {})
    .filter(([, name]) => name)
    .map(([part, name]) => `${MOTION_PART_LABELS[part] || part} ${name}`);
  if (motion.length) lines.push(`動作: ${motion.join("・")}`);
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
  const label = record.source === "session_close" ? "終了" : `Turn ${index + 1}`;
  meta.textContent = `${label} · ${formatLogTime(record.timestamp)}`;
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

function appendStoryPage(page, book, container = logViewer) {
  // Laid out at the tablet's own coordinates, scaled to the sheet's width,
  // so columns and line breaks read as they did on the e-ink page.
  const [width, height] = book.size;
  const percent = (value, whole) => `${(value / whole) * 100}%`;
  // Font sizes in units of the sheet's width (container query units).
  const scaled = (px) => `${(px / width) * 100}cqw`;

  const sheet = document.createElement("article");
  sheet.className = "story-page";
  sheet.style.aspectRatio = `${width} / ${height}`;
  if (page.illustration) {
    const [x, y, w, h] = book.picture_box;
    const picture = document.createElement("div");
    picture.className = "story-picture";
    Object.assign(picture.style, {
      left: percent(x, width), top: percent(y, height),
      width: percent(w, width), height: percent(h, height),
    });
    if (page.illustration_url) {
      const image = document.createElement("img");
      image.src = page.illustration_url;
      image.alt = "";
      picture.appendChild(image);
    } else {
      picture.textContent = `（さしえ: ${page.illustration}）`;
    }
    sheet.appendChild(picture);
  }
  for (const entry of page.entries) {
    const paragraph = document.createElement("div");
    paragraph.className = `story-entry story-${entry.style}`;
    Object.assign(paragraph.style, {
      left: percent(entry.x, width), top: percent(entry.y, height),
      width: percent(entry.w, width),
      fontSize: scaled(entry.px / YOMOGI_HEIGHT_EM),
      lineHeight: scaled(Math.floor(entry.px * 1.5)),
      textAlign: entry.align === "center" ? "center" : "left",
    });
    // The lines as the tablet wrote them.
    for (const line of entry.lines) {
      const row = document.createElement("div");
      row.textContent = line;
      paragraph.appendChild(row);
    }
    sheet.appendChild(paragraph);
  }
  const number = document.createElement("div");
  number.className = "story-page-number";
  number.textContent = String(page.index + 1);
  sheet.appendChild(number);
  container.appendChild(sheet);
}

async function loadSelectedStorybook(name) {
  const base = `/api/logs/${encodeURIComponent(name)}/storybook`;
  const layout = encodeURIComponent(logStorybookLayout.value);
  const token = encodeURIComponent(accessToken());
  // The PDF in the orientation shown here.
  logStorybookPdf.href = `${base}.pdf?token=${token}&layout=${layout}`;
  logStorybookPdf.hidden = false;
  const response = await fetch(`${base}?layout=${layout}`, {
    headers: authenticatedHeaders(),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  body.pages.forEach((page) => appendStoryPage(page, body));
  logViewerStatus.textContent = `${body.title || "絵本"} · ${body.pages.length}ページ`;
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
  logStorybookPdf.hidden = true;
  try {
    if (logStorybookToggle.checked) {
      await loadSelectedStorybook(name);
      return;
    }
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
      if (ended) {
        endedSessionId = sessionId;
        storybookButton.hidden = false;
      }
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
      endedSessionId = sessionId;
      storybookButton.hidden = false;
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
  storybookButton.hidden = true;
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

// Right after a session ends: its own book, from wherever the browser is.
async function loadOwnStorybook() {
  if (!endedSessionId) return;
  const base = `/api/sessions/${encodeURIComponent(endedSessionId)}/storybook`;
  const token = encodeURIComponent(accessToken());
  const layout = encodeURIComponent(storybookLayout.value);
  // The PDF in the orientation shown here.
  storybookPdf.href = `${base}.pdf?token=${token}&layout=${layout}`;
  storybookStatus.textContent = "読み込んでいます…";
  storybookViewer.replaceChildren();
  try {
    const response = await fetch(`${base}?layout=${layout}`, { headers: authenticatedHeaders() });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    body.pages.forEach((page) => appendStoryPage(page, body, storybookViewer));
    storybookStatus.textContent = `${body.title || "絵本"} · ${body.pages.length}ページ`;
  } catch (error) {
    storybookStatus.textContent = `絵本を読み込めませんでした: ${error.message}`;
  }
}

storybookButton.addEventListener("click", () => {
  storybookDialog.showModal();
  loadOwnStorybook();
});
storybookCloseButton.addEventListener("click", () => storybookDialog.close());
storybookLayout.addEventListener("change", loadOwnStorybook);

logCloseButton.addEventListener("click", () => logDialog.close());
logRefreshButton.addEventListener("click", refreshLogList);
logSelect.addEventListener("change", loadSelectedLog);
logStorybookToggle.addEventListener("change", loadSelectedLog);
logStorybookLayout.addEventListener("change", loadSelectedLog);

sessionId = storageGet(SESSION_KEY);
if (sessionId) connect();
else startNewSession();
