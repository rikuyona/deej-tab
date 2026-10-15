"""新しい版の確認と更新のテスト。GitHub には問い合わせず、偽の一覧・ダウンロードで確かめる"""

import asyncio
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import deej_tab as d  # noqa: E402
import updater  # noqa: E402

DL = "https://github.com/rikuyona/deej-tab/releases/download/"
PAGE = "https://github.com/rikuyona/deej-tab/releases/tag/"


def asset(name, tag, data=b"x", digest=True, url=None):
    return {"name": name, "size": len(data),
            "browser_download_url": url or f"{DL}{tag}/{name}",
            "digest": "sha256:" + hashlib.sha256(data).hexdigest() if digest else None}


def release(tag, *assets, prerelease=False, draft=False):
    return {"tag_name": tag, "html_url": PAGE + tag, "prerelease": prerelease, "draft": draft, "assets": list(assets)}


RELEASES = [
    # 拡張機能だけの Release (アプリのファイルは前の版のまま一緒に載せる)
    release("extension-v1.6.0", asset("deej-tab-1.4.0-setup.exe", "extension-v1.6.0"),
            asset("deej-tab-extension-1.6.0.zip", "extension-v1.6.0")),
    release("v1.5.0", asset("deej-tab-1.5.0-setup.exe", "v1.5.0", b"setup-1.5.0"),
            asset("deej-tab-1.5.0-windows.zip", "v1.5.0"), asset("deej-tab-1.5.0-mac.dmg", "v1.5.0")),
    release("v1.6.0-beta", asset("deej-tab-1.6.0-setup.exe", "v1.6.0-beta"), prerelease=True),
    release("v1.7.0", asset("deej-tab-1.7.0-setup.exe", "v1.7.0"), draft=True),
    release("v1.4.0", asset("deej-tab-1.4.0-setup.exe", "v1.4.0"), asset("deej-tab-1.4.0-mac.dmg", "v1.4.0")),
    release("pcb-v0.2.0", asset("deej-tab-pcb-0.2.0-gerber.zip", "pcb-v0.2.0")),
]


class VersionTest(unittest.TestCase):
    def test_newer(self):
        self.assertTrue(updater.newer("1.4.10", "1.4.9"))
        self.assertTrue(updater.newer("1.5", "1.4.9"))
        self.assertFalse(updater.newer("1.4.0", "1.4"))
        self.assertFalse(updater.newer("1.4.0", "1.4.0"))
        self.assertFalse(updater.newer("abc", "1.0"))


