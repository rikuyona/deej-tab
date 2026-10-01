// deej-6ch-led: スライダー 5 本 (LED 付き) + ノブ 1 つの deej 互換ファームウェア
//
// 送信 (Nano → PC): 公式 deej と同じ "v0|v1|v2|v3|v4|v5\n" (0〜1023)
//   起動した時と @I を受け取った時だけ "@ID|deej-6ch-led|1.3|A1B2C3D4" (このコントローラーの ID。公式 deej は読み飛ばす)。
//   CH340 には USB のシリアル番号がなく、別の USB の口に差すと COM 番号が変わるので、deej-tab はこの ID で探し直す
// 受信 (PC → Nano): LED の指示 (deej-tab 1.2 以降。app/leds.py と同じ取り決め)
//   @L|g|m0|m1|m2|m3|m4   g = 0 普通 / 1 一時停止
//                         m = N 普通 / O 消灯 / D 暗く / B 明滅 (100% 超え) / P 点滅 (ピックアップ待ち)
//   @V|l0|l1|l2|l3|l4     音の大きさ 0〜255 (LED を音に合わせて光らせる時)
//   @P|DIM=20|BRIGHT=255… 光り方の数値 (下の既定値と同じ名前。FADE_UP / FADE_DOWN は %)。
//                         変わったら 2 秒後に EEPROM に保存し、次に電源を入れた時もその光り方で動く
//   @B                    起動アニメをもう一度流す
//   @I                    ID をもう一度送る
//   @C|i|lo|hi            スライダー i (0〜5) の端の位置。生の値 lo を 0、hi を 1023 とみなす (deej-tab の「端の位置を合わせる」)。
//                         EEPROM に保存し、LED の判断 (0 で消灯・位置で明るさ) に使う。PC に送る値は生のまま
// PC から 3 秒何も来なければ、PC なしの光り方 (操作したら明るく約 2 秒、0 で消灯、5 分で減光) に戻る。
// 光り方の計算は app/leds.py の led_brightness() と同じにしてある (直す時は両方直す)。
//
// 配線: スライダー A0〜A4 (LED は D3 / D5 / D6 / D9 / D10、330Ω)、ノブ A5。D0 / D1 は USB シリアル

#include <EEPROM.h>

const char FW_NAME[] = "deej-6ch-led";
const char FW_VERSION[] = "1.3";

const int NUM_SLIDERS = 6;
const int NUM_LEDS = 5;
const int analogInputs[NUM_SLIDERS] = {A0, A1, A2, A3, A4, A5};
const int ledPins[NUM_LEDS] = {3, 5, 6, 9, 10};

// ---- 光り方の数値の既定値 (app/leds.py の PARAMS と同じ) ----
// deej-tab の設定画面で変えた数値は @P で届き、EEPROM に保存したものがこれより優先される
long DIM = 20;             // 普段の明るさ (0〜255)
long BRIGHT = 255;         // 操作した直後
long SLEEP = 4;            // 5 分操作がない時
long INACTIVE = 6;         // 割り当て先が動いていない時
long INACTIVE_TOUCH = 60;  // 割り当て先が動いていない時に操作した直後
long TOUCH_MS = 2000;      // 操作してから明るいままの時間
long SLEEP_MS = 300000;    // 5 分
long BREATH_MS = 1600;     // 100% 超えの明滅の周期
long BREATH_LOW = 60;      // 100% 超えの明滅の暗い側
long PAUSE_MS = 3000;      // 一時停止中のゆっくりした明滅の周期
long BLINK_MS = 250;       // ピックアップ待ちの点滅 (点灯・消灯それぞれ)
long BLINK_LEVEL = 160;    // ピックアップ待ちの点滅の明るさ
long SWEEP_MS = 120;       // 起動アニメの 1 こまの時間
long IDLE_STYLE = 0;       // 普段の光り方 0 一定 / 1 呼吸 / 2 流れる波 / 3 きらめき / 4 心拍 / 5 スキャナー / 6 位置で明るさ
long IDLE_MS = 4000;       // 普段の光り方の周期 (心拍はこの半分、きらめきは 1/4 ごとに光るか決める)
long IDLE_DEPTH = 180;     // 普段の光り方の強さ (DIM に上乗せする一番明るい所)
long TWINKLE_CHANCE = 20;  // きらめき: 1 こまで各 LED が光る確率 (%)
long BOOT_STYLE = 0;       // 起動アニメ 0 左から右へ / 1 中央から広がる / 2 全体がふわっと点く / 3 往復する
long TOUCH_STYLE = 0;      // 操作した時 0 明るく光る / 1 波紋 (端まで広がる) / 2 位置を表示
long RIPPLE_SPREAD = 60;   // 波紋: 1 つ離れるごとに残る明るさ (%)
long FADE_UP = 50;              // 明るくなる速さ (10ms ごとに差のこの % だけ近づく)
long FADE_DOWN = 15;            // 暗くなる速さ (%)
// ---- ここまで ----

