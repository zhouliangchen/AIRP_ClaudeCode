"""
Claude Code RP Bridge Server
Serves frontend + receives user input via POST, writes to input.txt for Claude Code to read.
Usage: python server.py [port]
"""
import http.server
import json
import os
import random
import signal
import socket
import subprocess
import sys
import urllib.parse
import mimetypes
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
HOST = (sys.argv[2] if len(sys.argv) > 2 else os.environ.get("AIRP_HOST", "0.0.0.0")).strip() or "0.0.0.0"
SKILLS = Path(__file__).parent
ROOT = SKILLS / "styles"
PROFILES_DIR = ROOT / "profiles"
INPUT_FILE = ROOT / "input.txt"
PENDING_FILE = ROOT / ".pending"
SETTINGS_FILE = ROOT / "settings.json"
CARD_PATH_FILE = ROOT / ".card_path"
INITVAR_FILE = ROOT / ".initvar"
SESSION_FILE = ROOT / ".session_init"

# Allow importing handler from skills/
sys.path.insert(0, str(SKILLS))
import handler

DEFAULT_SETTINGS = {
    "style": "北棱特调",
    "nsfw": "直白",
    "person": "第二人称",
    "antiImpersonation": True,
    "bgNpc": False,
    "charName": "",
    "wordCount": 600,
    "selfRepairMode": "limited",
    "allowSourceCodeSelfRepair": False,
    "modelDebugMode": False,
}

os.chdir(str(ROOT))


