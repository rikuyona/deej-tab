// 方式X: ページ内の <video>/<audio> の音量を直接変える
(() => {
  if (window.__deejTabVol) return;
  window.__deejTabVol = true;
  const original = new WeakMap(); // 触る前の音量 (解除時に戻す)
  let target = null;              // null = 制御していない
  let applying = false;

  const apply = (el) => {
    if (target === null) return;
    if (!original.has(el)) original.set(el, el.volume);
    if (Math.abs(el.volume - target) > 0.001) {
      applying = true;
      el.volume = target;
      applying = false;
    }
  };
  const applyAll = () => document.querySelectorAll('video, audio').forEach(apply);

  const reset = () => {
    target = null;
    document.querySelectorAll('video, audio').forEach((el) => {
      if (original.has(el)) { el.volume = original.get(el); original.delete(el); }
    });
  };

  // サイト側が音量を戻したら、スライダーの値で上書きし直す
  const onVolumeChange = (e) => {
    if (!applying && e.target instanceof HTMLMediaElement) apply(e.target);
  };
  const onPlay = (e) => {
    if (e.target instanceof HTMLMediaElement) apply(e.target);
  };
  document.addEventListener('volumechange', onVolumeChange, true);
  document.addEventListener('play', onPlay, true);
  const observer = new MutationObserver(applyAll);
  observer.observe(document.documentElement, { childList: true, subtree: true });

  const onMessage = (msg) => {
    if (msg.type === 'deej-volume') {
      target = msg.volume;
      applyAll();
    } else if (msg.type === 'deej-reset') {
      reset();
    }
  };
  chrome.runtime.onMessage.addListener(onMessage);

  // 拡張機能が再読み込み・削除されても、このスクリプトはページに残って音量を上書きし続ける。
  // Port の切断で検知して、元の音量に戻してから止まる (再注入もできるようにする)
  const shutdown = () => {
    reset();
    document.removeEventListener('volumechange', onVolumeChange, true);
    document.removeEventListener('play', onPlay, true);
    observer.disconnect();
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
