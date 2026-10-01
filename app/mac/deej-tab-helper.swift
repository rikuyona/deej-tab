// deej-tab の Mac 用の補助プログラム (deej-tab-helper)
//
// deej-tab 本体 (Python) から起動され、標準入出力の JSON (1 行 1 つ) でやりとりする。
//   本体 → {"id": 1, "cmd": "set_gains", "gains": {"discord.app": 0.25}}
//   補助 → {"id": 1, "ok": true}            失敗したら {"id": 1, "error": "..."}
//   補助 → {"event": "hotkey", "name": "pause"}   (ショートカットが押された時)
// 標準入力が閉じたら (本体が終了したら) 後片付けをして終わる。
//
// アプリごとの音量は Core Audio の Process Tap (macOS 14.2 以降) で行う:
// そのアプリの音を横取りして (横取りしている間はアプリの音は直接出ない)、音量をかけて出力デバイスに流し直す。
// 100% のアプリは横取りしない (音の遅れも負荷もない)。
//
// アプリは .app の名前 (小文字。例: discord.app) で区別する。Chrome の Helper のような子プロセスは
// 「責任を持つプロセス」(親のアプリ) と、実行ファイルのパスの一番外側の .app でまとめる。
//
// ビルド: build_mac.py (swiftc -O -swift-version 5 -target arm64-apple-macos14.2 ...)

import AppKit
import AudioToolbox
import Carbon
import CoreAudio
import Darwin
import Foundation

// MARK: - 出力

let outLock = NSLock()

func emit(_ obj: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj, options: []) else { return }
    outLock.lock()
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([0x0A]))
    outLock.unlock()
}

func logError(_ s: String) {
    FileHandle.standardError.write(("deej-tab-helper: " + s + "\n").data(using: .utf8)!)
}

struct Fail: Error, CustomStringConvertible {
    let description: String
    init(_ s: String) { description = s }
}

func onMain<T>(_ work: () throws -> T) rethrows -> T {
    if Thread.isMainThread { return try work() }
    return try DispatchQueue.main.sync(execute: work)
}

// MARK: - Core Audio の小道具

let systemObject = AudioObjectID(kAudioObjectSystemObject)
let unknownObject = AudioObjectID(kAudioObjectUnknown)

func address(_ selector: AudioObjectPropertySelector,
             _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}

func getValue<T>(_ object: AudioObjectID, _ addr: AudioObjectPropertyAddress, _ initial: T) -> T? {
    var addr = addr
    var value = initial
    var size = UInt32(MemoryLayout<T>.size)
    let err = AudioObjectGetPropertyData(object, &addr, 0, nil, &size, &value)
    return err == noErr ? value : nil
}

func getObjectList(_ object: AudioObjectID, _ addr: AudioObjectPropertyAddress) -> [AudioObjectID] {
    var addr = addr
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(object, &addr, 0, nil, &size) == noErr, size > 0 else { return [] }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(object, &addr, 0, nil, &size, &ids) == noErr else { return [] }
    return Array(ids.prefix(Int(size) / MemoryLayout<AudioObjectID>.size))
}

func getString(_ object: AudioObjectID, _ addr: AudioObjectPropertyAddress) -> String? {
    var addr = addr
    var value: Unmanaged<CFString>? = nil
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    let err = withUnsafeMutablePointer(to: &value) { ptr in
        AudioObjectGetPropertyData(object, &addr, 0, nil, &size, ptr)
    }
    guard err == noErr, let v = value else { return nil }
    return v.takeRetainedValue() as String
}

func defaultDevice(input: Bool) -> AudioObjectID? {
    let sel = input ? kAudioHardwarePropertyDefaultInputDevice : kAudioHardwarePropertyDefaultOutputDevice
    guard let id = getValue(systemObject, address(sel), unknownObject),
          id != unknownObject else { return nil }
    return id
}

// MARK: - 全体の音量・マイクの音量

func volumeAddress(input: Bool) -> AudioObjectPropertyAddress {
    address(kAudioHardwareServiceDeviceProperty_VirtualMainVolume,
            input ? kAudioDevicePropertyScopeInput : kAudioDevicePropertyScopeOutput)
}

