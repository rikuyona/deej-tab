// 方式X のページ側 (MAIN world で動く): <video>/<audio> の volume を横取りする。
// サイトが音量を入れ直しても (YouTube は動画・広告が始まるたびに入れる)、実際の音量は deej の音量のまま。
// 後から上書きし直すのではないので、元の音量で一瞬鳴ることがない。
// サイトが入れた音量は覚えておき、読み出しにはそれを返す (サイトの音量の表示・保存を変えない)。
// 拡張機能側 (content-media.js) とは document のイベントでやりとりする
(() => {
  if (window.__deejTabGuard) return;
  window.__deejTabGuard = true;

  const proto = HTMLMediaElement.prototype;
  const desc = Object.getOwnPropertyDescriptor(proto, 'volume');
  const nativeGet = (el) => desc.get.call(el);
  const nativeSet = (el, v) => desc.set.call(el, v);
  const nativePlay = proto.play;

  const site = new WeakMap(); // 要素 -> サイトが入れた音量 (deej が触る前の音量も)
  const refs = new Set();     // 触った要素 (WeakRef。元に戻す時に使う。ページから外れた要素も含む)
  let target = null;          // null = 制御していない

  const siteVolume = (el) => (site.has(el) ? site.get(el) : nativeGet(el));
  const remember = (el, v) => {
    if (!site.has(el)) refs.add(new WeakRef(el));
    site.set(el, v);
  };
  const apply = (el) => {
    if (target === null) return;
    if (!site.has(el)) remember(el, nativeGet(el));
    if (nativeGet(el) !== target) nativeSet(el, target);
  };
  const applyAll = () => {
    if (target === null) return;
    document.querySelectorAll('video, audio').forEach(apply);
    for (const r of refs) {
      const el = r.deref();
      if (el) apply(el);
      else refs.delete(r);
    }
  };
  const reset = () => {
    target = null;
    for (const r of refs) {
      const el = r.deref();
      if (el && site.has(el)) nativeSet(el, site.get(el));
    }
    refs.clear();
  };

  Object.defineProperty(proto, 'volume', {
    configurable: true,
    enumerable: desc.enumerable,
    get() {
      return target === null ? nativeGet(this) : siteVolume(this);
    },
    set(v) {
      if (target === null) {
        nativeSet(this, v);
        return;
      }
      // 範囲外はブラウザと同じ例外にする (サイトの動きを変えない)
      const n = Number(v);
      if (!Number.isFinite(n)) throw new TypeError("Failed to set the 'volume' property on 'HTMLMediaElement': The provided double value is non-finite.");
      if (n < 0 || n > 1) throw new DOMException(`The volume provided (${n}) is outside the range [0, 1].`, 'IndexSizeError');
      nativeGet(this); // HTMLMediaElement でない this ならここでブラウザと同じ例外になる
      const changed = siteVolume(this) !== n;
      remember(this, n);
      if (nativeGet(this) !== target) nativeSet(this, target);
      // 実際の音量は変わらないので volumechange が出ない。サイトの表示が追いつくように出す
      else if (changed) setTimeout(() => this.dispatchEvent(new Event('volumechange')));
    },
  });

  // 再生を始める前にかける (ページに入れていない new Audio() も)
  proto.play = function play(...args) {
    if (this instanceof HTMLMediaElement) apply(this);
    return nativePlay.apply(this, args);
  };
  // autoplay など play() を通らない再生と、あとから足された要素
  document.addEventListener('play', (e) => {
    if (e.target instanceof HTMLMediaElement) apply(e.target);
  }, true);
  new MutationObserver(applyAll).observe(document.documentElement, { childList: true, subtree: true });

  document.addEventListener('__deej_tab_volume', (e) => {
    const v = e.detail;
    if (typeof v === 'number' && v >= 0 && v <= 1) {
      target = v;
      applyAll();
    } else if (v === null) {
      reset();
    }
  });
})();
