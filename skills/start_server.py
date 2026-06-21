#!/usr/bin/env python3
"""
start_server.py — 桥接服务器启动器。

检查服务器是否已在运行，若未运行则清理残留并启动。
替代 CLAUDE.md「自动启动流程」中的手动 curl 检查 + 条件启动 + sleep 等待。

用法:
  python start_server.py <ROOT>
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


SERVER_HOST = os.environ.get("AIRP_HOST", "0.0.0.0").strip() or "0.0.0.0"
SERVER_PORT = 8765
MVU_PORT = 8766
POLL_INTERVAL = 0.5
MAX_WAIT = 15.0


def _probe_host():
    if SERVER_HOST in ("0.0.0.0", "::"):
        return "localhost"
    return SERVER_HOST


def _frontend_urls():
    urls = [f"http://localhost:{SERVER_PORT}"]
    if SERVER_HOST in ("0.0.0.0", "::"):
        candidates = set()
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                ip = info[4][0]
                if ip and not ip.startswith("127."):
                    candidates.add(ip)
        except OSError:
            pass
        for ip in sorted(candidates):
            urls.append(f"http://{ip}:{SERVER_PORT}")
    elif SERVER_HOST not in ("127.0.0.1", "localhost"):
        urls.append(f"http://{SERVER_HOST}:{SERVER_PORT}")
    return urls


def _listening_endpoints():
    """Return (local address, process ID) pairs listening on SERVER_PORT."""
    if sys.platform != "win32":
        return []
    cmd = (
        f"Get-NetTCPConnection -LocalPort {SERVER_PORT} -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object LocalAddress,OwningProcess | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=5
        )
    except subprocess.TimeoutExpired:
        return []
    text = result.stdout.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    endpoints = []
    for item in data:
        try:
            endpoints.append((str(item.get("LocalAddress", "")).strip(), int(item.get("OwningProcess", 0))))
        except (TypeError, ValueError):
            pass
    return endpoints


def _listening_addresses():
    """Return local addresses currently listening on SERVER_PORT."""
    return {addr for addr, _pid in _listening_endpoints() if addr}


def _listening_process_ids(addresses=None):
    """Return process IDs listening on SERVER_PORT."""
    endpoints = _listening_endpoints()
    allowed = {addr.lower() for addr in addresses} if addresses else None
    pids = set()
    for addr, pid in endpoints:
        if allowed and addr.lower() not in allowed:
            continue
        if pid:
            pids.add(pid)
    return pids


def _listener_matches_host(addresses, host):
    normalized = {addr.strip("[]").lower() for addr in addresses if addr}
    target = (host or "0.0.0.0").strip().lower()
    if target in ("0.0.0.0", "::"):
        return bool(normalized.intersection({"0.0.0.0", "::", "*"}))
    if target == "localhost":
        return bool(normalized.intersection({"127.0.0.1", "::1"}))
    return target in normalized


def _kill_port_listeners(addresses=None):
    """Kill processes currently owning SERVER_PORT."""
    current_pid = os.getpid()
    for pid in _listening_process_ids(addresses):
        if pid == current_pid:
            continue
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, timeout=5
            )
        except subprocess.TimeoutExpired:
            pass


def _kill_loopback_duplicates():
    """When LAN listener exists, remove old loopback-only listeners on the same port."""
    if SERVER_HOST not in ("0.0.0.0", "::"):
        return
    addresses = _listening_addresses()
    if not addresses.intersection({"0.0.0.0", "::"}):
        return
    _kill_port_listeners({"127.0.0.1", "::1"})


def _stale_python_process_query(current_pid):
    """PowerShell query for stale bridge server Python processes only."""
    return (
        "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | "
        f"Where-Object {{ $_.ProcessId -ne {current_pid} -and "
        "$_.CommandLine -match 'skills[\\\\/]server\\.py' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )


def _server_responding():
    """Quick check: is the bridge server already serving JSON?"""
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "2", f"http://{_probe_host()}:{SERVER_PORT}/api/pending"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3
        )
        stdout = (result.stdout or "").strip()
        if result.returncode == 0 and stdout:
            json.loads(stdout)  # validate it's JSON
            return True
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
        pass
    return False


def _kill_stale():
    """Kill any leftover Python server and Node mvu_server processes."""
    current_pid = os.getpid()
    # Python skills processes
    cmd_py = _stale_python_process_query(current_pid)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd_py],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip():
            for pid_str in result.stdout.strip().split():
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid_str],
                        capture_output=True, timeout=5
                    )
                except subprocess.TimeoutExpired:
                    pass
    except subprocess.TimeoutExpired:
        pass

    # Node mvu_server processes
    cmd_node = (
        "Get-Process node -ErrorAction SilentlyContinue | "
        "Where-Object { $_.CommandLine -like '*mvu_server*' } | "
        "Stop-Process -Force"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd_node],
            capture_output=True, text=True, timeout=10
        )
    except subprocess.TimeoutExpired:
        pass


def _start_server(root_dir: str):
    """Launch server.py as a detached background process."""
    root_path = Path(root_dir).resolve()
    server_py = str(root_path / "skills" / "server.py")
    skills_dir = str(root_path / "skills")

    # On Windows, use DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP to background
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    subprocess.Popen(
        [sys.executable, server_py],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=skills_dir,
        creationflags=flags if flags else 0,
        start_new_session=True if not flags else None,
    )


def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            "ok": False, "action": "error",
            "error": "Usage: python start_server.py <ROOT>"
        }, ensure_ascii=False))
        sys.exit(1)

    root_dir = sys.argv[1]

    # 1. Reuse only if the existing process listens on the requested host.
    if _server_responding():
        listen_addresses = _listening_addresses()
        if listen_addresses and _listener_matches_host(listen_addresses, SERVER_HOST):
            _kill_loopback_duplicates()
            listen_addresses = _listening_addresses()
            print(json.dumps({
                "ok": True,
                "action": "already_running",
                "host": SERVER_HOST,
                "port": SERVER_PORT,
                "listen_addresses": sorted(listen_addresses),
                "urls": _frontend_urls(),
                "message": f"Bridge server already running on {SERVER_HOST}:{SERVER_PORT}"
            }, ensure_ascii=False))
            return

    # 2. Kill stale or incorrectly-bound processes and start fresh.
    _kill_port_listeners()
    _kill_stale()

    # Brief wait for ports to release
    time.sleep(0.5)

    _start_server(root_dir)

    # 3. Poll until ready
    waited = 0.0
    while waited < MAX_WAIT:
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL
        if _server_responding():
            print(json.dumps({
                "ok": True,
                "action": "started",
                "host": SERVER_HOST,
                "port": SERVER_PORT,
                "mvu_port": MVU_PORT,
                "listen_addresses": sorted(_listening_addresses()),
                "urls": _frontend_urls(),
                "wait_seconds": round(waited, 1),
                "message": f"Server started on {SERVER_HOST}:{SERVER_PORT} after {waited:.1f}s"
            }, ensure_ascii=False))
            return

    # 4. Timeout — server didn't come up
    print(json.dumps({
        "ok": False,
        "action": "timeout",
        "error": f"Server did not respond on port {SERVER_PORT} within {MAX_WAIT}s"
    }, ensure_ascii=False))
    sys.exit(1)


if __name__ == "__main__":
    main()