func getVolume(input: Bool) throws -> Float {
    guard let dev = defaultDevice(input: input) else { throw Fail(input ? "入力デバイスがありません" : "出力デバイスがありません") }
    var addr = volumeAddress(input: input)
    var v = Float32(0)
    var size = UInt32(MemoryLayout<Float32>.size)
    let err = AudioHardwareServiceGetPropertyData(dev, &addr, 0, nil, &size, &v)
    guard err == noErr else { throw Fail("このデバイスは音量を変えられません (\(err))") }
    return v
}

func setVolume(input: Bool, _ value: Float) throws {
    guard let dev = defaultDevice(input: input) else { throw Fail(input ? "入力デバイスがありません" : "出力デバイスがありません") }
    var addr = volumeAddress(input: input)
    var v = Float32(max(0, min(1, value)))
    let err = AudioHardwareServiceSetPropertyData(dev, &addr, 0, nil, UInt32(MemoryLayout<Float32>.size), &v)
    guard err == noErr else { throw Fail("このデバイスは音量を変えられません (\(err))") }
}

// MARK: - プロセス → アプリ

typealias ResponsibleFn = @convention(c) (pid_t) -> pid_t
// 公開されていない関数なので、なければ使わない (Helper などの子プロセスを親のアプリにまとめるのに使う)
let responsibleFn: ResponsibleFn? = {
    guard let h = dlopen(nil, RTLD_NOW), let sym = dlsym(h, "responsibility_get_pid_responsible_for_pid") else { return nil }
    return unsafeBitCast(sym, to: ResponsibleFn.self)
}()

func executablePath(_ pid: pid_t) -> String? {
    var buf = [CChar](repeating: 0, count: 4096)
    let n = proc_pidpath(pid, &buf, UInt32(buf.count))
    return n > 0 ? String(cString: buf) : nil
}

struct AppIdent {
    let name: String     // 小文字の .app 名 (例: discord.app)。.app の外のプロセスは実行ファイル名
    let title: String    // 表示名 (例: Discord)
    let path: String?    // .app の場所
}

/// パスの一番外側の .app (/Applications/Discord.app/Contents/Frameworks/Discord Helper.app/... → Discord.app)
func appFromPath(_ path: String) -> AppIdent {
    let parts = path.split(separator: "/", omittingEmptySubsequences: false)
    for (i, p) in parts.enumerated() where p.hasSuffix(".app") {
        let bundle = parts[0...i].joined(separator: "/")
        return AppIdent(name: p.lowercased(), title: String(p.dropLast(4)), path: bundle)
    }
    let last = String(parts.last ?? "")
    return AppIdent(name: last.lowercased(), title: last, path: nil)
}

var appCache: [pid_t: AppIdent] = [:]
let appCacheLock = NSLock()

func appFor(pid: pid_t) -> AppIdent? {
    appCacheLock.lock()
    defer { appCacheLock.unlock() }
    if let a = appCache[pid] { return a }
    var owner = pid
    if let f = responsibleFn {
        let r = f(pid)
        if r > 0 { owner = r }
    }
    guard let path = executablePath(owner) ?? executablePath(pid) else { return nil }
    let a = appFromPath(path)
    appCache[pid] = a
    return a
}

func forgetPids(except alive: Set<pid_t>) {
    appCacheLock.lock()
    appCache = appCache.filter { alive.contains($0.key) }
    appCacheLock.unlock()
}

let ownPid = getpid()
let ownApp: String? = appFor(pid: ownPid)?.name

struct AudioProc {
    let objectID: AudioObjectID
    let pid: pid_t
    let app: AppIdent
    let output: Bool
}