def _safe_decode(data):
    """Try UTF-8 first, then common Chinese encodings."""
    for enc in ("utf-8", "gbk", "cp936", "gb18030"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _lan_frontend_urls():
    """Return likely frontend URLs for the current bind host."""
    urls = [f"http://localhost:{PORT}"]
    if HOST in ("0.0.0.0", "::"):
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
            urls.append(f"http://{ip}:{PORT}")
    elif HOST not in ("127.0.0.1", "localhost"):
        urls.append(f"http://{HOST}:{PORT}")
    return urls


def _card_folder():
    """Read the current card folder path from .card_path config."""
    if CARD_PATH_FILE.exists():
        for enc in ("utf-8", "gbk", "cp936"):
            try:
                path = CARD_PATH_FILE.read_text(encoding=enc).strip()
                if path and os.path.isdir(path):
                    return path
            except (UnicodeDecodeError, LookupError):
                continue
    return None


MBTI_STACKS = {
    "INTJ": {"主导": "Ni", "辅助": "Te", "第三": "Fi", "劣势": "Se"},
    "INTP": {"主导": "Ti", "辅助": "Ne", "第三": "Si", "劣势": "Fe"},
    "ENTJ": {"主导": "Te", "辅助": "Ni", "第三": "Se", "劣势": "Fi"},
    "ENTP": {"主导": "Ne", "辅助": "Ti", "第三": "Fe", "劣势": "Si"},
    "INFJ": {"主导": "Ni", "辅助": "Fe", "第三": "Ti", "劣势": "Se"},
    "INFP": {"主导": "Fi", "辅助": "Ne", "第三": "Si", "劣势": "Te"},
    "ENFJ": {"主导": "Fe", "辅助": "Ni", "第三": "Se", "劣势": "Ti"},
    "ENFP": {"主导": "Ne", "辅助": "Fi", "第三": "Te", "劣势": "Si"},
    "ISTJ": {"主导": "Si", "辅助": "Te", "第三": "Fi", "劣势": "Ne"},
    "ISFJ": {"主导": "Si", "辅助": "Fe", "第三": "Ti", "劣势": "Ne"},
    "ESTJ": {"主导": "Te", "辅助": "Si", "第三": "Ne", "劣势": "Fi"},
    "ESFJ": {"主导": "Fe", "辅助": "Si", "第三": "Ne", "劣势": "Ti"},
    "ISTP": {"主导": "Ti", "辅助": "Se", "第三": "Ni", "劣势": "Fe"},
    "ISFP": {"主导": "Fi", "辅助": "Se", "第三": "Ni", "劣势": "Te"},
    "ESTP": {"主导": "Se", "辅助": "Ti", "第三": "Fe", "劣势": "Ni"},
    "ESFP": {"主导": "Se", "辅助": "Fi", "第三": "Te", "劣势": "Ni"},
}


def _random_jungian():
    """Randomly assign a 16-type MBTI cognitive function stack."""
    mbti_type = random.choice(list(MBTI_STACKS.keys()))
    stack = dict(MBTI_STACKS[mbti_type])
    stack["_type"] = mbti_type
    return stack


def _random_age(gender="", role=""):
    """Generate a plausible age when user hasn't specified one.
    Weights toward young adult (20-28), with small chance of older."""
    roll = random.random()
    if roll < 0.55:
        return random.randint(20, 26)
    elif roll < 0.85:
        return random.randint(27, 35)
    elif roll < 0.95:
        return random.randint(36, 50)
    else:
        return random.randint(18, 19)


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/submit":
            length = int(self.headers.get("Content-Length", 0))
            body = _safe_decode(self.rfile.read(length))
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {"text": body}

            text = data.get("text", "")
            if text is None:
                text = ""
            if not isinstance(text, str):
                text = str(text)
            char_name = data.get("charName", "")
            if char_name is None:
                char_name = ""
            if not isinstance(char_name, str):
                char_name = str(char_name)
            char_name = char_name.strip()

            role_text = data.get("roleText")
            instruction_text = data.get("instructionText")
            has_dual_channel = role_text is not None or instruction_text is not None
            if has_dual_channel:
                role_text = "" if role_text is None else str(role_text)
                instruction_text = "" if instruction_text is None else str(instruction_text)
                full_raw = (
                    role_text + "\n\n[USER_INSTRUCTION]\n" + instruction_text
                    if instruction_text
                    else role_text
                )
                text = role_text

            if has_dual_channel and (role_text or instruction_text):
                has_visible_role_text = bool(role_text.strip())
                full = f"【{char_name}】{role_text}" if char_name and has_visible_role_text else (role_text if has_visible_role_text else "")
                INPUT_FILE.write_text(full_raw, encoding="utf-8")
                card = _card_folder()
                player_entry = None
                if card:
                    player_entry = handler.record_player_input(
                        card,
                        full_raw,
                        full,
                        role_text=role_text,
                        user_instruction_text=instruction_text,
                        input_schema="dual_channel_v1",
                    )
                    handler.write_pending_user_turn(
                        card,
                        full,
                        raw_text=full_raw,
                        input_id=player_entry.get("id"),
                        role_text=role_text,
                        user_instruction_text=instruction_text,
                        input_schema="dual_channel_v1",
                    )
                    handler.write_content_js(card)
                handler.write_progress("input.received", "已接收玩家输入", percent=10)
                PENDING_FILE.touch()
                self._json({"ok": True, "text": full, "player_input_id": player_entry.get("id") if player_entry else None})
            elif text.strip():
                # Write input for Claude Code
                full = f"【{char_name}】{text}" if char_name else text
                INPUT_FILE.write_text(full, encoding="utf-8")
                card = _card_folder()
                player_entry = None
                if card:
                    player_entry = handler.record_player_input(card, text, full)
                    handler.write_pending_user_turn(
                        card,
                        full,
                        raw_text=text,
                        input_id=player_entry.get("id"),
                    )
                    handler.write_content_js(card)
                handler.write_progress("input.received", "已接收玩家输入", percent=10)
                PENDING_FILE.touch()
                self._json({"ok": True, "text": full, "player_input_id": player_entry.get("id") if player_entry else None})
            else:
                self._json({"ok": False, "error": "empty input"})

        elif parsed.path == "/api/player_inputs/edit":
            card = _card_folder()
            if not card:
                self._json({"ok": False, "error": "no card path configured"}, 400)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = _safe_decode(self.rfile.read(length))
            try:
                data = json.loads(body)
                input_id = data.get("id") or data.get("input_id")
                new_text = data.get("new_text", data.get("text"))
                mode = data.get("mode", "update_only")
                if not input_id:
                    self._json({"ok": False, "error": "missing input id"}, 400)
                    return
                if not isinstance(new_text, str) or new_text == "":
                    self._json({"ok": False, "error": "empty input"}, 400)
                    return
                result = handler.edit_player_input(card, input_id, new_text, mode)
                self._json({"ok": True, "edit": result})
            except json.JSONDecodeError:
                self._json({"ok": False, "error": "invalid json"}, 400)
            except ValueError as e:
                self._json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._json({"ok": False, "error": str(e)}, 500)

        elif parsed.path == "/api/settings":
            length = int(self.headers.get("Content-Length", 0))
            body = _safe_decode(self.rfile.read(length))
            try:
                data = json.loads(body)
                current = DEFAULT_SETTINGS.copy()
                if SETTINGS_FILE.exists():
                    try:
                        current.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig")))
                    except Exception:
                        pass
                current.update(data)
                SETTINGS_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
                self._json({"ok": True, "settings": current})
            except json.JSONDecodeError:
                self._json({"ok": False, "error": "invalid json"})

        elif parsed.path == "/api/reroll":
            card = _card_folder()
            if not card:
                self._json({"ok": False, "error": "no card path configured"}, 400)
                return
            try:
                user_text = handler.reroll_last(card)
                if user_text:
                    self._json({"ok": True, "text": user_text})
                else:
                    self._json({"ok": False, "error": "no turns to reroll"}, 400)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._json({"ok": False, "error": str(e)}, 500)

        elif parsed.path == "/api/delete_turns":
            card = _card_folder()
            if not card:
                self._json({"ok": False, "error": "no card path configured"}, 400)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = _safe_decode(self.rfile.read(length))
            try:
                data = json.loads(body)
                from_index = int(data.get("from_index", 0))
                handler.delete_turns(card, from_index)
                self._json({"ok": True})
            except (json.JSONDecodeError, ValueError) as e:
                self._json({"ok": False, "error": str(e)}, 400)

        elif parsed.path == "/api/switch_opening":
            card = _card_folder()
            if not card:
                self._json({"ok": False, "error": "no card path configured"}, 400)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = _safe_decode(self.rfile.read(length))
            try:
                data = json.loads(body)
                opening_id = int(data.get("opening_id", 0))
                ok = handler.switch_opening(card, opening_id)
                if ok:
                    self._json({"ok": True})
                else:
                    self._json({"ok": False, "error": "cannot switch — opening already in progress"}, 400)
            except (json.JSONDecodeError, ValueError) as e:
                self._json({"ok": False, "error": str(e)}, 400)

        elif parsed.path == "/api/init_session":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = _safe_decode(self.rfile.read(length))
                data = json.loads(body)
                message = data.get("message", "")
                if message is None:
                    message = ""
                if not isinstance(message, str):
                    message = str(message)
                config = data.get("config", {})
                if not message.strip():
                    self._json({"ok": False, "error": "empty message"}, 400)
                    return

                # Check if card already has a native initvar from tavern_helper scripts
                card = _card_folder()
                card_has_native_initvar = False
                if card:
                    card_initvar_path = Path(card) / ".initvar.json"
                    if card_initvar_path.exists():
                        try:
                            existing = json.loads(card_initvar_path.read_text(encoding="utf-8"))
                            # Detect native initvar: has keys that don't match selector's hardcoded structure
                            selector_keys = {"世界设定", "玩家", "互动对象"}
                            native_keys = set(existing.keys())
                            if native_keys and not native_keys.issubset(selector_keys):
                                card_has_native_initvar = True
                        except Exception:
                            pass

                if card_has_native_initvar:
                    # Card has its own variable structure from tavern_helper Zod schema.
                    # Don't overwrite it — just write the user message and mark session active.
                    INPUT_FILE.write_text(message, encoding="utf-8")
                    if card:
                        player_entry = handler.record_player_input(card, message, message)
                        handler.write_pending_user_turn(
                            card,
                            message,
                            raw_text=message,
                            input_id=player_entry.get("id"),
                        )
                        handler.write_content_js(card)
                    handler.write_progress("input.received", "已接收玩家开局设定", percent=10)
                    PENDING_FILE.touch()
                    SESSION_FILE.touch()
                    self._json({"ok": True, "card_initvar_used": True})
                    return

                # Write initvar for MVU (selector's hardcoded structure, for cards without tavern_helper)
                initvar = {
                    "世界设定": {
                        "当前世界观类型": config.get("world", ""),
                        "性癖": "、".join(config.get("kinks", []))
                    },
                    "玩家": {
                        "姓名": "{{user}}",
                        "性别": config.get("gender", ""),
                        "年龄": config.get("age", 22),
                        "职业": config.get("role", "")
                    }
                }
                # Add partners to 互动对象
                partners_obj = {}
                for p in config.get("partners", []):
                    pname = p.get("name", "").strip()
                    if pname:
                        age_raw = p.get("age")
                        try:
                            age_val = int(age_raw) if age_raw and str(age_raw).strip() else None
                        except (ValueError, TypeError):
                            age_val = None
                        if age_val is None:
                            age_val = _random_age(p.get("gender", ""), p.get("desc", ""))
                        partners_obj[pname] = {
                            "姓名": pname,
                            "性别": p.get("gender", ""),
                            "年龄": age_val,
                            "职业": p.get("desc", ""),
                            "性格": {
                                "荣格八维": _random_jungian(),
                                "核心特征": "",
                                "隐藏的秘密": "",
                                "是否处子": None
                            },
                            "隐藏的秘密": "",
                            "是否处子": None
                        }
                initvar["互动对象"] = partners_obj
                INITVAR_FILE.write_text(json.dumps(initvar, ensure_ascii=False, indent=2), encoding="utf-8")
                # Also write to card folder as .initvar.json for handler.py
                card = _card_folder()
                if card:
                    card_initvar = Path(card) / ".initvar.json"
                    card_initvar.write_text(json.dumps(initvar, ensure_ascii=False, indent=2), encoding="utf-8")
                # Write user message to input.txt
                INPUT_FILE.write_text(message, encoding="utf-8")
                if card:
                    player_entry = handler.record_player_input(card, message, message)
                    handler.write_pending_user_turn(
                        card,
                        message,
                        raw_text=message,
                        input_id=player_entry.get("id"),
                    )
                    handler.write_content_js(card)
                handler.write_progress("input.received", "已接收玩家开局设定", percent=10)
                PENDING_FILE.touch()
                SESSION_FILE.touch()
                self._json({"ok": True})
            except json.JSONDecodeError:
                self._json({"ok": False, "error": "invalid json"}, 400)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._json({"ok": False, "error": str(e)}, 500)

        elif parsed.path == "/api/session_status":
            self._json({"initialized": SESSION_FILE.exists()})

        elif parsed.path == "/api/style-profiles/delete":
            length = int(self.headers.get("Content-Length", 0))
            body = _safe_decode(self.rfile.read(length))
            try:
                data = json.loads(body)
                name = data.get("name", "").strip()
                if not name:
                    self._json({"ok": False, "error": "missing name"}, 400)
                    return
                target = PROFILES_DIR / f"{name}.md"
                if target.exists():
                    target.unlink()
                    self._json({"ok": True})
                else:
                    self._json({"ok": False, "error": "profile not found"}, 404)
            except (json.JSONDecodeError, OSError) as e:
                self._json({"ok": False, "error": str(e)}, 400)

        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            return

        # API: check if new input is pending
        if parsed.path == "/api/pending":
            if PENDING_FILE.exists():
                text = INPUT_FILE.read_text(encoding="utf-8") if INPUT_FILE.exists() else ""
                self._json({"pending": True, "text": text})
            else:
                self._json({"pending": False})
            return

        # API: long-poll until pending becomes true (replaces Monitor polling)
        if parsed.path == "/api/wait_pending":
            import time
            timeout = 300
            start = time.time()
            while time.time() - start < timeout:
                if PENDING_FILE.exists():
                    text = INPUT_FILE.read_text(encoding="utf-8") if INPUT_FILE.exists() else ""
                    self._json({"pending": True, "text": text})
                    return
                time.sleep(1)
            self._json({"pending": False})
            return

        # API: mark as processed (Claude Code calls this after reading)
        if parsed.path == "/api/done":
            PENDING_FILE.unlink(missing_ok=True)
            self._json({"ok": True})
            return

        # API: list available openings
        if parsed.path == "/api/openings":
            openings = handler.list_openings()
            self._json(openings)
            return

        # API: list available style profiles
        if parsed.path == "/api/style-profiles":
            profiles = []
            if PROFILES_DIR.exists():
                for f in sorted(PROFILES_DIR.glob("*.md")):
                    name = f.stem
                    content = f.read_text(encoding="utf-8")
                    title = name
                    desc = ""
                    lines = content.strip().split("\n")
                    for line in lines:
                        if line.startswith("# ") and not line.startswith("## "):
                            title = line[2:].strip()
                        elif line.strip() and not line.startswith("#"):
                            desc = line.strip()
                            break
                    profiles.append({"name": name, "title": title, "description": desc})
            self._json(profiles)
            return

        # API: check if session is initialized
        if parsed.path == "/api/session_status":
            self._json({"initialized": SESSION_FILE.exists()})
            return

        # API: get current settings
        if parsed.path == "/api/settings":
            settings = DEFAULT_SETTINGS.copy()
            if SETTINGS_FILE.exists():
                try:
                    saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
                    settings.update(saved)
                except Exception:
                    pass
            self._json(settings)
            return

        # API: current response progress, if available
        if parsed.path == "/api/progress":
            self._json(handler.read_progress())
            return

        # API: player-authored input log for editing UI.
        if parsed.path == "/api/player_inputs":
            card = _card_folder()
            if not card:
                self._json({"ok": False, "error": "no card path configured"}, 400)
                return
            self._json({"ok": True, "inputs": handler.frontend_player_inputs(card)})
            return

        # API: serve generated/card-local assets safely from the active card folder
        if parsed.path.startswith("/api/card_asset/"):
            card = _card_folder()
            if not card:
                self.send_error(404, "no card path configured")
                return
            rel = urllib.parse.unquote(parsed.path[len("/api/card_asset/"):]).replace("\\", "/")
            if rel.startswith("/") or ".." in Path(rel).parts:
                self.send_error(400, "invalid asset path")
                return
            card_root = Path(card).resolve()
            target = (card_root / rel).resolve()
            try:
                target.relative_to(card_root)
            except ValueError:
                self.send_error(403, "asset outside card folder")
                return
            if not target.exists() or not target.is_file():
                self.send_error(404, "asset not found")
                return
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # Default: serve static files
        super().do_GET()

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, fmt, *args):
        # Quieter logging
        if "POST" in fmt or "/api/" in fmt:
            print(f"[server] {fmt % args}")