class LatestReleaseTest(unittest.TestCase):
    def test_picks_newest_with_the_right_file(self):
        r = updater.latest_release(RELEASES, "setup")
        self.assertEqual(r["version"], "1.5.0")   # プレリリース・下書きは見ない
        self.assertEqual(r["name"], "deej-tab-1.5.0-setup.exe")
        self.assertEqual(r["page"], PAGE + "v1.5.0")
        self.assertEqual(r["sha256"], hashlib.sha256(b"setup-1.5.0").hexdigest())
        self.assertTrue(r["url"].startswith(DL))
        self.assertEqual(updater.latest_release(RELEASES, "mac")["version"], "1.5.0")
        self.assertEqual(updater.latest_release(RELEASES, "zip")["version"], "1.5.0")

    def test_nothing(self):
        self.assertIsNone(updater.latest_release([], "setup"))
        self.assertIsNone(updater.latest_release({"message": "rate limit"}, "setup"))

    def test_refuses_files_it_cannot_check(self):
        rel = [release("v2.0.0", asset("deej-tab-2.0.0-setup.exe", "v2.0.0", digest=False))]
        self.assertIsNone(updater.latest_release(rel, "setup")["url"])   # SHA-256 がない
        rel = [release("v2.0.0", asset("deej-tab-2.0.0-setup.exe", "v2.0.0", url="https://example.com/setup.exe"))]
        self.assertIsNone(updater.latest_release(rel, "setup")["url"])   # ほかの場所のファイル

    def test_page_must_be_this_repository(self):
        rel = [release("v2.0.0", asset("deej-tab-2.0.0-setup.exe", "v2.0.0"))]
        rel[0]["html_url"] = "https://example.com/"
        self.assertEqual(updater.latest_release(rel, "setup")["page"], updater.RELEASES_PAGE)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.data = b"installer" * 10000
        self.rel = {"name": "deej-tab-1.5.0-setup.exe", "url": DL + "v1.5.0/deej-tab-1.5.0-setup.exe",
                    "size": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest()}

    def test_downloads_and_checks(self):
        seen = []
        path = updater.download(self.rel, self.dir, seen.append, opener=lambda req, timeout: FakeResponse(self.data))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), self.data)
        self.assertEqual(seen[-1], 100)
        self.assertEqual(os.listdir(self.dir), ["deej-tab-1.5.0-setup.exe"])

    def test_wrong_hash_is_removed(self):
        with self.assertRaisesRegex(updater.UpdateError, "記録と合いません"):
            updater.download(self.rel, self.dir, opener=lambda req, timeout: FakeResponse(b"x" * len(self.data)))
        self.assertEqual(os.listdir(self.dir), [])

    def test_cut_off(self):
        with self.assertRaisesRegex(updater.UpdateError, "途中で切れました"):
            updater.download(self.rel, self.dir, opener=lambda req, timeout: FakeResponse(self.data[:100]))
        self.assertEqual(os.listdir(self.dir), [])

    def test_refuses_without_hash(self):
        with self.assertRaises(updater.UpdateError):
            updater.download(dict(self.rel, sha256=None), self.dir)

    def test_not_installed_by_setup_here(self):
        self.assertFalse(updater.installed_by_setup(self.dir))


def make_app():
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("slider_mapping:\n  0: master\ncom_port: socket://127.0.0.1:9\n")
    c = d.Config(path)
    c.load()
    app = d.DeejTab(c, d.DummyAudio, system=False)
    app.broadcast = lambda m: None
    app.sent_ui = []
    app.broadcast_ui = app.sent_ui.append
    return app


