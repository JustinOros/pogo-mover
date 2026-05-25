import json
import math
import mimetypes
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

PORT = 7777
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COORDS_FILE = os.path.join(BASE_DIR, ".last_coords.json")
DAEMON_SCRIPT = os.path.join(BASE_DIR, "location_daemon.py")
DEFAULT_COORDS = {"lat": 33.4484, "lng": -112.0740}

def load_coords():
    try:
        with open(COORDS_FILE, "r") as f:
            data = json.load(f)
            if "lat" in data and "lng" in data:
                print(f"Resuming from last position: {data['lat']:.5f}, {data['lng']:.5f}")
                return data
    except Exception:
        pass
    return dict(DEFAULT_COORDS)

def save_coords(lat, lng):
    try:
        with open(COORDS_FILE, "w") as f:
            json.dump({"lat": lat, "lng": lng}, f)
    except Exception:
        pass

current_coords = load_coords()
last_result = {"method": None, "ok": False, "error": "not tried yet"}

_daemon_proc = None
_daemon_lock = threading.Lock()
_cached_rsd_host = None
_cached_rsd_port = None
_rsd_cache_time = 0
RSD_CACHE_TTL = 30

def get_rsd():
    global _cached_rsd_host, _cached_rsd_port, _rsd_cache_time
    now = time.time()
    if _cached_rsd_host and (now - _rsd_cache_time) < RSD_CACHE_TTL:
        return _cached_rsd_host, _cached_rsd_port
    try:
        with urllib.request.urlopen("http://127.0.0.1:49151/", timeout=3) as r:
            tunnels = json.loads(r.read())
            if isinstance(tunnels, dict) and tunnels:
                entry = list(tunnels.values())[0]
                if isinstance(entry, list) and entry:
                    t = entry[0]
                    host = t.get("tunnel-address")
                    port = t.get("tunnel-port")
                    if host and port:
                        _cached_rsd_host = str(host)
                        _cached_rsd_port = int(port)
                        _rsd_cache_time = now
                        return _cached_rsd_host, _cached_rsd_port
    except Exception:
        pass
    return None, None

def start_daemon():
    global _daemon_proc, _cached_rsd_host, _cached_rsd_port
    host, port = get_rsd()
    if not host:
        print("  [!] No RSD tunnel found — daemon not started", flush=True)
        return False
    try:
        proc = subprocess.Popen(
            [sys.executable, DAEMON_SCRIPT, host, str(port)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        ready_line = proc.stdout.readline().strip()
        if ready_line == "ready":
            _daemon_proc = proc
            print(f"  [✓] Location daemon connected via RSD {host}:{port}", flush=True)
            return True
        else:
            print(f"  [!] Daemon startup failed: {ready_line}", flush=True)
            proc.kill()
            return False
    except Exception as e:
        print(f"  [!] Could not start daemon: {e}", flush=True)
        return False

def send_via_daemon(lat, lng):
    global _daemon_proc, last_result
    with _daemon_lock:
        if _daemon_proc is None or _daemon_proc.poll() is not None:
            print("  [~] Daemon not running — restarting...", flush=True)
            start_daemon()
        if _daemon_proc is None:
            return False
        try:
            _daemon_proc.stdin.write(f"{lat},{lng}\n")
            _daemon_proc.stdin.flush()
            response = _daemon_proc.stdout.readline().strip()
            if response == "ok":
                last_result = {"method": "DVT daemon (persistent)", "ok": True, "error": None}
                return True
            else:
                print(f"  [!] Daemon response: {response}", flush=True)
                return False
        except Exception as e:
            print(f"  [!] Daemon write failed: {e}", flush=True)
            _daemon_proc = None
            return False

def send_location_cli(lat, lng):
    global last_result, _cached_rsd_host, _cached_rsd_port
    host, port = get_rsd()
    if host and port:
        try:
            r = subprocess.run(
                ["pymobiledevice3", "developer", "dvt", "simulate-location",
                 "set", "--rsd", host, str(port), "--", str(lat), str(lng)],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                last_result = {"method": "dvt --rsd (cli)", "ok": True, "error": None}
                return True
        except (subprocess.TimeoutExpired, Exception):
            _cached_rsd_host = None
    last_result = {"method": None, "ok": False, "error": "all methods failed"}
    return False

import random

def add_gps_jitter(lat, lng):
    meter_lat = 1.0 / 111320.0
    meter_lng = 1.0 / (111320.0 * max(abs(math.cos(math.radians(lat))), 0.001))
    jitter_m = 2.0
    return (
        lat + random.uniform(-jitter_m, jitter_m) * meter_lat,
        lng + random.uniform(-jitter_m, jitter_m) * meter_lng
    )

def set_location(lat, lng):
    jlat, jlng = add_gps_jitter(lat, lng)
    if not send_via_daemon(jlat, jlng):
        send_location_cli(jlat, jlng)

_target_lat = current_coords.get("lat")
_target_lng = current_coords.get("lng")
_target_updated = time.time()
_target_lock = threading.Lock()
_last_sent_lat = None
_last_sent_lng = None

def location_loop():
    global _last_sent_lat, _last_sent_lng
    while True:
        with _target_lock:
            lat = _target_lat
            lng = _target_lng

        if lat is None or lng is None:
            time.sleep(0.1)
            continue

        if lat != _last_sent_lat or lng != _last_sent_lng:
            set_location(lat, lng)
            _last_sent_lat = lat
            _last_sent_lng = lng
        else:
            time.sleep(0.1)

def set_target(lat, lng):
    global _target_lat, _target_lng, _target_updated
    with _target_lock:
        _target_lat = lat
        _target_lng = lng
        _target_updated = time.time()

class Handler(BaseHTTPRequestHandler):
    def handle_error(self, request, client_address):
        import sys as _sys
        exc = _sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)

    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/coords":
            return self.send_json(current_coords)
        if path == "/status":
            return self.send_json({"ok": True, "spoof": last_result})
        if path in ("/", "/index.html"):
            path = "/index.html"
        file_path = os.path.join(BASE_DIR, path.lstrip("/"))
        if os.path.isfile(file_path):
            mime, _ = mimetypes.guess_type(file_path)
            with open(file_path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global current_coords
        if urlparse(self.path).path == "/move":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                lat = float(data["lat"])
                lng = float(data["lng"])
                current_coords = {"lat": lat, "lng": lng}
                save_coords(lat, lng)
                set_target(lat, lng)
                self.send_json({"ok": True, "lat": lat, "lng": lng})
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == "__main__":
    print(f"PoGo Mover running at http://localhost:{PORT}")
    print("  Starting location daemon...", flush=True)
    start_daemon()
    threading.Thread(target=location_loop, daemon=True).start()
    server = HTTPServer(("localhost", PORT), Handler)
    server.serve_forever()
