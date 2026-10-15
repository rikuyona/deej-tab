"""新しい版の確認と更新 (GitHub の Releases)

- 一覧 (releases) から、使っている形 (インストーラー・zip・Mac) のファイルがあるいちばん新しい版を探す。
  Latest だけを見ないのは、拡張機能だけ・ファームウェアだけ・基板の Release もあるため (版はファイル名から読む)
- インストーラー版の Windows だけ、その場で更新できる: インストーラーを落とし、GitHub が出している
  SHA-256 (digest) と合うか確かめてから、/SILENT で起動する。インストーラーは動いている deej-tab に --quit を送って
  止め、入れ終わったら起動し直す (/deejrestart=1)
- zip 版・Mac・ソースから動かしている時は、知らせてダウンロードのページを開くだけ
- 送るのは普通の HTTPS の問い合わせだけ (GitHub の公開されている一覧を読む)。設定 (update_check) で止められる
- Mac は certifi の証明書で確かめる (Python が作った環境の証明書の置き場所を見に行き、配った .app では見つからないため)
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO = "rikuyona/deej-tab"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
PAGE_PREFIX = f"https://github.com/{REPO}/releases/"            # 開いてよいページ
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"   # 落としてよいファイル
# インストーラーの AppId (installer/deej-tab.iss)。アンインストールの登録から、インストーラー版かを見分ける
SETUP_APP_ID = "{2629F786-FC1B-4CC1-AC8F-9C03553B033F}"
TIMEOUT = 15            # 秒。問い合わせ・ダウンロードの 1 回の読み取りの待ち時間
CHUNK = 64 * 1024

# 使っている形ごとのファイル名 (版はここから読む)
KINDS = {
    "setup": re.compile(r"^deej-tab-(\d+(?:\.\d+){1,3})-setup\.exe$"),
    "zip": re.compile(r"^deej-tab-(\d+(?:\.\d+){1,3})-windows\.zip$"),
    "mac": re.compile(r"^deej-tab-(\d+(?:\.\d+){1,3})-mac\.dmg$"),
}
DIGEST_RE = re.compile(r"^sha256:([0-9a-f]{64})$")


class UpdateError(Exception):
    pass


def parse_version(v):
    """"1.4.10" → (1, 4, 10)。読めなければ ()"""
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return ()


def newer(a, b):
    """版 a が b より新しいか"""
    pa, pb = parse_version(a), parse_version(b)
    n = max(len(pa), len(pb))
    return pa + (0,) * (n - len(pa)) > pb + (0,) * (n - len(pb))


def latest_release(releases, kind):
    """GitHub の releases の一覧から、kind のファイルがあるいちばん新しい版。
    {"version", "page", "name", "url", "size", "sha256"} か None。下書き・プレリリースは見ない"""
    pattern = KINDS[kind]
    best = None
    for r in releases if isinstance(releases, list) else []:
        if not isinstance(r, dict) or r.get("draft") or r.get("prerelease"):
            continue
        page = str(r.get("html_url") or "")
        for a in r.get("assets") or []:
            m = pattern.match(str(a.get("name") or ""))
            if not m or (best and not newer(m.group(1), best["version"])):
                continue
            url = str(a.get("browser_download_url") or "")
            digest = DIGEST_RE.match(str(a.get("digest") or ""))
            best = {
                "version": m.group(1),
                "page": page if page.startswith(PAGE_PREFIX) else RELEASES_PAGE,
                "name": a["name"],
                # 落としてよいのは、このリポジトリの Releases のファイルで、SHA-256 がわかるものだけ
                "url": url if url.startswith(DOWNLOAD_PREFIX) and digest else None,
                "size": int(a.get("size") or 0),
                "sha256": digest.group(1) if digest else None,
            }
    return best


def ssl_context():
    """HTTPS の証明書の確かめ方。Mac は certifi の証明書を使う (None なら Python の既定)。
    Mac の Python (Homebrew・python.org) は、作った環境の証明書の置き場所 (/opt/homebrew/etc/openssl@3 など) を
    見に行くので、それがない Mac に配った .app では CERTIFICATE_VERIFY_FAILED になる。Windows は OS の証明書を読む"""
    if sys.platform != "darwin":
        return None
    import ssl
    import certifi
    return ssl.create_default_context(cafile=certifi.where())


def fetch_releases(version):
    """GitHub から releases の一覧を読む"""
    req = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"deej-tab/{version}",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl_context()) as r:
        return json.load(r)


def download(release, folder, progress=None, opener=urllib.request.urlopen):
    """release のファイルを folder に落とし、大きさと SHA-256 が合えばそのパスを返す。
    progress(0〜100) で進み具合を知らせる。合わなければ消して UpdateError"""
    if not release.get("url") or not release.get("sha256"):
        raise UpdateError("このファイルは確かめられないので、ダウンロードのページから入れてください")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, release["name"])
    part = path + ".part"
    h = hashlib.sha256()
    got = 0
    size = release.get("size") or 0
    try:
        req = urllib.request.Request(release["url"], headers={"User-Agent": "deej-tab"})
        with opener(req, timeout=TIMEOUT) as r, open(part, "wb") as f:
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress and size:
                    progress(min(99, got * 100 // size))
        if size and got != size:
            raise UpdateError(f"ダウンロードが途中で切れました ({got} / {size} バイト)")
        if h.hexdigest() != release["sha256"]:
            raise UpdateError("ダウンロードしたファイルが GitHub の記録と合いません。もう一度試してください")
        os.replace(part, path)
    except Exception:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    if progress:
        progress(100)
    return path


def installed_by_setup(app_dir):
    """app_dir がインストーラーで入れた場所か (Windows のアンインストールの登録を見る)"""
    if sys.platform != "win32":
        return False
    import winreg
    key = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{SETUP_APP_ID}_is1"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            loc = winreg.QueryValueEx(k, "InstallLocation")[0]
    except OSError:
        return False
    norm = lambda p: os.path.normcase(os.path.normpath(p))
    return bool(loc) and norm(loc) == norm(app_dir)


def run_setup(path):
    """落としたインストーラーを起動する。インストーラーが deej-tab に --quit を送って止め、入れ終わったら起動し直す。
    インストーラーが起動する deej-tab (--quit と、入れ終わった後) に、この exe の PyInstaller の環境を引き継がせない"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/deejrestart=1"],
                     close_fds=True, cwd=os.path.dirname(path), env=env)