/// 音を扱っているプロセス (Core Audio のクライアント)。自分 (と同じアプリ = deej-tab) は除く
func audioProcesses() -> [AudioProc] {
    var result: [AudioProc] = []
    var alive = Set<pid_t>()
    for id in getObjectList(systemObject, address(kAudioHardwarePropertyProcessObjectList)) {
        guard let pid = getValue(id, address(kAudioProcessPropertyPID), pid_t(0)), pid > 0 else { continue }
        alive.insert(pid)
        if pid == ownPid { continue }
        guard let app = appFor(pid: pid) else { continue }
        if let own = ownApp, app.name == own { continue }
        let running = getValue(id, address(kAudioProcessPropertyIsRunningOutput), UInt32(0)) ?? 0
        result.append(AudioProc(objectID: id, pid: pid, app: app, output: running != 0))
    }
    forgetPids(except: alive)
    return result
}

func ownProcessObject() -> AudioObjectID? {
    var addr = address(kAudioHardwarePropertyTranslatePIDToProcessObject)
    var pid = ownPid
    var id = unknownObject
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    let err = AudioObjectGetPropertyData(systemObject, &addr, UInt32(MemoryLayout<pid_t>.size), &pid, &size, &id)
    return err == noErr && id != unknownObject ? id : nil
}

// MARK: - Tap (アプリの音の横取り)

/// muted = true : 音量用。アプリの音を消して、音量をかけたものを出力デバイスに流す
/// muted = false: 音の大きさを測るだけ (アプリの音はそのまま)
final class Tap {
    let processes: [AudioObjectID]
    let global: Bool
    let muted: Bool
    let label: String
    var tapID = unknownObject
    var aggregateID = unknownObject
    var procID: AudioDeviceIOProcID?
    // 音を処理するスレッドと共有する値 (Float の読み書きだけなので鍵はかけない)
    var target: Float      // かける倍率 (0〜1)
    var current: Float     // 今かけている倍率 (target へ少しずつ寄せる。急に変えるとプツッと鳴る)
    var peak: Float = 0    // 前に読まれてからの最大の音の大きさ

    init(label: String, processes: [AudioObjectID], global: Bool, muted: Bool, gain: Float) {
        self.label = label
        self.processes = processes
        self.global = global
        self.muted = muted
        self.target = gain
        self.current = gain
    }