// @P で受け取れる数値と範囲 (app/leds.py の PARAM_LIMITS と同じ)。
// EEPROM には この順で保存するので、新しい数値は必ず最後に足す
struct Param { const char *name; long *value; long lo; long hi; };
Param paramTable[] = {
  {"DIM", &DIM, 0, 255}, {"BRIGHT", &BRIGHT, 20, 255}, {"SLEEP", &SLEEP, 0, 255},
  {"INACTIVE", &INACTIVE, 0, 255}, {"INACTIVE_TOUCH", &INACTIVE_TOUCH, 0, 255},
  {"TOUCH_MS", &TOUCH_MS, 200, 6000}, {"SLEEP_MS", &SLEEP_MS, 10000, 3600000},
  {"BREATH_MS", &BREATH_MS, 300, 5000}, {"BREATH_LOW", &BREATH_LOW, 0, 255},
  {"PAUSE_MS", &PAUSE_MS, 500, 8000}, {"BLINK_MS", &BLINK_MS, 60, 1000}, {"BLINK_LEVEL", &BLINK_LEVEL, 10, 255},
  {"FADE_UP", &FADE_UP, 2, 100}, {"FADE_DOWN", &FADE_DOWN, 1, 100}, {"SWEEP_MS", &SWEEP_MS, 30, 500},
  {"IDLE_STYLE", &IDLE_STYLE, 0, 6}, {"IDLE_MS", &IDLE_MS, 800, 10000}, {"IDLE_DEPTH", &IDLE_DEPTH, 0, 255},
  {"TWINKLE_CHANCE", &TWINKLE_CHANCE, 0, 100}, {"BOOT_STYLE", &BOOT_STYLE, 0, 3}, {"TOUCH_STYLE", &TOUCH_STYLE, 0, 2},
  {"RIPPLE_SPREAD", &RIPPLE_SPREAD, 10, 95},
};
const int NUM_PARAMS = sizeof(paramTable) / sizeof(paramTable[0]);
const unsigned int EEPROM_MAGIC = 0xDE01;   // EEPROM に deej-tab の数値が入っている印
const unsigned long SAVE_DELAY_MS = 2000;   // 最後に数値が届いてから保存するまで (スライダーを動かしている間は書かない)
bool paramsDirty = false;            // 光り方の数値か端の位置が変わった (SAVE_DELAY_MS 後にまとめて保存)
unsigned long paramsChangedAt = 0;

// 端の位置 (キャリブレーション)。範囲は app/leds.py の valid_calibration と同じ
const int CAL_ADDR = 200;                   // EEPROM の場所 (光り方の数値の後ろ。数値が増えても重ならないよう離してある)
const unsigned int CAL_MAGIC = 0xCA01;
const int CAL_LO_MAX = 400, CAL_HI_MIN = 623, CAL_MIN_SPAN = 300;
int calLo[NUM_SLIDERS] = {0, 0, 0, 0, 0, 0};
int calHi[NUM_SLIDERS] = {1023, 1023, 1023, 1023, 1023, 1023};

