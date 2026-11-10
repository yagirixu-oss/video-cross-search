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
const queryInput = document.querySelector("#query");
const inputError = document.querySelector("#input-error");
const warnings = document.querySelector("#warnings");
const sourceSummary = document.querySelector("#source-summary");
const listLabel = document.querySelector("#list-label");
const listMode = document.querySelector("#list-mode");
const listNotice = document.querySelector("#list-notice");
let activeController = null;
let requestNumber = 0;
let currentQuery = null; // nullは固定一覧。空文字の検索とは区別する。

// サイトごとの成功・失敗を表示する。返されたメッセージもHTMLとして解釈しない。
function showSources(sources) {
  if (!sources) return;
  const labels = { youtube: "YouTube", niconico: "ニコニコ動画" };
  const summaries = [];
  for (const [platform, label] of Object.entries(labels)) {
    const source = sources[platform];
    if (!source) continue;
    summaries.push(label + "：" + (source.status === "ok" ? source.count + "本" :
      source.code === "not_configured" ? "未設定" : "取得できませんでした"));
    if (source.status !== "ok") {
      const message = document.createElement("p");
      message.textContent = label + "：" + (source.message || "検索できませんでした。");
      warnings.append(message);
    }
  }
  sourceSummary.textContent = summaries.join(" / ");
  sourceSummary.hidden = false;
  warnings.hidden = !warnings.childElementCount;
}

// ── 【根幹】依頼 → 成功分を表示 → 失敗分を案内 ──────────
// 検索をやり直した場合、前のリクエストの結果で新しい画面を上書きしない。
async function loadVideos(query = null) {
  currentQuery = query;
  const thisRequest = ++requestNumber;
  if (activeController) activeController.abort();
  const controller = new AbortController();
  activeController = controller;
  grid.replaceChildren();
  grid.setAttribute("aria-busy", "true");
  retry.hidden = true;
  warnings.replaceChildren();
  warnings.hidden = true;
  sourceSummary.hidden = true;
  count.textContent = "—";
  listLabel.textContent = query === null ? "動画一覧" : "「" + query + "」の検索結果";
  listMode.textContent = query === null ? "公開動画・固定の12本" : "各サイト最大6本";
  listNotice.textContent = query === null
    ? "動作確認用に選んだ公開動画です。カードを選ぶと元サイトを開きます。"
    : "各サイトの検索結果を表示しています。カードを選ぶと元サイトを開きます。";
  status.textContent = query === null ? "動画を読み込んでいます…" : "両サイトを検索しています…";
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const url = query === null ? "/api/videos" : "/api/search?" + new URLSearchParams({ q: query });
    const response = await fetch(url, { signal: controller.signal });
    const data = await response.json();
    if (thisRequest !== requestNumber) return;
    // 502にもサイト別の説明があるので、HTTPエラー判定より先に表示する。
    showSources(data.sources);
    if (!response.ok) throw new Error("HTTP " + response.status);
    if (!Array.isArray(data.videos)) throw new Error("一覧の形式が不正です");
    const cards = data.videos.map((video) => {
      if (!video || typeof video.title !== "string" ||
          !["youtube", "niconico"].includes(video.platform)) throw new Error("動画の形式が不正です");
      return createCard(video);
    });
    grid.append(...cards);
    count.textContent = cards.length + "本";
    status.textContent = cards.length ? "" : data.partial
      ? "検索できたサイトでは、該当する動画がありませんでした。"
      : query === null ? "動画がありません。" : "該当する動画がありません。別のキーワードでお試しください。";
    retry.hidden = !Object.values(data.sources || {}).some(source => source.status === "error");
  } catch (error) {
    if (thisRequest !== requestNumber) return;
    status.textContent = error.name === "AbortError"
      ? "応答を待つ時間が長すぎたため中断しました。時間を空けて再試行してください。"
      : query === null ? "動画を取得できませんでした。サーバーの起動状態を確認してください。"
      : "検索結果を取得できませんでした。上の案内や通信状態を確認して再試行してください。";
    retry.hidden = false;
  } finally {
    clearTimeout(timeout);
    if (thisRequest === requestNumber) {
      grid.setAttribute("aria-busy", "false");
      activeController = null;
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
  loadVideos(query);
});
document.querySelector("#reset").addEventListener("click", () => {
  queryInput.value = "";
  inputError.hidden = true;
  loadVideos(null);
});
// 入力欄が編集中でも、再試行するのは最後に実行した検索。
retry.addEventListener("click", () => loadVideos(currentQuery));
loadVideos();
