import os
import sys
import time
import platform
import subprocess
import webbrowser
import threading

def banner(msg, char="="):
    print(f"\n{char * 50}")
    print(f"  {msg}")
    print(f"{char * 50}")

def step(msg):
    print(f"\n▶  {msg}")

def ok(msg):
    print(f"   ✓  {msg}")

def warn(msg):
    print(f"   ⚠  {msg}")

def fail(msg):
    print(f"   ✗  {msg}")

def check_macos():
    if platform.system() != "Darwin":
        fail("PoGo Mover requires macOS.")
        sys.exit(1)
    ok("macOS detected")

def check_python():
    if sys.version_info < (3, 8):
        fail("Python 3.8 or newer is required.")
        sys.exit(1)
    ok(f"Python {sys.version.split()[0]}")

def install_pymobiledevice3():
    step("Checking for pymobiledevice3...")
    try:
        import pymobiledevice3
        ok("pymobiledevice3 already installed")
        return
    except ImportError:
        pass
    warn("pymobiledevice3 not found — installing now (this may take a minute)...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "pymobiledevice3", "--quiet"],
        capture_output=False
    )
    if result.returncode != 0:
        fail("Installation failed. Try running: pip3 install pymobiledevice3")
        sys.exit(1)
    ok("pymobiledevice3 installed")