// このコントローラーの ID。初めて起動した時に作って EEPROM に保存する (書き込み直しても消えない)
const int ID_ADDR = 240;
const unsigned int ID_MAGIC = 0x1D01;
unsigned long deviceId = 0;

const unsigned long PC_TIMEOUT_MS = 3000;
const unsigned long METER_TIMEOUT_MS = 300;
const int TOUCH_THRESHOLD = 8;      // これ以上動いたら「操作した」(ノイズで光らないように)
const int OVERSAMPLE = 8;           // 1 回の値を何回の読み取りの平均にするか (実機は触っていなくても ±6 揺れた)
const int HOLD = 3;                 // 平均してもこれ未満の変化は前の値のまま (端の 0 / 1023 は必ず出す)

int analogValues[NUM_SLIDERS];
int lastTouchValue[NUM_SLIDERS];
unsigned long touched[NUM_LEDS];
unsigned long idleSince = 0;
unsigned long bootStart = 0;
float shown[NUM_LEDS];              // 実際に出している明るさ (なめらかに変える)

// PC からの指示
bool pcSeen = false;
unsigned long pcTime = 0;
bool pcPaused = false;
char pcMode[NUM_LEDS] = {'N', 'N', 'N', 'N', 'N'};
bool meterSeen = false;
unsigned long meterTime = 0;
int meterLevel[NUM_LEDS];

char rx[64];
int rxLen = 0;

// ---- 光り方の計算 (app/leds.py と同じ。直す時は両方直す) ----

int clamp8(long v) { return v < 0 ? 0 : (v > 255 ? 255 : (int)v); }

// lo → hi → lo を period ミリ秒で繰り返す三角波
long triangle(unsigned long t, unsigned long period, long lo, long hi) {
  if (period < 2) period = 2;
  unsigned long x = t % period;
  unsigned long half = period / 2;
  unsigned long up = x < half ? x : period - x;   // 0 → half → 0
  return lo + (hi - lo) * (long)up / (long)half;
}

// きらめきの乱数 (32 ビットで回る)
unsigned long twinkleHash(unsigned long slot, int i) {
  unsigned long h = slot * 1103515245UL + (unsigned long)i * 2654435761UL + 12345UL;
  return (h >> 16) & 0x7FFF;
}

// 心拍: ドクン (強)、ドクン (弱) の 2 回
long heartbeat(unsigned long now, unsigned long period, long amp) {
  if (period < 400) period = 400;
  unsigned long x = now % period;
  if (x < 120) return amp * (long)(x < 60 ? x : 120 - x) / 60;
  if (x >= 200 && x < 320) {
    unsigned long y = x - 200;
    return amp * 6 / 10 * (long)(y < 60 ? y : 120 - y) / 60;
  }
  return 0;
}

// 普段の明るさ
int idleLevel(unsigned long now, long base, int i, int raw) {
  long d = IDLE_DEPTH;
  unsigned long period = IDLE_MS;
  if (IDLE_STYLE == 0 || base <= SLEEP) return (int)base;
  if (IDLE_STYLE == 1) return clamp8(triangle(now, period, base, base + d));
  if (IDLE_STYLE == 2) return clamp8(triangle(now + (unsigned long)i * period / (NUM_LEDS * 2), period, base, base + d));
  if (IDLE_STYLE == 3) {
    unsigned long slotMs = period / 4 < 50 ? 50 : period / 4;
    if ((long)(twinkleHash(now / slotMs, i) % 100) < TWINKLE_CHANCE) return clamp8(base + triangle(now, slotMs, 0, d));
    return (int)base;
  }
  if (IDLE_STYLE == 4) return clamp8(base + heartbeat(now, period / 2, d));
  if (IDLE_STYLE == 5) {
    long pos = triangle(now, period, 0, (NUM_LEDS - 1) * 100);
    long dist = labs((long)i * 100 - pos);
    return dist < 150 ? clamp8(base + d * (150 - dist) / 150) : (int)base;
  }
  if (IDLE_STYLE == 6) return clamp8((long)raw * (base + d) / 1023);
  return (int)base;
}

