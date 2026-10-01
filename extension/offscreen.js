// 方式Y: タブ音声をキャプチャしてゲインをかけて再生する
//
//   source → gain ─┬→ dry ─────────────────────→ 出力   (100% 以下の時)
//                  └→ pre(1/HEADROOM) → limiter → wet → 出力   (100% を超える時)
//
// 100% を超えると音が割れやすいので、その時だけ柔らかく頭を抑えるリミッターを通す。
// 100% 以下では通さない (音を一切変えない)。切り替えは dry / wet を滑らかに入れ替える
const HEADROOM = 4;       // 最大ゲイン (background.js の MAX_GAIN と同じ)
const KNEE = 0.9;         // これより大きい振幅だけを抑える
const SMOOTH = 0.02;      // 秒。ゲインの変化をなめらかにする時定数

const METER_MS = 66;      // 音の大きさを測って送る間隔 (15 回/秒)

const captures = new Map(); // slot -> { ctx, gain, dry, wet, stream, analyser }
let limiterCurve = null;
let meterOn = false;
let meterTimer = null;

// WaveShaper の入力は -1〜1 なので、1/HEADROOM に縮めて入れ、カーブの中で元の振幅に戻して抑える
function makeLimiterCurve() {
  const n = 2 * 4096 * HEADROOM + 1;
  const curve = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const x = ((i / (n - 1)) * 2 - 1) * HEADROOM;
    const a = Math.abs(x);
    const y = a <= KNEE ? a : KNEE + (1 - KNEE) * Math.tanh((a - KNEE) / (1 - KNEE));
    curve[i] = Math.sign(x) * y;
  }
  return curve;
}

function setGain(c, value, now = false) {
  const t = c.ctx.currentTime;
  const boost = value > 1;
  if (now) {
    c.gain.gain.value = value;
    c.dry.gain.value = boost ? 0 : 1;
    c.wet.gain.value = boost ? 1 : 0;
    return;
  }
  c.gain.gain.setTargetAtTime(value, t, SMOOTH);
  c.dry.gain.setTargetAtTime(boost ? 0 : 1, t, SMOOTH);
  c.wet.gain.setTargetAtTime(boost ? 1 : 0, t, SMOOTH);
}

async function start(slot, streamId, gainValue) {
  stop(slot);
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
  });
  const ctx = new AudioContext({ latencyHint: 'interactive' });
  const gain = ctx.createGain();
  const dry = ctx.createGain();
  const pre = ctx.createGain();
  const wet = ctx.createGain();
  const limiter = ctx.createWaveShaper();
  pre.gain.value = 1 / HEADROOM;
  limiter.curve = limiterCurve ||= makeLimiterCurve();
  ctx.createMediaStreamSource(stream).connect(gain);
  gain.connect(dry).connect(ctx.destination);
  gain.connect(pre).connect(limiter).connect(wet).connect(ctx.destination);
  const c = { ctx, gain, dry, wet, stream, analyser: null };
  setGain(c, gainValue, true);
  stream.getAudioTracks().forEach((t) => t.addEventListener('ended', () => {
    // capture-move でスロットが変わっていることがあるので、今のスロットを探す
    const s = [...captures].find(([, v]) => v === c)?.[0];
    if (s === undefined) return;
    stop(s);
    chrome.runtime.sendMessage({ type: 'capture-ended', slot: s }).catch(() => {});
  }));
  captures.set(slot, c);
  updateMeter();
}

// ---------------------------------------------------------------- 音の大きさ (LED を音に合わせる時だけ)
// 聞こえている音 (スライダーの音量をかけた後) の一番大きい振幅を 0〜1 で送る

function updateMeter() {
  if (meterOn && captures.size && !meterTimer) {
    meterTimer = setInterval(sendLevels, METER_MS);
  } else if ((!meterOn || !captures.size) && meterTimer) {
    clearInterval(meterTimer);
    meterTimer = null;
  }
}

function sendLevels() {
  const levels = {};
  for (const [slot, c] of captures) {
    if (!c.analyser) {
      c.analyser = c.ctx.createAnalyser();
      c.analyser.fftSize = 1024;
      c.buf = new Float32Array(c.analyser.fftSize);
      c.gain.connect(c.analyser);
    }
    c.analyser.getFloatTimeDomainData(c.buf);
    let peak = 0;
    for (const x of c.buf) peak = Math.max(peak, Math.abs(x));
    levels[slot] = Math.min(1, +peak.toFixed(3));
  }
  chrome.runtime.sendMessage({ type: 'meter-levels', levels }).catch(() => {});
}

function stop(slot) {
  const c = captures.get(slot);
  if (!c) return;
  captures.delete(slot);
  c.stream.getTracks().forEach((t) => t.stop());
  c.ctx.close();
  updateMeter();
}

// 別のスロットへ移す (キャプチャは取り直さない)
function move(from, to) {
  const c = captures.get(from);
  if (!c) return false;
  stop(to);
  captures.delete(from);
  captures.set(to, c);
  return true;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.target !== 'offscreen') return;
  switch (msg.type) {
    case 'meter-on':
      meterOn = !!msg.on;
      updateMeter();
      break;
    case 'capture-start':
      if (msg.meter !== undefined) meterOn = !!msg.meter;
      start(msg.slot, msg.streamId, msg.gain)
        .then(() => sendResponse({ ok: true }))
        .catch((e) => sendResponse({ ok: false, error: String(e?.message || e) }));
      return true;
    case 'capture-move':
      sendResponse({ ok: move(msg.from, msg.to) });
      break;
    case 'capture-list':
      sendResponse({ slots: [...captures.keys()] });
      break;
    case 'gain': {
      const c = captures.get(msg.slot);
      if (c) setGain(c, msg.gain);
      break;
    }
    case 'capture-stop':
      stop(msg.slot);
      sendResponse({ ok: true });
      break;
    case 'capture-stop-all':
      [...captures.keys()].forEach(stop);
      sendResponse({ ok: true });
      break;
  }
});