    func start(outputUID: String) throws {
        let desc = global
            ? CATapDescription(stereoGlobalTapButExcludeProcesses: processes)
            : CATapDescription(stereoMixdownOfProcesses: processes)
        let uuid = UUID()
        desc.uuid = uuid
        desc.name = "deej-tab \(label)"
        desc.isPrivate = true
        desc.muteBehavior = muted ? .mutedWhenTapped : .unmuted
        var err = AudioHardwareCreateProcessTap(desc, &tapID)
        guard err == noErr else {
            tapID = unknownObject
            throw Fail("Process Tap を作れません (\(err))。システム設定 → プライバシーとセキュリティ → 画面とシステムオーディオの録音 を確認してください")
        }
        let settings: [String: Any] = [
            kAudioAggregateDeviceNameKey: "deej-tab \(label)",
            kAudioAggregateDeviceUIDKey: "deej-tab-" + uuid.uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: outputUID,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID]],
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapUIDKey: uuid.uuidString,
                                               kAudioSubTapDriftCompensationKey: true]],
        ]
        err = AudioHardwareCreateAggregateDevice(settings as CFDictionary, &aggregateID)
        guard err == noErr else {
            aggregateID = unknownObject
            stop()
            throw Fail("集約デバイスを作れません (\(err))")
        }
        err = AudioDeviceCreateIOProcIDWithBlock(&procID, aggregateID, nil) { _, input, _, output, _ in
            self.render(input, output)
        }
        guard err == noErr, procID != nil else {
            stop()
            throw Fail("音の処理を始められません (\(err))")
        }
        err = AudioDeviceStart(aggregateID, procID)
        guard err == noErr else {
            stop()
            throw Fail("音の処理を始められません (\(err))")
        }
    }

    func stop() {
        if let p = procID {
            AudioDeviceStop(aggregateID, p)
            AudioDeviceDestroyIOProcID(aggregateID, p)
            procID = nil
        }
        if aggregateID != unknownObject {
            AudioHardwareDestroyAggregateDevice(aggregateID)
            aggregateID = unknownObject
        }
        if tapID != unknownObject {
            AudioHardwareDestroyProcessTap(tapID)
            tapID = unknownObject
        }
    }

    func takePeak() -> Float {
        let p = peak
        peak = 0
        return p.isFinite ? min(p, 1) : 0
    }

    // ----- 音を処理するスレッド (メモリを確保しない・止まらない処理だけ) -----

    /// 全チャンネル通しての番号 channel の、サンプルの先頭・フレーム数・間隔
    private static func channel(_ channel: Int, _ list: UnsafeMutableAudioBufferListPointer)
        -> (UnsafeMutablePointer<Float>, Int, Int)? {
        var first = 0
        for buf in list {
            let n = Int(buf.mNumberChannels)
            guard n > 0 else { continue }
            if channel >= first && channel < first + n, let data = buf.mData {
                let samples = Int(buf.mDataByteSize) / MemoryLayout<Float>.size
                return (data.assumingMemoryBound(to: Float.self) + (channel - first), samples / n, n)
            }
            first += n
        }
        return nil
    }

    private static func channelCount(_ list: UnsafeMutableAudioBufferListPointer) -> Int {
        var n = 0
        for buf in list { n += Int(buf.mNumberChannels) }
        return n
    }

    private func render(_ input: UnsafePointer<AudioBufferList>, _ output: UnsafeMutablePointer<AudioBufferList>) {
        let ins = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
        let outs = UnsafeMutableAudioBufferListPointer(output)
        for buf in outs {
            if let d = buf.mData { memset(d, 0, Int(buf.mDataByteSize)) }
        }
        // Tap は 2ch。出力デバイスに入力があればその後ろに並ぶので、最後の 2ch を使う
        let total = Tap.channelCount(ins)
        guard total > 0, let left = Tap.channel(max(total - 2, 0), ins) else { return }
        let right = Tap.channel(total - 1, ins) ?? left
        let frames = min(left.1, right.1)
        guard frames > 0 else { return }
        let g0 = current
        let g1 = target
        var p = peak
        if !muted {
            for i in 0..<frames {
                p = max(p, abs(left.0[i * left.2]), abs(right.0[i * right.2]))
            }
            peak = p
            return
        }
        let outL = Tap.channel(0, outs)
        let outR = Tap.channel(1, outs)
        let step = (g1 - g0) / Float(frames)
        for i in 0..<frames {
            let g = g0 + step * Float(i + 1)
            let l = left.0[i * left.2] * g
            let r = right.0[i * right.2] * g
            p = max(p, abs(l), abs(r))
            if let o = outR, let oL = outL {
                if i < oL.1 { oL.0[i * oL.2] = l }
                if i < o.1 { o.0[i * o.2] = r }
            } else if let oL = outL, i < oL.1 {
                oL.0[i * oL.2] = (l + r) * 0.5
            }
        }
        current = g1
        peak = p
    }
}

// MARK: - 音量の管理

final class Engine {
    let queue = DispatchQueue(label: "deej-tab.engine")
    var gains: [String: Float] = [:]          // アプリごとの倍率 (書いていないアプリは 1)
    var others: Float? = nil                  // 「そのほかのアプリ」の倍率
    var othersExclude: Set<String> = []       // 「そのほか」に入れないアプリ
    var meterApps: Set<String> = []           // 音の大きさを測るアプリ
    var meterOthers: Set<String>? = nil       // 「そのほか」の音の大きさを測る (入れないアプリ)。nil なら測らない
    var meterMaster = false
    var volumeTaps: [String: Tap] = [:]
    var meterTaps: [String: Tap] = [:]        // "app:<名前>" / "others" / "master"
    var outputUID: String? = nil
    var failedAt: [String: Date] = [:]        // 作れなかった Tap (すぐには作り直さない)
    var scheduled = false

    func gain(_ app: String) -> Float {
        if let g = gains[app] { return g }
        if let o = others, !othersExclude.contains(app) { return o }
        return 1
    }

