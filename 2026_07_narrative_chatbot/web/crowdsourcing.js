/* Optional study UI. All completion state and code issuance live on the server. */
"use strict";
(() => {
  if (document.body.dataset.crowdsourcing !== "true") return;
  const byId = id => document.getElementById(id);
  const dialog = byId("storybook-dialog");
  const viewer = byId("storybook-viewer");
  const completion = byId("study-completion");
  const readButton = byId("study-read-button");
  const codePanel = byId("study-code-panel");
  const codeInput = byId("study-code");
  const status = byId("study-completion-status");
  const hint = byId("study-reading-hint");
  const passphrasePanel = byId("study-passphrase-panel");
  const passphrase = byId("study-passphrase");
  const RECEIPT_KEY = "pooh-study-session-id";
  let session = null, progress = {}, revision = null, layout = null;
  let observer = null, sentinel = null, opening = false, reporting = false, confirming = false;
  let generation = 0;
  function storage(value) {
    try {
      if (value === undefined) return sessionStorage.getItem(RECEIPT_KEY);
      if (value === null) sessionStorage.removeItem(RECEIPT_KEY);
      else sessionStorage.setItem(RECEIPT_KEY, value);
    } catch { /* receipt still exists on the server */ }
    return null;
  }
  function headers() {
    let token = new URLSearchParams(location.search).get("token") || "";
    try { token = token || sessionStorage.getItem("pooh-access-token") || ""; } catch {}
    return { "X-Pooh-Token": token, "Content-Type": "application/json" };
  }
  function render() {
    codePanel.hidden = !progress.completion_code;
    codeInput.value = progress.completion_code || "";
    passphrasePanel.hidden = !(progress.completion_code && progress.passphrase);
    passphrase.value = progress.passphrase || "";
    readButton.hidden = Boolean(progress.completion_code);
    hint.hidden = Boolean(progress.completion_code);
    readButton.disabled = confirming || !revision || progress.book_revision !== revision || !progress.last_page_reported_at;
  }
  function disconnect() {
    observer?.disconnect(); observer = null;
    sentinel?.remove(); sentinel = null;
  }
  async function report(event) {
    const requestedSession = session, requestedRevision = revision, requestedGeneration = generation;
    const response = await fetch(`/api/sessions/${encodeURIComponent(requestedSession)}/completion`, {
      method: "POST", headers: headers(),
      body: JSON.stringify({ event, revision: requestedRevision, layout }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "記録できませんでした。もう一度お試しください。");
    if (session !== requestedSession || revision !== requestedRevision || generation !== requestedGeneration) return false;
    progress = body; render();
    return true;
  }
  async function checkLastPage() {
    if (!sentinel || !dialog.open || document.hidden || opening || reporting || progress.completion_code) return;
    if (progress.book_revision !== revision || progress.last_page_reported_at) return;
    const root = viewer.getBoundingClientRect(), end = sentinel.getBoundingClientRect();
    const modal = dialog.getBoundingClientRect();
    if (end.top < Math.max(root.top, modal.top, 0) ||
        end.bottom > Math.min(root.bottom, modal.bottom, window.innerHeight) + 1) return;
    reporting = true;
    try { if (await report("last_page")) status.textContent = "最後のページまで表示しました。読了を確認できます。"; }
    catch (error) { status.textContent = error.message; }
    finally { reporting = false; }
  }
  function observeEnd() {
    disconnect();
    sentinel = document.createElement("div");
    sentinel.className = "study-book-end";
    sentinel.setAttribute("aria-hidden", "true");
    viewer.appendChild(sentinel);
    observer = new IntersectionObserver(checkLastPage, { root: viewer, threshold: 1 });
    observer.observe(sentinel);
    checkLastPage();
  }
  const imageReady = image => new Promise((resolve, reject) => {
    if (image.complete) { image.naturalWidth ? resolve() : reject(new Error("挿絵を読み込めませんでした。絵本を開き直してください。")); return; }
    image.addEventListener("load", resolve, { once: true });
    image.addEventListener("error", () => reject(new Error("挿絵を読み込めませんでした。絵本を開き直してください。")), { once: true });
  });
  const api = {
    get goalReached() { return Boolean(progress.goal_reached); },
    get savedSessionId() { return storage(); },
    setState(state, id) {
      if (session !== id) { api.reset(); session = id; }
      storage(id);
      progress = state || {}; render();
    },
    updateControls({ ended }) {
      byId("study-goal-notice").hidden = ended || !api.goalReached;
    },
    reset() {
      generation++; disconnect();
      session = null; progress = {}; revision = null; layout = null;
      opening = reporting = confirming = false;
      storage(null); completion.hidden = true; status.textContent = ""; render();
    },
    pendingBook() {
      generation++; disconnect(); revision = null; opening = false; reporting = false;
      completion.hidden = false;
      status.textContent = "絵本の準備ができると、読了を確認できます。"; render();
    },
    async bookReady(book, id) {
      if (!book.study_revision || session !== id) return;
      completion.hidden = false;
      if (progress.completion_code) { render(); return; }
      if (revision === book.study_revision) {
        if (opening) return;
        if (sentinel?.isConnected) { checkLastPage(); return; }
      }
      revision = book.study_revision; layout = byId("storybook-layout").value;
      generation++; disconnect(); opening = true; reporting = false; render();
      const expected = generation;
      try {
        await Promise.all([...viewer.querySelectorAll("img")].map(imageReady));
        document.fonts?.ready.then(checkLastPage);
        if (expected !== generation || !dialog.open) return;
        if (await report("book_opened")) {
          opening = false; status.textContent = ""; observeEnd();
        }
      } catch (error) { if (expected === generation) status.textContent = error.message; }
      finally { if (expected === generation) opening = false; }
    },
    async restoreReceipt(id) {
      const expected = generation;
      try {
        const response = await fetch(`/api/sessions/${encodeURIComponent(id)}/completion`, { headers: headers() });
        if (!response.ok) return;
        const body = await response.json();
        if (!body.completion_code || generation !== expected) return;
        api.setState(body, id); completion.hidden = false; render();
        if (!dialog.open) dialog.showModal();
        status.textContent = "保存済みの完了コードです。";
      } catch { /* an expired, incomplete session uses the normal restart UI */ }
    },
  };
  readButton.addEventListener("click", async () => {
    if (readButton.disabled) return;
    confirming = true; render(); status.textContent = "完了コードを取得しています…";
    try { if (await report("read_confirmed")) status.textContent = "完了コードをコピーして回答欄に入力してください。"; }
    catch (error) { status.textContent = error.message; }
    finally { confirming = false; render(); }
  });
  async function copy(text, input, name) {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      status.textContent = `${name}をコピーしました。クラウドソーシングの回答欄に貼り付けてください。`;
    } catch {
      input.focus(); input.select();
      status.textContent = `${name}を選択しました。手動でコピーしてください。`;
    }
  }
  byId("study-copy-button").addEventListener("click", () => copy(progress.completion_code, codeInput, "完了コード"));
  byId("study-passphrase-copy-button").addEventListener("click", () => copy(progress.passphrase, passphrase, "合言葉"));
  viewer.addEventListener("scroll", checkLastPage, { passive: true });
  dialog.addEventListener("scroll", checkLastPage, { passive: true });
  document.addEventListener("visibilitychange", checkLastPage);
  window.addEventListener("resize", checkLastPage);
  window.PoohCrowdsourcing = api;
})();
