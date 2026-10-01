// deej-tab (PCアプリ) と WebSocket でつながり、スロットに割り当てたタブの音量を変える

importScripts('i18n.js');   // 文言 (t)。言語は deej-tab の設定に合わせる

const WS_URL = 'ws://127.0.0.1:8765';
const HEALTH_URL = 'http://127.0.0.1:8765/health';
// スライダーの音量(0〜1) → ゲイン の変換。2 = 2乗 (小さい音量側を細かく調整できる)。1 にすると比例
const CURVE_POWER = 2;
// 100% を超える音量 (deej-tab でスライダーの上限を上げた時) はそのままの倍率。これより上は切る
// (offscreen.js の HEADROOM と同じ値にする)
const MAX_GAIN = 4;
const RETRY_MS = 2000;          // deej-tab が起動していない間の確認間隔
const POPUP_VALUES_MS = 50;     // ポップアップへスライダー値を送る最短間隔
const LED = '#ff4d4d';          // 実機のスライダー LED の赤 (設定画面と同じ)

const ICON_ON = { 16: 'icons/icon16.png', 32: 'icons/icon32.png' };
const ICON_OFF = { 16: 'icons/icon16-off.png', 32: 'icons/icon32-off.png' };

let ws = null;
let connected = false;
let enabled = true;  // deej-tab の「スライダー操作」。一時停止中はタブを元の音量 (100%) に戻す
let slots = [];     // config.yaml に書かれた tab.N の N
let values = {};    // { slot: 音量 (1 = 100%。上限を上げたスライダーは 1 を超える) }
const injected = new Map(); // 方式X: tabId -> スクリプト注入の Promise
const popups = new Set();   // 開いているポップアップの Port
const currentTouched = new Set(); // A4 (表示中のタブ) で音量を変えたタブ (一時停止の時に戻す)

// 100% までは 2 乗のカーブ、超えた分はそのまま (200% = 2 倍)。1 でつながる
const toGain = (v) => {
  v = Math.max(0, Math.min(MAX_GAIN, v));
  return v <= 1 ? Math.pow(v, CURVE_POWER) : v;
};
// 方式X (動画の volume) は 1 までしか設定できない
const toMediaVolume = (v) => Math.min(1, toGain(v));
// そのスロットに今かけるゲイン。一時停止中や値がまだ届いていない時は元の音量
const slotGain = (slot) => (enabled && values[slot] !== undefined ? toGain(values[slot]) : 1);

// ---------------------------------------------------------------- 保存データ
// メモリに持ち、変えた時だけ保存する (スライダーを動かすたびに読み直さない)。
// assignments はタブIDなのでブラウザを閉じると無効 → storage.session
// method は保持したいので storage.local

let assignments = {}; // { slot: tabId }
let method = 'Y';     // 既定は Y (X は Web Audio 等の音が残り、0 で無音にならないサイトがある)

const ready = (async () => {
  const [{ assignments: a = {} }, { method: m = 'Y' }] = await Promise.all([
    chrome.storage.session.get('assignments'),
    chrome.storage.local.get('method'),
  ]);
  assignments = a;
  method = m;
  await loadLang();
  await reconcile();
  for (const [s, t] of Object.entries(assignments)) setBadge(t, s);
})();

const saveAssignments = () => {
  reportAssigned();
  return chrome.storage.session.set({ assignments });
};
// deej-tab に、タブを割り当て済みのスロットを伝える (割り当てのないスライダーの LED を暗くするため)
function reportAssigned() {
  wsSend({ type: 'assigned', slots: Object.keys(assignments).map(Number) });
}
function wsSend(msg) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}
const slotOf = (tabId) => {
  const e = Object.entries(assignments).find(([, t]) => t === tabId);
  return e && Number(e[0]);
};

// 割り当ての変更は1つずつ順に行う (ポップアップの連打やタブを閉じた通知と重ならないように)
let queue = Promise.resolve();
function serial(fn) {
  const p = queue.then(() => ready).then(fn);
  queue = p.catch(() => {});
  return p;
}