    func start() {
        var procs = address(kAudioHardwarePropertyProcessObjectList)
        AudioObjectAddPropertyListenerBlock(systemObject, &procs, queue) { _, _ in self.schedule() }
        var dev = address(kAudioHardwarePropertyDefaultOutputDevice)
        AudioObjectAddPropertyListenerBlock(systemObject, &dev, queue) { _, _ in self.schedule() }
        // 念のため定期的にも合わせる (知らせが来ない変化もあるので)
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + 2, repeating: 2)
        timer.setEventHandler { self.reconcile() }
        timer.resume()
        timerRef = timer
    }

    var timerRef: DispatchSourceTimer?

    func schedule() {
        if scheduled { return }
        scheduled = true
        queue.asyncAfter(deadline: .now() + 0.2) {
            self.scheduled = false
            self.reconcile()
        }
    }

    /// 今のアプリ・設定に合わせて Tap を作る・消す・倍率を変える (queue の上で呼ぶ)
    func reconcile() {
        let procs = audioProcesses()
        var groups: [String: [AudioObjectID]] = [:]
        for p in procs { groups[p.app.name, default: []].append(p.objectID) }

        // 出力デバイスが変わったら (ヘッドホンをつないだ等) 全部作り直す
        let uid = defaultDevice(input: false).flatMap { getString($0, address(kAudioDevicePropertyDeviceUID)) }
        if uid != outputUID {
            stopAll()
            outputUID = uid
            failedAt = [:]
        }
        guard let output = uid else { return }

        // 音量
        for (app, tap) in volumeTaps {
            let ids = groups[app]
            if ids == nil || gain(app) >= 0.9999 || Set(ids!) != Set(tap.processes) {
                tap.stop()
                volumeTaps[app] = nil
            }
        }
        for (app, ids) in groups {
            let g = gain(app)
            if let tap = volumeTaps[app] {
                tap.target = g
                continue
            }
            if g >= 0.9999 || recentlyFailed("vol:" + app) { continue }
            let tap = Tap(label: app, processes: ids, global: false, muted: true, gain: g)
            do {
                try tap.start(outputUID: output)
                volumeTaps[app] = tap
            } catch {
                failedAt["vol:" + app] = Date()
                logError("\(app): \(error)")
            }
        }

        // 音の大きさ (音量用の Tap があるアプリはそちらで測る)
        var wanted: [String: (ids: [AudioObjectID], global: Bool)] = [:]
        for app in meterApps where volumeTaps[app] == nil {
            if let ids = groups[app] { wanted["app:" + app] = (ids, false) }
        }
        let own = ownProcessObject().map { [$0] } ?? []
        if let ex = meterOthers {
            var ids = own
            for (app, g) in groups where ex.contains(app) { ids += g }
            wanted["others"] = (ids, true)
        }
        if meterMaster { wanted["master"] = (own, true) }
        for (key, tap) in meterTaps {
            if let w = wanted[key], Set(w.ids) == Set(tap.processes) { continue }
            tap.stop()
            meterTaps[key] = nil
        }
        for (key, w) in wanted where meterTaps[key] == nil && !recentlyFailed("meter:" + key) {
            let tap = Tap(label: key, processes: w.ids, global: w.global, muted: false, gain: 1)
            do {
                try tap.start(outputUID: output)
                meterTaps[key] = tap
            } catch {
                failedAt["meter:" + key] = Date()
                logError("\(key): \(error)")
            }
        }
    }

    func recentlyFailed(_ key: String) -> Bool {
        guard let t = failedAt[key] else { return false }
        return Date().timeIntervalSince(t) < 10
    }

    func stopAll() {
        for t in volumeTaps.values { t.stop() }
        for t in meterTaps.values { t.stop() }
        volumeTaps = [:]
        meterTaps = [:]
    }

    func peaks() -> [String: Any] {
        var apps: [String: Float] = [:]
        for app in meterApps {
            apps[app] = volumeTaps[app]?.takePeak() ?? meterTaps["app:" + app]?.takePeak() ?? 0
        }
        return ["apps": apps,
                "others": meterTaps["others"]?.takePeak() ?? 0,
                "master": meterTaps["master"]?.takePeak() ?? 0]
    }
}