class AppUpdateTest(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.dir = tempfile.mkdtemp()
        p = mock.patch.multiple(d, UPDATE_DIR=self.dir, UPDATE_PENDING=os.path.join(self.dir, "pending.json"),
                                VERSION="1.4.0")
        p.start()
        self.addCleanup(p.stop)

    def check(self, releases=RELEASES):
        with mock.patch.object(updater, "fetch_releases", return_value=releases):
            asyncio.run(self.app.check_update())

    def test_check_finds_newer(self):
        self.app.update_kind = "setup"
        self.check()
        st = self.app.update_status()
        self.assertEqual((st["current"], st["latest"], st["can_install"], st["error"]), ("1.4.0", "1.5.0", True, ""))
        self.assertIsNotNone(st["checked"])
        self.assertEqual(self.app.ui_status()["update"], st)

    def test_check_up_to_date(self):
        self.app.update_kind = "setup"
        with mock.patch.object(d, "VERSION", "1.5.0"):
            self.check()
        self.assertIsNone(self.app.update_status()["latest"])

    def test_check_offline(self):
        self.app.update_kind = "setup"
        with mock.patch.object(updater, "fetch_releases", side_effect=OSError("getaddrinfo failed")):
            with self.assertLogs("deej-tab", "WARNING"):
                asyncio.run(self.app.check_update())
        st = self.app.update_status()
        self.assertIn("GitHub に接続できません", st["error"])
        self.assertFalse(st["checking"])

    def test_zip_and_mac_only_open_the_page(self):
        for kind in ("zip", "mac"):
            self.app.update_kind = kind
            self.check()
            self.assertFalse(self.app.update_status()["can_install"])
            with self.assertRaisesRegex(ValueError, "ダウンロードのページ"):
                self.app.start_update()
        with mock.patch("webbrowser.open") as op:
            asyncio.run(self.app.ui_command(None, {"type": "open_update_page"}))
        op.assert_called_once_with(PAGE + "v1.5.0")

    def test_install_downloads_marks_and_runs_setup(self):
        self.app.update_kind = "setup"
        self.check()
        path = os.path.join(self.dir, "deej-tab-1.5.0-setup.exe")
        with mock.patch.object(updater, "download", return_value=path) as dl, \
                mock.patch.object(updater, "run_setup") as run:
            self.app._update(dict(self.app.update["latest"]))
        dl.assert_called_once()
        run.assert_called_once_with(path)
        with open(os.path.join(self.dir, "pending.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"version": "1.5.0", "from": "1.4.0"})
        self.assertEqual(self.app.sent_ui[-1]["type"], "notice")

    def test_install_failure_is_reported(self):
        self.app.update_kind = "setup"
        self.check()
        self.app.update["progress"] = 0
        with mock.patch.object(updater, "download", side_effect=updater.UpdateError("記録と合いません")), \
                mock.patch.object(updater, "run_setup") as run:
            with self.assertLogs("deej-tab", "WARNING"):
                self.app._update(dict(self.app.update["latest"]))
        run.assert_not_called()
        self.assertIsNone(self.app.update["progress"])
        self.assertEqual(self.app.sent_ui[-1], {"type": "error", "message": "更新できません: 記録と合いません"})

    def test_no_double_start(self):
        self.app.update_kind = "setup"
        self.check()
        self.app.update["progress"] = 10
        with self.assertRaisesRegex(ValueError, "更新しています"):
            self.app.start_update()

    def test_result_after_update(self):
        with open(os.path.join(self.dir, "pending.json"), "w", encoding="utf-8") as f:
            json.dump({"version": "1.4.0", "from": "1.3.2"}, f)
        with self.assertLogs("deej-tab", "INFO"):
            n = d.take_update_result()
        self.assertEqual(n["type"], "notice")
        self.assertIn("1.4.0 に更新しました", n["message"])
        self.assertFalse(os.path.exists(self.dir))   # 落としたインストーラーも消す
        self.assertIsNone(d.take_update_result())

    def test_result_when_update_did_not_finish(self):
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, "pending.json"), "w", encoding="utf-8") as f:
            json.dump({"version": "1.5.0", "from": "1.4.0"}, f)
        with self.assertLogs("deej-tab", "WARNING"):
            n = d.take_update_result()
        self.assertEqual(n["type"], "error")

    def test_option_update_check(self):
        self.app.ui_joined = asyncio.Event()
        asyncio.run(self.app.ui_command(None, {"type": "set_option", "key": "update_check", "value": False}))
        self.assertFalse(self.app.config.update_check)
        self.assertFalse(self.app.ui_config()["options"]["update_check"])
        asyncio.run(self.app.ui_command(None, {"type": "set_option", "key": "update_check", "value": True}))
        self.assertTrue(self.app.update_wake.is_set())   # オンにしたらすぐ確かめる


class UpdateLoopTest(unittest.TestCase):
    def run_loop(self, body, update_check=True):
        app = make_app()
        app.update_kind = "setup"
        app.config.update_check = update_check
        calls = []

        async def fake_check():
            calls.append(1)

        app.check_update = fake_check

        async def main():
            app.update_wake = asyncio.Event()
            task = asyncio.ensure_future(app.update_loop())
            try:
                await body(app, calls)
            finally:
                task.cancel()
        with mock.patch.object(d, "UPDATE_FIRST_DELAY", 0.2):
            asyncio.run(main())
        return calls

    def test_checks_after_first_delay(self):
        async def body(app, calls):
            await asyncio.sleep(0.1)
            self.assertEqual(len(calls), 0)
            await asyncio.sleep(0.3)
        calls = self.run_loop(body)
        self.assertEqual(len(calls), 1)   # 次は 1 日後

    def test_off_does_not_check_but_manual_does(self):
        async def body(app, calls):
            await asyncio.sleep(0.4)
            app.update_wake.set()
            await asyncio.sleep(0.1)
        calls = self.run_loop(body, update_check=False)
        self.assertEqual(len(calls), 1)   # 「今すぐ確かめる」の 1 回だけ


if __name__ == "__main__":
    unittest.main()
