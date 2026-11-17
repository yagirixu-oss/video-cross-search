"use strict";

// ── 画面の部品を取得 ──────────────────────────────────
// deferにより、HTMLの読み込みが済んでからこのファイルが実行される。
const grid = document.querySelector("#videos");
const status = document.querySelector("#status");
const retry = document.querySelector("#retry");
const count = document.querySelector("#count");

// ── URLの確認 ─────────────────────────────────────────
// APIからの文字列を無条件にリンクにせず、HTTP(S)だけを利用する。
function safeUrl(value) {
  if (typeof value !== "string" || value.length === 0) return null;
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

// ── カード1枚を組み立てる ──────────────────────────────
// innerHTMLではなくtextContentを使い、動画タイトルをHTMLとして実行しない。
function createCard(video) {
  const article = document.createElement("article");
  article.className = "video-card";
  const videoUrl = safeUrl(video.video_url);
  const content = document.createElement(videoUrl ? "a" : "div");
  content.className = "card-link";
  if (videoUrl) {
    content.href = videoUrl;
    content.target = "_blank";
    content.rel = "noopener noreferrer";
  }

  const thumbnail = document.createElement("div");
  thumbnail.className = "thumbnail";
  const image = document.createElement("img");
  image.alt = ""; // タイトルが直後にあるので、読み上げの重複を避ける。
  image.loading = "lazy";
  const fallback = document.createElement("span");
  fallback.className = "image-fallback";
  fallback.textContent = "画像を表示できません";
  fallback.hidden = true;
  // 画像が壊れてもカードやタイトルは残す。代替画像の再取得ループは起こさない。
  image.addEventListener("error", () => {
    image.hidden = true;
    fallback.hidden = false;
  }, { once: true });
  const imageUrl = safeUrl(video.thumbnail_url);
  if (imageUrl) image.src = imageUrl;
  else { image.hidden = true; fallback.hidden = false; }
  thumbnail.append(image, fallback);

  const meta = document.createElement("div");
  meta.className = "card-meta";
  const source = document.createElement("span");
  source.className = "source " + video.platform;
  source.textContent = video.platform === "youtube" ? "YouTube" : "ニコニコ動画";
  const state = document.createElement("span");
  state.className = "pending";
  state.textContent = videoUrl ? "元サイトで見る ↗" : "準備中";
  meta.append(source, state);
  const title = document.createElement("h3");
  title.textContent = video.title;
  content.append(thumbnail, meta, title);
  article.append(content);
  return article;
}

// ── 検索・固定一覧で共有する状態 ────────────────────────
const searchForm = document.querySelector("#search-form");
const targetInput = document.querySelector("#search-target");
const targetLabels = { all: "両方", youtube: "YouTubeのみ", niconico: "ニコニコのみ" };
let currentTarget = "all"; // 続き・再試行は、選択欄の編集中の値ではなく実行済み条件を使う。
const queryInput = document.querySelector("#query");
const inputError = document.querySelector("#input-error");
const warnings = document.querySelector("#warnings");
const sourceSummary = document.querySelector("#source-summary");
const listLabel = document.querySelector("#list-label");
const listMode = document.querySelector("#list-mode");
const listNotice = document.querySelector("#list-notice");
const more = document.querySelector("#load-more");
const pageStatus = document.querySelector("#page-status");
let activeController = null;
let requestNumber = 0;
let currentQuery = null; // nullは固定一覧。入力欄の編集中の文字列とは分ける。
let nextCursor = null;
let retryCursor = null;
let retryRequest = null;
let sourceStates = {};
const displayed = new Map(); // 動画IDをキーにして、ページをまたぐ重複を防ぐ。

// ── サイト別の累計と案内 ──────────────────────────────
// 今回取得した件数ではなく、画面に残っている動画の合計を表示する。
function showSources() {
  warnings.replaceChildren();
  const labels = { youtube: "YouTube", niconico: "ニコニコ動画" };
  const summaries = [];
  for (const [platform, label] of Object.entries(labels)) {
    const source = sourceStates[platform];
    if (!source || source.selected === false) continue;
    const total = [...displayed.values()].filter(video => video.platform === platform).length;
    summaries.push(label + "：" + total + "本表示中" +
      (source.code === "not_configured" ? "（未設定）" : source.status === "error" ? "（取得失敗）" : ""));
    if (source.status !== "ok" || source.end_reason === "offset_limit") {
      const message = document.createElement("p");
      message.textContent = label + "：" + (source.end_reason === "offset_limit"
        ? "APIで取得できる続きの上限に達しました。検索語を絞ってください。"
        : source.message || "検索できませんでした。");
      warnings.append(message);
    }
  }
  sourceSummary.textContent = summaries.join(" / ");
  sourceSummary.hidden = !summaries.length;
  warnings.hidden = !warnings.childElementCount;
}

// すでにDOMから取り除いたボタンへフォーカスが残らないよう、結果の案内へ移す。
function restorePagingFocus(wasPaging) {
  if (wasPaging && more.hidden && retry.hidden) pageStatus.focus({ preventScroll: true });
}

// ── 【根幹】初回は置き換え、続きは既存カードの下へ追加 ──
// cursorはバックエンドから受け取った文字列をそのまま返す。画面側では分解しない。
async function loadVideos(query = null, cursor = null, append = false, target = "all") {
  // 追加ボタン連打による二重通信を防ぐ。新しい検索は実行中でも切り替えられる。
  if (append && activeController) return;
  const wasPaging = document.activeElement === more || document.activeElement === retry;
  currentQuery = query;
  currentTarget = target;
  const thisRequest = ++requestNumber;
  if (activeController) activeController.abort();
  const controller = new AbortController();
  activeController = controller;
  more.disabled = true;
  retry.disabled = true;
  more.textContent = "読み込み中…";
  grid.setAttribute("aria-busy", "true");
  if (!append) {
    // 検索語を変更したときは、動画・続き位置・サイト別状態をすべてリセットする。
    grid.replaceChildren();
    displayed.clear();
    sourceStates = {};
    nextCursor = null;
    retryCursor = null;
    retryRequest = null;
    warnings.replaceChildren();
    warnings.hidden = true;
    sourceSummary.hidden = true;
    more.hidden = true;
    retry.hidden = true;
    pageStatus.textContent = "";
    count.textContent = "—";
    listLabel.textContent = query === null ? "動画一覧" : "「" + query + "」の検索結果";
    listMode.textContent = query === null ? "公開動画・固定の12本" : targetLabels[target] + " / 1回につき各サイト最大6本";
    listNotice.textContent = query === null
      ? "動作確認用に選んだ公開動画です。カードを選ぶと元サイトを開きます。"
      : "続きは下の「もっと見る」で取得できます。カードを選ぶと元サイトを開きます。";
    status.textContent = query === null ? "動画を読み込んでいます…" : targetLabels[target] + "を検索しています…";
  } else {
    pageStatus.textContent = "動画を追加しています…";
  }
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const params = new URLSearchParams({ q: query, target });
    if (cursor) params.set("cursor", cursor);
    const url = query === null ? "/api/videos" : "/api/search?" + params;
    const response = await fetch(url, { signal: controller.signal });
    const data = await response.json();
    if (thisRequest !== requestNumber) return;
    if (response.status === 410) {
      // 期限切れやサーバー再起動でも、ユーザーが再検索するまでカードは残す。
      nextCursor = null;
      more.hidden = true;
      retryRequest = { query, cursor: null, append: false, target };
      retry.textContent = "同じキーワードで検索し直す";
      retry.hidden = false;
      pageStatus.textContent = "続きの情報が期限切れ、または無効です。検索し直してください。";
      return;
    }
    if (!response.ok && response.status !== 502) throw new Error("HTTP " + response.status);
    if (!Array.isArray(data.videos)) throw new Error("一覧の形式が不正です");
    // すべてのカードを検証してからDOMへ追加し、途中の失敗で半端な表示にしない。
    const additions = new Map();
    for (const video of data.videos) {
      if (!video || typeof video.id !== "string" || typeof video.title !== "string" ||
          !["youtube", "niconico"].includes(video.platform)) throw new Error("動画の形式が不正です");
      if (!displayed.has(video.id)) additions.set(video.id, video);
    }
    const cards = [...additions.values()].map(createCard);
    grid.append(...cards);
    for (const [id, video] of additions) displayed.set(id, video);
    for (const [platform, source] of Object.entries(data.sources || {})) {
      // 失敗サイトだけの再試行では、今回呼んでいないサイトの案内を保持する。
      if (source.requested || !sourceStates[platform]) sourceStates[platform] = source;
    }
    nextCursor = data.next_cursor || null;
    retryCursor = data.retry_cursor || null;
    retryRequest = retryCursor ? { query, cursor: retryCursor, append: true, target } : null;
    retry.textContent = "取得できなかったサイトを再試行";
    retry.hidden = !retryRequest;
    // 全失敗では再試行だけを表示。部分成功なら他方の続きも読み進められる。
    more.hidden = !nextCursor || response.status === 502;
    count.textContent = displayed.size + "本";
    showSources();
    status.textContent = displayed.size ? "" : response.status === 502
      ? "検索結果を取得できませんでした。案内を確認して再試行してください。"
      : nextCursor ? "このページに表示できる動画はありません。続きも確認できます。"
      : data.partial ? "検索できたサイトでは、該当する動画がありませんでした。"
      : query === null ? "動画がありません。" : "該当する動画がありません。別のキーワードでお試しください。";
    pageStatus.textContent = query === null ? "" : response.status === 502
      ? "取得に失敗しました。表示済みの動画はそのまま残しています。"
      : (append ? additions.size + "本追加しました。" : "") +
        (!nextCursor ? "取得できる検索結果はここまでです。" :
          retryCursor ? "一部のサイトを取得できませんでした。再試行できます。" :
            append && !additions.size ? "重複や対象外の動画のみでした。さらに続きを取得できます。" : "");
  } catch (error) {
    if (thisRequest !== requestNumber) return;
    // ネットワーク切断では位置を進めず、失敗したリクエストをそのまま再試行する。
    retryRequest = { query, cursor, append, target };
    retry.textContent = append ? "追加の読み込みを再試行" : "もう一度読み込む";
    retry.hidden = false;
    more.hidden = true;
    const message = error.name === "AbortError"
      ? "応答を待つ時間が長すぎたため中断しました。時間を空けて再試行してください。"
      : "動画を取得できませんでした。サーバーの起動状態や通信を確認して再試行してください。";
    if (append) pageStatus.textContent = message + " 表示済みの動画は残しています。";
    else status.textContent = message;
  } finally {
    clearTimeout(timeout);
    if (thisRequest === requestNumber) {
      grid.setAttribute("aria-busy", "false");
      activeController = null;
      more.disabled = false;
      retry.disabled = false;
      more.textContent = "もっと見る";
      restorePagingFocus(wasPaging);
    }
  }
}

// ── 入力と再試行 ────────────────────────────────────────
// 日本語IMEで変換確定中のEnterを、検索実行と取り違えない。
searchForm.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.isComposing) event.preventDefault();
});
searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query || [...queryInput.value].length > 100 || /[\x00-\x1f\x7f]/.test(query)) {
    inputError.textContent = "検索キーワードを1〜100文字で入力してください。";
    inputError.hidden = false;
    queryInput.focus();
    return;
  }
  inputError.hidden = true;
  loadVideos(query, null, false, targetInput.value);
});
document.querySelector("#reset").addEventListener("click", () => {
  queryInput.value = "";
  targetInput.value = "all";
  inputError.hidden = true;
  loadVideos(null);
});
// 入力欄が編集中でも、再試行するのは最後に実行した検索。
retry.addEventListener("click", () => {
  if (retryRequest) loadVideos(retryRequest.query, retryRequest.cursor, retryRequest.append, retryRequest.target);
});
// 続きの検索を開始するのは、このボタンを押したときだけ。
more.addEventListener("click", () => {
  if (nextCursor) loadVideos(currentQuery, nextCursor, true, currentTarget);
});
loadVideos();