// Service Worker が止まっている間に閉じたタブや、終わったキャプチャの割り当てを外す
async function reconcile() {
  let live = null;
  if (method === 'Y' && Object.keys(assignments).length) {
    const res = await toOffscreen({ type: 'capture-list' });
    live = new Set((res?.slots || []).map(String));
  }
  let changed = false;
  for (const [s, t] of Object.entries(assignments)) {
    const exists = await chrome.tabs.get(t).then(() => true, () => false);
    if (!exists || (live && !live.has(s))) {
      if (exists) clearBadge(t);
      delete assignments[s];
      changed = true;
    }
  }
  if (changed) await saveAssignments();
}

// ---------------------------------------------------------------- ツールバーのアイコン
// 未起動の間はつまみが灰色のアイコン (トレイと同じ)。割り当てたタブにはスロット番号を出す

chrome.action.setBadgeBackgroundColor({ color: LED });
chrome.action.setBadgeTextColor({ color: '#ffffff' });

let iconKey = null;
// deej-tab の言語に合わせる。覚えておき、アイコンの説明・右クリックメニュー・ポップアップを描き直す
function setLang(lang) {
  if ((lang !== 'ja' && lang !== 'en') || lang === LANG) return;
  LANG = lang;
  chrome.storage.local.set({ lang }).catch(() => {});
  iconKey = null;
  updateIcon();
  buildMenus();
  pushState();
}
function setConnected(on) {
  connected = on;
  updateIcon();
  pushState();
}
// 起動中かつ有効ならつまみが光る。一時停止中は未起動と同じ灰色
function updateIcon() {
  const key = connected ? (enabled ? 'on' : 'paused') : 'off';
  if (iconKey === key) return;
  iconKey = key;
  chrome.action.setIcon({ path: key === 'on' ? ICON_ON : ICON_OFF }).catch(() => {});
  chrome.action.setTitle({
    title: {
      on: t('name'),
      paused: t('titlePaused'),
      off: t('titleOff'),
    }[key],
  }).catch(() => {});
}
function setBadge(tabId, slot) {
  chrome.action.setBadgeText({ tabId, text: String(slot) }).catch(() => {});
}
function clearBadge(tabId) {
  chrome.action.setBadgeText({ tabId, text: '' }).catch(() => {});
}

// ---------------------------------------------------------------- WebSocket

// deej-tab が起動しているかを先に HTTP で確認する。
// 未起動のまま WebSocket を開くと chrome://extensions にエラーが溜まるため
let probing = false;
let retryTimer = null;
function scheduleRetry() {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(connect, RETRY_MS);
}
async function isAppRunning() {
  try {
    const res = await fetch(HEALTH_URL, { cache: 'no-store', signal: AbortSignal.timeout(1500) });
    return res.ok;
  } catch {
    return false;
  }
}

async function connect() {
  clearTimeout(retryTimer);
  if (probing || ws) return;
  probing = true;
  const running = await isAppRunning();
  probing = false;
  if (!running) {
    setConnected(false);
    scheduleRetry();
    return;
  }
  if (ws) return;
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    setConnected(true);
    ready.then(reportAssigned);
  };
  ws.onclose = () => {
    ws = null;
    setConnected(false);
    scheduleRetry();
  };
  ws.onerror = () => {};
  ws.onmessage = async (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    await ready;
    if (msg.type === 'slots') {
      slots = msg.slots;
      buildMenus();
      pushState();
    } else if (msg.type === 'state') {
      values = Object.fromEntries(Object.entries(msg.values).map(([k, v]) => [Number(k), v]));
      setEnabled(msg.enabled ?? true);
      for (const s of Object.keys(values)) applySlot(Number(s));
      pushValues();
    } else if (msg.type === 'volume') {
      values[msg.slot] = msg.value;
      applySlot(msg.slot);
      pushValues();
    } else if (msg.type === 'current') {
      applyCurrentTab(msg.value);
    } else if (msg.type === 'enabled') {
      setEnabled(msg.value);
    } else if (msg.type === 'meter') {
      setMeter(msg.on);
    } else if (msg.type === 'lang') {
      setLang(msg.value);
    }
    // 'ping' は Service Worker を止めないためのもので、何もしない
  };
}

// Service Worker が止まっても復帰して再接続する
chrome.alarms.create('reconnect', { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === 'reconnect') connect(); });
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();

// ---------------------------------------------------------------- 音量適用

