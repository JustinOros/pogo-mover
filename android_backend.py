import os
import re
import shutil
import socket
import subprocess
import threading
import time

PROVIDERS = ("gps", "network")
OPTIONAL_PROVIDERS = ("fused",)
_active = list(PROVIDERS)

_serial = None
_ready = False
_lock = threading.Lock()
_stream = None
_saved = {}
_mode = "none"
_sock = None
_app_error = [""]
APP_PACKAGE = "com.pogomover.mock"
APP_PORT = 7788
TWEAKS = (
    ("secure", "location_mode", "1"),
    ("global", "wifi_scan_always_enabled", "0"),
    ("global", "ble_scan_always_enabled", "0"),
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_APK_CANDIDATES = (
    os.path.join(BASE_DIR, "pogo-mock.apk"),
    os.path.join(BASE_DIR, "android-app", "app", "build", "outputs", "apk", "debug", "app-debug.apk"),
)


def find_apk():
    for path in APP_APK_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def find_adb():
    home = os.path.expanduser("~")
    candidates = [
        os.environ.get("POGO_ADB"),
        os.path.join(BASE_DIR, "platform-tools", "adb"),
        shutil.which("adb"),
        os.path.join(home, "Library/Android/sdk/platform-tools/adb"),
        os.path.join(home, "Android/Sdk/platform-tools/adb"),
        "/opt/homebrew/bin/adb",
        "/usr/local/bin/adb",
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _run(args, timeout=8):
    adb = find_adb()
    if not adb:
        return -1, "adb not found"
    try:
        r = subprocess.run([adb] + args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return -2, "timed out"
    except Exception as e:
        return -3, str(e)


def _failed(rc, out):
    return rc != 0 or "Exception" in out or "not allowed" in out


def _shell(command):
    prefix = ["-s", _serial] if _serial else []
    return _run(prefix + ["shell", command])


def list_devices():
    rc, out = _run(["devices"])
    devices = []
    if rc != 0:
        return devices
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devices.append((parts[0], parts[1]))
    return devices


def pick_device():
    devices = list_devices()
    for serial, state in devices:
        if state == "device":
            return serial, state
    if devices:
        return devices[0]
    return None, None


def mode():
    return _mode


def app_installed():
    rc, out = _shell(f"pm list packages {APP_PACKAGE}")
    return rc == 0 and f"package:{APP_PACKAGE}" in out


def _connect_app():
    global _sock
    if _sock is not None:
        try:
            _sock.close()
        except Exception:
            pass
        _sock = None
    prefix = ["-s", _serial] if _serial else []
    _run(prefix + ["forward", f"tcp:{APP_PORT}", f"tcp:{APP_PORT}"])
    for _ in range(40):
        try:
            s = socket.create_connection(("127.0.0.1", APP_PORT), timeout=2)
            s.settimeout(2)
            s.sendall(b"ping\n")
            if s.recv(16).startswith(b"ok"):
                _sock = s
                return True
            s.close()
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _setup_app():
    if not app_installed():
        apk = find_apk()
        if not apk:
            return False, "mock app not installed and pogo-mock.apk not found"
        prefix = ["-s", _serial] if _serial else []
        rc, out = _run(prefix + ["install", "-r", apk], timeout=120)
        if rc != 0 or "Success" not in out:
            return False, f"apk install failed: {out[:200]}"
    _shell(f"pm grant {APP_PACKAGE} android.permission.ACCESS_FINE_LOCATION")
    _shell(f"pm grant {APP_PACKAGE} android.permission.ACCESS_COARSE_LOCATION")
    _shell(f"pm grant {APP_PACKAGE} android.permission.POST_NOTIFICATIONS")
    rc, out = _shell(f"appops set {APP_PACKAGE} android:mock_location allow")
    if _failed(rc, out):
        return False, f"could not allow mock location for app: {out[:200]}"
    _shell(f"am force-stop {APP_PACKAGE}")
    _shell("input keyevent KEYCODE_WAKEUP")
    rc, out = _shell(f"am start -n {APP_PACKAGE}/.MainActivity --ez autostart true")
    if _failed(rc, out) or "Error" in out:
        return False, f"could not open mock app (unlock the phone): {out[:200]}"
    if not _connect_app():
        return False, "mock app service did not answer"
    return True, ""


def setup():
    global _serial, _ready, _mode
    with _lock:
        serial, state = pick_device()
        if state != "device":
            _ready = False
            _mode = "none"
            return False, f"no authorized Android device (state: {state})"
        _serial = serial
        _shell("cmd location set-location-enabled true")
        ok, message = _setup_app()
        if ok:
            _mode = "app"
            _ready = True
            return True, ""
        _app_error[0] = message
        _mode = "adb"
        for namespace, key, value in TWEAKS:
            if (namespace, key) not in _saved:
                rc, out = _shell(f"settings get {namespace} {key}")
                _saved[(namespace, key)] = out.strip() if rc == 0 else "null"
            _shell(f"settings put {namespace} {key} {value}")
        for provider in PROVIDERS:
            _shell(f"cmd location providers remove-test-provider {provider}")
            add = f"cmd location providers add-test-provider {provider}"
            rc, out = _shell(add)
            if _failed(rc, out):
                match = re.search(r"from (\d+) not allowed", out)
                uid = match.group(1) if match else "2000"
                rc2, out2 = _shell(f"appops set {uid} android:mock_location allow")
                if _failed(rc2, out2):
                    _shell(f"appops set --uid {uid} android:mock_location allow")
                rc, out = _shell(add)
            if _failed(rc, out):
                _ready = False
                return False, out
            rc, out = _shell(f"cmd location providers set-test-provider-enabled {provider} true")
            if _failed(rc, out):
                _ready = False
                return False, out
        _active[:] = list(PROVIDERS)
        for provider in OPTIONAL_PROVIDERS:
            _shell(f"cmd location providers remove-test-provider {provider}")
            rc, out = _shell(f"cmd location providers add-test-provider {provider}")
            if _failed(rc, out):
                continue
            rc, out = _shell(f"cmd location providers set-test-provider-enabled {provider} true")
            if not _failed(rc, out):
                _active.append(provider)
        _ready = True
        return True, ""


def _open_stream():
    global _stream
    adb = find_adb()
    if not adb or not _serial:
        return None
    if _stream is not None and _stream.poll() is None:
        return _stream
    try:
        _stream = subprocess.Popen(
            [adb, "-s", _serial, "shell"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except Exception:
        _stream = None
    return _stream


def app_error():
    return _app_error[0]


def _send_app(lat, lng):
    global _ready
    data = f"{lat:.7f},{lng:.7f}\n".encode()
    for attempt in range(2):
        try:
            if _sock is None and not _connect_app():
                break
            _sock.sendall(data)
            return True, ""
        except Exception:
            if not _connect_app():
                break
    _ready = False
    return False, "lost connection to mock app"


def send(lat, lng):
    global _ready, _stream
    if not _ready:
        ok, message = setup()
        if not ok:
            return False, message
    if _mode == "app":
        with _lock:
            return _send_app(lat, lng)
    with _lock:
        location = f"{lat:.7f},{lng:.7f}"
        command = "; ".join(
            f"cmd location providers set-test-provider-location {p} --location {location}"
            for p in _active
        )
        proc = _open_stream()
        if proc is None:
            _ready = False
            return False, "could not open adb shell"
        try:
            proc.stdin.write(command + "\n")
            proc.stdin.flush()
        except Exception as e:
            _stream = None
            _ready = False
            return False, str(e)
        return True, ""


def cleanup():
    global _ready, _stream, _sock, _mode
    with _lock:
        if _sock is not None:
            try:
                _sock.close()
            except Exception:
                pass
            _sock = None
        if _mode == "app" and _serial:
            _shell(f"am force-stop {APP_PACKAGE}")
            _run(["-s", _serial, "forward", "--remove", f"tcp:{APP_PORT}"])
        if _stream is not None:
            try:
                _stream.kill()
            except Exception:
                pass
            _stream = None
        if _serial:
            for provider in list(PROVIDERS) + list(OPTIONAL_PROVIDERS):
                _shell(f"cmd location providers remove-test-provider {provider}")
            for (namespace, key), value in _saved.items():
                if value in ("", "null"):
                    _shell(f"settings delete {namespace} {key}")
                else:
                    _shell(f"settings put {namespace} {key} {value}")
            _saved.clear()
        _ready = False


def diagnose():
    lines = []
    adb = find_adb()
    lines.append(f"adb: {adb}")
    serial, state = pick_device()
    lines.append(f"device: {serial} ({state})")
    lines.append(f"mode: {_mode}")
    if _mode == "adb" and _app_error[0]:
        lines.append(f"mock app unavailable: {_app_error[0]}")
    ok, message = send(33.4484, -112.0740)
    lines.append(f"test send: {'ok' if ok else 'FAILED ' + message[:200]}")
    rc, out = _shell("dumpsys location")
    keep = [l.strip() for l in out.splitlines() if re.search(r"(gps|network|fused|passive).*(provider|mock|test)|mock|test provider", l, re.I)]
    lines.extend(keep[:25])
    return lines