// 起動アニメの明るさ。終わっていたら -1
int bootLevel(int i, unsigned long t) {
  unsigned long step = SWEEP_MS < 10 ? 10 : SWEEP_MS;
  unsigned long k = t / step;
  if (BOOT_STYLE == 1) {                       // 中央から広がる
    if (k >= 3) return -1;
    return abs(i - 2) == (int)k ? (int)BRIGHT : (int)DIM;
  }
  if (BOOT_STYLE == 2) {                       // 全体がふわっと点く
    unsigned long total = step * 6, half = total / 2;
    if (t >= total) return -1;
    if (t < half) return (int)(BRIGHT * (long)t / (long)half);
    return (int)(BRIGHT - (BRIGHT - DIM) * (long)(t - half) / (long)(total - half));
  }
  if (BOOT_STYLE == 3) {                       // 往復する
    const int order[9] = {0, 1, 2, 3, 4, 3, 2, 1, 0};
    if (k >= 9) return -1;
    return order[k] == i ? (int)BRIGHT : (int)DIM;
  }
  if (k >= (unsigned long)NUM_LEDS) return -1; // 左から右へ流れる
  return (int)k == i ? (int)BRIGHT : (int)DIM;
}

// 操作した直後の明るさ (位置を表示なら、動かしている間は位置に合わせる)
int touchLevel(int raw) {
  if (TOUCH_STYLE == 2) {
    long v = (long)raw * BRIGHT / 1023;
    return (int)(v > DIM ? v : DIM);
  }
  return (int)BRIGHT;
}

// 端の位置を合わせた値 (0〜1023)。app/leds.py の calibrated() と同じ
int calibratedValue(int i) {
  int raw = analogValues[i];
  if (raw <= calLo[i]) return 0;
  if (raw >= calHi[i]) return 1023;
  return (int)((long)(raw - calLo[i]) * 1023 / (calHi[i] - calLo[i]));
}

int ledBrightness(int i, unsigned long now, int ripple) {
  int raw = calibratedValue(i);   // 明るさの判断は端の位置を合わせた値で (操作したかどうかは生の値で見る)
  bool sleeping = now - idleSince > (unsigned long)SLEEP_MS;
  int base = sleeping ? (int)SLEEP : idleLevel(now, DIM, i, raw);
  bool lit = now - touched[i] < (unsigned long)TOUCH_MS;
  if (!pcSeen || now - pcTime > PC_TIMEOUT_MS) {
    if (raw < 8) return 0;
    if (lit) return touchLevel(raw);
    return base > ripple ? base : ripple;
  }
  if (pcPaused) return (int)triangle(now, PAUSE_MS, 0, DIM);
  char m = pcMode[i];
  if (m == 'O') return 0;
  if (m == 'P') return ((now / BLINK_MS) % 2 == 0) ? (int)BLINK_LEVEL : 0;
  if (m == 'D') {   // 割り当て先が動いていなくても、隣を操作した時の波紋は出す
    int own = lit ? (int)INACTIVE_TOUCH : (int)INACTIVE;
    return ripple > own ? ripple : own;
  }
  if (lit) return touchLevel(raw);
  if (ripple > base) base = ripple;
  if (m == 'B') return (int)triangle(now, BREATH_MS, BREATH_LOW, BRIGHT);
  if (meterSeen && now - meterTime <= METER_TIMEOUT_MS) {
    return base + (int)((long)meterLevel[i] * (BRIGHT - base) / 255);
  }
  return base;
}