function applySlot(slot) {
  const tabId = assignments[slot];
  if (tabId === undefined) return;
  if (method === 'X') {
    if (enabled && values[slot] !== undefined) setMediaVolume(tabId, toMediaVolume(values[slot]));
    else if (!enabled) resetMedia(tabId);
  } else if (!enabled || values[slot] !== undefined) {
    toOffscreen({ type: 'gain', slot, gain: slotGain(slot) });
  }
}

// 一時停止 / 再開。一時停止中はタブを元の音量に戻し、再開したらスライダーの音量をかけ直す
// (再開後はアプリがスライダーの値を送り直してくる)
function setEnabled(on) {
  if (enabled === on) return;
  enabled = on;
  for (const s of Object.keys(assignments)) applySlot(Number(s));
  if (!on) {
    for (const t of currentTouched) resetMedia(t);
    currentTouched.clear();
  }
  updateIcon();
  pushState();
}

// deej.current で Chrome が最前面の時: 表示中のタブの音量を変える。
// 方式Yはタブごとにクリックが必要で自動では使えないので、方針に関わらず方式Xで変える。
// スロットに割り当て済みのタブはスライダーで操作するので触らない
async function applyCurrentTab(value) {
  if (!enabled) return;
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab || slotOf(tab.id) !== undefined) return;
  currentTouched.add(tab.id);
  setMediaVolume(tab.id, toMediaVolume(value));
}

async function setMediaVolume(tabId, volume) {
  if (!(await ensureInjected(tabId))) return;
  // 注入を待っている間に一時停止されたら送らない (元に戻した後に古い音量で上書きしないように)
  if (!enabled) return;
  chrome.tabs.sendMessage(tabId, { type: 'deej-volume', volume }).catch(() => {});
}
function resetMedia(tabId) {
  chrome.tabs.sendMessage(tabId, { type: 'deej-reset' }).catch(() => {});
}

// 注入中に届いた音量も、注入が終わってから順に送る (同じ Promise を待たせる)
function ensureInjected(tabId) {
  if (!injected.has(tabId)) {
    injected.set(tabId, chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      files: ['content-media.js'],
    }).then(() => true, () => {
      injected.delete(tabId); // chrome:// など注入できないページ
      return false;
    }));
  }
  return injected.get(tabId);
}

// ---------------------------------------------------------------- 方式Y (offscreen)

async function ensureOffscreen() {
  const contexts = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
  if (contexts.length) return;
  await chrome.offscreen.createDocument({
    url: 'offscreen.html',
    reasons: ['USER_MEDIA'],
    justification: 'Play the tab audio with a gain applied',
  });
}
// offscreen が無い時は undefined を返す
function toOffscreen(msg) {
  return chrome.runtime.sendMessage({ target: 'offscreen', ...msg }).catch(() => undefined);
}

// ---------------------------------------------------------------- 割り当て

// Chrome のエラーを分かる言葉にする
function explain(e) {
  const m = String(e?.message || e);
  if (/Chrome pages cannot be captured|cannot be captured/i.test(m)) return t('errCannotCapture');
  if (/not been invoked|activeTab|invoked for the current page/i.test(m)) {
    return t('errReopen');
  }
  if (/active stream/i.test(m)) return t('errBusy');
  if (/No tab with id/i.test(m)) return t('errNoTab');
  return m;
}

// スロットの割り当てを外して、タブを元の状態に戻す (保存は呼び出し側)
async function release(slot) {
  const tabId = assignments[slot];
  if (tabId === undefined) return;
  delete assignments[slot];
  clearBadge(tabId);
  if (method === 'X') resetMedia(tabId);
  else await toOffscreen({ type: 'capture-stop', slot });
}