if __name__ == "__main__":
    # --- Clean up stale mvu_server processes ---
    try:
        raw = subprocess.check_output(
            'powershell -Command "Get-Process node | Where-Object { $_.CommandLine -like \'*mvu_server*\' } | Select-Object -ExpandProperty Id"',
            shell=True, timeout=10
        )
        out = _safe_decode(raw).strip()
        if out:
            for pid_str in out.split():
                os.kill(int(pid_str), signal.SIGTERM)
            print(f"[server] 清理残留 mvu_server 进程: {out}")
    except Exception:
        pass

    # --- Launch MVU Server ---
    mvu_proc = None
    card = _card_folder()
    mvu_script = SKILLS / "mvu_server.js"
    if mvu_script.exists():
        card_arg = f"--card={card}" if card else f"--card={str(SKILLS.parent)}"
        try:
            mvu_proc = subprocess.Popen(
                ["node", str(mvu_script), card_arg, "--port=8766"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(SKILLS)
            )
            # Wait briefly to see if it starts
            import time
            time.sleep(1.5)
            if mvu_proc.poll() is not None:
                stderr = _safe_decode(mvu_proc.stderr.read())
                print(f"[server] mvu_server 启动失败: {stderr}")
                mvu_proc = None
            else:
                print(f"[server] mvu_server 已启动 (PID {mvu_proc.pid})")
        except FileNotFoundError:
            print("[server] Node.js 未安装，跳过 mvu_server")
        except Exception as e:
            print(f"[server] mvu_server 启动异常: {e}")
            mvu_proc = None
    else:
        print(f"[server] mvu_server.js 不存在: {mvu_script}")

    print(f"\n  RP Bridge Server")
    print(f"  Listening → {HOST}:{PORT}")
    for idx, url in enumerate(_lan_frontend_urls()):
        label = "Local frontend" if idx == 0 else "LAN frontend"
        print(f"  {label} → {url}")
    print(f"  Input file → {INPUT_FILE}")
    if HOST in ("0.0.0.0", "::"):
        print("  LAN access requires the device to be on the same network and Windows Firewall to allow Python.")
    print(f"  Ctrl+C to stop\n")
    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] 正在关闭...")
    finally:
        if mvu_proc and mvu_proc.poll() is None:
            mvu_proc.terminate()
            try:
                mvu_proc.wait(timeout=5)
            except Exception:
                mvu_proc.kill()
            print("[server] mvu_server 已停止")
        server.shutdown()
        print("[server] 已停止")
