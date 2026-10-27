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

// ── 【根幹】API取得 → 配列順のカード表示 ────────────────
// 読み込み・空一覧・失敗を分け、失敗時には再試行できる状態に戻す。
async function loadVideos() {
  grid.replaceChildren();
  grid.setAttribute("aria-busy", "true");
  retry.hidden = true;
  count.textContent = "—";
  status.textContent = "動画を読み込んでいます…";
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    // 相対パスなのでlocalhostでも127.0.0.1でも、開いたサーバーに接続する。
    const response = await fetch("/api/videos", { signal: controller.signal });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    if (!Array.isArray(data.videos)) throw new Error("一覧の形式が不正です");
    const cards = data.videos.map((video) => {
      // 途中の不正データで半端なカード一覧を表示しないよう、先に全件組み立てる。
      if (!video || typeof video.title !== "string" ||
          !["youtube", "niconico"].includes(video.platform)) {
        throw new Error("動画の形式が不正です");
      }
      return createCard(video);
    });
    grid.append(...cards);
    count.textContent = cards.length + "本";
    status.textContent = cards.length ? "" : "動画がありません。";
  } catch {
    status.textContent = "動画を取得できませんでした。サーバーの起動状態を確認して、もう一度お試しください。";
    retry.hidden = false;
  } finally {
    clearTimeout(timeout);
    grid.setAttribute("aria-busy", "false");
  }
}

// ── 初回表示と再試行 ───────────────────────────────────
retry.addEventListener("click", loadVideos);
loadVideos();