// ---- 光り方の数値 (@P と EEPROM) ----

// EEPROM: [0] 印 (2 バイト) [2] 数値の個数 [3〜] 数値 (long) を paramTable の順に
void loadParams() {
  unsigned int magic;
  EEPROM.get(0, magic);
  if (magic != EEPROM_MAGIC) return;
  int count = EEPROM.read(2);
  for (int i = 0; i < NUM_PARAMS && i < count; i++) {
    long v;
    EEPROM.get(3 + i * 4, v);
    if (v >= paramTable[i].lo && v <= paramTable[i].hi) *paramTable[i].value = v;
  }
}

// EEPROM.put は変わったバイトだけ書くので、同じ数値なら書き込み回数は増えない
void saveParams() {
  EEPROM.put(0, EEPROM_MAGIC);
  EEPROM.update(2, (byte)NUM_PARAMS);
  for (int i = 0; i < NUM_PARAMS; i++) EEPROM.put(3 + i * 4, *paramTable[i].value);
}

bool validCalibration(long lo, long hi) {
  return lo >= 0 && lo <= CAL_LO_MAX && hi >= CAL_HI_MIN && hi <= 1023 && hi - lo >= CAL_MIN_SPAN;
}

// EEPROM: [CAL_ADDR] 印 (2 バイト) [+2〜] スライダーごとに 下端・上端 (int)
void loadCalibration() {
  unsigned int magic;
  EEPROM.get(CAL_ADDR, magic);
  if (magic != CAL_MAGIC) return;
  for (int i = 0; i < NUM_SLIDERS; i++) {
    int lo, hi;
    EEPROM.get(CAL_ADDR + 2 + i * 4, lo);
    EEPROM.get(CAL_ADDR + 4 + i * 4, hi);
    if (validCalibration(lo, hi)) { calLo[i] = lo; calHi[i] = hi; }
  }
}

void saveCalibration() {
  EEPROM.put(CAL_ADDR, CAL_MAGIC);
  for (int i = 0; i < NUM_SLIDERS; i++) {
    EEPROM.put(CAL_ADDR + 2 + i * 4, calLo[i]);
    EEPROM.put(CAL_ADDR + 4 + i * 4, calHi[i]);
  }
}

// ID を読む。まだなければ作る (アナログ入力の揺れと時間を混ぜる。暗号用ではなく、手持ちのコントローラーどうしが重ならなければよい)
void loadOrMakeId() {
  unsigned int magic;
  EEPROM.get(ID_ADDR, magic);
  if (magic == ID_MAGIC) {
    EEPROM.get(ID_ADDR + 2, deviceId);
    if (deviceId != 0 && deviceId != 0xFFFFFFFFUL) return;
  }
  unsigned long h = 2166136261UL;
  for (int k = 0; k < 64; k++) {
    for (int i = 0; i < NUM_SLIDERS; i++) {
      h = (h ^ (unsigned long)analogRead(analogInputs[i])) * 16777619UL;
    }
    h = (h ^ micros()) * 16777619UL;
    delayMicroseconds((h & 0x3F) + 10);
  }
  if (h == 0 || h == 0xFFFFFFFFUL) h = 0x13579BDFUL;
  deviceId = h;
  EEPROM.put(ID_ADDR + 2, deviceId);
  EEPROM.put(ID_ADDR, ID_MAGIC);
}

void sendId() {
  char buf[48];
  snprintf(buf, sizeof(buf), "@ID|%s|%s|%08lX", FW_NAME, FW_VERSION, deviceId);
  Serial.println(buf);
}

