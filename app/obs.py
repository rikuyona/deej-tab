"""OBS 連携: obs-websocket (v5。OBS 28 以降に標準で入っている) で音声ソースの音量を変える

- 割り当て先は "obs:ソース名" (設定ファイルは小文字になるので、名前は大文字小文字を区別せずに探す)
- 音量は拡張機能のタブと同じカーブ: 100% までは 2 乗、超えた分はそのままの倍率 (OBS は 20 倍まで)
- 同じソースへの変更は間引いて、最後の値だけ送る
"""

import asyncio
import base64
import hashlib
import json
import logging
import uuid

import websockets
from websockets.asyncio.client import connect

log = logging.getLogger("deej-tab")

RETRY = 5.0            # 秒。つながらない時の再接続間隔
SEND_INTERVAL = 0.03   # 秒。音量を送る最短間隔
SUB_INPUTS = 1 << 3    # イベント: 入力の作成・削除・名前変更・音量変更
MAX_MUL = 20.0


def volume_to_mul(v):
    v = max(0.0, min(MAX_MUL, v))
    return v * v if v <= 1 else v


def auth_string(password, salt, challenge):
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class ObsClient:
    """asyncio のループの中で動く。settings() で {enabled, host, port, password} を返す関数を受け取る"""

    def __init__(self, settings, on_change=None):
        self.settings = settings
        self.on_change = on_change or (lambda: None)   # 接続状態・ソース一覧が変わった時
        self.connected = False
        self.error = ""
        self.inputs = {}        # {ソース名: 今の倍率}
        self.original = {}      # {ソース名: 最初に変える前の倍率} (元に戻す用)
        self.ws = None
        self.pending = {}       # {ソース名: 送る倍率}
        self.flushing = False
        self.futures = {}
        self.wake = asyncio.Event()
        self._identified = asyncio.Event()
        self.loop = None

    # ---------- 外から呼ぶ (どのスレッドからでも) ----------

    def find(self, name):
        """小文字の名前から、OBS の本当のソース名を探す"""
        name = name.lower()
        for n in self.inputs:
            if n.lower() == name:
                return n
        return None

    def set_volume(self, name, v):
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._queue, name, v)

    def restore(self):
        """最初に変える前の音量に戻す"""
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._restore)

    def reconnect(self):
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self._kick)

    def status(self):
        s = self.settings()
        return {"enabled": bool(s.get("enabled")), "connected": self.connected, "error": self.error}

    # ---------- ループの中 ----------

    def _kick(self):
        self.wake.set()
        if self.ws is not None:
            asyncio.ensure_future(self.ws.close())

    def _queue(self, name, v):
        real = self.find(name)
        if real is None or not self.connected:
            return
        mul = volume_to_mul(v)
        self.original.setdefault(real, self.inputs.get(real, 1.0))
        self.pending[real] = mul
        if not self.flushing:
            self.flushing = True
            asyncio.ensure_future(self._flush())

    def _restore(self):
        for name, mul in self.original.items():
            if name in self.inputs:
                self.pending[name] = mul
        self.original.clear()
        if self.pending and not self.flushing:
            self.flushing = True
            asyncio.ensure_future(self._flush())

    async def _flush(self):
        try:
            while self.pending:
                name, mul = self.pending.popitem()
                self.inputs[name] = mul
                try:
                    await self.request("SetInputVolume", {"inputName": name, "inputVolumeMul": mul}, wait=False)
                except Exception as e:
                    log.warning("OBS の音量変更に失敗 (%s): %s", name, e)
                await asyncio.sleep(SEND_INTERVAL)
        finally:
            self.flushing = False

    async def request(self, rtype, data=None, wait=True):
        rid = uuid.uuid4().hex
        fut = None
        if wait:
            fut = asyncio.get_running_loop().create_future()
            self.futures[rid] = fut
        await self.ws.send(json.dumps({"op": 6, "d": {"requestType": rtype, "requestId": rid,
                                                       "requestData": data or {}}}))
        if fut is None:
            return None
        try:
            return await asyncio.wait_for(fut, 5)
        finally:
            self.futures.pop(rid, None)

    def _set_state(self, connected, error=""):
        if (connected, error) != (self.connected, self.error):
            self.connected, self.error = connected, error
            self.on_change()

    async def run(self):
        self.loop = asyncio.get_running_loop()
        last_error = None
        while True:
            s = self.settings()
            if not s.get("enabled"):
                self._set_state(False, "")
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), 2)
                except asyncio.TimeoutError:
                    pass
                continue
            url = f"ws://{s.get('host') or '127.0.0.1'}:{int(s.get('port') or 4455)}"
            try:
                await self._session(url, str(s.get("password") or ""))
                err = "切断されました"
            except (OSError, asyncio.TimeoutError):
                err = "OBS に接続できません (OBS が起動していて、WebSocket サーバーが有効か確認してください)"
            except websockets.exceptions.InvalidStatus as e:
                err = f"OBS に接続できません: {e}"
            except AuthError as e:
                err = str(e)
            except websockets.exceptions.ConnectionClosed as e:
                # 4009 = 認証に失敗 (obs-websocket の決まり)
                if e.rcvd is not None and e.rcvd.code == 4009:
                    err = "OBS のパスワードが違います (OBS の [ツール] → [WebSocket サーバー設定] で確認できます)"
                else:
                    err = "OBS との接続が切れました"
            except Exception as e:
                err = f"OBS との通信に失敗: {e}"
            self.ws = None
            self.inputs = {}
            self._set_state(False, err)
            if err != last_error:
                log.warning("OBS: %s", err)
                last_error = err
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), RETRY)
            except asyncio.TimeoutError:
                pass

    async def _session(self, url, password):
        async with connect(url, open_timeout=3, max_size=2 ** 22) as ws:
            self.ws = ws
            hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
            if hello.get("op") != 0:
                raise AuthError("OBS からの最初の応答が想定と違います")
            ident = {"rpcVersion": 1, "eventSubscriptions": SUB_INPUTS}
            auth = hello["d"].get("authentication")
            if auth:
                if not password:
                    raise AuthError("OBS の WebSocket にパスワードが設定されています。設定画面で入力してください")
                ident["authentication"] = auth_string(password, auth["salt"], auth["challenge"])
            await ws.send(json.dumps({"op": 1, "d": ident}))
            reader = asyncio.ensure_future(self._read(ws))
            try:
                done, _ = await asyncio.wait({reader, asyncio.ensure_future(self._identified.wait())},
                                             timeout=5, return_when=asyncio.FIRST_COMPLETED)
                if reader in done:
                    reader.result()
                if not self._identified.is_set():
                    raise AuthError("OBS に接続を認めてもらえませんでした (パスワードを確認してください)")
                log.info("OBS に接続しました: %s", url)
                await self._load_inputs()
                self._set_state(True, "")
                await reader
            finally:
                reader.cancel()
                self._identified.clear()
                for f in self.futures.values():
                    if not f.done():
                        f.cancel()
                self.futures.clear()

    async def _read(self, ws):
        async for raw in ws:
            msg = json.loads(raw)
            op, d = msg.get("op"), msg.get("d") or {}
            if op == 2:
                self._identified.set()
            elif op == 7:
                fut = self.futures.get(d.get("requestId"))
                if fut and not fut.done():
                    fut.set_result(d)
            elif op == 5:
                et, ed = d.get("eventType"), d.get("eventData") or {}
                if et == "InputVolumeChanged":
                    if ed.get("inputName") in self.inputs:
                        self.inputs[ed["inputName"]] = ed.get("inputVolumeMul", 1.0)
                elif et in ("InputCreated", "InputRemoved", "InputNameChanged"):
                    asyncio.ensure_future(self._reload_inputs())
        # 閉じた (OBS の終了など)
        if ws.close_code == 4009:
            raise AuthError("OBS のパスワードが違います")

    async def _reload_inputs(self):
        try:
            await self._load_inputs()
        except Exception as e:
            log.warning("OBS のソース一覧を取れません: %s", e)

    async def _load_inputs(self):
        """音声を持っているソースだけ (GetInputVolume が成功するもの) を、今の音量と一緒に覚える"""
        res = await self.request("GetInputList")
        names = [i["inputName"] for i in (res.get("responseData") or {}).get("inputs", [])]
        inputs = {}
        for n in names:
            r = await self.request("GetInputVolume", {"inputName": n})
            if (r.get("requestStatus") or {}).get("result"):
                inputs[n] = (r.get("responseData") or {}).get("inputVolumeMul", 1.0)
        self.inputs = inputs
        self.on_change()


class AuthError(Exception):
    pass
