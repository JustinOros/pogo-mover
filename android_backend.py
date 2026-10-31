import os
import re
import shutil
import subprocess
import threading

PROVIDERS = ("gps", "network")

_serial = None
_ready = False
_lock = threading.Lock()


def find_adb():
    home = os.path.expanduser("~")
    candidates = [
        os.environ.get("POGO_ADB"),
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
        _ready = True
        return True, ""


def send(lat, lng):
    global _ready
    if not _ready:
        ok, message = setup()
        if not ok:
            return False, message
    with _lock:
        location = f"{lat:.7f},{lng:.7f}"
        command = "; ".join(
            f"cmd location providers set-test-provider-location {p} --location {location}"
            for p in PROVIDERS
        )
        rc, out = _shell(command)
        if _failed(rc, out):
            _ready = False
            return False, out
        return True, ""


def cleanup():
    global _ready
    with _lock:
        if _serial:
            for provider in PROVIDERS:
                _shell(f"cmd location providers remove-test-provider {provider}")
        _ready = False
