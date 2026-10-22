// 方式X のページ側 (MAIN world で動く): <video>/<audio> の volume を横取りする。
// サイトが音量を入れ直しても (YouTube は動画・広告が始まるたびに入れる)、実際の音量は deej の音量のまま。
// 後から上書きし直すのではないので、元の音量で一瞬鳴ることがない。
// サイトが入れた音量は覚えておき、読み出しにはそれを返す (サイトの音量の表示・保存を変えない)。
// 拡張機能側 (content-media.js) とは document のイベントでやりとりする。
// どのページにも読み込みの最初から入る (manifest の content_scripts)。音量が届くまでは何も変えない
(() => {
  if (window.__deejTabGuard) return;
  window.__deejTabGuard = true;

  // 返事を待つ間に鳴り始めたら、音量 (か null) が届くまで 0 にしておく。これだけ待っても届かなければ元の音量に戻す
  // (拡張機能が答えない時に無音のままにしない)
  const HOLD_MAX_MS = 1500;

  const proto = HTMLMediaElement.prototype;
  const desc = Object.getOwnPropertyDescriptor(proto, 'volume');
  const nativeGet = (el) => desc.get.call(el);
  const nativeSet = (el, v) => desc.set.call(el, v);
  const nativePlay = proto.play;

  const site = new WeakMap(); // 要素 -> サイトが入れた音量 (deej が触る前の音量も)
  const refs = new Set();     // 触った要素 (WeakRef。元に戻す時に使う。ページから外れた要素も含む)
  let target = null;          // null = 制御していない
  // 鳴り始めたら返事まで待つか。読み込み直後 (まだ拡張機能の返事がない) と、自動で割り当てるのを待つタブ (arm)。
  // 前は割り当てるまで (鳴ってから 1 秒) とページの読み込みが終わるまで、元の音量で鳴っていた
  let hold = true;
  const held = new Set();     // 返事を待つ間 0 にしている要素
  let holdTimer = null;

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
  const stopHolding = () => {
    clearTimeout(holdTimer);
    holdTimer = null;
    held.clear();
  };
  const applyAll = () => {
    if (target === null) return;
    stopHolding();
    document.querySelectorAll('video, audio').forEach(apply);
    for (const r of refs) {
      const el = r.deref();
      if (el) apply(el);
      else refs.delete(r);
    }
  };
  const reset = () => {
    target = null;
    stopHolding();
    for (const r of refs) {
      const el = r.deref();
      if (el && site.has(el)) nativeSet(el, site.get(el));
    }
    refs.clear();
  };
  // 鳴り始めた: 制御していなくて返事を待つタブなら、0 にして拡張機能に聞く
  const onPlay = (el) => {
    if (target !== null) {
      apply(el);
      return;
    }
    if (!hold) return;
    if (!site.has(el)) remember(el, nativeGet(el));
    nativeSet(el, 0);
    held.add(el);
    if (!holdTimer) holdTimer = setTimeout(reset, HOLD_MAX_MS);
    document.dispatchEvent(new CustomEvent('__deej_tab_play'));
  };

  Object.defineProperty(proto, 'volume', {
    configurable: true,
    enumerable: desc.enumerable,
    get() {
      return target === null && !held.has(this) ? nativeGet(this) : siteVolume(this);
    },
    set(v) {
      if (target === null && !held.has(this)) {
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
      const actual = target === null ? 0 : target;   // 返事を待つ間は 0 のまま
      if (nativeGet(this) !== actual) nativeSet(this, actual);
      // 実際の音量は変わらないので volumechange が出ない。サイトの表示が追いつくように出す
      else if (changed) setTimeout(() => this.dispatchEvent(new Event('volumechange')));
    },
  });

  // 再生を始める前にかける (ページに入れていない new Audio() も)
  proto.play = function play(...args) {
    if (this instanceof HTMLMediaElement) onPlay(this);
    return nativePlay.apply(this, args);
  };
  // autoplay など play() を通らない再生と、あとから足された要素
  document.addEventListener('play', (e) => {
    if (e.target instanceof HTMLMediaElement) onPlay(e.target);
  }, true);
  // 読み込みの最初は documentElement がまだないことがあるので document を見る
  new MutationObserver(applyAll).observe(document, { childList: true, subtree: true });

  // 返事がないまま (拡張機能が答えない) 時間がたったら、もう待たない
  let told = false;
  setTimeout(() => { if (!told && !held.size) hold = false; }, HOLD_MAX_MS);

  document.addEventListener('__deej_tab_arm', (e) => {
    told = true;
    hold = e.detail === true;
  });
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
