// 方式X の拡張機能側: background.js から届いた音量を、ページ側の media-guard.js に渡す。
// 音量をかけるのは media-guard.js (ページの volume を横取りしないと、サイトが入れ直した時に一瞬元の音量で鳴る)。
// どのページにも読み込みの最初から入り、このタブにかける音量を拡張機能に聞く
(() => {
  if (window.__deejTabVol) return;
  window.__deejTabVol = true;

  // null を渡すと元の音量に戻して制御をやめる
  const send = (volume) => {
    if (typeof volume === 'number') watch();
    document.dispatchEvent(new CustomEvent('__deej_tab_volume', { detail: volume }));
  };
  // true: 鳴り始めたら、返事が来るまで音を出さずに待たせる (自動で割り当てるタブ)
  const arm = (on) => document.dispatchEvent(new CustomEvent('__deej_tab_arm', { detail: on }));

  const onMessage = (msg) => {
    if (msg.type === 'deej-volume') send(msg.volume);
    else if (msg.type === 'deej-reset') send(null);
  };
  chrome.runtime.onMessage.addListener(onMessage);

  // 拡張機能に、このタブに今かける音量を聞く (読み込み直後と、鳴り始めた時)。
  // 返事: { volume: 音量 か null, armed: 自動で割り当てるのを待つタブか }。
  // armed で音量がまだない (鳴る前) 時は何も変えない。待っている要素は、鳴り始めた時の問い合わせで決まる
  let asking = false;
  let again = false;   // 聞いている間に鳴り始めた
  const ask = async (type) => {
    if (asking) {
      if (type === 'media-play') again = true;
      return;
    }
    asking = true;
    try {
      const r = await chrome.runtime.sendMessage({ type });
      const armed = r?.armed === true;
      arm(armed);
      if (typeof r?.volume === 'number') send(r.volume);
      else if (!armed) send(null);
    } catch {
      arm(false);
      send(null);
    } finally {
      asking = false;
    }
    if (again) {
      again = false;
      ask('media-play');
    }
  };
  const onPlay = () => ask('media-play');
  document.addEventListener('__deej_tab_play', onPlay);

  // 拡張機能が再読み込み・削除されても、ページ側は音量を抑え続ける。
  // Port の切断で検知して、元の音量に戻してから止まる (再注入もできるようにする)。
  // Port は音量をかけ始めてからつなぐ (どのページにも入るので、触らないページで Port を開いたままにしない)
  let watching = false;
  const shutdown = () => {
    arm(false);
    send(null);
    try { chrome.runtime.onMessage.removeListener(onMessage); } catch {}
    document.removeEventListener('__deej_tab_play', onPlay);
    window.__deejTabVol = false;
  };
  function watch() {
    if (watching) return;
    watching = true;
    let port;
    try {
      port = chrome.runtime.connect({ name: 'deej-media' });
    } catch {
      shutdown();
      return;
    }
    port.onDisconnect.addListener(() => {
      watching = false;
      // Service Worker が止まっただけなら拡張機能は生きているので、つなぎ直す
      if (chrome.runtime?.id) setTimeout(watch, 1000);
      else shutdown();
    });
  }
  ask('media-hello');
})();
