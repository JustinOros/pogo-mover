import os
import re
import shutil
import subprocess
import threading

PROVIDERS = ("gps", "network")
OPTIONAL_PROVIDERS = ("fused",)
_active = list(PROVIDERS)

_serial = None
_ready = False
_lock = threading.Lock()
_stream = None
_saved = {}
TWEAKS = (
    ("secure", "location_mode", "1"),
    ("global", "wifi_scan_always_enabled", "0"),
    ("global", "ble_scan_always_enabled", "0"),
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


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


def setup():
    global _serial, _ready
    with _lock:
        serial, state = pick_device()
        if state != "device":
            _ready = False
            return False, f"no authorized Android device (state: {state})"
        _serial = serial
        _shell("cmd location set-location-enabled true")
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


def send(lat, lng):
    global _ready, _stream
    if not _ready:
        ok, message = setup()
        if not ok:
            return False, message
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
    global _ready, _stream
    with _lock:
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
    ok, message = send(33.4484, -112.0740)
    lines.append(f"test send: {'ok' if ok else 'FAILED ' + message[:200]}")
    rc, out = _shell("dumpsys location")
    keep = [l.strip() for l in out.splitlines() if re.search(r"(gps|network|fused|passive).*(provider|mock|test)|mock|test provider", l, re.I)]
    lines.extend(keep[:25])
    return lines
