"use strict";

// ── 履歴管理：このブラウザ専用のキーと、明示的な操作 ─────
// 【根幹】履歴をURL・外部API・共通の検索キャッシュに入れない。
const HistoryUI = (() => {
  const message = document.querySelector("#history-message");
  const list = document.querySelector("#history-list");
  const recordInput = document.querySelector("#record-history");
  const openInput = document.querySelector("#record-open-history");
  const openMessage = document.querySelector("#open-history-message");
  const personalizeInput = document.querySelector("#personalize-search");
  const dialog = document.querySelector("#player-dialog");
  const playerMessage = document.querySelector("#player-message");
  let player = null;
  let activePlayback = null;
  let apiPromise = null;
  let playbackNumber = 0;
  let historyNumber = 0;
  const opening = new Set(); // 保存中の同じカードの連打は、画面側でも重複送信しない。

  // 設定保存に失敗しても、通常の検索は使えるよう例外を閉じ込める。
  function saved(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  }

  // 他端末への同期は行わない。記録先の識別子を失うと元の履歴には接続できない。
  function save(key, value) {
    localStorage.setItem(key, value);
  }

  // 本人識別用の推測困難な値を作る。タイトルや履歴そのものはブラウザに複製しない。
  function headers(create = false) {
    let token = saved("vcs-history-token");
    if (!/^[a-f0-9]{64}$/.test(token || "")) {
      if (!create) return {};
      const bytes = crypto.getRandomValues(new Uint8Array(32));
      token = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
      save("vcs-history-token", token);
    }
    return { "X-History-Token": token };
  }

  // 履歴操作の共通通信。生の例外や識別キーを画面へ表示しない。
  async function request(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(path, {
        ...options, signal: controller.signal,
        headers: { ...headers(true), "Content-Type": "application/json", ...options.headers }
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "入力内容を確認してください。");
      return data;
    } catch (error) {
      if (error.name === "AbortError" || error instanceof TypeError) {
        throw new Error("履歴の通信に失敗しました。サーバーと接続を確認してください。");
      }
      throw error;
    } finally { clearTimeout(timeout); }
  }

  // 保存した履歴を文字列として描画する。TakeoutのHTMLを実行しない。
  async function refresh() {
    const sequence = ++historyNumber;
    if (!saved("vcs-history-token")) return;
    try {
      const data = await request("/api/history");
      if (sequence !== historyNumber) return;
      document.querySelector("#history-total").textContent = data.total + "件";
      list.replaceChildren();
      for (const event of data.events) {
        const row = document.createElement("li");
        const title = document.createElement("span");
        title.textContent = event.title;
        const detail = document.createElement("small");
        const sources = {
          takeout: "YouTubeから取込", player: "アプリ内で10秒以上再生",
          manual: "本人が視聴済みと記録", click: "元サイトを開いた（再生は未確認）"
        };
        detail.textContent = new Date(event.watched_at).toLocaleString("ja-JP") + " · " + sources[event.source];
        row.append(title, detail);
        list.append(row);
      }
    } catch (error) { message.textContent = error.message; }
  }

  // ── Takeout取込と削除 ─────────────────────────────────
  // ファイルを選ぶだけでは送信せず、取込ボタンを押したときだけサーバーへ送る。
  async function importHistory() {
    const file = document.querySelector("#history-file").files[0];
    if (!file) { message.textContent = "視聴履歴のJSONファイルを選んでください。"; return; }
    if (file.size > 10 * 1024 * 1024) { message.textContent = "ファイルは10MB以内にしてください。"; return; }
    const button = document.querySelector("#history-import");
    button.disabled = true;
    message.textContent = "履歴を取り込んでいます…";
    try {
      const data = await request("/api/history/import", { method: "POST", body: await file.text() });
      message.textContent = data.added + "件追加、" + data.duplicates + "件重複、" + data.skipped +
        "件対象外。最新5000件まで保存します。検索への反映は「履歴を検索に反映」をオンにして検索してください。";
      document.querySelector("#history-file").value = "";
      await refresh();
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; }
  }

  // 削除はアプリ内の保存分だけ。再生途中の保存も止め、削除直後に履歴を復活させない。
  async function clearHistory() {
    const button = document.querySelector("#history-delete");
    button.disabled = true;
    stopPlayback();
    try {
      await request("/api/history", { method: "DELETE" });
      recordInput.checked = false;
      openInput.checked = false;
      personalizeInput.checked = false;
      save("vcs-history-record", "false");
      save("vcs-history-open", "false");
      save("vcs-history-personalize", "false");
      openMessage.hidden = true;
      message.textContent = "このアプリの履歴を削除しました。YouTube本体の履歴は変更していません。表示中の検索結果は、再検索すると更新されます。";
      document.querySelector("#history-delete-confirm").checked = false;
      await refresh();
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = !document.querySelector("#history-delete-confirm").checked; }
  }

  // ── カードクリック：リンクの標準動作を保ちながら記録 ──
  function showOpenMessage(text) {
    // 履歴パネルが閉じていても、保存の成功・失敗を一覧の近くで確認できる。
    openMessage.textContent = text;
    openMessage.hidden = false;
  }

  async function recordOpen(video) {
    // 【根幹】クリックの記録と、10秒再生した記録は区別する。秒数を捏造しない。
    if (!openInput.checked || opening.has(video.id)) return;
    opening.add(video.id);
    try {
      const data = await request("/api/history/open", {
        method: "POST", keepalive: true,
        body: JSON.stringify({
          video_id: video.id, title: video.title.slice(0, 500),
          author_id: (video.author_id || "").slice(0, 200),
          author_name: (video.author_name || "").slice(0, 200)
        })
      });
      showOpenMessage(data.added
        ? "元サイトを開いた履歴を保存しました。再生は未確認です。履歴反映がオンなら、次の検索で利用します。"
        : "同じ動画を60秒以内に開いた履歴は、1件にまとめています。");
      await refresh();
    } catch (error) {
      // 保存が失敗してもリンク遷移を妨げない。成功したようには表示しない。
      showOpenMessage("元サイトを開いた履歴を保存できませんでした。" + error.message);
    } finally {
      opening.delete(video.id);
    }
  }

  function attachCardLink(link, video) {
    // 左クリック・Enterはclick、中クリックはauxclick。右クリックだけでは記録しない。
    // preventDefaultを呼ばないため、別タブ・Ctrlクリックなどの標準操作を維持できる。
    link.addEventListener("click", event => {
      if (!event.defaultPrevented && event.button === 0) void recordOpen(video);
    });
    link.addEventListener("auxclick", event => {
      if (!event.defaultPrevented && event.button === 1) void recordOpen(video);
    });
  }

  // ── YouTubeの公式プレイヤーを必要なときだけ読み込む ───
  function youtubeAPI() {
    if (window.YT && window.YT.Player) return Promise.resolve();
    if (apiPromise) return apiPromise;
    apiPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      let timer = setTimeout(() => {
        apiPromise = null;
        script.remove();
        reject(new Error("YouTubeプレイヤーを読み込めませんでした。元サイトで視聴できます。"));
      }, 12000);
      window.onYouTubeIframeAPIReady = () => { clearTimeout(timer); resolve(); };
      script.src = "https://www.youtube.com/iframe_api";
      script.onerror = () => {
        clearTimeout(timer); apiPromise = null; script.remove();
        reject(new Error("YouTubeプレイヤーへ接続できませんでした。"));
      };
      document.head.append(script);
    });
    return apiPromise;
  }

  // 閉じたプレイヤーの遅延応答・タイマーが、別の動画の履歴として動かないよう無効化する。
  function stopPlayback() {
    playbackNumber += 1;
    if (activePlayback) clearInterval(activePlayback.timer);
    activePlayback = null;
    if (player) { player.destroy(); player = null; }
    document.querySelector("#player-host").replaceChildren();
  }

  // 【根幹】再生状態・経過時刻・再生位置を組み合わせ、停止・シーク・非表示時間を数えない。
  async function playbackTick(state) {
    if (activePlayback !== state || !player) return;
    const now = performance.now();
    const position = player.getCurrentTime();
    const playing = player.getPlayerState() === 1;
    const elapsed = (now - state.tick) / 1000;
    const advanced = position - state.position;
    if (recordInput.checked && state.allowed && playing && state.playing && !document.hidden &&
        elapsed > 0 && elapsed < 2.5 && advanced > 0 && advanced < elapsed * 2.5 + 0.5) {
      state.seconds += Math.min(elapsed, advanced);
    }
    state.tick = now; state.position = position; state.playing = playing;
    if (state.seconds < 10 || state.saved || state.saving || !recordInput.checked || !state.permit) return;
    state.saving = true;
    try {
      await request("/api/history/playback", { method: "POST", body: JSON.stringify({ permit: state.permit, seconds: state.seconds }) });
      if (activePlayback !== state) return;
      state.saved = true;
      playerMessage.textContent = "10秒以上の再生を履歴に記録しました。次の検索から利用できます。";
      await refresh();
    } catch (error) {
      // 自動リトライを繰り返さない。開き直すことで再試行できる。
      state.saved = true;
      if (activePlayback === state) playerMessage.textContent = "履歴を保存できませんでした。" + error.message;
    } finally { state.saving = false; }
  }

  // 先に埋込可否を確認する。子供向け指定や状態不明の動画では自動記録をしない。
  async function openPlayer(video) {
    stopPlayback();
    const sequence = playbackNumber;
    const videoId = video.id.replace(/^youtube:/, "");
    if (!/^[A-Za-z0-9_-]{11}$/.test(videoId)) return;
    document.querySelector("#player-title").textContent = video.title;
    document.querySelector("#player-original").href = "https://www.youtube.com/watch?v=" + videoId;
    playerMessage.textContent = "再生情報を確認しています…";
    dialog.showModal();
    try {
      const metadata = await request("/api/playback/youtube/" + videoId);
      if (sequence !== playbackNumber) return;
      if (!metadata.embeddable) { playerMessage.textContent = "この動画は埋め込み再生できません。元サイトで視聴してください。"; return; }
      await youtubeAPI();
      if (sequence !== playbackNumber) return;
      const target = document.createElement("div");
      document.querySelector("#player-host").append(target);
      const state = { allowed: metadata.tracking_allowed, permit: metadata.permit, seconds: 0, position: 0,
        tick: performance.now(), playing: false, saved: false, saving: false, timer: null };
      activePlayback = state;
      playerMessage.textContent = metadata.notice || (recordInput.checked
        ? "再生ボタンを押すと開始します。10秒以上の再生で履歴に記録します。"
        : "履歴の記録はオフです。再生しても保存しません。");
      player = new YT.Player(target, {
        videoId, width: "100%", height: "100%",
        playerVars: { autoplay: 0, playsinline: 1, origin: window.location.origin },
        events: {
          onReady: () => {
            if (activePlayback === state) state.timer = setInterval(() => playbackTick(state), 1000);
          },
          onStateChange: event => {
            // バッファリングから復帰した直後も、停止時間は再生秒数に加えない。
            if (event.data !== 1) state.playing = false;
          },
          onError: () => {
            state.allowed = false;
            clearInterval(state.timer);
            if (activePlayback === state) playerMessage.textContent = "この動画を再生できません。元サイトで視聴してください。履歴には記録しません。";
          }
        }
      });
    } catch (error) { if (sequence === playbackNumber) playerMessage.textContent = error.message; }
  }

  // ニコニコは実際に視聴したことを本人が指定する。外部リンクのクリックでは呼ばない。
  async function manualWatch(video, button) {
    button.disabled = true;
    try {
      await request("/api/history/manual", { method: "POST", body: JSON.stringify({
        video_id: video.id, title: video.title.slice(0, 500),
        author_id: video.author_id || "", author_name: video.author_name || ""
      }) });
      button.textContent = "視聴済みを記録しました";
      message.textContent = "ニコニコ動画の視聴済みを記録しました。次の検索から利用できます。";
      await refresh();
    } catch (error) { message.textContent = error.message; button.disabled = false; }
  }

  // 動画リンクとは別に操作を置く。ボタンを押して意図せず元サイトへ移動することを防ぐ。
  function actions(video) {
    const row = document.createElement("div");
    row.className = "card-actions";
    if (!video.video_url) return row;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = video.platform === "youtube" ? "アプリ内で再生" : "見たと記録";
    button.setAttribute("aria-label", button.textContent + "：" + video.title);
    button.addEventListener("click", () => video.platform === "youtube" ? openPlayer(video) : manualWatch(video, button));
    row.append(button);
    return row;
  }

  // ── 設定と画面の初期化 ────────────────────────────────
  function initialize() {
    recordInput.checked = saved("vcs-history-record") === "true";
    // 新しいクリック記録は初期値オン。オフにした選択は再読込後も維持する。
    openInput.checked = saved("vcs-history-open") !== "false";
    personalizeInput.checked = saved("vcs-history-personalize") === "true";
    for (const [input, key] of [[recordInput, "vcs-history-record"], [openInput, "vcs-history-open"],
                              [personalizeInput, "vcs-history-personalize"]]) {
      input.addEventListener("change", () => {
        try {
          if (input.checked) headers(true);
          save(key, String(input.checked));
          // オフの間の経過時間を、再びオンにした直後に加算しない。
          if (activePlayback && input !== openInput) { activePlayback.playing = false; activePlayback.seconds = 0; }
        } catch {
          input.checked = false;
          message.textContent = "ブラウザの保存が使えません。サイトの保存設定を確認してください。";
        }
      });
    }
    document.querySelector("#history-import").addEventListener("click", importHistory);
    document.querySelector("#history-refresh").addEventListener("click", refresh);
    document.querySelector("#history-delete-confirm").addEventListener("change", event => {
      document.querySelector("#history-delete").disabled = !event.target.checked;
    });
    document.querySelector("#history-delete").addEventListener("click", clearHistory);
    document.querySelector("#player-close").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", stopPlayback);
    refresh();
  }

  return { initialize, actions, attachCardLink, headers, enabled: () => personalizeInput.checked };
})();
