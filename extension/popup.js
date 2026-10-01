// background.js と Port でつながり、状態が変わった時だけ描き直す (スライダー値は値だけ更新)

const METHOD_HINTS = {
  Y: 'タブの音をまとめて調整します。0 で完全に無音になり、100% を超えて大きくもできます。割り当て中は、タブにキャプチャ中のマークが出ます',
  X: 'ページ内の動画・音声の音量を直接変えます。100% までで、サイトによっては 0 でもかすかに聞こえます',
};
const SWITCH_NOTE = '切り替えると割り当ては解除されます';

let currentTab = null;
let supported = true;
let state = null;
const rows = new Map(); // slot -> { li, bar, val }

const $ = (id) => document.getElementById(id);

// 音量を変えられないページ (chrome:// や ウェブストアなど)
function isSupported(url) {
  if (!url) return false;
  if (!/^(https?|file):/.test(url)) return false;
  return !/^https:\/\/(chromewebstore\.google\.com|chrome\.google\.com\/webstore)/.test(url);
}

function showError(text) {
  const err = $('error');
  err.textContent = text || '';
  err.hidden = !text;
}

async function act(msg, button) {
  if (button) button.disabled = true;
  try {
    const res = await chrome.runtime.sendMessage(msg);
    showError(res?.ok ? '' : (res?.error || '操作に失敗しました'));
  } catch (e) {
    showError(String(e?.message || e));
  } finally {
    if (button) button.disabled = false;
  }
}

// 割り当て先のタブへ移動する
async function focusTab(t) {
  if (!t?.windowId) return;
  await chrome.tabs.update(t.id, { active: true });
  await chrome.windows.update(t.windowId, { focused: true });
  window.close();
}

function favicon(url) {
  const img = document.createElement('img');
  img.className = 'fav';
  img.alt = '';
  img.width = img.height = 14;
  if (url && /^(https?|data):/.test(url)) img.src = url;
  else img.hidden = true;
  img.onerror = () => { img.hidden = true; };
  return img;
}

function render() {
  const s = state;
  const status = $('status');
  const paused = s.connected && s.enabled === false;
  status.className = 'pill ' + (s.connected && !paused ? 'on' : 'off');
  status.querySelector('span').textContent = !s.connected ? 'deej-tab 未起動' : paused ? '一時停止中' : '接続中';
  status.title = paused ? 'deej-tab でスライダー操作が一時停止されています。タブは元の音量です' : '';
  document.body.classList.toggle('paused', paused);

  document.querySelectorAll('.seg button').forEach((b) => {
    b.setAttribute('aria-checked', String(b.dataset.method === s.method));
  });
  $('method-hint').textContent = `${METHOD_HINTS[s.method] || ''}。${SWITCH_NOTE}`;

  const list = $('slots');
  list.replaceChildren();
  rows.clear();
  $('empty').hidden = s.slots.length > 0;

  for (const slot of s.slots) {
    const t = s.tabs[slot];
    const mine = t && t.id === currentTab?.id;
    const li = document.createElement('li');
    li.className = t ? (mine ? 'assigned mine' : 'assigned') : '';

    const led = document.createElement('span');
    led.className = 'led';

    const body = document.createElement('div');
    body.className = 'body';
    const head = document.createElement('div');
    head.className = 'head';
    const name = document.createElement('b');
    name.textContent = `タブ ${slot}`;
    const val = document.createElement('span');
    val.className = 'val';
    head.append(name, val);

    const meter = document.createElement('div');
    meter.className = 'meter';
    const bar = document.createElement('i');
    meter.append(bar);

    const target = document.createElement(t && !mine ? 'button' : 'div');
    target.className = 'target';
    if (t) {
      target.append(favicon(t.favIconUrl));
      const title = document.createElement('span');
      title.textContent = mine ? 'このタブ' : t.title;
      target.append(title);
      if (!mine) {
        target.title = 'このタブを表示';
        target.addEventListener('click', () => focusTab(t));
      }
    } else {
      target.textContent = '未割り当て';
    }
    body.append(head, meter, target);

    const actions = document.createElement('div');
    actions.className = 'actions';
    if (mine) {
      const b = document.createElement('button');
      b.className = 'btn';
      b.textContent = '解除';
      b.addEventListener('click', () => act({ type: 'unassign', slot }, b));
      actions.append(b);
    } else {
      const b = document.createElement('button');
      b.className = 'btn primary';
      b.textContent = t ? '入れ替え' : '割り当て';
      b.title = t ? 'このタブに入れ替える' : 'このタブを割り当てる';
      b.disabled = !supported;
      b.addEventListener('click', () => act({ type: 'assign', slot, tabId: currentTab.id }, b));
      actions.append(b);
      if (t) {
        const x = document.createElement('button');
        x.className = 'btn icon';
        x.textContent = '×';
        x.title = '割り当てを解除';
        x.setAttribute('aria-label', `タブ ${slot} の割り当てを解除`);
        x.addEventListener('click', () => act({ type: 'unassign', slot }, x));
        actions.append(x);
      }
    }

    li.append(led, body, actions);
    list.append(li);
    rows.set(slot, { li, bar, val });
  }
  renderValues();
}

function renderValues() {
  for (const [slot, r] of rows) {
    const v = state.values[slot];
    const pct = v === undefined ? 0 : Math.round(v * 100);
    r.val.textContent = v === undefined ? '—' : `${pct}%`;
    // 100% を超える分 (deej-tab でスライダーの上限を上げた時) はバーの色を変える。バーは 100% で満杯
    r.bar.style.width = `${Math.min(100, pct)}%`;
    r.li.classList.toggle('over', pct > 100);
    // 実機と同じく、0 まで下げたら LED を消す
    r.li.classList.toggle('muted', v !== undefined && pct === 0);
  }
}

document.querySelectorAll('.seg button').forEach((b) => {
  b.addEventListener('click', () => {
    if (b.getAttribute('aria-checked') === 'true') return;
    act({ type: 'setMethod', method: b.dataset.method }, b);
  });
});

(async () => {
  [currentTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  supported = isSupported(currentTab?.url);
  $('current-title').textContent = currentTab?.title || '';
  const icon = $('current-icon');
  if (currentTab?.favIconUrl && /^(https?|data):/.test(currentTab.favIconUrl)) {
    icon.src = currentTab.favIconUrl;
    icon.hidden = false;
    icon.onerror = () => { icon.hidden = true; };
  }
  $('unsupported').hidden = supported;

  const port = chrome.runtime.connect({ name: 'popup' });
  port.onMessage.addListener((msg) => {
    if (msg.type === 'state') {
      state = msg;
      render();
    } else if (msg.type === 'values' && state) {
      state.values = msg.values;
      renderValues();
    }
  });
})();
