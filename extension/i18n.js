// 拡張機能の文言 (日本語 / English)。background.js と popup.js で使う
// 言語は deej-tab の設定 (アプリから "lang" で届き、storage.local に覚える) に合わせる。
// まだ届いていなければ Chrome の表示言語

const I18N = {
  ja: {
    name: 'deej タブ音量',
    titlePaused: 'deej タブ音量 — 一時停止中 (タブは元の音量です)',
    titleOff: 'deej タブ音量 — deej-tab が起動していません',
    errCannotCapture: 'このページの音声はキャプチャできません',
    errReopen: 'このタブでポップアップを開き直してから割り当ててください',
    errBusy: 'このタブは別の機能で録音・共有中のため、キャプチャできません',
    errNoTab: 'タブが見つかりません (閉じられた可能性があります)',
    errMove: 'キャプチャを移せませんでした',
    errStart: 'キャプチャを開始できませんでした',
    errMethod: '不明な方式です: {0}',
    errFailed: '操作に失敗しました',
    closedTab: '(閉じたタブ)',
    badgeFull: '満',
    menuSlot: 'このタブをタブ {0} に割り当て',
    menuUnassign: 'このタブの割り当てを解除',
    checking: '確認中',
    notRunning: 'deej-tab 未起動',
    paused: '一時停止中',
    connected: '接続中',
    pausedTitle: 'deej-tab でスライダー操作が一時停止されています。タブは元の音量です',
    thisTab: 'このタブ',
    unsupported: 'このページ（Chrome の設定画面など）の音量は変えられません',
    emptyTitle: '割り当てられる番号がありません',
    emptyText: 'deej-tab の設定画面で、スライダーに\n「Chrome タブ 1」などを割り当てると、ここに出ます',
    hint: 'ポップアップを開かなくても、<b>Alt+Shift+A</b>（空いている番号へ。押すたびに次へ移り、最後は解除）・<b>Alt+Shift+1〜2</b>・ページの右クリック →「deej タブ音量」で割り当てられます（キーは chrome://extensions/shortcuts で変更可）',
    methodTitle: '音量の変え方',
    methodY: 'タブの音をまとめて調整',
    methodX: 'ページ内の動画の音量を変える',
    hintY: 'タブの音をまとめて調整します。0 で完全に無音になり、100% を超えて大きくもできます。割り当て中は、タブにキャプチャ中のマークが出ます',
    hintX: 'ページ内の動画・音声の音量を直接変えます。100% までで、サイトによっては 0 でもかすかに聞こえます',
    switchNote: '切り替えると割り当ては解除されます',
    tabN: 'タブ {0}',
    showTab: 'このタブを表示',
    unassigned: '未割り当て',
    unassign: '解除',
    swap: '入れ替え',
    assign: '割り当て',
    swapTitle: 'このタブに入れ替える',
    assignTitle: 'このタブを割り当てる',
    unassignTitle: '割り当てを解除',
    unassignAria: 'タブ {0} の割り当てを解除',
  },
  en: {
    name: 'deej Tab Volume',
    titlePaused: 'deej Tab Volume — Paused (tabs are at their original volume)',
    titleOff: "deej Tab Volume — deej-tab isn't running",
    errCannotCapture: "This page's audio can't be captured",
    errReopen: 'Open the popup on this tab again, then assign it',
    errBusy: "This tab is being recorded or shared by another feature, so it can't be captured",
    errNoTab: 'Tab not found (it may have been closed)',
    errMove: "Couldn't move the capture",
    errStart: "Couldn't start the capture",
    errMethod: 'Unknown method: {0}',
    errFailed: 'Something went wrong',
    closedTab: '(closed tab)',
    badgeFull: 'Full',
    menuSlot: 'Assign this tab to Tab {0}',
    menuUnassign: 'Unassign this tab',
    checking: 'Checking',
    notRunning: "deej-tab isn't running",
    paused: 'Paused',
    connected: 'Connected',
    pausedTitle: 'Sliders are paused in deej-tab. Tabs are at their original volume',
    thisTab: 'This tab',
    unsupported: "The volume of this page (such as Chrome's settings) can't be changed",
    emptyTitle: 'No tab numbers to assign',
    emptyText: 'Assign "Chrome tab 1" or similar to a slider\nin the deej-tab settings, and it appears here',
    hint: 'Without the popup, use <b>Alt+Shift+A</b> (next free number; press again to move on, last press unassigns), <b>Alt+Shift+1–2</b>, or right-click the page → "deej Tab Volume" (change keys at chrome://extensions/shortcuts)',
    methodTitle: 'How to change the volume',
    methodY: "Adjust the tab's audio",
    methodX: 'Change video volume in the page',
    hintY: 'Adjusts all of the tab\'s audio. 0 is complete silence, and you can go above 100%. While assigned, the tab shows a capture icon',
    hintX: "Changes the volume of videos and audio in the page directly. Up to 100%, and some sites can still be faintly heard at 0",
    switchNote: 'Switching unassigns all tabs',
    tabN: 'Tab {0}',
    showTab: 'Go to this tab',
    unassigned: 'Not assigned',
    unassign: 'Unassign',
    swap: 'Swap',
    assign: 'Assign',
    swapTitle: 'Swap to this tab',
    assignTitle: 'Assign this tab',
    unassignTitle: 'Unassign',
    unassignAria: 'Unassign Tab {0}',
  },
};

let LANG = (chrome.i18n.getUILanguage() || '').toLowerCase().startsWith('ja') ? 'ja' : 'en';

function t(key, ...args) {
  let s = I18N[LANG][key] ?? I18N.ja[key] ?? key;
  args.forEach((v, i) => { s = s.replaceAll(`{${i}}`, v); });
  return s;
}

// 覚えている言語を読む (アプリから届いた言語)。読めたら true
async function loadLang() {
  try {
    const { lang } = await chrome.storage.local.get('lang');
    if (lang === 'ja' || lang === 'en') { LANG = lang; return true; }
  } catch { /* 読めなければ Chrome の言語のまま */ }
  return false;
}