let engine = Engine()

// MARK: - 最前面のアプリ

func frontApp() -> [String: Any] {
    onMain {
        guard let app = NSWorkspace.shared.frontmostApplication else { return ["name": NSNull(), "pid": 0, "full": false] }
        let pid = app.processIdentifier
        let ident = app.bundleURL.map { appFromPath($0.path) } ?? appFor(pid: pid)
        // 全画面: そのアプリのいちばん手前のウィンドウが画面全体を覆っている (フルスクリーン・ボーダーレス)
        var full = false
        let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
        if let list = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] {
            for w in list {
                guard (w[kCGWindowOwnerPID as String] as? Int).map({ pid_t($0) }) == pid,
                      (w[kCGWindowLayer as String] as? Int) == 0,
                      let b = w[kCGWindowBounds as String] as? NSDictionary,
                      let r = CGRect(dictionaryRepresentation: b as CFDictionary) else { continue }
                full = NSScreen.screens.contains { r.width >= $0.frame.width && r.height >= $0.frame.height }
                break
            }
        }
        return ["name": ident?.name ?? NSNull(), "pid": Int(pid), "full": full]
    }
}

/// 起動中の普通のアプリ (Dock に出るもの)
func runningApps() -> [[String: Any]] {
    onMain {
        NSWorkspace.shared.runningApplications.compactMap { app -> [String: Any]? in
            guard app.activationPolicy == .regular, let url = app.bundleURL else { return nil }
            let ident = appFromPath(url.path)
            if let own = ownApp, ident.name == own { return nil }
            return ["name": ident.name, "title": app.localizedName ?? ident.title, "path": url.path]
        }
    }
}

func iconPNG(path: String, size: Int) -> String? {
    onMain {
        let img = NSWorkspace.shared.icon(forFile: path)
        guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
                                         bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                         colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { return nil }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        img.draw(in: NSRect(x: 0, y: 0, width: size, height: size))
        NSGraphicsContext.restoreGraphicsState()
        return rep.representation(using: .png, properties: [:])?.base64EncodedString()
    }
}

// MARK: - ショートカット (Carbon の RegisterEventHotKey。アクセシビリティの許可はいらない)

var hotkeyRefs: [EventHotKeyRef] = []
var hotkeyNames: [UInt32: String] = [:]
var hotkeyHandlerInstalled = false

func installHotkeyHandler() {
    if hotkeyHandlerInstalled { return }
    hotkeyHandlerInstalled = true
    var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
    InstallEventHandler(GetApplicationEventTarget(), { _, event, _ in
        var hk = EventHotKeyID()
        let err = GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                                    nil, MemoryLayout<EventHotKeyID>.size, nil, &hk)
        if err == noErr, let name = hotkeyNames[hk.id] {
            emit(["event": "hotkey", "name": name])
        }
        return noErr
    }, 1, &spec, nil, nil)
}

/// bindings: {名前: {"mods": Carbon の修飾キー, "key": 仮想キーコード}}。登録できなかったものを返す
func setHotkeys(_ bindings: [String: [String: Any]]) -> [String: String] {
    onMain {
        installHotkeyHandler()
        for ref in hotkeyRefs { UnregisterEventHotKey(ref) }
        hotkeyRefs = []
        hotkeyNames = [:]
        var errors: [String: String] = [:]
        var n: UInt32 = 0
        for name in bindings.keys.sorted() {
            guard let b = bindings[name], let mods = (b["mods"] as? NSNumber)?.uint32Value,
                  let key = (b["key"] as? NSNumber)?.uint32Value else { continue }
            n += 1
            var ref: EventHotKeyRef?
            let id = EventHotKeyID(signature: OSType(0x6465_6A74), id: n)   // 'dejt'
            let err = RegisterEventHotKey(key, mods, id, GetApplicationEventTarget(), 0, &ref)
            if err == noErr, let r = ref {
                hotkeyRefs.append(r)
                hotkeyNames[n] = name
            } else {
                errors[name] = "ほかのアプリが使っているため登録できません"
            }
        }
        return errors
    }
}

