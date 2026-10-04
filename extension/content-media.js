// 方式X の拡張機能側: background.js から届いた音量を、ページ側の media-guard.js に渡す。
// 音量をかけるのは media-guard.js (ページの volume を横取りしないと、サイトが入れ直した時に一瞬元の音量で鳴る)
(() => {
  if (window.__deejTabVol) return;
  window.__deejTabVol = true;

  // null を渡すと元の音量に戻して制御をやめる
  const send = (volume) => document.dispatchEvent(new CustomEvent('__deej_tab_volume', { detail: volume }));

  const onMessage = (msg) => {
    if (msg.type === 'deej-volume') send(msg.volume);
    else if (msg.type === 'deej-reset') send(null);
  };
  chrome.runtime.onMessage.addListener(onMessage);

  // 拡張機能が再読み込み・削除されても、ページ側は音量を抑え続ける。
  // Port の切断で検知して、元の音量に戻してから止まる (再注入もできるようにする)
  const shutdown = () => {
    send(null);
    try { chrome.runtime.onMessage.removeListener(onMessage); } catch {}
    window.__deejTabVol = false;
  };
  const watch = () => {
    let port;
    try {
      port = chrome.runtime.connect({ name: 'deej-media' });
    } catch {
      shutdown();
      return;
    }
    port.onDisconnect.addListener(() => {
      // Service Worker が止まっただけなら拡張機能は生きているので、つなぎ直す
      if (chrome.runtime?.id) setTimeout(watch, 1000);
      else shutdown();
    });
  };
  watch();
})();