def detect_iphone_system_profiler():
    try:
        result = subprocess.run(
            ["system_profiler", "SPUSBDataType", "-json"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and ("iPhone" in result.stdout or "iPad" in result.stdout):
            return True
    except Exception:
        pass
    return False

def detect_iphone_ioreg():
    try:
        result = subprocess.run(
            ["ioreg", "-p", "IOUSB", "-l", "-w", "0"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and "iPhone" in result.stdout:
            return True
    except Exception:
        pass
    return False

def detect_iphone_pymobiledevice():
    try:
        result = subprocess.run(
            ["pymobiledevice3", "list-devices"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout.strip()
        if output and len(output) > 8:
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["pymobiledevice3", "usbmux", "list"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout.strip()
        if output and len(output) > 8:
            return True
    except Exception:
        pass

    return False

def check_iphone():
    step("Checking for connected iPhone...")

    if detect_iphone_system_profiler() or detect_iphone_ioreg():
        ok("iPhone detected via USB")
        return True

    if detect_iphone_pymobiledevice():
        ok("iPhone detected via pymobiledevice3")
        return True

    warn("iPhone not detected via USB.")
    print()
    print("   Things to try:")
    print("   1. Make sure the USB cable is fully plugged in")
    print("   2. Unlock your iPhone and tap 'Trust' if prompted")
    print("   3. Try a different USB cable or port")
    print("   4. Open Finder — does your iPhone appear in the sidebar?")
    print()
    choice = input("   Press Enter to retry, S to skip this check, or Ctrl+C to quit: ").strip().lower()
    if choice == "s":
        warn("Skipping iPhone check — continuing anyway.")
        return False
    return check_iphone()

def get_ios_version_from_ioreg():
    try:
        result = subprocess.run(
            ["ioreg", "-p", "IOUSB", "-l", "-w", "0"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if "ProductVersion" in line:
                parts = line.split('"')
                for i, p in enumerate(parts):
                    if p == "ProductVersion" and i + 2 < len(parts):
                        version_str = parts[i + 2]
                        return int(version_str.split(".")[0])
    except Exception:
        pass
    return 0

def get_ios_version_from_pymobiledevice():
    try:
        import json
        for cmd in [
            ["pymobiledevice3", "list-devices", "--json"],
            ["pymobiledevice3", "usbmux", "list", "--json"],
        ]:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            try:
                devices = json.loads(result.stdout)
                if isinstance(devices, list) and devices:
                    v = devices[0].get("ProductVersion") or devices[0].get("product_version", "0")
                    return int(str(v).split(".")[0])
                if isinstance(devices, dict):
                    for v in ["ProductVersion", "product_version"]:
                        if v in devices:
                            return int(str(devices[v]).split(".")[0])
            except Exception:
                pass
    except Exception:
        pass
    return 0

def get_ios_version():
    v = get_ios_version_from_pymobiledevice()
    if v > 0:
        return v
    return get_ios_version_from_ioreg()

def maybe_start_tunnel():
    step("Starting GPS tunnel...")

    ios = get_ios_version()
    if ios == 0:
        ios = 17

    if ios < 17:
        ok(f"iOS {ios} detected — no tunnel needed.")
        return None

    tunnel_cmd = ["sudo", "pymobiledevice3", "remote", "tunneld"]

    warn("The tunnel requires your Mac password (sudo).")
    print()

    proc = subprocess.Popen(
        tunnel_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    deadline = time.time() + 30
    tunnel_ready = False

    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            time.sleep(0.1)
            continue
        line = line.strip()
        if "Application startup complete" in line or "Uvicorn running" in line:
            tunnel_ready = True
            break
        if "password" in line.lower() or "Password" in line or "ERROR" in line.upper():
            print(f"   {line}")

    if proc.poll() is not None:
        fail("Tunnel process exited unexpectedly.")
        warn("Try running manually in a separate tab:")
        warn("  sudo pymobiledevice3 remote tunneld")
        input("   Press Enter once the tunnel is running, or Ctrl+C to quit...")
        return None

    if tunnel_ready:
        ok("Tunnel ready — tunneld HTTP service is running")
    else:
        ok("Tunnel started (waiting for device connection...)")

    threading.Thread(target=lambda: _drain_tunnel(proc), daemon=True).start()
    return proc

def _drain_tunnel(proc):
    try:
        for line in proc.stdout:
            pass
    except Exception:
        pass

def check_xcode():
    try:
        result = subprocess.run(
            ["xcode-select", "-p"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            path = result.stdout.strip()
            if "Xcode.app" in path:
                ok(f"Xcode installed at {path}")
                return "xcode"
            else:
                warn("Only Command Line Tools found — Xcode app is needed for Developer Mode")
                return "clt_only"
    except Exception:
        pass
    warn("Xcode not found")
    return "none"

def check_developer_mode():
    try:
        result = subprocess.run(
            ["pymobiledevice3", "amfi", "developer-mode-status"],
            capture_output=True, text=True, timeout=8
        )
        status = result.stdout.strip().lower()
        if "true" in status:
            ok("Developer Mode is ON ✓")
            return True
        elif "false" in status:
            return False
        else:
            return None
    except Exception:
        return None

def check_and_guide_developer_mode():
    step("Checking Developer Mode status on iPhone...")

    dev_mode = check_developer_mode()

    if dev_mode is True:
        return

    if dev_mode is False:
        fail("Developer Mode is OFF — GPS spoofing will not work until this is enabled.")
    else:
        warn("Could not determine Developer Mode status.")

    print()
    xcode_status = check_xcode()
    print()

    if xcode_status == "xcode":
        print("   ┌─────────────────────────────────────────────────┐")
        print("   │  HOW TO ENABLE DEVELOPER MODE (Xcode found)     │")
        print("   ├─────────────────────────────────────────────────┤")
        print("   │  1. Open Xcode → create or open any project     │")
        print("   │  2. Menu: Window → Devices and Simulators        │")
        print("   │     (this activates the iPhone connection)       │")
        print("   │  3. On iPhone: Settings → Privacy & Security    │")
        print("   │     → scroll to bottom → Developer Mode → ON    │")
        print("   │  4. iPhone will restart automatically            │")
        print("   │  5. After restart: Swipe Up and enter passcode  │")
        print("   │     to confirm Developer Mode                    │")
        print("   │  6. Come back here and rerun start.py            │")
        print("   └─────────────────────────────────────────────────┘")
    elif xcode_status == "clt_only":
        print("   ┌─────────────────────────────────────────────────┐")
        print("   │  HOW TO ENABLE DEVELOPER MODE                   │")
        print("   ├─────────────────────────────────────────────────┤")
        print("   │  Command Line Tools found but Xcode app needed. │")
        print("   │                                                  │")
        print("   │  Option A — Install Xcode (recommended):        │")
        print("   │    1. Open Mac App Store → search 'Xcode'       │")
        print("   │    2. Install (free, ~10GB)                     │")
        print("   │    3. Open Xcode → Window → Devices             │")
        print("   │    4. Click iPhone → Enable Developer Mode       │")
        print("   │                                                  │")
        print("   │  Option B — Try without Xcode:                  │")
        print("   │    On iPhone: Settings → Privacy & Security     │")
        print("   │    → scroll to bottom → Developer Mode → ON     │")
        print("   │    (only appears after connecting to Xcode once) │")
        print("   └─────────────────────────────────────────────────┘")
    else:
        print("   ┌─────────────────────────────────────────────────┐")
        print("   │  HOW TO ENABLE DEVELOPER MODE (no Xcode found)  │")
        print("   ├─────────────────────────────────────────────────┤")
        print("   │  Option A — Install Xcode (recommended):        │")
        print("   │    1. Open Mac App Store → search 'Xcode'       │")
        print("   │    2. Install (free, ~10GB)                     │")
        print("   │    3. Open Xcode → Window → Devices             │")
        print("   │    4. Click iPhone → Enable Developer Mode       │")
        print("   │    5. Confirm on iPhone → restart               │")
        print("   │                                                  │")
        print("   │  Option B — Apple Configurator 2 (tiny, free):  │")
        print("   │    1. Mac App Store → 'Apple Configurator 2'    │")
        print("   │    2. Open it with iPhone connected             │")
        print("   │    3. Actions → Advanced → Enable Developer Mode │")
        print("   │                                                  │")
        print("   │  Option C — Apple Store:                        │")
        print("   │    Ask staff to enable Developer Mode — free    │")
        print("   │    and takes 2 minutes with their tools         │")
        print("   └─────────────────────────────────────────────────┘")

    print()

    if dev_mode is False:
        choice = input("   Press Enter to continue anyway (GPS won't work until fixed), or Ctrl+C to quit: ").strip()
    else:
        ok("Continuing — Developer Mode status unknown, GPS may still work.")

    step("Mounting developer disk image (if needed)...")
    try:
        result = subprocess.run(
            ["pymobiledevice3", "mounter", "auto-mount"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            ok("Developer disk image ready")
        else:
            warn("Mount step returned non-zero — likely already mounted, continuing.")
    except Exception as e:
        warn(f"Mount step skipped: {e}")

def mount_developer_image():
    step("Mounting developer disk image (if needed)...")
    try:
        result = subprocess.run(
            ["pymobiledevice3", "mounter", "auto-mount"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            ok("Developer disk image ready")
        else:
            ok("Developer disk image already mounted")
    except Exception as e:
        warn(f"Mount step skipped: {e}")

def open_browser():
    url = "http://localhost:7777"
    step("Opening PoGo Mover in your browser...")
    time.sleep(1.5)
    webbrowser.open(url)
    ok(f"Opened: {url}")

def start_server():
    server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
    step("Starting GPS server on http://localhost:7777...")
    ok("Server running — press Ctrl+C to stop.")
    print()
    try:
        subprocess.run([sys.executable, server_path])
    except KeyboardInterrupt:
        pass

def main():
    banner("PoGo Mover — Pokémon GO GPS Controller", "=")
    print("  Setting everything up automatically...\n")

    check_macos()
    check_python()
    install_pymobiledevice3()
    check_iphone()
    check_and_guide_developer_mode()
    tunnel_proc = maybe_start_tunnel()
    mount_developer_image()

    banner("All set! Launching PoGo Mover", "-")

    threading.Thread(target=open_browser, daemon=True).start()
    start_server()

    if tunnel_proc and tunnel_proc.poll() is None:
        tunnel_proc.terminate()
        tunnel_proc.wait()

if __name__ == "__main__":
    main()