// MARK: - 命令

func number(_ v: Any?) -> Float? { (v as? NSNumber)?.floatValue }

func handle(_ msg: [String: Any]) throws -> [String: Any] {
    let cmd = msg["cmd"] as? String ?? ""
    switch cmd {
    case "hello":
        return ["version": 1, "pid": Int(ownPid)]
    case "get_volume":
        return ["value": try getVolume(input: (msg["scope"] as? String) == "input")]
    case "set_volume":
        guard let v = number(msg["value"]) else { throw Fail("value がありません") }
        try setVolume(input: (msg["scope"] as? String) == "input", v)
        return [:]
    case "processes":
        let procs = engine.queue.sync { audioProcesses() }
        var apps: [String: [String: Any]] = [:]
        for p in procs {
            var a = apps[p.app.name] ?? ["name": p.app.name, "title": p.app.title, "output": false]
            if let path = p.app.path { a["path"] = path }
            if p.output { a["output"] = true }
            apps[p.app.name] = a
        }
        return ["processes": Array(apps.values)]
    case "set_gains":
        guard let g = msg["gains"] as? [String: Any] else { throw Fail("gains がありません") }
        engine.queue.sync {
            for (app, v) in g {
                guard let x = number(v) else { continue }
                engine.gains[app] = max(0, min(1, x))
            }
            engine.reconcile()
        }
        return [:]
    case "set_others":
        let ex = Set((msg["exclude"] as? [String]) ?? [])
        engine.queue.sync {
            engine.others = number(msg["gain"]).map { max(0, min(1, $0)) }
            engine.othersExclude = ex
            // 「そのほか」を変えたら、そのほかのアプリの個別の倍率は消す (後から変えたほうが効く)
            engine.gains = engine.gains.filter { ex.contains($0.key) }
            engine.reconcile()
        }
        return [:]
    case "reset":
        engine.queue.sync {
            engine.gains = [:]
            engine.others = nil
            engine.othersExclude = []
            engine.reconcile()
        }
        return [:]
    case "meter":
        engine.queue.sync {
            engine.meterApps = Set((msg["apps"] as? [String]) ?? [])
            engine.meterOthers = (msg["others_exclude"] as? [String]).map { Set($0) }
            engine.meterMaster = (msg["master"] as? Bool) ?? false
            engine.reconcile()
        }
        return [:]
    case "peaks":
        return engine.queue.sync { engine.peaks() }
    case "front":
        return frontApp()
    case "apps":
        return ["apps": runningApps()]
    case "names":
        let paths = (msg["paths"] as? [String]) ?? []
        var names: [String: String] = [:]
        for p in paths { names[p] = FileManager.default.displayName(atPath: p) }
        return ["names": names]
    case "icon":
        guard let path = msg["path"] as? String else { throw Fail("path がありません") }
        let size = (msg["size"] as? NSNumber)?.intValue ?? 32
        return ["png": iconPNG(path: path, size: size) ?? NSNull()]
    case "hotkeys":
        let b = (msg["bindings"] as? [String: [String: Any]]) ?? [:]
        return ["errors": setHotkeys(b)]
    default:
        throw Fail("不明な命令です: \(cmd)")
    }
}

// MARK: - 起動

func readCommands() {
    while let line = readLine(strippingNewline: true) {
        guard let data = line.data(using: .utf8),
              let msg = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { continue }
        let id = msg["id"] ?? NSNull()
        do {
            var r = try handle(msg)
            r["id"] = id
            r["ok"] = true
            emit(r)
        } catch {
            emit(["id": id, "error": "\(error)"])
        }
    }
    // 本体が終わった: 横取りをやめてから終わる (やめないままでも、このプロセスが終われば音は元に戻る)
    engine.queue.sync { engine.stopAll() }
    exit(0)
}

signal(SIGPIPE, SIG_IGN)
let application = NSApplication.shared
application.setActivationPolicy(.prohibited)   // Dock にもメニューにも出さない
engine.queue.async { engine.start() }
Thread.detachNewThread { readCommands() }
application.run()