async function assign(slot, tabId) {
  const from = slotOf(tabId);
  if (from === slot) return;
  if (assignments[slot] !== undefined) await release(slot); // このスロットの前のタブ
  // A4 (表示中のタブ) で下げていた音量は戻してから、スロットの音量で操作する
  resetMedia(tabId);
  currentTouched.delete(tabId);
  if (method === 'Y') {
    if (from !== undefined) {
      // 別のスロットから移すだけなら、キャプチャはそのまま使う
      const res = await toOffscreen({ type: 'capture-move', from, to: slot });
      if (!res?.ok) throw new Error(t('errMove'));
    } else {
      // ポップアップを開いた(=拡張機能を呼び出した)タブでのみ取得できる
      const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
      await ensureOffscreen();
      const res = await toOffscreen({
        type: 'capture-start', slot, streamId, gain: slotGain(slot), meter: meterOn,
      });
      if (!res?.ok) throw new Error(res?.error || t('errStart'));
    }
  }
  if (from !== undefined) delete assignments[from];
  assignments[slot] = tabId;
  await saveAssignments();
  setBadge(tabId, slot);
  applySlot(slot);
}

async function unassign(slot) {
  await release(slot);
  await saveAssignments();
}

async function setMethod(m) {
  if (m !== 'X' && m !== 'Y') throw new Error(t('errMethod', m));
  if (m === method) return;
  // 方式Yはタブごとにクリックが必要なので、切り替え時は全部外す
  for (const s of Object.keys(assignments)) await release(Number(s));
  await saveAssignments();
  if (method === 'Y') await toOffscreen({ type: 'capture-stop-all' });
  method = m;
  await chrome.storage.local.set({ method });
}

// タブが閉じられたら割り当てを外す
chrome.tabs.onRemoved.addListener((tabId) => {
  injected.delete(tabId);
  currentTouched.delete(tabId);
  serial(async () => {
    const slot = slotOf(tabId);
    if (slot === undefined) return;
    await unassign(slot);
    pushState();
  });
});

chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  // 方式X: ページ移動で注入が消えるので入れ直す
  if (info.status === 'loading') injected.delete(tabId);
  await ready;
  const slot = slotOf(tabId);
  if (slot === undefined) return;
  if (info.status === 'complete') {
    setBadge(tabId, slot);
    applySlot(slot);
  }
  if (info.title !== undefined || info.favIconUrl !== undefined) pushState();
});

// ---------------------------------------------------------------- ポップアップ

async function snapshot() {
  const tabs = {};
  for (const [s, t] of Object.entries(assignments)) {
    try {
      const tab = await chrome.tabs.get(t);
      tabs[s] = { id: t, title: tab.title, favIconUrl: tab.favIconUrl, windowId: tab.windowId };
    } catch {
      tabs[s] = { id: t, title: t('closedTab') };
    }
  }
  return { type: 'state', connected, enabled, slots, values, tabs, method, lang: LANG };
}

async function pushState() {
  if (!popups.size) return;
  const s = await snapshot();
  for (const p of popups) p.postMessage(s);
}

// スライダー値は頻繁に届くので、間引いて送る
let valuesTimer = null;
function pushValues() {
  if (!popups.size || valuesTimer) return;
  valuesTimer = setTimeout(() => {
    valuesTimer = null;
    for (const p of popups) p.postMessage({ type: 'values', values });
  }, POPUP_VALUES_MS);
}

chrome.runtime.onConnect.addListener((port) => {
  // 'deej-media' は方式Xのスクリプトが拡張機能の生存確認に使う Port (何もしない)
  if (port.name !== 'popup') return;
  popups.add(port);
  port.onDisconnect.addListener(() => popups.delete(port));
  ready.then(async () => port.postMessage(await snapshot()));
  connect();
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.target === 'offscreen') return;
  if (msg.type === 'meter-levels') {
    // offscreen が測ったタブの音の大きさを deej-tab へ (LED を音に合わせて光らせる時)
    if (meterOn) wsSend({ type: 'meter', levels: msg.levels });
    return;
  }
  if (msg.type === 'capture-ended') {
    // タブ側でキャプチャが終わった (offscreen は停止済み)
    serial(async () => {
      const tabId = assignments[msg.slot];
      if (tabId === undefined) return;
      delete assignments[msg.slot];
      clearBadge(tabId);
      await saveAssignments();
      pushState();
    });
    return;
  }
  const actions = {
    assign: () => assign(msg.slot, msg.tabId),
    unassign: () => unassign(msg.slot),
    setMethod: () => setMethod(msg.method),
  };
  if (!actions[msg.type]) return;
  serial(actions[msg.type]).then(
    () => sendResponse({ ok: true }),
    (e) => sendResponse({ ok: false, error: explain(e) }),
  ).finally(pushState);
  return true;
});