// "2|12|1008" (番号|下端|上端) を読む。範囲外は捨てる
void handleCalibration(char *s) {
  char *p1 = strchr(s, '|');
  if (!p1) return;
  char *p2 = strchr(p1 + 1, '|');
  if (!p2) return;
  int i = atoi(s);
  long lo = atol(p1 + 1), hi = atol(p2 + 1);
  if (i < 0 || i >= NUM_SLIDERS || !validCalibration(lo, hi)) return;
  if (calLo[i] != lo || calHi[i] != hi) {
    calLo[i] = (int)lo;
    calHi[i] = (int)hi;
    paramsDirty = true;
    paramsChangedAt = millis();
  }
}

// "DIM=20|BRIGHT=255" を読む。知らない名前は無視、範囲外は範囲に収める
void handleParams(char *s) {
  while (*s) {
    char *end = strchr(s, '|');
    if (end) *end = 0;
    char *eq = strchr(s, '=');
    if (eq) {
      *eq = 0;
      long v = atol(eq + 1);
      for (int i = 0; i < NUM_PARAMS; i++) {
        if (strcmp(s, paramTable[i].name) != 0) continue;
        if (v < paramTable[i].lo) v = paramTable[i].lo;
        if (v > paramTable[i].hi) v = paramTable[i].hi;
        if (*paramTable[i].value != v) {
          *paramTable[i].value = v;
          paramsDirty = true;
          paramsChangedAt = millis();
        }
        break;
      }
    }
    if (!end) break;
    s = end + 1;
  }
}

// "@L|0|N|N|D|B|O" / "@V|12|0|255|3|0" / "@P|DIM=20|…" / "@C|0|12|1008" / "@B" を読む。形が違う行は捨てる
void handleLine(char *line) {
  if (line[0] != '@') return;
  if (line[1] == 'B' && line[2] == 0) {
    bootStart = millis();
    return;
  }
  if (line[1] == 'I' && line[2] == 0) {
    sendId();
    return;
  }
  if (line[1] == 'P' && line[2] == '|') {
    handleParams(line + 3);
    return;
  }
  if (line[1] == 'C' && line[2] == '|') {
    handleCalibration(line + 3);
    return;
  }
  if (line[2] != '|') return;
  char kind = line[1];
  char *fields[1 + NUM_LEDS];
  int n = 0;
  char *p = line + 3;
  fields[n++] = p;
  while (*p && n < 1 + NUM_LEDS) {
    if (*p == '|') { *p = 0; fields[n++] = p + 1; }
    p++;
  }
  unsigned long now = millis();
  if (kind == 'L' && n == 1 + NUM_LEDS) {
    for (int i = 0; i < NUM_LEDS; i++) {
      char m = fields[1 + i][0];
      if (m != 'N' && m != 'O' && m != 'D' && m != 'B' && m != 'P') return;
    }
    pcPaused = fields[0][0] == '1';
    for (int i = 0; i < NUM_LEDS; i++) pcMode[i] = fields[1 + i][0];
    pcSeen = true;
    pcTime = now;
  } else if (kind == 'V' && n == NUM_LEDS) {
    for (int i = 0; i < NUM_LEDS; i++) {
      int v = atoi(fields[i]);
      meterLevel[i] = v < 0 ? 0 : (v > 255 ? 255 : v);
    }
    meterSeen = true;
    meterTime = now;
  }
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (rxLen > 0) {
        rx[rxLen] = 0;
        handleLine(rx);
      }
      rxLen = 0;
    } else if (rxLen < (int)sizeof(rx) - 1) {
      rx[rxLen++] = c;
    } else {
      rxLen = 0;   // 長すぎる行は捨てる
    }
  }
}

int readAveraged(int pin) {
  analogRead(pin);   // ピンを切り替えた直後の 1 回は前のピンの電圧が残るので捨てる
  long sum = 0;
  for (int k = 0; k < OVERSAMPLE; k++) sum += analogRead(pin);
  return (int)((sum + OVERSAMPLE / 2) / OVERSAMPLE);
}

