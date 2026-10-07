// background.js と Port でつながり、状態が変わった時だけ描き直す (スライダー値は値だけ更新)

const METHOD_HINTS = { Y: 'hintY', X: 'hintX' };   // i18n.js のキー

let currentTab = null;
let supported = true;
let state = null;
const rows = new Map(); // slot -> { li, bar, val }

const $ = (id) => document.getElementById(id);

// HTML の固定の文字を今の言語にする (data-i18n: 文字, data-i18n-html: 太字入り, data-i18n-aria: aria-label)
function applyStatic() {
  document.documentElement.lang = LANG;
  document.title = t('name');
  document.querySelectorAll('[data-i18n]').forEach((e) => {
    e.replaceChildren(...t(e.dataset.i18n).split('\n').flatMap((line, i) => (i ? [document.createElement('br'), line] : [line])));
  });
  document.querySelectorAll('[data-i18n-html]').forEach((e) => { e.innerHTML = t(e.dataset.i18nHtml); });   // 文言は i18n.js の固定の文字だけ
  document.querySelectorAll('[data-i18n-aria]').forEach((e) => e.setAttribute('aria-label', t(e.dataset.i18nAria)));
}

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
    showError(res?.ok ? '' : (res?.error || t('errFailed')));
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
  status.querySelector('span').textContent = !s.connected ? t('notRunning') : paused ? t('paused') : t('connected');
  status.title = paused ? t('pausedTitle') : '';
  document.body.classList.toggle('paused', paused);

  document.querySelectorAll('.seg button').forEach((b) => {
    b.setAttribute('aria-checked', String(b.dataset.method === s.method));
  });
  $('auto-assign').checked = !!s.autoAssign;
  $('auto-hint').textContent = t(s.method === 'Y' ? 'autoHintY' : 'autoHintX');
  $('method-hint').textContent = `${METHOD_HINTS[s.method] ? t(METHOD_HINTS[s.method]) : ''}${LANG === 'ja' ? '。' : '. '}${t('switchNote')}`;

  const list = $('slots');
  list.replaceChildren();
  rows.clear();
  $('empty').hidden = s.slots.length > 0;

  for (const slot of s.slots) {
    const tab = s.tabs[slot];
    const mine = tab && tab.id === currentTab?.id;
    const li = document.createElement('li');
    li.className = tab ? (mine ? 'assigned mine' : 'assigned') : '';

    const led = document.createElement('span');
    led.className = 'led';

    const body = document.createElement('div');
    body.className = 'body';
    const head = document.createElement('div');
    head.className = 'head';
    const name = document.createElement('b');
    name.textContent = t('tabN', slot);
    // 自動で割り当てたタブ (方式Yの時も、ページ内の動画の音量で変えている)
    if (tab && s.autoSlots?.includes(slot)) {
      const tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = t('autoTag');
      tag.title = t('autoTagTitle');
      name.append(tag);
    }
    const val = document.createElement('span');
    val.className = 'val';
    head.append(name, val);

    const meter = document.createElement('div');
    meter.className = 'meter';
    const bar = document.createElement('i');
    meter.append(bar);

    const target = document.createElement(tab && !mine ? 'button' : 'div');
    target.className = 'target';
    if (tab) {
      target.append(favicon(tab.favIconUrl));
      const title = document.createElement('span');
      title.textContent = mine ? t('thisTab') : tab.title;
      target.append(title);
      if (!mine) {
        target.title = t('showTab');
        target.addEventListener('click', () => focusTab(tab));
      }
    } else {
      target.textContent = t('unassigned');
    }
    body.append(head, meter, target);

    const actions = document.createElement('div');
    actions.className = 'actions';
    if (mine) {
      const b = document.createElement('button');
      b.className = 'btn';
      b.textContent = t('unassign');
      b.addEventListener('click', () => act({ type: 'unassign', slot }, b));
      actions.append(b);
    } else {
      const b = document.createElement('button');
      b.className = 'btn primary';
      b.textContent = tab ? t('swap') : t('assign');
      b.title = tab ? t('swapTitle') : t('assignTitle');
      b.disabled = !supported;
      b.addEventListener('click', () => act({ type: 'assign', slot, tabId: currentTab.id }, b));
      actions.append(b);
      if (tab) {
        const x = document.createElement('button');
        x.className = 'btn icon';
        x.textContent = '×';
        x.title = t('unassignTitle');
        x.setAttribute('aria-label', t('unassignAria', slot));
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

$('auto-assign').addEventListener('change', (e) => {
  act({ type: 'setAutoAssign', on: e.target.checked }, e.target);
});

document.querySelectorAll('.seg button').forEach((b) => {
  b.addEventListener('click', () => {
    if (b.getAttribute('aria-checked') === 'true') return;
    act({ type: 'setMethod', method: b.dataset.method }, b);
  });
});

(async () => {
  await loadLang();
  applyStatic();
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
      if (msg.lang && msg.lang !== LANG) { LANG = msg.lang; applyStatic(); }
      state = msg;
      render();
    } else if (msg.type === 'values' && state) {
      state.values = msg.values;
      renderValues();
    }
  });
})();