// ---------------------------------------------------------------- 音の大きさ (LED を音に合わせる時)
// deej-tab から頼まれた時だけ offscreen で測る (普段は測らない)

let meterOn = false;
function setMeter(on) {
  meterOn = !!on;
  toOffscreen({ type: 'meter-on', on: meterOn });
}

// ---------------------------------------------------------------- ショートカット・右クリックメニュー
// ポップアップを開かずに「このタブ → タブ N」。どちらも拡張機能を呼び出した扱いになるので、
// キャプチャ方式に必要な許可もその場で取れる

chrome.commands.onCommand.addListener((command, tab) => {
  if (!tab) return;
  if (command === 'slot-next') {
    nextSlot(tab);
    return;
  }
  const m = /^slot-(\d+)$/.exec(command);
  if (m) toggleSlot(Number(m[1]), tab, true);
});

// Alt+Shift+A: 空いている一番小さいスロットへ。もう一度押すと次の空きへ移り、最後の次は解除。
// ほかのタブが使っているスロットは飛ばす (追い出さない)。空きがなければバッジに「満」
function nextSlot(tab) {
  serial(async () => {
    const cur = slotOf(tab.id);
    const order = [...slots].sort((a, b) => a - b).filter((s) => s === cur || assignments[s] === undefined);
    if (cur === undefined) {
      if (!order.length) {
        flash(tab.id, slots.length ? t('badgeFull') : '?');
        return;
      }
      await assign(order[0], tab.id);
      return;
    }
    const next = order[order.indexOf(cur) + 1];
    if (next === undefined) await unassign(cur);
    else await assign(next, tab.id);
  }).catch((e) => {
    flash(tab.id, '!');
    console.warn('deej: 割り当てられません:', explain(e));
  }).finally(pushState);
}

// toggle: もう同じスロットに割り当ててあれば解除する (ショートカットを 2 回押した時)
async function toggleSlot(slot, tab, toggle) {
  if (!slots.includes(slot)) {
    flash(tab.id, '?');   // deej-tab の設定にないスロット (または deej-tab が起動していない)
    return;
  }
  try {
    await serial(async () => {
      if (toggle && assignments[slot] === tab.id) await unassign(slot);
      else await assign(slot, tab.id);
    });
  } catch (e) {
    flash(tab.id, '!');
    console.warn('deej: 割り当てられません:', explain(e));
  } finally {
    pushState();
  }
}

// アイコンのバッジに一瞬だけ記号を出す (? = スロットがない、! = このページは割り当てられない)
function flash(tabId, text) {
  chrome.action.setBadgeText({ tabId, text }).catch(() => {});
  setTimeout(() => {
    const s = slotOf(tabId);
    chrome.action.setBadgeText({ tabId, text: s === undefined ? '' : String(s) }).catch(() => {});
  }, 1500);
}

const MENU_CONTEXTS = ['page', 'frame', 'video', 'audio', 'action'];
let menuKey = null;
function buildMenus() {
  const key = LANG + ':' + slots.join(',');   // 言語が変わっても作り直す
  if (key === menuKey) return;
  menuKey = key;
  chrome.contextMenus.removeAll(() => {
    if (!slots.length) return;
    chrome.contextMenus.create({ id: 'deej', title: t('name'), contexts: MENU_CONTEXTS });
    for (const n of slots) {
      chrome.contextMenus.create({
        id: `slot-${n}`, parentId: 'deej', title: t('menuSlot', n), contexts: MENU_CONTEXTS,
      });
    }
    chrome.contextMenus.create({ id: 'sep', parentId: 'deej', type: 'separator', contexts: MENU_CONTEXTS });
    chrome.contextMenus.create({ id: 'unassign', parentId: 'deej', title: t('menuUnassign'), contexts: MENU_CONTEXTS });
  });
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (!tab || tab.id < 0) return;
  const m = /^slot-(\d+)$/.exec(String(info.menuItemId));
  if (m) {
    toggleSlot(Number(m[1]), tab, false);
  } else if (info.menuItemId === 'unassign') {
    serial(async () => {
      const slot = slotOf(tab.id);
      if (slot !== undefined) await unassign(slot);
    }).finally(pushState);
  }
});