void updateSliderValues() {
  unsigned long now = millis();
  for (int i = 0; i < NUM_SLIDERS; i++) {
    int v = readAveraged(analogInputs[i]);
    if (abs(v - analogValues[i]) >= HOLD || v == 0 || v == 1023) analogValues[i] = v;
    if (abs(analogValues[i] - lastTouchValue[i]) >= TOUCH_THRESHOLD) {
      lastTouchValue[i] = analogValues[i];
      if (i < NUM_LEDS) touched[i] = now;
      idleSince = now;
    }
  }
}

void sendSliderValues() {
  String builtString = String("");
  for (int i = 0; i < NUM_SLIDERS; i++) {
    builtString += String((int)analogValues[i]);
    if (i < NUM_SLIDERS - 1) builtString += String("|");
  }
  Serial.println(builtString);
}

void updateLeds() {
  unsigned long now = millis();
  // 起動アニメの間はそのまま出す
  if (bootLevel(0, now - bootStart) >= 0) {
    for (int i = 0; i < NUM_LEDS; i++) {
      shown[i] = bootLevel(i, now - bootStart);
      analogWrite(ledPins[i], (int)shown[i]);
    }
    return;
  }
  // 波紋: 操作した LED から端まで全部に広げる (1 つ離れるごとに RIPPLE_SPREAD % ずつ弱く)。
  // 割り当て先が動いていない (D) LED を操作した時は、その LED と同じ INACTIVE_TOUCH から広げる (真ん中より隣が明るくならないように)
  bool pcLive = pcSeen && now - pcTime <= PC_TIMEOUT_MS;
  int ripple[NUM_LEDS] = {0, 0, 0, 0, 0};
  if (TOUCH_STYLE == 1) {
    for (int j = 0; j < NUM_LEDS; j++) {
      if (now - touched[j] >= (unsigned long)TOUCH_MS) continue;
      long peak = (pcLive && pcMode[j] == 'D') ? INACTIVE_TOUCH : BRIGHT;
      for (int i = 0; i < NUM_LEDS; i++) {
        if (i == j) continue;
        long v = peak;
        for (int d = abs(i - j); d > 0; d--) v = v * RIPPLE_SPREAD / 100;
        if (v > ripple[i]) ripple[i] = (int)v;
      }
    }
  }
  for (int i = 0; i < NUM_LEDS; i++) {
    int target = ledBrightness(i, now, ripple[i]);
    // 明るくする時は速く、暗くする時はゆっくり (点滅・一時停止の明滅はそのまま出す)
    float k = (target > shown[i] ? FADE_UP : FADE_DOWN) / 100.0;
    if (pcLive && (pcMode[i] == 'P' || pcPaused)) k = 1.0;
    shown[i] += (target - shown[i]) * k;
    analogWrite(ledPins[i], (int)(shown[i] + 0.5));
  }
}

void setup() {
  loadParams();
  loadCalibration();
  for (int i = 0; i < NUM_SLIDERS; i++) pinMode(analogInputs[i], INPUT);
  for (int i = 0; i < NUM_LEDS; i++) {
    pinMode(ledPins[i], OUTPUT);
    shown[i] = DIM;
  }
  Serial.begin(9600);
  loadOrMakeId();
  sendId();
  for (int i = 0; i < NUM_SLIDERS; i++) {
    analogValues[i] = readAveraged(analogInputs[i]);
    lastTouchValue[i] = analogValues[i];
  }
  unsigned long now = millis();
  idleSince = now;
  bootStart = now;   // 起動アニメは updateLeds の中で時間に合わせて出す (その間も値は送る)
  for (int i = 0; i < NUM_LEDS; i++) touched[i] = now - (unsigned long)TOUCH_MS;
}

void loop() {
  readSerial();
  updateSliderValues();
  sendSliderValues();
  updateLeds();
  if (paramsDirty && millis() - paramsChangedAt >= SAVE_DELAY_MS) {
    saveParams();
    saveCalibration();
    paramsDirty = false;
  }
  delay(10);
}
