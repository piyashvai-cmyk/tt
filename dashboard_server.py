# -*- coding: utf-8 -*-
"""
FreeFire Level Up Bot - Professional Web Dashboard & Real-Time EXP Tracker
Embedded Async Web Server (aiohttp)
"""

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional
from aiohttp import web

# Global bot state shared between Main.py and Web Dashboard
class BotState:
    def __init__(self):
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.logs: List[Dict[str, Any]] = []
        self.max_logs = 200
        self.total_matches = 0
        self.total_gained_exp = 0
        self.start_time = time.time()
        self.account_workers: Dict[str, asyncio.Task] = {}
        self.refresh_callbacks: Dict[str, Any] = {}
        self.account_credentials: Dict[str, Dict[str, Any]] = {}
        self.daily_limit_file = "daily_limits.json"
        self.daily_limits: Dict[str, Dict[str, Any]] = self._load_daily_limits()

    @staticmethod
    def _bd_now():
        return datetime.now(timezone(timedelta(hours=6)))

    @classmethod
    def current_cycle_date(cls) -> str:
        now = cls._bd_now()
        if now.hour < 7:
            now -= timedelta(days=1)
        return now.date().isoformat()

    def _load_daily_limits(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open("daily_limits.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def save_daily_limits(self) -> None:
        tmp = self.daily_limit_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.daily_limits, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.daily_limit_file)
        except OSError as exc:
            self.log(f"Could not save daily XP state: {exc}", "error")

    def ensure_daily_state(self, uid: str, current_exp: int = 0) -> Dict[str, Any]:
        uid = str(uid)
        state = self.daily_limits.get(uid)
        if not isinstance(state, dict):
            state = {
                "cycle_date": self.current_cycle_date(),
                "daily_gained_exp": 0,
                "daily_paused": False,
                "manual_paused": False,
                "last_exp": int(current_exp or 0),
            }
            self.daily_limits[uid] = state
            self.save_daily_limits()
        state.setdefault("cycle_date", self.current_cycle_date())
        state.setdefault("daily_gained_exp", 0)
        state.setdefault("daily_paused", False)
        state.setdefault("manual_paused", False)
        state.setdefault("last_exp", int(current_exp or 0))
        return state

    def set_manual_pause(self, uid: str, paused: bool) -> None:
        state = self.ensure_daily_state(uid, self.accounts.get(str(uid), {}).get("current_exp", 0))
        state["manual_paused"] = bool(paused)
        self.save_daily_limits()
        acc = self.accounts.get(str(uid))
        if acc is not None:
            acc["manual_paused"] = bool(paused)

    def remove_daily_state(self, uid: str) -> None:
        if str(uid) in self.daily_limits:
            self.daily_limits.pop(str(uid), None)
            self.save_daily_limits()

    def log(self, message: str, level: str = "info", uid: Optional[str] = None):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "uid": uid
        }
        self.logs.append(entry)
        if len(self.logs) > self.max_logs:
            self.logs.pop(0)

    def register_account(self, uid: str, nickname: str, region: str, level: int, exp: int, likes: int = 0):
        uid_str = str(uid)
        lvl_val = max(1, int(level or 1))
        default_mode = "BR"
        daily = self.ensure_daily_state(uid_str, exp)
        initially_paused = bool(daily.get("manual_paused") or daily.get("daily_paused"))
        if uid_str not in self.accounts:
            self.accounts[uid_str] = {
                "uid": uid_str,
                "nickname": nickname or f"Player_{uid_str[:6]}",
                "region": region or "BD",
                "level": lvl_val,
                "initial_exp": exp,
                "current_exp": exp,
                "gained_exp": 0,
                "daily_gained_exp": int(daily.get("daily_gained_exp", 0) or 0),
                "daily_limit_exp": 40000,
                "daily_paused": bool(daily.get("daily_paused")),
                "manual_paused": bool(daily.get("manual_paused")),
                "is_paused": initially_paused,
                "likes": likes or 0,
                "mode": default_mode,
                "mode_manual": False,
                "status": "PAUSED" if initially_paused else "ONLINE",
                "matches_played": 0,
                "active_matches": 0,
                "last_match_time": None,
                "last_updated": time.strftime("%H:%M:%S")
            }
        else:
            acc = self.accounts[uid_str]
            old_level = int(acc.get("level", 1) or 1)
            if nickname:
                acc["nickname"] = nickname
            if region:
                acc["region"] = region
            if level:
                acc["level"] = lvl_val
            acc["current_exp"] = exp
            acc["gained_exp"] = max(0, exp - acc["initial_exp"])
            acc["daily_gained_exp"] = int(daily.get("daily_gained_exp", 0) or 0)
            acc["daily_paused"] = bool(daily.get("daily_paused"))
            acc["manual_paused"] = bool(daily.get("manual_paused"))
            acc["is_paused"] = bool(daily.get("manual_paused") or daily.get("daily_paused"))
            acc["likes"] = likes
            if not acc.get("mode_manual", False):
                acc["mode"] = "BR"
            acc["status"] = "PAUSED" if acc["is_paused"] else "ONLINE"
            acc["last_updated"] = time.strftime("%H:%M:%S")
        self.recalc_totals()

    def update_exp(self, uid: str, current_exp: int, level: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            acc = self.accounts[uid_str]
            old_exp = acc["current_exp"]
            old_level = int(acc.get("level", 1) or 1)
            acc["current_exp"] = current_exp
            if level is not None and level > 0:
                acc["level"] = int(level)
            current_level = int(acc.get("level", 1) or 1)

            # BR remains the automatic/default mode forever. LONE_WOLF
            # is entered only through an explicit manual mode switch.
            if not acc.get("mode_manual", False):
                acc["mode"] = "BR"

            acc["gained_exp"] = max(0, current_exp - acc["initial_exp"])
            acc["last_updated"] = time.strftime("%H:%M:%S")
            diff = current_exp - old_exp
            daily = self.ensure_daily_state(uid_str, current_exp)
            if diff > 0:
                daily["daily_gained_exp"] = int(daily.get("daily_gained_exp", 0) or 0) + diff
            daily["last_exp"] = int(current_exp)
            acc["daily_gained_exp"] = int(daily.get("daily_gained_exp", 0) or 0)
            acc["daily_paused"] = bool(daily.get("daily_paused"))
            acc["manual_paused"] = bool(daily.get("manual_paused"))
            acc["is_paused"] = bool(daily.get("manual_paused") or daily.get("daily_paused"))
            if diff > 0:
                self.save_daily_limits()
                self.log(
                    f"Account {acc['nickname']} ({uid_str}) gained +{diff} EXP! "
                    f"Total Gained: +{acc['gained_exp']} | Mode: {acc['mode']}",
                    "success", uid_str
                )
            self.recalc_totals()

    def get_account_level(self, uid: str) -> int:
        acc = self.accounts.get(str(uid))
        return int(acc.get("level", 1) or 1) if acc else 1

    def get_account_mode(self, uid: str) -> str:
        acc = self.accounts.get(str(uid))
        if not acc:
            return "BR"
        # BR is the default through Level 10. Once the account is above Level 10,
        # expose the effective automatic mode as Lone Wolf regardless of the saved preference.
        level = int(acc.get("level", 1) or 1)
        if level > 10:
            return "LONE_WOLF"
        mode = str(acc.get("mode", "BR") or "BR").upper()
        return "LONE_WOLF" if mode == "LONE_WOLF" and acc.get("mode_manual", False) else "BR"

    def set_account_mode(self, uid: str, mode: str):
        uid_str = str(uid)
        mode = str(mode or "").upper().replace("-", "_").replace(" ", "_")
        if mode in ("LW", "LONEWOLF", "LONE_WOLF"):
            mode = "LONE_WOLF"
        elif mode == "BR":
            mode = "BR"
        else:
            return False, "Invalid mode"

        acc = self.accounts.get(uid_str)
        if not acc:
            return False, "Account not found"

        level = int(acc.get("level", 1) or 1)
        if level < 5 and mode != "BR":
            return False, "Lone Wolf is locked until Level 5"

        acc["mode"] = mode
        acc["mode_manual"] = True
        acc["last_updated"] = time.strftime("%H:%M:%S")
        self.log(
            f"UID {uid_str} ({acc.get('nickname', 'Unknown')}) switched to {mode}",
            "success", uid_str
        )
        return True, mode

    def update_status(self, uid: str, status: str, active_matches: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            self.accounts[uid_str]["status"] = status
            if active_matches is not None:
                self.accounts[uid_str]["active_matches"] = active_matches
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")

    def increment_match(self, uid: str):
        uid_str = str(uid)
        self.total_matches += 1
        if uid_str in self.accounts:
            self.accounts[uid_str]["matches_played"] += 1
            self.accounts[uid_str]["last_match_time"] = time.strftime("%H:%M:%S")
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")
            self.log(f"Account {self.accounts[uid_str]['nickname']} finished Match #{self.accounts[uid_str]['matches_played']}", "info", uid_str)

    def recalc_totals(self):
        self.total_gained_exp = sum(acc.get("gained_exp", 0) for acc in self.accounts.values())


bot_state = BotState()

# Convenience cursor for the dashboard batch importer.
batch_import_state = {"last_start": 0, "last_count": 0, "last_end": 0, "total": 0}


# ==================== HTTP HANDLERS ====================

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")

async def handle_index(request: web.Request) -> web.Response:
    if os.path.exists(TEMPLATE_PATH):
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = "<h1>templates/index.html not found!</h1>"
    return web.Response(text=content, content_type="text/html", charset="utf-8")


async def handle_get_stats(request: web.Request) -> web.Response:
    accounts_data = list(bot_state.accounts.values())
    accounts_data.sort(key=lambda x: x.get("gained_exp", 0), reverse=True)
    return web.json_response({
        "total_accounts": len(bot_state.accounts),
        "total_matches": bot_state.total_matches,
        "total_gained_exp": bot_state.total_gained_exp,
        "accounts": accounts_data,
        "logs": bot_state.logs[-60:],
        "uptime": int(time.time() - bot_state.start_time),
        "import_state": dict(batch_import_state)
    })


async def handle_add_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        accounts_file = "accounts.json"
        existing = []
        if os.path.exists(accounts_file):
            try:
                with open(accounts_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = []

        if "uid" in data and "password" in data:
            uid = str(data["uid"]).strip()
            pwd = str(data["password"]).strip()
            if not uid or not pwd:
                return web.json_response({"status": "error", "error": "UID and Password are required"})
            existing = [acc for acc in existing if str(acc.get("uid")) != uid]
            existing.append({"uid": uid, "password": pwd})
        elif "token" in data:
            token = str(data["token"]).strip()
            if not token:
                return web.json_response({"status": "error", "error": "Token is required"})
            existing = [acc for acc in existing if acc.get("token") != token]
            existing.append({"token": token})
        else:
            return web.json_response({"status": "error", "error": "Invalid payload"})

        with open(accounts_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)

        bot_state.log(f"New account added: {data.get('uid') or 'Token'}", "success")
        
        # Trigger dynamic worker launch
        if "on_account_added" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_account_added"](data))

        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_delete_account(request: web.Request) -> web.Response:
    """Stop the live worker before removing credentials and dashboard state."""
    try:
        data = await request.json()
        uid = str(data.get("uid", "") or "").strip()
        auth_uid = str(data.get("auth_uid", "") or "").strip()
        token = str(data.get("token", "") or "").strip()
        target_uid = uid or auth_uid
        if not target_uid and not token:
            return web.json_response({"status": "error", "error": "UID or token is required"}, status=400)

        # Let the main bot resolve token-backed workers and await cancellation.
        credentials = bot_state.account_credentials.get(target_uid, {}) if target_uid else {}
        callback = bot_state.refresh_callbacks.get("on_account_delete")
        if callback:
            # Pass both the displayed game UID and the original login UID.
            # Guest workers are keyed by the original login UID, not game UID.
            await callback(target_uid, token, auth_uid)

        accounts = await _load_accounts_file()
        target_token = token or str(credentials.get("auth_token", "") or "")
        kept = []
        for item in accounts:
            item_uid = str(item.get("uid", "") or "").strip()
            item_token = str(item.get("token", "") or "").strip()
            matches = (target_uid and item_uid in {target_uid, auth_uid}) or (target_token and item_token == target_token)
            if not matches:
                kept.append(item)
        _save_accounts_file(kept)

        # Remove dashboard rows and cached credentials associated with this account.
        ids_to_remove = {x for x in (uid, auth_uid) if x}
        for key, value in list(bot_state.account_credentials.items()):
            if key in ids_to_remove or (target_token and str(value.get("auth_token", "")) == target_token):
                ids_to_remove.add(str(value.get("account_id", "")))
                bot_state.account_credentials.pop(key, None)
        ids_to_remove.add(str(target_uid or ""))
        for account_id in ids_to_remove:
            if account_id:
                bot_state.accounts.pop(account_id, None)
                bot_state.remove_daily_state(account_id)
        bot_state.recalc_totals()
        bot_state.log(f"Account {target_uid or (target_token[:10] + '…')} deleted; worker stopped.", "warning", target_uid or None)
        return web.json_response({"status": "ok", "deleted": target_uid or target_token[:10]})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def _load_accounts_file() -> List[Dict[str, Any]]:
    if not os.path.exists("accounts.json"):
        return []
    try:
        with open("accounts.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_accounts_file(accounts: List[Dict[str, Any]]) -> None:
    with open("accounts.json", "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2, ensure_ascii=False)


def _normalize_uploaded_accounts(payload: Any) -> tuple[List[Dict[str, str]], Optional[str]]:
    if isinstance(payload, dict):
        for key in ("accounts", "data", "users"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        return [], "JSON must contain an account list (array or accounts/data/users array)"

    result: List[Dict[str, str]] = []
    for pos, item in enumerate(payload, 1):
        if not isinstance(item, dict):
            return [], f"Account #{pos} is not an object"
        uid = str(item.get("uid", "") or "").strip()
        password = str(item.get("password", "") or item.get("pass", "") or "").strip()
        token = str(item.get("token", "") or "").strip()
        if uid and password:
            result.append({"uid": uid, "password": password})
        elif token:
            result.append({"token": token})
        else:
            return [], f"Account #{pos} needs uid + password, or token"
    return result, None


async def handle_batch_import(request: web.Request) -> web.Response:
    global batch_import_state
    try:
        reader = await request.multipart()
        file_bytes = None
        file_filename = ""
        start_raw, count_raw = "1", "1"
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "file":
                file_filename = part.filename or ""
                file_bytes = await part.read(decode=False)
            elif part.name == "start":
                start_raw = (await part.text()).strip() or "1"
            elif part.name == "count":
                count_raw = (await part.text()).strip() or "1"

        if file_bytes is None or not file_filename:
            return web.json_response({"status": "error", "error": "Please select a JSON file"}, status=400)
        try:
            start, count = int(start_raw), int(count_raw)
        except ValueError:
            return web.json_response({"status": "error", "error": "Start and count must be whole numbers"}, status=400)
        if start < 1 or count < 1:
            return web.json_response({"status": "error", "error": "Start must be >= 1 and count must be >= 1"}, status=400)
        if count > 500:
            return web.json_response({"status": "error", "error": "Maximum 500 accounts per batch"}, status=400)

        raw = file_bytes
        if len(raw) > 2 * 1024 * 1024:
            return web.json_response({"status": "error", "error": "JSON file is too large (max 2 MB)"}, status=400)
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except Exception as e:
            return web.json_response({"status": "error", "error": f"Invalid JSON: {e}"}, status=400)

        source, err = _normalize_uploaded_accounts(payload)
        if err:
            return web.json_response({"status": "error", "error": err}, status=400)
        total = len(source)
        if total == 0:
            return web.json_response({"status": "error", "error": "JSON contains no accounts"}, status=400)
        if start > total:
            return web.json_response({"status": "error", "error": f"Start account #{start} is outside the JSON (total {total})"}, status=400)

        end = min(total, start + count - 1)
        selected = source[start - 1:end]
        existing = await _load_accounts_file()
        for acc in selected:
            if "uid" in acc:
                existing = [x for x in existing if str(x.get("uid", "")).strip() != acc["uid"]]
            else:
                existing = [x for x in existing if str(x.get("token", "")).strip() != acc["token"]]
            existing.append(acc)
        _save_accounts_file(existing)

        batch_import_state = {"last_start": start, "last_count": len(selected), "last_end": end, "total": total}
        callback = bot_state.refresh_callbacks.get("on_account_added")
        started = 0
        if callback:
            for acc in selected:
                await callback(acc)
                started += 1
                if started < len(selected):
                    await asyncio.sleep(0.20)

        bot_state.log(f"Batch import: accounts {start}-{end} selected from {total}; {len(selected)} loaded.", "success")
        return web.json_response({"status": "ok", "total": total, "start": start, "count": len(selected), "end": end, "next_start": end + 1 if end < total else None, "started": started, "message": f"Accounts {start}-{end} imported"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def handle_delete_all_accounts(request: web.Request) -> web.Response:
    try:
        accounts = await _load_accounts_file()
        removed = len(accounts)
        workers = list(bot_state.account_workers.values())
        for worker in workers:
            if worker and not worker.done():
                worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        bot_state.account_workers.clear()
        _save_accounts_file([])
        bot_state.accounts.clear()
        bot_state.account_credentials.clear()
        bot_state.daily_limits.clear()
        bot_state.save_daily_limits()
        bot_state.recalc_totals()
        bot_state.log(f"All accounts deleted. {removed} account(s) removed from rotation.", "warning")
        return web.json_response({"status": "ok", "removed": removed})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def handle_pause_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid", "") or "").strip()
        if not uid or uid not in bot_state.accounts:
            return web.json_response({"status": "error", "error": "Account not found"}, status=404)
        acc = bot_state.accounts[uid]
        daily = bot_state.ensure_daily_state(uid, acc.get("current_exp", 0))
        is_paused = bool(acc.get("is_paused", False) or acc.get("manual_paused") or acc.get("daily_paused") or str(acc.get("status", "")).upper() == "PAUSED")

        if not is_paused:
            callback = bot_state.refresh_callbacks.get("on_account_pause")
            if not callback:
                return web.json_response({"status": "error", "error": "Pause handler unavailable"}, status=503)
            bot_state.set_manual_pause(uid, True)
            await callback(uid)
            acc["is_paused"] = True
            acc["status"] = "PAUSED"
            acc["active_matches"] = 0
            bot_state.log(f"Account {uid} manually paused.", "warning", uid)
            return web.json_response({"status": "ok", "uid": uid, "is_paused": True})

        # The daily cap cannot be bypassed by pressing Resume. Manual pause can be
        # cleared, but the account remains off until the next 07:00 Bangladesh reset.
        if bool(daily.get("daily_paused")) or int(daily.get("daily_gained_exp", 0) or 0) >= 40000:
            daily["daily_paused"] = True
            bot_state.save_daily_limits()
            acc["daily_paused"] = True
            acc["is_paused"] = True
            acc["status"] = "PAUSED"
            return web.json_response({"status": "error", "error": "Daily limit reached (40,000 EXP). Account will resume at 07:00 Bangladesh time unless manually paused.", "daily_paused": True}, status=409)

        callback = bot_state.refresh_callbacks.get("on_account_resume")
        if not callback:
            return web.json_response({"status": "error", "error": "Resume handler unavailable"}, status=503)
        bot_state.set_manual_pause(uid, False)
        result = await callback(uid)
        if result is False:
            bot_state.set_manual_pause(uid, True)
            acc["status"] = "PAUSED"
            acc["is_paused"] = True
            return web.json_response({"status": "error", "error": "Saved login credentials not found; account was not resumed"}, status=409)
        acc["is_paused"] = False
        acc["manual_paused"] = False
        acc["status"] = "CONNECTING"
        acc["active_matches"] = 0
        acc["last_updated"] = time.strftime("%H:%M:%S")
        bot_state.log(f"Account {uid} resumed manually.", "success", uid)
        return web.json_response({"status": "ok", "uid": uid, "is_paused": False})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def handle_pause_all(request: web.Request) -> web.Response:
    try:
        accounts = list(bot_state.accounts.items())
        all_paused = bool(accounts) and all(bool(acc.get("is_paused") or acc.get("status") == "PAUSED") for _, acc in accounts)
        pause_now = not all_paused
        callback_name = "on_account_pause" if pause_now else "on_account_resume"
        callback = bot_state.refresh_callbacks.get(callback_name)
        if not callback:
            return web.json_response({"status": "error", "error": f"{callback_name} handler unavailable"}, status=503)
        for uid, acc in accounts:
            daily = bot_state.ensure_daily_state(uid, acc.get("current_exp", 0))
            if pause_now:
                bot_state.set_manual_pause(uid, True)
                await callback(uid)
                acc["is_paused"] = True
                acc["status"] = "PAUSED"
            else:
                bot_state.set_manual_pause(uid, False)
                if bool(daily.get("daily_paused")) or int(daily.get("daily_gained_exp", 0) or 0) >= 40000:
                    daily["daily_paused"] = True
                    acc["daily_paused"] = True
                    acc["is_paused"] = True
                    acc["status"] = "PAUSED"
                    continue
                result = await callback(uid)
                if result is False:
                    acc["status"] = "OFFLINE"
                    acc["is_paused"] = False
                    continue
                acc["is_paused"] = False
                acc["status"] = "CONNECTING"
            acc["active_matches"] = 0
            acc["last_updated"] = time.strftime("%H:%M:%S")
        bot_state.save_daily_limits()
        bot_state.log("All accounts manually paused." if pause_now else "All eligible accounts resumed; daily-capped accounts remain paused until 07:00 BD time.", "warning" if pause_now else "success")
        return web.json_response({"status": "ok", "all_paused": pause_now})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def handle_restart_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid", "") or "").strip()
        if not uid or uid not in bot_state.accounts:
            return web.json_response({"status": "error", "error": "Account not found"}, status=404)
        daily = bot_state.ensure_daily_state(uid, bot_state.accounts[uid].get("current_exp", 0))
        if bool(daily.get("manual_paused")):
            return web.json_response({"status": "error", "error": "Account is manually paused. Resume it first."}, status=409)
        if bool(daily.get("daily_paused")) or int(daily.get("daily_gained_exp", 0) or 0) >= 40000:
            return web.json_response({"status": "error", "error": "Daily 40,000 EXP limit reached. Account will resume at 07:00 Bangladesh time."}, status=409)
        callback = bot_state.refresh_callbacks.get("on_account_restart")
        if not callback:
            return web.json_response({"status": "error", "error": "Restart handler unavailable"}, status=503)
        result = await callback(uid)
        if result is False:
            return web.json_response({"status": "error", "error": "Saved credentials not found; account was not restarted"}, status=409)
        bot_state.accounts[uid]["is_paused"] = False
        bot_state.accounts[uid]["status"] = "CONNECTING"
        bot_state.accounts[uid]["active_matches"] = 0
        return web.json_response({"status": "ok", "uid": uid})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "accounts": len(bot_state.accounts), "uptime": int(time.time() - bot_state.start_time)})


async def handle_refresh_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid")).strip()
        if "on_refresh_account" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_refresh_account"](uid))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_mode_switch(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        mode = str(data.get("mode", "")).strip()
        ok, result = bot_state.set_account_mode(uid, mode)
        if not ok:
            return web.json_response({"status": "error", "error": result}, status=400)
        return web.json_response({
            "status": "ok",
            "uid": uid,
            "mode": result,
            "level": bot_state.get_account_level(uid)
        })
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=400)


async def start_web_dashboard(host: str = "0.0.0.0", port: int = 5000):
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/stats", handle_get_stats)
    app.router.add_post("/api/account/add", handle_add_account)
    app.router.add_post("/api/account/delete", handle_delete_account)
    app.router.add_post("/api/account/pause", handle_pause_account)
    app.router.add_post("/api/account/pause_all", handle_pause_all)
    app.router.add_post("/api/account/restart", handle_restart_account)
    app.router.add_post("/api/account/import", handle_batch_import)
    app.router.add_post("/api/account/delete-all", handle_delete_all_accounts)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/api/account/refresh", handle_refresh_account)
    app.router.add_post("/api/account/mode", handle_mode_switch)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"\033[92m[+] Web Dashboard running on http://localhost:{port}\033[0m")
