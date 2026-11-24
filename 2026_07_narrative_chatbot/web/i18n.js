"use strict";

// English mode (docs/english_mode.md): the page's own words in English when
// the server says <html lang="en">. The Japanese text is the key, so the
// Japanese page is unchanged and a missing entry shows the Japanese.
// t(text, values) also fills {name} placeholders.

const I18N_EN = {
  // index.html
  "詳細": "Details",
  "絵本を見る": "See the picture book",
  "過去ログ": "Past logs",
  "終了": "End",
  "はじめから": "Start over",
  "お話がひと区切りつきました。「終了して絵本を見る」から、今回のお話を絵本で振り返れます。":
    "The story has come to a pause. Press “End and see the picture book” to look back on today's story as a picture book.",
  "プーの3D表示": "Pooh in 3D",
  "立体のクマ。頭・腕・お腹・足をタップすると触れます。ドラッグまたは左右キーで回転、Homeキーで向きを戻せます。":
    "A 3D bear. Tap its head, arms, tummy or feet to touch it. Drag or use the left and right keys to turn it, and the Home key to face it forward again.",
  "はじめる": "Start",
  "🔊 音が出る設定にしてください": "🔊 Please turn your sound on",
  "話しかける・行動を書く": "Say something, or describe what you do",
  "送る": "Send",
  "閉じる": "Close",
  "確認するログ": "Log to view",
  "更新": "Refresh",
  "絵本で見る": "As a picture book",
  "絵本の向き": "Book orientation",
  "横": "Landscape",
  "縦": "Portrait",
  "ポストカード": "Postcards",
  "絵本": "Picture book",
  "体験の完了": "Finishing",
  "最後のページまで進んだら、読了を確認してください。": "When you reach the last page, please confirm that you have read it.",
  "絵本を読み終えた": "I have read the picture book",
  "完了コード": "Completion code",
  "完了コードをコピー": "Copy the completion code",
  "このコードをクラウドソーシングの回答欄に入力してください。": "Please enter this code in the answer field of the crowdsourcing task.",
  "合言葉": "Passphrase",
  "合言葉をコピー": "Copy the passphrase",
  "合言葉も、クラウドソーシングの回答欄に入力してください。": "Please enter the passphrase in the answer field too.",
  // app.js
  "まだお話の途中です。お話がひと区切りつくと、「終了して絵本を見る」を押せるようになります。":
    "The story is still going on. Once it comes to a pause, you can press “End and see the picture book”.",
  "終了して絵本を見る": "End and see the picture book",
  "終了しました。「はじめから」で新しく開始できます。": "The story has ended. Press “Start over” to begin a new one.",
  "接続しています…": "Connecting…",
  "プーが考えています…": "Pooh is thinking…",
  "プー": "Pooh",
  "時刻不明": "Unknown time",
  "参加者: {text}": "Participant: {text}",
  "プー: {text}": "Pooh: {text}",
  "記録の詳細": "Record details",
  "挿絵を読み込めませんでした。絵本を開いている間に再試行します。":
    "The picture could not be loaded. It will be tried again while the book is open.",
  "（さしえ: {name}）": "(picture: {name})",
  "{title} · {count}ページ": "{title} · {count} pages",
  "確認できるログはありません。": "There are no logs to view.",
  "読み込んでいます…": "Loading…",
  "{count}件": "{count} records",
  "読めない行: {count}": "Unreadable lines: {count}",
  "表示上限以降は省略": "Records past the display limit are left out",
  "ログを読み込めませんでした: {message}": "The log could not be loaded: {message}",
  "ログ一覧を取得しています…": "Getting the list of logs…",
  "ログ一覧を取得できませんでした: {message}": "The list of logs could not be loaded: {message}",
  "セッションの有効期限が切れました。「はじめから」で新しく開始できます。":
    "This session has expired. Press “Start over” to begin a new one.",
  "準備しています…": "Getting ready…",
  "サーバに接続できませんでした。": "Could not connect to the server.",
  "開始できませんでした（{status}）。": "Could not start ({status}).",
  "タップの記録には、Webサーバを再起動してから画面を再読み込みしてください。":
    "To record taps, restart the Web server and reload this page.",
  "本当に終了しますか？": "Do you really want to end?",
  "現在のセッションを終了して、はじめから始めますか？": "End this session and start over?",
  "{title} · 挿絵を準備しています（{ready}/{total}）…": "{title} · Preparing the pictures ({ready}/{total})…",
  "絵本を読み込めませんでした: {message}": "The picture book could not be loaded: {message}",
  "ポストカードを準備中…": "Preparing the postcards…",
  "ポストカードを作れませんでした": "The postcards could not be made",
  // crowdsourcing.js
  "お話の途中でも、「終了して絵本を見る」から終えて、今回のお話を絵本で振り返れます。":
    "Even in the middle of the story, you can press “End and see the picture book” to finish and look back on today's story as a picture book.",
  "記録できませんでした。もう一度お試しください。": "This could not be recorded. Please try again.",
  "最後のページまで表示しました。読了を確認できます。": "You have reached the last page. You can now confirm that you have read it.",
  "挿絵を読み込めませんでした。絵本を開き直してください。": "A picture could not be loaded. Please open the picture book again.",
  "絵本の準備ができると、読了を確認できます。": "Once the picture book is ready, you can confirm that you have read it.",
  "保存済みの完了コードです。": "This is your saved completion code.",
  "完了コードを取得しています…": "Getting the completion code…",
  "完了コードをコピーして回答欄に入力してください。": "Please copy the completion code and enter it in the answer field.",
  "{name}をコピーしました。クラウドソーシングの回答欄に貼り付けてください。":
    "The {name} has been copied. Please paste it in the answer field of the crowdsourcing task.",
  "{name}を選択しました。手動でコピーしてください。": "The {name} is selected. Please copy it yourself.",
  // bear.js
  "クマを表示できませんでした。会話は続けられます。": "The bear could not be shown. You can go on talking.",
  "表示エラーの詳細": "Details of the display error",
  "クマの3D表示を開始できませんでした。": "The 3D bear could not be started.",
  "クマの描画プログラムを開始できませんでした。": "The bear's drawing program could not be started.",
  "クマの3D表示が中断しました。": "The 3D bear stopped.",
};

const PAGE_IN_ENGLISH = document.documentElement.lang === "en";

function t(text, values) {
  let result = (PAGE_IN_ENGLISH && I18N_EN[text]) || text;
  if (values) {
    for (const [name, value] of Object.entries(values)) result = result.split(`{${name}}`).join(String(value));
  }
  return result;
}

// The page's own text and the attributes a reader sees or hears.
function translatePage(root) {
  if (!PAGE_IN_ENGLISH) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.nodeValue.trim();
    if (text && I18N_EN[text]) node.nodeValue = node.nodeValue.replace(text, I18N_EN[text]);
  }
  for (const element of root.querySelectorAll("[placeholder], [aria-label], [title], [alt]")) {
    for (const name of ["placeholder", "aria-label", "title", "alt"]) {
      const value = element.getAttribute(name);
      if (value && I18N_EN[value]) element.setAttribute(name, I18N_EN[value]);
    }
  }
}

translatePage(document.body);
