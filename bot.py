import os
import json
import time
import random
import asyncio
import logging
import tempfile
import threading
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from base64 import b64decode
from typing import Optional, List, Dict, Any, Tuple, Set, Callable, Awaitable
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from github import Github, GithubException

try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

from crunchyroll_checker import (
    check_cookie_file,
    check_cookie_content,
    quick_check_cookie_content,
)

BOT_VERSION = "v1.0.2"
CRUNCHYROLL_BANNER_GIF = "https://cdn.discordapp.com/attachments/1515490244353458227/1553918478677966998/CRLogos-high.gif"
CRUNCHYROLL_LOGO = "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7b/Crunchyroll_Logo.png/320px-Crunchyroll_Logo.png"
GET_KEY_URL = "https://linkjust.com/"

TUTORIAL_VIDEO_URL = "https://pixeldrain.com/u/3Z8K1ucY"

DISCORD_USER_URL = "https://discord.com/users/994817247061225633"
DISCORD_SERVER_URL = "https://discord.gg/btRCeujadA"

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

if not DISCORD_BOT_TOKEN:
    raise ValueError("Missing DISCORD_TOKEN environment variable")

EGYPT_TZ = ZoneInfo("Africa/Cairo")

COOKIES_FOLDER = Path("cookies-2")
USER_LOG_FILE = Path("cr_users.txt")
CONFIG_FILE = Path("cr_config.json")
SETUP_TRACKER_FILE = Path("cr_setup_messages.json")
GUILD_CONFIG_FILE = Path("cr_guild_config.json")
CHECK_ALL_SCHEDULE_FILE = Path("cr_check_all_schedule.json")

SCRIPT_TIMEOUT = 60
QUICK_CHECK_TIMEOUT = 20
CREATE_COOKIE_BUDGET_SECONDS = 300

CLEANUP_DELAY_SECONDS = 1800

COOLDOWN_HOURS = 24

CHECK_ALL_HOUR = 3
CHECK_ALL_MINUTE = 0
CHECK_ALL_INTERVAL_DAYS = 2

COOKIE_CHECK_LIMIT = int(os.environ.get("COOKIE_CHECK_LIMIT", "5"))
COOKIE_CHECK_WINDOW_HOURS = int(os.environ.get("COOKIE_CHECK_WINDOW_HOURS", "24"))
COOKIE_CHECK_WINDOW_SECONDS = COOKIE_CHECK_WINDOW_HOURS * 60 * 60

_DEFAULT_CRUNCHYROLL_LOG_URL = "https://raw.githubusercontent.com/Afrsto/bot-users/main/CrunchyRoll-users.txt"
CRUNCHYROLL_LOG_URL = os.environ.get("REMOTE_LOG_URL", "").strip() or _DEFAULT_CRUNCHYROLL_LOG_URL

MAX_CONCURRENT_CHECKS = int(os.environ.get("MAX_CONCURRENT_CHECKS", "8"))
_check_all_executor = ThreadPoolExecutor(
    max_workers=max(1, MAX_CONCURRENT_CHECKS),
    thread_name_prefix="cr_check_all",
)
CREATE_SAME_COOKIE_TIMEOUT_RETRIES = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("CrunchyrollBot")

ALLOWED_GUILD_IDS: List[int] = []
for key, val in os.environ.items():
    if key.startswith("GUILD_ID_") and val and val.strip().isdigit():
        ALLOWED_GUILD_IDS.append(int(val.strip()))
_legacy_guild = os.environ.get("GUILD_ID", "").strip()
if _legacy_guild.isdigit() and int(_legacy_guild) not in ALLOWED_GUILD_IDS:
    ALLOWED_GUILD_IDS.append(int(_legacy_guild))

DEFAULT_CHANNEL_ID: Optional[int] = None
_default_ch = os.environ.get("DEFAULT_CHANNEL_ID", "").strip()
if _default_ch.isdigit():
    DEFAULT_CHANNEL_ID = int(_default_ch)

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


def _parse_github_blob_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    if not url:
        return None, None
    p = urlparse(url)
    if p.netloc != "github.com":
        return None, None
    parts = p.path.strip("/").split("/")
    if len(parts) < 2:
        return None, None
    repo = f"{parts[0]}/{parts[1]}"
    if "blob" in parts:
        bi = parts.index("blob")
        if bi + 2 < len(parts):
            return repo, "/".join(parts[bi + 2:])
    return repo, None


def _parse_github_tree_url(url: str) -> Tuple[Optional[str], Optional[str], str]:
    if not url:
        return None, None, "main"
    p = urlparse(url)
    if p.netloc != "github.com":
        return None, None, "main"
    parts = p.path.strip("/").split("/")
    if len(parts) < 2:
        return None, None, "main"
    repo = f"{parts[0]}/{parts[1]}"
    branch, path = "main", ""
    if "tree" in parts:
        ti = parts.index("tree")
        if ti + 1 < len(parts):
            branch = parts[ti + 1]
        if ti + 2 < len(parts):
            path = "/".join(parts[ti + 2:])
    return repo, path, branch


REMOTE_LOG_URL = os.environ.get("REMOTE_LOG_URL", "").strip() or None
CHANNEL_LOG_URL = os.environ.get("CHANNEL_LOG_URL", "").strip() or None
BAN_USERS_URL = os.environ.get("BAN_USERS_URL", "https://github.com/Afrsto/bot-users/blob/main/ban-users.txt").strip() or None
BAN_SERVERS_URL = os.environ.get("BAN_SERVERS_URL", "https://github.com/Afrsto/bot-users/blob/main/ban-servers.txt").strip() or None
ADMIN_USERS_URL = os.environ.get("ADMIN_USERS_URL", "https://github.com/Afrsto/bot-users/blob/main/admin-users.txt").strip() or None
COOKIES_REPO_URL = os.environ.get("COOKIES_REPO_URL", "").strip() or None
BACKUP_REPO_URL = os.environ.get("BACKUP_REPO_URL", "").strip() or None

GITHUB_REPO, GITHUB_FILE_PATH = _parse_github_blob_url(REMOTE_LOG_URL)
CHANNEL_LOG_GITHUB_REPO, CHANNEL_LOG_GITHUB_PATH = _parse_github_blob_url(CHANNEL_LOG_URL)
BAN_USERS_GITHUB_REPO, BAN_USERS_GITHUB_PATH = _parse_github_blob_url(BAN_USERS_URL)
BAN_SERVERS_GITHUB_REPO, BAN_SERVERS_GITHUB_PATH = _parse_github_blob_url(BAN_SERVERS_URL)
ADMIN_USERS_GITHUB_REPO, ADMIN_USERS_GITHUB_PATH = _parse_github_blob_url(ADMIN_USERS_URL)
COOKIES_GITHUB_REPO, COOKIES_GITHUB_PATH, COOKIES_GITHUB_BRANCH = _parse_github_tree_url(COOKIES_REPO_URL)
BACKUP_GITHUB_REPO, BACKUP_GITHUB_PATH, BACKUP_GITHUB_BRANCH = _parse_github_tree_url(BACKUP_REPO_URL)


def _gh_client():
    if not GITHUB_TOKEN:
        return None
    return Github(GITHUB_TOKEN)


def _get_repo(repo_name: Optional[str]):
    if not repo_name:
        return None
    gh = _gh_client()
    if not gh:
        return None
    try:
        return gh.get_repo(repo_name)
    except GithubException as exc:
        log.error(f"Cannot access GitHub repo {repo_name}: {exc}")
        return None


def _read_github_file(repo_name: Optional[str], file_path: Optional[str]) -> Tuple[str, Optional[str]]:
    if not repo_name or not file_path:
        return "", None
    repo = _get_repo(repo_name)
    if not repo:
        return "", None
    try:
        contents = repo.get_contents(file_path)
        return b64decode(contents.content).decode("utf-8"), contents.sha
    except GithubException as exc:
        if exc.status == 404:
            return "", None
        log.error(f"Failed to read {repo_name}/{file_path}: {exc}")
        return "", None


def _write_github_file(repo_name: Optional[str], file_path: Optional[str], content: str,
                       commit_msg: str, max_retries: int = 3) -> bool:
    if not repo_name or not file_path:
        return False
    repo = _get_repo(repo_name)
    if not repo:
        return False
    for attempt in range(1, max_retries + 1):
        try:
            try:
                contents = repo.get_contents(file_path)
                current_sha = contents.sha
            except GithubException as exc:
                if exc.status == 404:
                    current_sha = None
                else:
                    raise
            if current_sha:
                repo.update_file(file_path, commit_msg, content, current_sha, branch="main")
            else:
                repo.create_file(file_path, commit_msg, content, branch="main")
            log.info(f"GitHub write OK ({repo_name}/{file_path}) attempt {attempt}")
            return True
        except GithubException as exc:
            status = exc.status
            msg = exc.data.get("message", "") if isinstance(exc.data, dict) else str(exc.data)
            if status in (409, 500, 502, 503) and attempt < max_retries:
                wait = 1 if status == 409 else 2
                log.warning(f"GitHub write {status} – retry {attempt}/{max_retries} in {wait}s")
                time.sleep(wait)
                continue
            log.error(f"GitHub write failed after {attempt} attempt(s): {status} – {msg}")
            return False
        except Exception as exc:
            log.error(f"Unexpected GitHub write error (attempt {attempt}): {exc}")
            if attempt < max_retries:
                time.sleep(1)
                continue
            return False
    return False


BOT_OWNER_ID = 994817247061225633
BOT_COADMIN_ID = 1138625081942233273
_PRIVILEGED_IDS = frozenset({BOT_OWNER_ID, BOT_COADMIN_ID})
_admin_registry: Dict[int, Dict] = {}
_banned_user_ids: set = set()
_ban_attempt_counts: Dict[int, int] = {}
_banned_guild_ids: set = set()
_cookie_check_attempts: Dict[int, List[float]] = {}


def load_admins_from_github() -> Dict[int, Dict]:
    raw, _ = _read_github_file(ADMIN_USERS_GITHUB_REPO, ADMIN_USERS_GITHUB_PATH)
    registry: Dict[int, Dict] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = {kv.split("=", 1)[0].strip(): kv.split("=", 1)[1].strip()
                     for kv in line.split("|") if "=" in kv}
            uid = int(parts.get("user_id", "0"))
            if uid:
                registry[uid] = {
                    "username": parts.get("username", str(uid)),
                    "added_by": parts.get("added_by", "system"),
                    "added_at": parts.get("added_at", "unknown"),
                }
        except Exception:
            continue
    log.info(f"Loaded {len(registry)} admin(s) from GitHub")
    return registry


def _serialize_admins(registry: Dict[int, Dict]) -> str:
    lines = ["# Admin users – managed automatically by the bot", ""]
    for uid, info in registry.items():
        lines.append(
            f"user_id={uid} | username={info['username']} "
            f"| added_by={info['added_by']} | added_at={info['added_at']}"
        )
    return "\n".join(lines) + "\n"


def save_admins_to_github(registry: Dict[int, Dict]) -> bool:
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M")
    return _write_github_file(
        ADMIN_USERS_GITHUB_REPO, ADMIN_USERS_GITHUB_PATH,
        _serialize_admins(registry),
        f"👮 Update admin list [{now_str} EGY]",
    )


def load_banned_users_from_github() -> set:
    if not BAN_USERS_GITHUB_REPO or not BAN_USERS_GITHUB_PATH:
        return set()
    raw, _ = _read_github_file(BAN_USERS_GITHUB_REPO, BAN_USERS_GITHUB_PATH)
    banned = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            banned.add(int(line.split("|")[0].strip()))
        except (ValueError, IndexError):
            continue
    log.info(f"Loaded {len(banned)} banned user(s)")
    return banned


def _write_ban_list(lines: List[str]) -> bool:
    content = "\n".join(lines) + ("\n" if lines else "")
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M")
    return _write_github_file(
        BAN_USERS_GITHUB_REPO, BAN_USERS_GITHUB_PATH,
        content, f"🚫 Update ban list [{now_str} EGY]",
    )


def add_ban_to_github(user_id: int, username: str) -> bool:
    if not BAN_USERS_GITHUB_REPO or not BAN_USERS_GITHUB_PATH:
        return False
    raw, _ = _read_github_file(BAN_USERS_GITHUB_REPO, BAN_USERS_GITHUB_PATH)
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("#"):
            continue
        try:
            if int(ln.split("|")[0].strip()) == user_id:
                return True
        except (ValueError, IndexError):
            continue
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"{user_id} | username={username} | banned_at={now_str} | attempts=0")
    return _write_ban_list(lines)


def remove_ban_from_github(user_id: int) -> bool:
    if not BAN_USERS_GITHUB_REPO or not BAN_USERS_GITHUB_PATH:
        return False
    raw, _ = _read_github_file(BAN_USERS_GITHUB_REPO, BAN_USERS_GITHUB_PATH)
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    new_lines, removed = [], False
    for ln in lines:
        if ln.startswith("#"):
            new_lines.append(ln)
            continue
        try:
            if int(ln.split("|")[0].strip()) == user_id:
                removed = True
                continue
        except (ValueError, IndexError):
            pass
        new_lines.append(ln)
    return True if not removed else _write_ban_list(new_lines)


def update_ban_attempts_on_github(user_id: int, attempts: int) -> None:
    if not BAN_USERS_GITHUB_REPO or not BAN_USERS_GITHUB_PATH:
        return
    raw, _ = _read_github_file(BAN_USERS_GITHUB_REPO, BAN_USERS_GITHUB_PATH)
    new_lines, updated = [], False
    for ln in raw.splitlines():
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(ln)
            continue
        try:
            if int(stripped.split("|")[0].strip()) == user_id:
                parts = [p.strip() for p in stripped.split("|")]
                new_parts = [f"attempts={attempts}" if p.startswith("attempts=") else p for p in parts]
                new_lines.append(" | ".join(new_parts))
                updated = True
                continue
        except (ValueError, IndexError):
            pass
        new_lines.append(ln)
    if updated:
        _write_ban_list(new_lines)


def load_banned_servers_from_github() -> set:
    if not BAN_SERVERS_GITHUB_REPO or not BAN_SERVERS_GITHUB_PATH:
        return set()
    raw, _ = _read_github_file(BAN_SERVERS_GITHUB_REPO, BAN_SERVERS_GITHUB_PATH)
    banned = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            banned.add(int(line.split("|")[0].strip()))
        except (ValueError, IndexError):
            continue
    log.info(f"Loaded {len(banned)} banned server(s)")
    return banned


def _write_server_ban_list(lines: List[str]) -> bool:
    content = "\n".join(lines) + ("\n" if lines else "")
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M")
    return _write_github_file(
        BAN_SERVERS_GITHUB_REPO, BAN_SERVERS_GITHUB_PATH,
        content, f"🚫 Update server ban list [{now_str} EGY]",
    )


def add_server_ban_to_github(guild_id: int, guild_name: str, reason: str) -> bool:
    if not BAN_SERVERS_GITHUB_REPO or not BAN_SERVERS_GITHUB_PATH:
        return False
    raw, _ = _read_github_file(BAN_SERVERS_GITHUB_REPO, BAN_SERVERS_GITHUB_PATH)
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("#"):
            continue
        try:
            if int(ln.split("|")[0].strip()) == guild_id:
                return True
        except (ValueError, IndexError):
            continue
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    lines.append(
        f"{guild_id} | guild_name={guild_name} | reason={reason.replace('|', '-')} | banned_at={now_str}"
    )
    return _write_server_ban_list(lines)


def remove_server_ban_from_github(guild_id: int) -> bool:
    if not BAN_SERVERS_GITHUB_REPO or not BAN_SERVERS_GITHUB_PATH:
        return False
    raw, _ = _read_github_file(BAN_SERVERS_GITHUB_REPO, BAN_SERVERS_GITHUB_PATH)
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    new_lines, removed = [], False
    for ln in lines:
        if ln.startswith("#"):
            new_lines.append(ln)
            continue
        try:
            if int(ln.split("|")[0].strip()) == guild_id:
                removed = True
                continue
        except (ValueError, IndexError):
            pass
        new_lines.append(ln)
    return True if not removed else _write_server_ban_list(new_lines)


def is_admin(user_id: int) -> bool:
    return user_id in _PRIVILEGED_IDS or user_id in _admin_registry


def is_owner(user_id: int) -> bool:
    return user_id == BOT_OWNER_ID


def is_user_banned(user_id: int) -> bool:
    return user_id in _banned_user_ids


def is_server_banned(guild_id: int) -> bool:
    return guild_id in _banned_guild_ids


def record_ban_attempt(user_id: int) -> int:
    _ban_attempt_counts[user_id] = _ban_attempt_counts.get(user_id, 0) + 1
    count = _ban_attempt_counts[user_id]
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, update_ban_attempts_on_github, user_id, count)
    except RuntimeError:
        pass
    return count


LOCALE_TO_COUNTRY: Dict[str, str] = {
    "ar": "Arab Region", "ar-AE": "UAE", "ar-BH": "Bahrain",
    "ar-DZ": "Algeria", "ar-EG": "Egypt", "ar-IQ": "Iraq",
    "ar-JO": "Jordan", "ar-KW": "Kuwait", "ar-LB": "Lebanon",
    "ar-LY": "Libya", "ar-MA": "Morocco", "ar-OM": "Oman",
    "ar-QA": "Qatar", "ar-SA": "Saudi Arabia", "ar-SD": "Sudan",
    "ar-SY": "Syria", "ar-TN": "Tunisia", "ar-YE": "Yemen",
    "en": "Unknown", "en-US": "USA", "en-GB": "UK",
    "en-AU": "Australia", "en-CA": "Canada", "en-IN": "India",
    "en-NZ": "New Zealand", "en-ZA": "South Africa",
    "fr": "France", "fr-BE": "Belgium", "fr-CA": "Canada",
    "fr-CH": "Switzerland", "fr-FR": "France",
    "de": "Germany", "de-AT": "Austria", "de-CH": "Switzerland", "de-DE": "Germany",
    "es": "Spain", "es-ES": "Spain", "es-MX": "Mexico", "es-AR": "Argentina",
    "tr": "Turkey", "tr-TR": "Turkey",
    "ru": "Russia", "ru-RU": "Russia",
    "zh": "China", "zh-CN": "China", "zh-TW": "Taiwan",
    "ja": "Japan", "ja-JP": "Japan",
    "ko": "South Korea", "ko-KR": "South Korea",
    "pt": "Portugal", "pt-BR": "Brazil", "pt-PT": "Portugal",
    "it": "Italy", "it-IT": "Italy",
    "nl": "Netherlands", "nl-NL": "Netherlands", "nl-BE": "Belgium",
    "pl": "Poland", "pl-PL": "Poland",
    "sv": "Sweden", "sv-SE": "Sweden",
    "no": "Norway", "nb": "Norway", "nb-NO": "Norway",
    "da": "Denmark", "da-DK": "Denmark",
    "fi": "Finland", "fi-FI": "Finland",
    "cs": "Czech Republic", "cs-CZ": "Czech Republic",
    "ro": "Romania", "ro-RO": "Romania",
    "hu": "Hungary", "hu-HU": "Hungary",
    "el": "Greece", "el-GR": "Greece",
    "he": "Israel", "he-IL": "Israel",
    "fa": "Iran", "fa-IR": "Iran",
    "hi": "India", "hi-IN": "India",
    "id": "Indonesia", "id-ID": "Indonesia",
    "ms": "Malaysia", "ms-MY": "Malaysia",
    "th": "Thailand", "th-TH": "Thailand",
    "vi": "Vietnam", "vi-VN": "Vietnam",
    "uk": "Ukraine", "uk-UA": "Ukraine",
}
LOCALE_TO_TZ: Dict[str, str] = {
    "ar-AE": "Asia/Dubai", "ar-BH": "Asia/Bahrain",
    "ar-DZ": "Africa/Algiers", "ar-EG": "Africa/Cairo",
    "ar-IQ": "Asia/Baghdad", "ar-JO": "Asia/Amman",
    "ar-KW": "Asia/Kuwait", "ar-LB": "Asia/Beirut",
    "ar-LY": "Africa/Tripoli", "ar-MA": "Africa/Casablanca",
    "ar-OM": "Asia/Muscat", "ar-QA": "Asia/Qatar",
    "ar-SA": "Asia/Riyadh", "ar-SD": "Africa/Khartoum",
    "ar-SY": "Asia/Damascus", "ar-TN": "Africa/Tunis",
    "ar-YE": "Asia/Aden",
    "en-US": "America/New_York", "en-GB": "Europe/London",
    "en-AU": "Australia/Sydney", "en-CA": "America/Toronto",
    "en-IN": "Asia/Kolkata", "en-NZ": "Pacific/Auckland",
    "en-ZA": "Africa/Johannesburg",
    "fr-FR": "Europe/Paris", "fr-BE": "Europe/Brussels",
    "fr-CH": "Europe/Zurich", "fr-CA": "America/Toronto",
    "de-DE": "Europe/Berlin", "de-AT": "Europe/Vienna",
    "de-CH": "Europe/Zurich",
    "es-ES": "Europe/Madrid", "es-MX": "America/Mexico_City",
    "es-AR": "America/Argentina/Buenos_Aires",
    "tr-TR": "Europe/Istanbul", "ru-RU": "Europe/Moscow",
    "zh-CN": "Asia/Shanghai", "zh-TW": "Asia/Taipei",
    "ja-JP": "Asia/Tokyo", "ko-KR": "Asia/Seoul",
    "pt-BR": "America/Sao_Paulo", "pt-PT": "Europe/Lisbon",
    "it-IT": "Europe/Rome",
    "nl-NL": "Europe/Amsterdam", "nl-BE": "Europe/Brussels",
    "pl-PL": "Europe/Warsaw", "sv-SE": "Europe/Stockholm",
    "nb-NO": "Europe/Oslo", "da-DK": "Europe/Copenhagen",
    "fi-FI": "Europe/Helsinki", "cs-CZ": "Europe/Prague",
    "ro-RO": "Europe/Bucharest", "hu-HU": "Europe/Budapest",
    "el-GR": "Europe/Athens", "he-IL": "Asia/Jerusalem",
    "fa-IR": "Asia/Tehran", "hi-IN": "Asia/Kolkata",
    "id-ID": "Asia/Jakarta", "ms-MY": "Asia/Kuala_Lumpur",
    "th-TH": "Asia/Bangkok", "vi-VN": "Asia/Ho_Chi_Minh",
    "uk-UA": "Europe/Kiev",
}


def get_locale_info(locale_str: str) -> Tuple[str, str, str]:
    locale = str(locale_str)
    country = LOCALE_TO_COUNTRY.get(locale) or LOCALE_TO_COUNTRY.get(locale.split("-")[0], "Unknown")
    tz_key = LOCALE_TO_TZ.get(locale) or LOCALE_TO_TZ.get(locale.split("-")[0])
    if tz_key:
        try:
            tz = ZoneInfo(tz_key)
            return country, datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S"), tz_key
        except Exception:
            return country, "N/A", "Unknown"
    return country, "N/A", "Unknown"


TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "en": {
        "lang_prompt": "🌐 **Please select your language:**",
        "lang_selected": "✅ Language selected: **English**",
        "confirm_prompt": "🍣 **Please choose your Crunchyroll plan**\n",
        "device_prompt": "📱 **Choose your device: PC, Phone, or TV**",
        "pc_label": "PC",
        "phone_label": "Phone",
        "tv_label": "TV",
        "progress": "⏳ **Generating your Crunchyroll cookie… please wait.**",
        "retry_status": "⏳ **Generating your Crunchyroll cookie… please wait.**",
        "wait_stock_check": "⏳ Stock check is running… please wait. Your cookie will generate when it finishes.",
        "no_cookies_folder": "❌ Cookies folder not found. Please contact the administrator.",
        "no_cookie_files": "❌ No accounts available right now. Please try again later.",
        "timeout": "⌛ Validation took too long. Please try again later.",
        "unexpected_error": "⚠️ An unexpected error occurred. Please try again.",
        "cookie_invalid": "❌ The selected session is invalid or expired. Please try again.",
        "success_title": "✅ 🍣 Crunchyroll Premium Cookie Ready!",
        "success_desc": "🍪 Your cookie is below. Copy it and import it into your browser.",
        "footer": "⚠️ This cookie is for personal use only – do not share it.",
        "fan_label": "Fan 🎈",
        "mega_fan_label": "Mega Fan 💎",
        "ultimate_fan_label": "Ultimate Fan 🚀",
        "cancelled": "🚫 Process cancelled.",
        "not_for_you": "🚫 You cannot interact with this menu.",
        "timeout_msg": "⏰ Request timed out due to inactivity.",
        "wrong_channel_no_config": "⚠️ No channel configured. Admins must run `/channel` first.",
        "wrong_channel_with_config": "❌ This command can only be used in {channel}.",
        "wrong_guild": "❌ This bot is not available in this server.",
        "not_admin": "❌ You do not have permission to use this command.",
        "cooldown": "⏳ You already generated a cookie recently.\n\n⌛ Please wait **{hours}h {minutes}m** before creating another one.",
        "retry_prompt": "❌ The attempt failed. Please try again.\n\n🔄 **Try Again**",
        "retry_button": "🔄 Try Again",
        "account_inactive": "❌ This account is not currently active or cannot generate a valid cookie. It may be unsubscribed or expired.",
        "validation_failed": "❌ Could not validate the account. Please try again later.",
        "failure": "❌ Failure",
        "cookie_editor_instruction": (
            "🍪 **Copy the cookie header above** and use it to authenticate.\n"
            "You can add these cookies manually using a browser extension like **Cookie-Editor**.\n"
            "🔗 **Download it from:** <https://cookie-editor.com/>"
        ),
        "tutorial_title": "🎥 Tutorial Video",
        "tutorial_desc": "Watch this short video to see exactly how to import your cookie:",
        "tv_instruction": (
            "📺 **TV Activation Instructions**\n\n"
            "1️⃣  Open **https://www.crunchyroll.com/activate** on your browser.\n"
            "2️⃣  Enter the activation code shown on your TV screen.\n"
            "3️⃣  The cookie will be automatically linked to your TV session.\n\n"
            "💡 If you don't see a code, make sure your TV app is up to date."
        ),
    },
    "ar": {
        "lang_prompt": "🌐 **Please select your language:**\n🌐 **الرجاء اختيار اللغة:**",
        "lang_selected": "\u200f✅ تم اختيار اللغة: **العربية**",
        "confirm_prompt": "\u200f🍣 **يرجى اختيار باقة كرانشيرول**\n",
        "device_prompt": "\u200f📱 **اختر جهازك: الكمبيوتر، الهاتف، أو التلفاز**",
        "pc_label": "PC",
        "phone_label": "Phone",
        "tv_label": "TV",
        "progress": "\u200f⏳ **جاري إنشاء الكوكي الخاص بك… يرجى الانتظار.**",
        "retry_status": "\u200f⏳ **جاري إنشاء الكوكي الخاص بك… يرجى الانتظار.**",
        "wait_stock_check": "\u200f⏳ جاري فحص المخزون… يرجى الانتظار. سيتم إنشاء الكوكي بعد انتهائه.",
        "no_cookies_folder": "\u200f❌ مجلد الكوكيز غير موجود. يرجى الاتصال بالمسؤول.",
        "no_cookie_files": "\u200f❌ لا توجد حسابات متاحة حالياً. حاول لاحقاً.",
        "timeout": "\u200f⌛ استغرق التحقق وقتاً طويلاً. يرجى المحاولة مرة أخرى.",
        "unexpected_error": "\u200f⚠️ حدث خطأ غير متوقع.",
        "cookie_invalid": "\u200f❌ الحساب المختار غير صالح أو منتهي الصلاحية. حاول مجدداً.",
        "success_title": "\u200f✅ 🍣 كوكي كرانشيرول بريميوم جاهز!",
        "success_desc": "\u200f🍪 الكوكي موجود أدناه. انسخه واستورده في متصفحك.",
        "footer": "\u200f⚠️ هذا الكوكي للاستخدام الشخصي فقط – يُمنع مشاركته.",
        "fan_label": "Fan 🎈",
        "mega_fan_label": "Mega Fan 💎",
        "ultimate_fan_label": "Ultimate Fan 🚀",
        "cancelled": "\u200f🚫 تم إلغاء العملية.",
        "not_for_you": "\u200f🚫 لا يمكنك التفاعل مع هذه القائمة.",
        "timeout_msg": "\u200f⏰ انتهت مهلة الطلب بسبب عدم التفاعل.",
        "wrong_channel_no_config": "\u200f⚠️ لم يتم إعداد القناة. يجب على المسؤول استخدام `/channel` أولاً.",
        "wrong_channel_with_config": "\u200f❌ لا يمكن استخدام هذا الأمر إلا في {channel}.",
        "wrong_guild": "\u200f❌ هذا البوت غير متاح في هذا السيرفر.",
        "not_admin": "\u200f❌ ليس لديك صلاحية استخدام هذا الأمر.",
        "cooldown": "\u200f⏳ لقد حصلت على كوكي مؤخراً.\n\n\u200f⌛ انتظر **{hours} ساعة و{minutes} دقيقة** قبل إنشاء كوكي جديد.",
        "retry_prompt": "❌ The attempt failed. Please try again.\n\n❌ فشلت المحاولة. يرجى المحاولة مرة أخرى.\n\n🔄 **Try Again | حاول مرة أخرى**",
        "retry_button": "🔄 Try Again | حاول مرة أخرى",
        "account_inactive": "❌ هذا الحساب غير نشط حاليًا أو لا يمكن إنشاء كوكي صالح. قد يكون غير مشترك أو منتهي الصلاحية.",
        "validation_failed": "❌ تعذر التحقق من الحساب. يرجى المحاولة مرة أخرى لاحقًا.",
        "failure": "❌ فشل",
        "cookie_editor_instruction": (
            "🍪 **انسخ رأس الكوكي أعلاه** واستخدمه للمصادقة.\n"
            "يمكنك إضافة هذه الكوكيز يدويًا باستخدام إضافة متصفح مثل **Cookie-Editor**.\n"
            "🔗 **حمّلها من:** <https://cookie-editor.com/>"
        ),
        "tutorial_title": "🎥 فيديو تعليمي",
        "tutorial_desc": "شاهد هذا الفيديو القصير لمعرفة كيفية استيراد الكوكي خطوة بخطوة:",
        "tv_instruction": (
            "\u200f📺 **تعليمات تفعيل التلفاز**\n\n"
            "1️⃣  افتح **https://www.crunchyroll.com/activate** في متصفحك.\n"
            "2️⃣  أدخل رمز التفعيل الظاهر على شاشة التلفاز.\n"
            "3️⃣  سيتم ربط الكوكي تلقائياً بجلسة التلفاز الخاصة بك.\n\n"
            "💡 إذا لم يظهر رمز، تأكد من تحديث تطبيق التلفاز."
        ),
    },
}


def get_user_lang(interaction: discord.Interaction) -> str:
    try:
        return "ar" if str(interaction.locale).startswith("ar") else "en"
    except Exception:
        return "en"


def parse_netscape_to_cookie_header(content: str) -> str:
    pairs = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            name, value = parts[5].strip(), parts[6].strip()
            if name and value:
                pairs.append(f"{name}={value}")
        else:
            for token in line.split(";"):
                token = token.strip()
                if "=" in token:
                    k, v = token.split("=", 1)
                    if k and v:
                        pairs.append(f"{k.strip()}={v.strip()}")
    return "; ".join(pairs)


async def _send_tutorial_video_message(
    interaction: discord.Interaction,
    language: str,
) -> Optional[discord.Message]:
    t = TRANSLATIONS.get(language) or TRANSLATIONS["en"]
    try:
        embed = discord.Embed(
            title=t["tutorial_title"],
            description=(
                f"{t['tutorial_desc']}\n\n"
                f"▶️ **Watch here:** [Click to play the video]({TUTORIAL_VIDEO_URL})\n\n"
                f"{TUTORIAL_VIDEO_URL}"
            ),
            color=CRUNCHYROLL_ORANGE,
            timestamp=datetime.now(EGYPT_TZ),
        )
        embed.set_footer(text="X2 Salah Utility • Crunchyroll Bot 🍣")
        msg = await interaction.followup.send(embed=embed, ephemeral=True)
        log.info("Sent tutorial video message.")
        return msg
    except Exception as e:
        log.error(f"Failed to send tutorial video message: {e}")
        return None


class ChannelLogConfig:
    def __init__(self, file_path: Path = GUILD_CONFIG_FILE):
        self.file_path = file_path
        self.data: Dict[str, Any] = {}
        self._load()

    def _load(self):
        if self.file_path.exists():
            try:
                with open(self.file_path, "r") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}
        else:
            self.data = {"guilds": {}, "last_fetch": None}

    def _save(self):
        try:
            with open(self.file_path, "w") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            log.error(f"Failed to save guild_config: {e}")

    def get_guild_config(self, guild_id: int) -> Dict[str, Any]:
        return self.data["guilds"].get(str(guild_id), {})

    def set_guild_config(self, guild_id: int, config: Dict[str, Any]):
        self.data["guilds"][str(guild_id)] = config
        self._save()

    def get_channel_id(self, guild_id: int) -> Optional[int]:
        return self.get_guild_config(guild_id).get("channel_id")

    def set_channel(self, guild_id: int, channel_id: int, sync_done: bool = False, last_ts: Optional[str] = None):
        self.set_guild_config(guild_id, {
            "channel_id": channel_id,
            "sync_done": sync_done,
            "last_timestamp": last_ts,
        })

    def get_last_fetch(self) -> Optional[str]:
        return self.data.get("last_fetch")

    def set_last_fetch(self, timestamp: str):
        self.data["last_fetch"] = timestamp
        self._save()


@dataclass
class LogEntry:
    timestamp: datetime
    user: str
    display_name: str
    user_id: int
    server: str
    server_id: int
    channel: str
    language: str
    device: str
    status: str
    result: str
    plan: str
    days_left: str
    files_used: str

    @classmethod
    def from_line(cls, line: str) -> Optional["LogEntry"]:
        pattern = re.compile(
            r"^\[(?P<timestamp>[^\]]+)\]\s+"
            r"👤\s+User:\s+(?P<user>[^\s(]+)\s+\(Display:\s+(?P<display>[^)]+)\)\s+\|\s+"
            r"🆔\s+ID:\s+(?P<user_id>\d+)\s+\|\s+"
            r"🎁\s+Plan:\s+(?P<plan>[^|]+)\|\s+"
            r"⏸️\s+Days\s+Left:\s+(?P<days_left>[^|]+)\|\s+"
            r"💻\s+Device:\s+(?P<device>[^|]+)\|\s+"
            r"🏠\s+Server:\s+(?P<server>[^(]+)\(ID:\s+(?P<server_id>\d+)\)\s+\|\s+"
            r"💬\s+Channel:\s+#(?P<channel>[^|]+)\|\s+"
            r"🔎\s+Result:\s+(?P<result>[^|]+)\|\s+"
            r"🌐\s+Language:\s+(?P<language>[^|]+)\|\s+"
            r"📄\s+Files\s+Used:\s+(?P<files_used>[^|]+)\|\s+"
            r"📊\s+Status:\s+(?P<status>.+)$"
        )
        match = pattern.match(line)
        if not match:
            return None
        data = match.groupdict()
        try:
            ts = datetime.strptime(data["timestamp"], "%Y-%m-%d %H:%M:%S EGY")
        except ValueError:
            return None
        return cls(
            timestamp=ts,
            user=data["user"],
            display_name=data["display"],
            user_id=int(data["user_id"]),
            server=data["server"].strip(),
            server_id=int(data["server_id"]),
            channel=data["channel"].strip(),
            language=data["language"].strip(),
            device=data["device"].strip(),
            status=data["status"].strip(),
            result=data["result"].strip(),
            plan=data["plan"].strip(),
            days_left=data["days_left"].strip(),
            files_used=data["files_used"].strip(),
        )


class LogFetcher:
    def __init__(self, url: str = CRUNCHYROLL_LOG_URL):
        self.url = url
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    async def fetch_log(self, retries: int = 3) -> List[LogEntry]:
        if self.session is None:
            self.session = aiohttp.ClientSession()
        for attempt in range(retries):
            try:
                async with self.session.get(self.url, timeout=15) as resp:
                    if resp.status != 200:
                        log.warning(f"Remote log fetch status {resp.status}")
                        continue
                    text = await resp.text()
                    entries = [LogEntry.from_line(ln.strip())
                               for ln in text.splitlines() if ln.strip()]
                    entries = [e for e in entries if e]
                    log.info(f"Fetched {len(entries)} entries from remote log")
                    return entries
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning(f"Attempt {attempt + 1}/{retries} failed: {e}")
                await asyncio.sleep(2 ** attempt)
        log.error("Failed to fetch remote log after retries")
        return []


class CrunchyrollMonitor:
    def __init__(self, bot: commands.Bot, config: ChannelLogConfig):
        self.bot = bot
        self.config = config
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self._embed_color = discord.Color.from_rgb(247, 120, 23)

    def start(self):
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._run())

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _run(self):
        log.info("CrunchyrollMonitor started – checking every 60s.")
        while self.running:
            try:
                await self._check_and_post()
            except Exception as e:
                log.error(f"Monitor error: {e}", exc_info=True)
            await asyncio.sleep(60)

    async def _check_and_post(self):
        async with LogFetcher() as fetcher:
            all_entries = await fetcher.fetch_log()
        if not all_entries:
            return
        all_entries.sort(key=lambda e: e.timestamp)

        last_fetch_str = self.config.get_last_fetch()
        last_fetch = None
        if last_fetch_str:
            try:
                last_fetch = datetime.fromisoformat(last_fetch_str)
            except ValueError:
                pass

        new_entries = [e for e in all_entries if not last_fetch or e.timestamp > last_fetch]
        if not new_entries:
            return

        for guild_id_str, cfg in self.config.data.get("guilds", {}).items():
            guild_id = int(guild_id_str)
            channel_id = cfg.get("channel_id")
            if not channel_id:
                continue
            channel = self.bot.get_channel(channel_id)
            if not channel:
                log.warning(f"Channel {channel_id} not found for guild {guild_id}, removing config.")
                self.config.data["guilds"].pop(guild_id_str, None)
                self.config._save()
                continue

            guild_last_str = cfg.get("last_timestamp")
            guild_last = None
            if guild_last_str:
                try:
                    guild_last = datetime.fromisoformat(guild_last_str)
                except ValueError:
                    pass

            guild_new = [e for e in new_entries if not guild_last or e.timestamp > guild_last]
            if not guild_new:
                continue

            for entry in guild_new:
                try:
                    await channel.send(embed=self._build_embed(entry))
                    log.info(f"Posted log entry for {entry.user} in guild {guild_id}")
                except discord.Forbidden:
                    log.warning(f"No permission to post in channel {channel_id}")
                    break
                except Exception as e:
                    log.error(f"Failed to post embed: {e}")

            latest = max(e.timestamp for e in guild_new)
            self.config.set_guild_config(guild_id, {
                "channel_id": channel_id,
                "sync_done": True,
                "last_timestamp": latest.isoformat(),
            })

        latest_all = max(e.timestamp for e in all_entries)
        self.config.set_last_fetch(latest_all.isoformat())

    def _build_embed(self, entry: LogEntry) -> discord.Embed:
        embed = discord.Embed(
            title="🍣 User Activity Log",
            color=self._embed_color,
            timestamp=entry.timestamp,
        )
        embed.set_thumbnail(url=CRUNCHYROLL_LOGO)
        fields = [
            ("👤 User", f"{entry.user} ({entry.display_name})", True),
            ("🆔 ID", str(entry.user_id), True),
            ("📌 Date of Use", entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"), True),
            ("🎁 Plan", entry.plan, True),
            ("⏸️ Days Left", entry.days_left, True),
            ("💻 Device", entry.device, True),
            ("🏠 Server", entry.server, True),
            ("💬 Channel", f"#{entry.channel}", True),
            ("🔎 Result", entry.result, True),
            ("🌐 Language", entry.language, True),
            ("📄 Files Used", entry.files_used, True),
            ("📊 Status", entry.status, True),
        ]
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
        embed.set_footer(text="X2 Salah Utility • Crunchyroll Bot 🍣")
        return embed


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

channel_log_config = ChannelLogConfig()
monitor = CrunchyrollMonitor(bot, channel_log_config)


class Config:
    def __init__(self) -> None:
        self.guilds: Dict[str, int] = {}
        self.allowed_channel_id: Optional[int] = None
        self._db_pool = None

    async def init_db(self) -> None:
        if DATABASE_URL and HAS_ASYNCPG:
            try:
                dsn = DATABASE_URL.replace("postgres://", "postgresql://", 1)
                self._db_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
                async with self._db_pool.acquire() as conn:
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS guild_config (
                            guild_id   TEXT  PRIMARY KEY,
                            channel_id BIGINT NOT NULL
                        )
                    """)
                    rows = await conn.fetch("SELECT guild_id, channel_id FROM guild_config")
                    for row in rows:
                        self.guilds[row["guild_id"]] = int(row["channel_id"])
                log.info(f"PostgreSQL loaded – {len(self.guilds)} guild(s)")
            except Exception as exc:
                log.error(f"PostgreSQL init failed: {exc} – using file fallback")
                self._db_pool = None
                self._load_from_file()
        else:
            log.warning("PostgreSQL unavailable – using file/env/GitHub fallback")
            self._load_from_file()

        loop = asyncio.get_event_loop()
        github_links = await loop.run_in_executor(None, load_channel_links_from_github)
        for gid, cid in github_links.items():
            if gid not in self.guilds:
                self.guilds[gid] = cid
                log.info(f"Restored from GitHub logs: guild {gid} → channel {cid}")

    async def _save_to_db(self, guild_id: str, channel_id: int) -> None:
        if not self._db_pool:
            return
        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO guild_config (guild_id, channel_id) VALUES ($1, $2)
                    ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id
                """, guild_id, channel_id)
        except Exception as exc:
            log.error(f"PostgreSQL save failed: {exc}")

    def _load_from_file(self) -> None:
        if not CONFIG_FILE.exists():
            return
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            if isinstance(data.get("guilds"), dict):
                self.guilds = {str(k): int(v) for k, v in data["guilds"].items()}
            elif data.get("allowed_channel_id"):
                self.allowed_channel_id = int(data["allowed_channel_id"])
        except Exception as exc:
            log.error(f"Failed to read config: {exc}")

    def _save_to_file(self) -> None:
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump({"guilds": self.guilds}, f, indent=2)
        except Exception as exc:
            log.warning(f"Could not save config: {exc}")

    def get_channel_for_guild(self, guild_id: int) -> Optional[int]:
        guild_key = str(guild_id)
        if guild_key in self.guilds:
            return self.guilds[guild_key]
        if self.allowed_channel_id:
            return self.allowed_channel_id
        return DEFAULT_CHANNEL_ID

    async def set_allowed_channel(
        self, guild_id: int, channel_id: int,
        guild_name: str = "Unknown", channel_name: str = "Unknown",
    ) -> None:
        guild_key = str(guild_id)
        self.guilds[guild_key] = channel_id
        self.allowed_channel_id = channel_id
        await self._save_to_db(guild_key, channel_id)
        self._save_to_file()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, save_channel_link_to_github,
            guild_id, guild_name, channel_id, channel_name,
        )
        log.info(f"Channel set: guild {guild_id} → channel {channel_id}")


def save_channel_link_to_github(guild_id: int, guild_name: str, channel_id: int, channel_name: str) -> None:
    if not CHANNEL_LOG_GITHUB_REPO or not CHANNEL_LOG_GITHUB_PATH:
        return
    raw, _ = _read_github_file(CHANNEL_LOG_GITHUB_REPO, CHANNEL_LOG_GITHUB_PATH)
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    new_entry = (
        f"CHANNEL_LINK | guild_id={guild_id} | guild_name={guild_name} | "
        f"channel_id={channel_id} | channel_name={channel_name} | set_at={now_str}\n"
    )
    lines = [ln for ln in raw.splitlines(keepends=True)
             if not (ln.startswith("CHANNEL_LINK") and f"guild_id={guild_id}" in ln)]
    lines.append(new_entry)
    _write_github_file(
        CHANNEL_LOG_GITHUB_REPO, CHANNEL_LOG_GITHUB_PATH, "".join(lines),
        f"📌 Update channel link: guild {guild_id} → channel {channel_id}",
    )


def load_channel_links_from_github() -> Dict[str, int]:
    if not CHANNEL_LOG_GITHUB_REPO or not CHANNEL_LOG_GITHUB_PATH:
        return {}
    raw, _ = _read_github_file(CHANNEL_LOG_GITHUB_REPO, CHANNEL_LOG_GITHUB_PATH)
    result: Dict[str, int] = {}
    for line in raw.splitlines():
        if not line.startswith("CHANNEL_LINK"):
            continue
        try:
            parts = {kv.split("=", 1)[0].strip(): kv.split("=", 1)[1].strip()
                     for kv in line.split("|")[1:] if "=" in kv}
            result[parts["guild_id"]] = int(parts["channel_id"])
        except Exception:
            continue
    log.info(f"Loaded {len(result)} channel link(s) from GitHub logs")
    return result


config = Config()

PLAN_FOLDER_MAP = {
    "fan": "Fan",
    "mega_fan": "Mega Fan",
    "ultimate_fan": "Ultimate Fan",
}
PLAN_DISPLAY_MAP = {
    "fan": "Fan 🎈",
    "mega_fan": "Mega Fan 💎",
    "ultimate_fan": "Ultimate Fan 🚀",
}


def _count_txt_files_in_folder(plan_folder: str) -> int:
    if COOKIES_GITHUB_REPO and COOKIES_GITHUB_PATH is not None:
        base_path = (COOKIES_GITHUB_PATH.rstrip("/") + "/" + plan_folder) if COOKIES_GITHUB_PATH else plan_folder
        try:
            return len(_fetch_github_cookie_list_in_path(base_path))
        except Exception:
            pass
    local_dir = COOKIES_FOLDER / plan_folder
    if not local_dir.exists():
        return 0
    return len(list(local_dir.glob("*.txt")))


_used_cookie_files: List[Path] = []
_used_github_cookie_names: List[str] = []
_cookies_repo_cache = None
_cookie_rotation_lock = threading.Lock()


def pick_cookie_file(txt_files: List[Path]) -> Path:
    global _used_cookie_files
    with _cookie_rotation_lock:
        _used_cookie_files = [f for f in _used_cookie_files if f in txt_files]
        remaining = [f for f in txt_files if f not in _used_cookie_files]
        if not remaining:
            log.info("All cookie files used – resetting rotation")
            _used_cookie_files.clear()
            remaining = list(txt_files)
        chosen = random.choice(remaining)
        _used_cookie_files.append(chosen)
        return chosen


def pick_github_cookie_rotation(filenames: List[str]) -> str:
    global _used_github_cookie_names
    with _cookie_rotation_lock:
        _used_github_cookie_names = [f for f in _used_github_cookie_names if f in filenames]
        remaining = [f for f in filenames if f not in _used_github_cookie_names]
        if not remaining:
            log.info("All GitHub cookie files used – resetting rotation")
            _used_github_cookie_names.clear()
            remaining = list(filenames)
        chosen = random.choice(remaining)
        _used_github_cookie_names.append(chosen)
        return chosen


def _get_cookies_repo():
    global _cookies_repo_cache
    if _cookies_repo_cache is not None:
        return _cookies_repo_cache
    repo = _get_repo(COOKIES_GITHUB_REPO)
    if repo is not None:
        _cookies_repo_cache = repo
    return repo


def _fetch_github_cookie_list_in_path(folder_path: str) -> List[str]:
    repo = _get_cookies_repo()
    if not repo:
        return []
    try:
        contents = repo.get_contents(folder_path, ref=COOKIES_GITHUB_BRANCH)
        return [c.name for c in contents if c.type == "file" and c.name.endswith(".txt")]
    except GithubException as exc:
        log.error(f"Failed to list GitHub path {folder_path}: {exc}")
        return []


def _fetch_github_cookie_content_in_path(folder_path: str, filename: str) -> Optional[str]:
    repo = _get_cookies_repo()
    if not repo:
        return None
    try:
        file_path = f"{folder_path.rstrip('/')}/{filename}"
        content_obj = repo.get_contents(file_path, ref=COOKIES_GITHUB_BRANCH)
        return b64decode(content_obj.content).decode("utf-8")
    except GithubException as exc:
        log.error(f"Failed to download {folder_path}/{filename}: {exc}")
        return None


async def _pick_cookie_candidate(
    plan_folder: str, exclude_names: Set[str],
) -> Tuple[Optional[str], Optional[str]]:
    if COOKIES_GITHUB_REPO and COOKIES_GITHUB_PATH is not None:
        base_path = (COOKIES_GITHUB_PATH.rstrip("/") + "/" + plan_folder) if COOKIES_GITHUB_PATH else plan_folder
        github_names = await asyncio.to_thread(_fetch_github_cookie_list_in_path, base_path)
        available = [n for n in github_names if n not in exclude_names]
        if available:
            chosen_file_name = await asyncio.to_thread(pick_github_cookie_rotation, available)
            cookie_content = await asyncio.to_thread(
                _fetch_github_cookie_content_in_path, base_path, chosen_file_name
            )
            if cookie_content is not None:
                return chosen_file_name, cookie_content

    local_dir = COOKIES_FOLDER / plan_folder
    if not local_dir.exists():
        local_dir = COOKIES_FOLDER
    if not local_dir.exists():
        return None, None

    txt_files = [p for p in local_dir.glob("*.txt") if p.name not in exclude_names]
    if not txt_files:
        return None, None

    chosen_path = pick_cookie_file(txt_files)
    try:
        return chosen_path.name, chosen_path.read_text(encoding="utf-8")
    except Exception as exc:
        log.error(f"Failed to read local cookie: {exc}")
        return chosen_path.name, None


async def log_user_activity(
    interaction: discord.Interaction,
    condition: str,
    result: str,
    used_txt_files: Optional[List[str]] = None,
    language: Optional[str] = None,
    plan_key: Optional[str] = None,
    device: Optional[str] = None,
    plan: Optional[str] = None,
    days_left: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> None:
    if timestamp is None:
        timestamp = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")

    user = interaction.user
    guild = interaction.guild
    channel_name = getattr(interaction.channel, "name", "N/A")
    lang_label = {"ar": "Arabic 🇸🇦", "en": "English 🇬🇧"}.get(language or "", language or "N/A")
    device_label = {"pc": "PC", "phone": "Phone", "tv": "TV", "all": "All"}.get(device or "", "N/A")
    plan_display = plan or PLAN_DISPLAY_MAP.get(plan_key or "", "N/A")
    days_display = days_left or "N/A"
    files_used_display = ", ".join(used_txt_files) if used_txt_files else "N/A"

    line = (
        f"[{timestamp} EGY] "
        f"👤 User: {user} (Display: {user.display_name}) | "
        f"🆔 ID: {user.id} | "
        f"🎁 Plan: {plan_display} | "
        f"⏸️ Days Left: {days_display} | "
        f"💻 Device: {device_label} | "
        f"🏠 Server: {guild.name if guild else 'DM'} (ID: {guild.id if guild else 'N/A'}) | "
        f"💬 Channel: #{channel_name} | "
        f"🔎 Result: {result} | "
        f"🌐 Language: {lang_label} | "
        f"📄 Files Used: {files_used_display} | "
        f"📊 Status: {condition}\n"
    )

    try:
        with open(USER_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as exc:
        log.error(f"Failed to write local log: {exc}")

    await asyncio.to_thread(update_users_txt_on_github, line)


def update_users_txt_on_github(new_line: str) -> bool:
    if not GITHUB_REPO or not GITHUB_FILE_PATH:
        return False
    raw, _ = _read_github_file(GITHUB_REPO, GITHUB_FILE_PATH)
    lines = raw.splitlines(keepends=True)
    if len(lines) >= 500:
        lines = lines[-499:]
        log.info("users.txt trimmed to 500 lines")
    content = "".join(lines) + new_line
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M")
    return _write_github_file(
        GITHUB_REPO, GITHUB_FILE_PATH, content,
        f"📝 Log entry [{now_str} EGY]",
    )


def check_user_cooldown(user_id: int) -> Tuple[bool, float]:
    if is_admin(user_id):
        return False, 0.0
    if not GITHUB_REPO or not GITHUB_FILE_PATH:
        return False, 0.0
    raw, _ = _read_github_file(GITHUB_REPO, GITHUB_FILE_PATH)
    if not raw:
        return False, 0.0
    user_id_str = str(user_id)
    last_success_dt: Optional[datetime] = None
    for line in raw.splitlines():
        if f"🆔 ID: {user_id_str}" not in line:
            continue
        if "📊 Status: ✅ Success" not in line:
            continue
        try:
            ts_part = line.split("]")[0].lstrip("[").strip()
            ts_clean = " ".join(ts_part.split()[:2])
            entry_dt = datetime.strptime(ts_clean, "%Y-%m-%d %H:%M:%S").replace(tzinfo=EGYPT_TZ)
            if last_success_dt is None or entry_dt > last_success_dt:
                last_success_dt = entry_dt
        except Exception:
            continue
    if last_success_dt is None:
        return False, 0.0
    now_egypt = datetime.now(EGYPT_TZ)
    elapsed_hours = (now_egypt - last_success_dt).total_seconds() / 3600.0
    if elapsed_hours < COOLDOWN_HOURS:
        return True, COOLDOWN_HOURS - elapsed_hours
    return False, 0.0


_setup_message_ids: Dict[int, Dict[str, Optional[int]]] = {}


def _load_setup_tracker() -> None:
    global _setup_message_ids
    if SETUP_TRACKER_FILE.exists():
        try:
            with open(SETUP_TRACKER_FILE) as f:
                raw = json.load(f)
            _setup_message_ids = {int(k): v for k, v in raw.items()}
        except Exception as exc:
            log.warning(f"Could not load setup tracker: {exc}")


def _save_setup_tracker() -> None:
    try:
        with open(SETUP_TRACKER_FILE, "w") as f:
            json.dump({str(k): v for k, v in _setup_message_ids.items()}, f, indent=2)
    except Exception as exc:
        log.warning(f"Could not save setup tracker: {exc}")


CRUNCHYROLL_ORANGE = discord.Color.from_rgb(247, 120, 23)
FOOTER_TEXT = "X2 Salah Utility • Crunchyroll Bot 🍣"


async def _build_main_embed() -> discord.Embed:
    fan_c, mega_c, ult_c = await asyncio.gather(
        asyncio.to_thread(_count_txt_files_in_folder, "Fan"),
        asyncio.to_thread(_count_txt_files_in_folder, "Mega Fan"),
        asyncio.to_thread(_count_txt_files_in_folder, "Ultimate Fan"),
    )
    total = fan_c + mega_c + ult_c
    description = (
        f"🍪 **Alive cookies** — `{total}`\n"
        f"🎈 **Fan:** `{fan_c}`   "
        f"💎 **Mega Fan:** `{mega_c}`   "
        f"🚀 **Ultimate Fan:** `{ult_c}`\n"
        f"🏷️ **Version:** `{BOT_VERSION}`    •    🟢 **Status:** Online\n"
        f"\n"
        f"**Discord**\n"
        f"`{DISCORD_USER_URL}`\n"
        f"\n"
        f"**Discord Server**\n"
        f"`{DISCORD_SERVER_URL}`"
    )
    embed = discord.Embed(
        title="🍣 Crunchyroll Checker | X2 Salah Utility",
        description=description,
        color=CRUNCHYROLL_ORANGE,
        timestamp=datetime.now(EGYPT_TZ),
    )
    embed.set_image(url=CRUNCHYROLL_BANNER_GIF)
    embed.set_footer(text="X2 Salah Utility • Crunchyroll Bot 🍣")
    return embed


def _build_cookie_header_chunks(cookie_header: str, max_len: int = 1900) -> List[str]:
    if len(cookie_header) <= max_len:
        return [f"```text\n{cookie_header}\n```"]
    parts = [cookie_header[i:i + max_len] for i in range(0, len(cookie_header), max_len)]
    return [f"```text\n{part}\n```" for part in parts]


async def _log_cookie_check_to_channel(
    interaction: discord.Interaction,
    status: str,
    result: str,
    info: Optional[Dict[str, Any]] = None,
    used_files: Optional[List[str]] = None,
) -> None:
    guild = interaction.guild
    if not guild:
        return
    channel_id = channel_log_config.get_channel_id(guild.id)
    if not channel_id:
        return
    channel = interaction.client.get_channel(channel_id)
    if not channel:
        return

    member = interaction.user
    info = info or {}
    embed = discord.Embed(
        title="⚙️ Cookie Check Activity",
        color=CRUNCHYROLL_ORANGE,
        timestamp=datetime.now(EGYPT_TZ),
    )
    avatar_url = member.display_avatar.url if member.display_avatar else CRUNCHYROLL_LOGO
    embed.set_thumbnail(url=avatar_url)
    channel_name = interaction.channel.mention if interaction.channel else "N/A"

    fields = [
        ("👤 User", f"{member.mention} ({member.display_name})", True),
        ("🆔 ID", str(member.id), True),
        ("🏠 Server", guild.name, True),
        ("💬 Channel", channel_name, True),
        ("📊 Status", status, True),
        ("🔎 Result", result, True),
    ]
    if info:
        fields.extend([
            ("🎁 Plan", str(info.get("plan", "N/A")), True),
            ("⏸️ Days Left", str(info.get("days_left", "N/A")), True),
            ("🌍 Country", str(info.get("country", "N/A")), True),
        ])
    if used_files:
        fields.append(("📄 Files Used", ", ".join(used_files), True))

    for name, value, inline in fields:
        embed.add_field(name=name, value=value, inline=inline)
    embed.set_footer(text="X2 Salah Utility • Cookie Check Log")

    try:
        await channel.send(embed=embed)
        log.info(f"Sent cookie-check activity ({status}) for {member} to channel {channel.id}")
    except Exception as e:
        log.error(f"Failed to send cookie-check activity to log channel: {e}")


class CookieCheckModal(discord.ui.Modal, title="⚙️ Check Cookie → Info"):
    cookie_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Paste your Crunchyroll cookie here",
        style=discord.TextStyle.paragraph,
        placeholder="etp_rt=...; session_id=...; device_id=...; OptanonConsent=...",
        required=True,
        max_length=4000,
    )

    def __init__(self, original_interaction: discord.Interaction) -> None:
        super().__init__()
        self.original_interaction = original_interaction

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cookie_content = (self.cookie_input.value or "").strip()
        if not cookie_content:
            await interaction.response.send_message("❌ No cookie provided.", ephemeral=True)
            asyncio.create_task(_log_cookie_check_to_channel(
                self.original_interaction,
                status="⚠️ Empty Submission",
                result="User submitted the modal without a cookie",
            ))
            return

        if not is_admin(interaction.user.id):
            now = time.time()
            history = _cookie_check_attempts.get(interaction.user.id, [])
            history = [t for t in history if now - t < COOKIE_CHECK_WINDOW_SECONDS]
            if len(history) >= COOKIE_CHECK_LIMIT:
                oldest = min(history)
                remaining_seconds = COOKIE_CHECK_WINDOW_SECONDS - (now - oldest)
                remaining_hours = max(0.0, remaining_seconds / 3600.0)
                await interaction.response.send_message(
                    f"⏳ You've reached the limit of **{COOKIE_CHECK_LIMIT}** cookie checks per "
                    f"{COOKIE_CHECK_WINDOW_HOURS} hours.\n"
                    f"Please try again in about **{remaining_hours:.1f} h**.",
                    ephemeral=True,
                )
                asyncio.create_task(_log_cookie_check_to_channel(
                    self.original_interaction,
                    status="⏳ Rate Limited",
                    result=f"Hit limit ({COOKIE_CHECK_LIMIT}/{COOKIE_CHECK_WINDOW_HOURS}h)",
                ))
                return
            history.append(now)
            _cookie_check_attempts[interaction.user.id] = history

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            _link, info = await asyncio.wait_for(
                asyncio.to_thread(check_cookie_content, cookie_content),
                timeout=SCRIPT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send("⌛ Validation took too long. Please try again later.", ephemeral=True)
            asyncio.create_task(_log_cookie_check_to_channel(
                self.original_interaction, status="⌛ Timeout", result="Validation timed out",
            ))
            return
        except Exception as exc:
            log.error(f"Cookie check error: {exc}")
            await interaction.followup.send("⚠️ An unexpected error occurred. Please try again.", ephemeral=True)
            asyncio.create_task(_log_cookie_check_to_channel(
                self.original_interaction, status="⚠️ Error", result=f"Exception: {exc}",
            ))
            return

        if not info:
            msg = "❌ This cookie is **invalid**, **expired**, or missing the required fields."
            await interaction.followup.send(msg, ephemeral=True)
            asyncio.create_task(_log_cookie_check_to_channel(
                self.original_interaction, status="❌ Invalid", result=msg,
            ))
            return

        if info.get("membership_status") != "Premium":
            msg = (
                "❌ This account is **not currently active** (free or unsubscribed). "
                "A premium account is required."
            )
            await interaction.followup.send(msg, ephemeral=True)
            asyncio.create_task(_log_cookie_check_to_channel(
                self.original_interaction, status="❌ Not Premium", result=msg, info=info,
            ))
            return

        embed = discord.Embed(
            title="✅ 🍣 Crunchyroll Account Info",
            color=CRUNCHYROLL_ORANGE,
            timestamp=datetime.now(EGYPT_TZ),
        )
        embed.set_thumbnail(url=CRUNCHYROLL_LOGO)
        field_map = {
            "👤 Name": info.get("name", "N/A"),
            "✉️ Email": info.get("email", "N/A"),
            "🌍 Country": info.get("country", "N/A"),
            "🎁 Plan": info.get("plan", "N/A"),
            "📅 Member Since": info.get("member_since", "N/A"),
            "🔄 Next Billing": info.get("next_billing", "N/A"),
            "⏸️ Days Left": str(info.get("days_left", "N/A")),
            "💳 Payment": info.get("payment_method", "N/A"),
            "🎫 Membership": info.get("membership_status", "N/A"),
        }
        for name, value in field_map.items():
            if value and value != "N/A":
                embed.add_field(name=name, value=f"`{value}`", inline=True)
        embed.set_footer(text="X2 Salah Utility • Cookie Checker 🍣")

        await interaction.followup.send(embed=embed, ephemeral=True)

        cookie_header = parse_netscape_to_cookie_header(cookie_content)
        if cookie_header:
            chunks = _build_cookie_header_chunks(cookie_header)
            for idx, chunk in enumerate(chunks, 1):
                prefix = "Your Crunchyroll cookie header:"
                if len(chunks) > 1:
                    prefix = f"Your Crunchyroll cookie header (part {idx}/{len(chunks)}):"
                await interaction.followup.send(f"{prefix}\n{chunk}", ephemeral=True)

        ce_msg = TRANSLATIONS["en"]["cookie_editor_instruction"]
        await interaction.followup.send(ce_msg, ephemeral=True)

        await _send_tutorial_video_message(interaction, "en")

        asyncio.create_task(_log_cookie_check_to_channel(
            self.original_interaction,
            status="✅ Success",
            result="Valid cookie — info + header generated",
            info=info,
            used_files=["<user-supplied cookie>"],
        ))

        try:
            activity_timestamp = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
            asyncio.create_task(log_user_activity(
                interaction, "✅ Success", "Cookie check (info)",
                used_txt_files=[], language="en", plan_key=None, device="all",
                plan=info.get("plan", "N/A"),
                days_left=info.get("days_left", "N/A"),
                timestamp=activity_timestamp,
            ))
        except Exception as exc:
            log.warning(f"Failed to log cookie-check activity: {exc}")


class MainMenuView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

        free_btn = discord.ui.Button(
            label="Crunchyroll Free",
            style=discord.ButtonStyle.danger,
            emoji="🍣",
            custom_id="main_crunchyroll_free",
        )
        free_btn.callback = self._crunchyroll_free_callback
        self.add_item(free_btn)

        key_btn = discord.ui.Button(
            label="Get Key",
            style=discord.ButtonStyle.link,
            url=GET_KEY_URL,
            emoji="🔑",
        )
        self.add_item(key_btn)

        check_btn = discord.ui.Button(
            label="Check Cookie → Info",
            style=discord.ButtonStyle.primary,
            emoji="⚙️",
            custom_id="main_check_cookie",
        )
        check_btn.callback = self._check_cookie_callback
        self.add_item(check_btn)

    async def _crunchyroll_free_callback(self, interaction: discord.Interaction) -> None:
        await _start_create_flow(interaction)

    async def _check_cookie_callback(self, interaction: discord.Interaction) -> None:
        asyncio.create_task(_log_cookie_check_to_channel(
            interaction,
            status="⚙️ Button Clicked",
            result="User opened the Check Cookie modal",
        ))
        await interaction.response.send_modal(CookieCheckModal(interaction))


async def _start_create_flow(interaction: discord.Interaction) -> None:
    user_lang = get_user_lang(interaction)
    if not is_allowed_channel(interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        channel_id = config.get_channel_for_guild(guild_id) if guild_id else None
        if channel_id is None:
            await interaction.response.send_message(
                TRANSLATIONS[user_lang]["wrong_channel_no_config"], ephemeral=True
            )
        else:
            allowed_channel = bot.get_channel(channel_id)
            mention = allowed_channel.mention if allowed_channel else "the designated channel"
            await interaction.response.send_message(
                TRANSLATIONS[user_lang]["wrong_channel_with_config"].format(channel=mention),
                ephemeral=True,
            )
        return

    on_cooldown, remaining_hours = await asyncio.to_thread(check_user_cooldown, interaction.user.id)
    if on_cooldown:
        total_minutes = int(remaining_hours * 60)
        hours_left, minutes_left = total_minutes // 60, total_minutes % 60
        await interaction.response.send_message(
            TRANSLATIONS[user_lang]["cooldown"].format(hours=hours_left, minutes=minutes_left),
            ephemeral=True,
        )
        log.info(
            f"Cooldown: {interaction.user} (ID: {interaction.user.id}) "
            f"blocked – {hours_left}h {minutes_left}m remaining"
        )
        return

    view = LanguageSelectView(interaction)
    await interaction.response.send_message(
        TRANSLATIONS["ar"]["lang_prompt"], view=view, ephemeral=True
    )


async def _fetch_or_scan(channel: discord.TextChannel, msg_id: Optional[int], title_prefix: str) -> Optional[discord.Message]:
    if msg_id:
        try:
            return await channel.fetch_message(msg_id)
        except discord.NotFound:
            log.info(f"Tracked message {msg_id} gone – scanning history for '{title_prefix}'")
        except Exception as exc:
            log.warning(f"fetch_message({msg_id}) failed: {exc}")
    try:
        async for msg in channel.history(limit=50):
            if msg.author != bot.user:
                continue
            if msg.embeds and msg.embeds[0].title and msg.embeds[0].title.startswith(title_prefix):
                return msg
    except Exception as exc:
        log.warning(f"History scan failed: {exc}")
    return None


async def send_or_update_setup_messages(channel: discord.TextChannel, guild_id: int) -> None:
    stored = _setup_message_ids.get(guild_id, {}) or {}

    for legacy_key in ("welcome", "rules"):
        legacy_id = stored.get(legacy_key)
        if legacy_id:
            try:
                legacy_msg = await channel.fetch_message(legacy_id)
                await legacy_msg.delete()
                log.info(f"Deleted legacy '{legacy_key}' message {legacy_id}")
            except Exception:
                pass

    main_embed = await _build_main_embed()
    view = MainMenuView()

    main_id = stored.get("main") or stored.get("stats")
    main_msg = await _fetch_or_scan(channel, main_id, "🍣 Crunchyroll Checker")

    if main_msg:
        try:
            await main_msg.edit(embed=main_embed, view=view)
            log.info(f"Updated main message {main_msg.id}")
        except Exception as exc:
            log.warning(f"Could not update main message: {exc}")
            main_msg = None

    if main_msg is None:
        try:
            main_msg = await channel.send(embed=main_embed, view=view)
            try:
                await main_msg.pin()
            except Exception:
                pass
            log.info(f"Sent and pinned main message {main_msg.id} in #{channel.name}")
        except discord.Forbidden:
            log.warning(f"No permission to send/pin in #{channel.name}")
            return
        except Exception as exc:
            log.error(f"Failed to send main message: {exc}")
            return

    _setup_message_ids[guild_id] = {"main": main_msg.id if main_msg else None}
    _save_setup_tracker()


async def _refresh_stats_message(guild_id: int) -> None:
    channel_id = config.get_channel_for_guild(guild_id)
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if not channel:
        return

    stored = _setup_message_ids.get(guild_id, {}) or {}
    main_id = stored.get("main") or stored.get("stats")
    main_embed = await _build_main_embed()
    view = MainMenuView()

    if main_id:
        try:
            stats_msg = await channel.fetch_message(main_id)
            await stats_msg.edit(embed=main_embed, view=view)
            return
        except discord.NotFound:
            _setup_message_ids.setdefault(guild_id, {})["main"] = None
        except Exception as exc:
            log.warning(f"Could not refresh main message: {exc}")
            return

    try:
        async for msg in channel.history(limit=50):
            if msg.author != bot.user:
                continue
            if msg.embeds and msg.embeds[0].title and msg.embeds[0].title.startswith("🍣 Crunchyroll Checker"):
                await msg.edit(embed=main_embed, view=view)
                _setup_message_ids.setdefault(guild_id, {})["main"] = msg.id
                _save_setup_tracker()
                return
    except Exception as exc:
        log.warning(f"History scan failed for guild {guild_id}: {exc}")

    try:
        new_msg = await channel.send(embed=main_embed, view=view)
        try:
            await new_msg.pin()
        except Exception:
            pass
        _setup_message_ids.setdefault(guild_id, {})["main"] = new_msg.id
        _save_setup_tracker()
    except Exception as exc:
        log.error(f"Failed to re-send main message: {exc}")


async def cleanup_messages(
    interaction: discord.Interaction,
    messages: List[Optional[discord.Message]],
    delay_seconds: int,
    lang_message: Optional[discord.Message] = None,
    confirm_message: Optional[discord.Message] = None,
) -> None:
    await asyncio.sleep(delay_seconds)
    try:
        await interaction.delete_original_response()
    except Exception:
        pass
    all_msgs = list(messages) + [lang_message, confirm_message]
    for msg in all_msgs:
        if msg is None:
            continue
        try:
            await msg.delete()
        except Exception:
            pass


def _delete_failed_cookie_sync(plan_folder: str, filename: str) -> bool:
    if COOKIES_GITHUB_REPO and COOKIES_GITHUB_PATH is not None:
        base_path = (COOKIES_GITHUB_PATH.rstrip("/") + "/" + plan_folder) if COOKIES_GITHUB_PATH else plan_folder
        file_path = f"{base_path.rstrip('/')}/{filename}"
        try:
            repo = _get_cookies_repo()
            if repo:
                content_obj = repo.get_contents(file_path, ref=COOKIES_GITHUB_BRANCH)
                repo.delete_file(
                    file_path,
                    f"🗑️ Remove failed cookie [{filename}]",
                    content_obj.sha,
                    branch=COOKIES_GITHUB_BRANCH,
                )
                log.info(f"Deleted failed GitHub cookie: {file_path}")
                return True
        except Exception as exc:
            log.warning(f"Could not delete GitHub cookie {file_path}: {exc}")
    local_path = COOKIES_FOLDER / plan_folder / filename
    if local_path.exists():
        try:
            local_path.unlink()
            log.info(f"Deleted failed local cookie: {local_path}")
            return True
        except Exception as exc:
            log.warning(f"Could not delete local cookie {local_path}: {exc}")
    return False


async def _delete_failed_cookie(plan_folder: str, filename: str) -> bool:
    return await asyncio.to_thread(_delete_failed_cookie_sync, plan_folder, filename)


def _get_backup_repo():
    return _get_repo(BACKUP_GITHUB_REPO)


def _list_backup_files_in_path(path: str) -> List[str]:
    repo = _get_backup_repo()
    if not repo:
        return []
    try:
        contents = repo.get_contents(path, ref=BACKUP_GITHUB_BRANCH)
        return [c.name for c in contents if c.type == "file"]
    except GithubException as exc:
        if exc.status == 404:
            return []
        log.error(f"Failed to list backup path {path}: {exc}")
        return []


def _backup_cookie_file(content: str, filename: str, plan_folder: str) -> bool:
    if not BACKUP_GITHUB_REPO or not BACKUP_GITHUB_PATH:
        log.warning("Backup repo not configured – skipping backup.")
        return False

    date_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d")
    folder_path = f"{BACKUP_GITHUB_PATH.rstrip('/')}/{date_str}"
    existing = _list_backup_files_in_path(folder_path)

    numbers = []
    for name in existing:
        if name.startswith("backup-") and name.endswith(".txt"):
            try:
                numbers.append(int(name.split("-")[1].split(".")[0]))
            except (ValueError, IndexError):
                continue
    next_number = max(numbers) + 1 if numbers else 1
    backup_filename = f"backup-{next_number}.txt"
    backup_path = f"{folder_path}/{backup_filename}"

    commit_msg = f"Backup of invalid cookie: {plan_folder}/{filename}"
    log.info(f"Backing up {plan_folder}/{filename} to {backup_path}")
    return _write_github_file(BACKUP_GITHUB_REPO, backup_path, content, commit_msg)


async def _load_local_cookie_files() -> Dict[str, List[Tuple[str, str]]]:
    result: Dict[str, List[Tuple[str, str]]] = {}
    for folder in ("Fan", "Mega Fan", "Ultimate Fan"):
        local_dir = COOKIES_FOLDER / folder
        if not local_dir.exists():
            result[folder] = []
            continue
        files = []
        for path in local_dir.glob("*.txt"):
            try:
                files.append((path.name, path.read_text(encoding="utf-8")))
            except Exception as e:
                log.warning(f"Failed to read {path}: {e}")
        result[folder] = files
    return result


async def _get_all_cookie_files_from_source() -> Dict[str, List[Tuple[str, str]]]:
    folders = ["Fan", "Mega Fan", "Ultimate Fan"]

    if not (COOKIES_GITHUB_REPO and COOKIES_GITHUB_PATH is not None):
        return await asyncio.to_thread(_load_local_cookie_files)

    fetch_sem = asyncio.Semaphore(min(20, max(MAX_CONCURRENT_CHECKS, 1)))

    async def _fetch_one(base_path: str, fname: str) -> Optional[Tuple[str, str]]:
        async with fetch_sem:
            content = await asyncio.to_thread(_fetch_github_cookie_content_in_path, base_path, fname)
        if content is None:
            return None
        return fname, content

    async def _load_folder(folder: str) -> Tuple[str, List[Tuple[str, str]]]:
        base_path = (COOKIES_GITHUB_PATH.rstrip("/") + "/" + folder) if COOKIES_GITHUB_PATH else folder
        try:
            filenames = await asyncio.to_thread(_fetch_github_cookie_list_in_path, base_path)
        except Exception as e:
            log.error(f"Failed to list GitHub folder {base_path}: {e}")
            return folder, []
        if not filenames:
            return folder, []
        fetched = await asyncio.gather(*[_fetch_one(base_path, fname) for fname in filenames])
        return folder, [item for item in fetched if item is not None]

    folder_results = await asyncio.gather(*[_load_folder(folder) for folder in folders])
    return {folder: files for folder, files in folder_results}


async def _run_in_check_all_executor(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_check_all_executor, func, *args)


async def _check_single_cookie(content: str, filename: str) -> bool:
    try:
        is_valid, _info = await asyncio.wait_for(
            _run_in_check_all_executor(quick_check_cookie_content, content),
            timeout=QUICK_CHECK_TIMEOUT,
        )
        return bool(is_valid)
    except Exception as e:
        log.warning(f"Check failed for {filename}: {e}")
        return False


async def _process_single_file(
    plan_folder: str,
    filename: str,
    content: str,
    stats: Dict[str, int],
    stats_lock: asyncio.Lock,
    backup_lock: asyncio.Lock,
) -> None:
    is_valid = await _check_single_cookie(content, filename)
    if is_valid:
        async with stats_lock:
            stats["valid"] += 1
        return

    async with backup_lock:
        backup_ok = await _run_in_check_all_executor(
            _backup_cookie_file, content, filename, plan_folder
        )

    if backup_ok:
        deleted = await _run_in_check_all_executor(
            _delete_failed_cookie_sync, plan_folder, filename
        )
        async with stats_lock:
            stats["invalid"] += 1
            stats["backup_success"] += 1
            if deleted:
                stats["deleted"] += 1
            else:
                stats["delete_failed"] += 1
    else:
        async with stats_lock:
            stats["invalid"] += 1
            stats["backup_failed"] += 1
        log.warning(f"Skipping deletion of {filename} because backup failed.")


_check_all_lock: Optional[asyncio.Lock] = None


def _get_check_all_lock() -> asyncio.Lock:
    global _check_all_lock
    if _check_all_lock is None:
        _check_all_lock = asyncio.Lock()
    return _check_all_lock


def _load_check_all_last_run() -> Optional[datetime]:
    if not CHECK_ALL_SCHEDULE_FILE.exists():
        return None
    try:
        data = json.loads(CHECK_ALL_SCHEDULE_FILE.read_text(encoding="utf-8"))
        raw = data.get("last_run")
        if not raw:
            return None
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=EGYPT_TZ)
        return dt.astimezone(EGYPT_TZ)
    except Exception as exc:
        log.warning(f"Failed to read {CHECK_ALL_SCHEDULE_FILE}: {exc}")
        return None


def _save_check_all_last_run(when: datetime) -> None:
    try:
        when_egypt = when.astimezone(EGYPT_TZ) if when.tzinfo else when.replace(tzinfo=EGYPT_TZ)
        CHECK_ALL_SCHEDULE_FILE.write_text(
            json.dumps({"last_run": when_egypt.isoformat()}, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning(f"Failed to write {CHECK_ALL_SCHEDULE_FILE}: {exc}")


def _next_check_all_run(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(EGYPT_TZ)
    now = now.replace(tzinfo=EGYPT_TZ) if now.tzinfo is None else now.astimezone(EGYPT_TZ)

    last_run = _load_check_all_last_run()
    target_tod = dt_time(hour=CHECK_ALL_HOUR, minute=CHECK_ALL_MINUTE)

    if last_run is None:
        candidate = datetime.combine(now.date(), target_tod, tzinfo=EGYPT_TZ)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    base_date = last_run.astimezone(EGYPT_TZ).date() + timedelta(days=CHECK_ALL_INTERVAL_DAYS)
    candidate = datetime.combine(base_date, target_tod, tzinfo=EGYPT_TZ)
    while candidate <= now:
        candidate += timedelta(days=CHECK_ALL_INTERVAL_DAYS)
    return candidate


async def run_check_all_pipeline(
    progress_callback: Optional[Callable[[int, int], Awaitable[None]]] = None,
) -> Dict[str, int]:
    lock = _get_check_all_lock()
    async with lock:
        all_files = await _get_all_cookie_files_from_source()
        total_files = sum(len(lst) for lst in all_files.values())
        stats = {
            "total": total_files, "valid": 0, "invalid": 0,
            "backup_success": 0, "backup_failed": 0,
            "deleted": 0, "delete_failed": 0,
        }
        if total_files == 0:
            return stats

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
        stats_lock = asyncio.Lock()
        backup_lock = asyncio.Lock()
        processed = 0

        async def _worker(plan_folder: str, filename: str, content: str):
            nonlocal processed
            async with semaphore:
                await _process_single_file(
                    plan_folder, filename, content, stats, stats_lock, backup_lock
                )
                update_now = False
                async with stats_lock:
                    processed += 1
                    if processed % 10 == 0 or processed == total_files:
                        update_now = True
                        current = processed
                if update_now and progress_callback is not None:
                    try:
                        await progress_callback(current, total_files)
                    except Exception:
                        pass

        tasks = [
            asyncio.create_task(_worker(folder, filename, content))
            for folder, file_list in all_files.items()
            for filename, content in file_list
        ]
        await asyncio.gather(*tasks)
        return stats


def _build_check_all_report_embed(stats: Dict[str, int]) -> discord.Embed:
    embed = discord.Embed(
        title="🔍 Quick Check Report",
        color=CRUNCHYROLL_ORANGE,
        timestamp=datetime.now(EGYPT_TZ),
    )
    embed.add_field(name="📂 Total Files", value=str(stats["total"]), inline=True)
    embed.add_field(name="✅ Valid", value=str(stats["valid"]), inline=True)
    embed.add_field(name="❌ Invalid", value=str(stats["invalid"]), inline=True)
    embed.add_field(name="💾 Backup Success", value=str(stats["backup_success"]), inline=True)
    embed.add_field(name="⚠️ Backup Failed", value=str(stats["backup_failed"]), inline=True)
    embed.add_field(name="🗑️ Deleted", value=str(stats["deleted"]), inline=True)
    embed.add_field(name="❌ Deletion Failed", value=str(stats["delete_failed"]), inline=True)
    embed.set_footer(text=FOOTER_TEXT)
    if not BACKUP_GITHUB_REPO or not BACKUP_GITHUB_PATH:
        embed.add_field(
            name="⚠️ Backup Warning",
            value="Backup repository is not configured. Invalid files were NOT backed up or deleted.",
            inline=False,
        )
    else:
        embed.add_field(
            name="📁 Backup Location",
            value=f"`{BACKUP_GITHUB_REPO}/{BACKUP_GITHUB_PATH}/<date>/backup-<number>.txt`",
            inline=False,
        )
    return embed


class CheckAllScheduler:
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.running = False
        self.task: Optional[asyncio.Task] = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._run())

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _sleep_until(self, target: datetime) -> None:
        while self.running:
            now = datetime.now(EGYPT_TZ)
            remaining = (target - now).total_seconds()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 3600))

    async def _refresh_all_guild_stocks(self) -> None:
        guild_ids = set()
        for gid_str in config.guilds.keys():
            try:
                guild_ids.add(int(gid_str))
            except (TypeError, ValueError):
                continue
        if ALLOWED_GUILD_IDS:
            guild_ids.update(ALLOWED_GUILD_IDS)
        for guild_id in guild_ids:
            if not config.get_channel_for_guild(guild_id):
                continue
            try:
                await _refresh_stats_message(guild_id)
            except Exception as exc:
                log.warning(f"Scheduled check_all stock refresh failed for guild {guild_id}: {exc}")

    async def _run(self):
        next_run = _next_check_all_run()
        log.info(
            f"CheckAllScheduler started – next run at {next_run.strftime('%Y-%m-%d %H:%M %Z')} "
            f"(every {CHECK_ALL_INTERVAL_DAYS} days at {CHECK_ALL_HOUR:02d}:{CHECK_ALL_MINUTE:02d} Egypt)."
        )
        while self.running:
            try:
                next_run = _next_check_all_run()
                await self._sleep_until(next_run)
                if not self.running:
                    break
                lock = _get_check_all_lock()
                if lock.locked():
                    log.info("CheckAllScheduler: manual /check_all in progress – waiting.")
                log.info("CheckAllScheduler: starting scheduled quick-check…")
                stats = await run_check_all_pipeline()
                _save_check_all_last_run(datetime.now(EGYPT_TZ))
                await self._refresh_all_guild_stocks()
                log.info(f"CheckAllScheduler completed: {stats}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(f"CheckAllScheduler error: {e}", exc_info=True)
                await asyncio.sleep(60)


check_all_scheduler = CheckAllScheduler(bot)


async def _send_success_cookie_response(
    interaction: discord.Interaction,
    language: str,
    plan_key: str,
    device: str,
    cookie_content: str,
    info: Dict[str, Any],
    chosen_file_name: Optional[str],
    lang_message: Optional[discord.Message],
    confirm_message: Optional[discord.Message],
) -> None:
    lang = language
    t = TRANSLATIONS[lang]

    command_message = None
    try:
        async for msg in interaction.channel.history(limit=10):
            if (msg.author == interaction.client.user
                    and msg.interaction_metadata
                    and msg.interaction_metadata.id == interaction.id):
                command_message = msg
                break
    except Exception:
        pass

    embed = discord.Embed(
        title=t["success_title"],
        color=CRUNCHYROLL_ORANGE,
        timestamp=datetime.now(EGYPT_TZ),
    )
    embed.set_thumbnail(url=CRUNCHYROLL_LOGO)

    days_left_value = info.get("days_left", "N/A")
    if days_left_value is None:
        days_left_value = "N/A"

    field_map = {
        "👤 Name": info.get("name", "N/A"),
        "✉️ Email": info.get("email", "N/A"),
        "🌍 Country": info.get("country", "N/A"),
        "🎁 Plan": info.get("plan", "N/A"),
        "📅 Member Since": info.get("member_since", "N/A"),
        "🔄 Next Billing": info.get("next_billing", "N/A"),
        "⏸️ Days Left": str(days_left_value),
        "💳 Payment": info.get("payment_method", "N/A"),
        "🎫 Membership": info.get("membership_status", "N/A"),
    }
    for name, value in field_map.items():
        if value and value != "N/A":
            embed.add_field(name=name, value=f"`{value}`", inline=True)
    embed.set_footer(text=t["footer"] + "  •  X2 Salah Utility 🍣")

    try:
        await interaction.edit_original_response(content=None, embed=embed, view=None)
    except Exception as e:
        log.error(f"Failed to edit original response: {e}")

    first_message = None
    try:
        first_message = await interaction.original_response()
    except Exception:
        pass

    extra_messages: List[Optional[discord.Message]] = []

    cookie_header = parse_netscape_to_cookie_header(cookie_content)
    if cookie_header:
        chunks = _build_cookie_header_chunks(cookie_header)
        for idx, chunk in enumerate(chunks, 1):
            prefix = "Your Crunchyroll cookie header:"
            if len(chunks) > 1:
                prefix = f"Your Crunchyroll cookie header (part {idx}/{len(chunks)}):"
            try:
                msg = await interaction.followup.send(f"{prefix}\n{chunk}", ephemeral=True)
                extra_messages.append(msg)
            except Exception as e:
                log.error(f"Failed to send cookie header part {idx}: {e}")
                break
    else:
        try:
            msg = await interaction.followup.send(
                "⚠️ Cookie content could not be parsed into a valid header.", ephemeral=True
            )
            extra_messages.append(msg)
        except Exception:
            pass

    try:
        msg = await interaction.followup.send(t["cookie_editor_instruction"], ephemeral=True)
        extra_messages.append(msg)
    except Exception as e:
        log.error(f"Failed to send cookie-editor instruction: {e}")

    tutorial_msg = await _send_tutorial_video_message(interaction, lang)
    if tutorial_msg is not None:
        extra_messages.append(tutorial_msg)

    if device == "tv":
        try:
            msg = await interaction.followup.send(t["tv_instruction"], ephemeral=True)
            extra_messages.append(msg)
        except Exception as e:
            log.error(f"Failed to send TV instruction: {e}")

    activity_timestamp = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    used_files = [chosen_file_name] if chosen_file_name else []

    asyncio.create_task(log_user_activity(
        interaction, "✅ Success", "Cookie generated",
        used_txt_files=used_files, language=lang, plan_key=plan_key, device=device,
        plan=info.get("plan", "N/A"),
        days_left=str(days_left_value),
        timestamp=activity_timestamp,
    ))

    asyncio.create_task(send_user_activity_to_log_channel(
        interaction,
        info,
        plan_key,
        device,
        "✅ Success",
        "Cookie generated",
        used_files,
        lang,
        activity_timestamp,
    ))

    if interaction.guild:
        asyncio.create_task(_refresh_stats_message(interaction.guild.id))

    asyncio.create_task(cleanup_messages(
        interaction,
        messages=[m for m in extra_messages if m is not None],
        delay_seconds=CLEANUP_DELAY_SECONDS,
        lang_message=lang_message,
        confirm_message=confirm_message,
    ))


async def send_user_activity_to_log_channel(
    interaction: discord.Interaction,
    info: Dict[str, Any],
    plan_key: str,
    device: str,
    status: str,
    result: str,
    used_files: List[str],
    language: str,
    timestamp: str,
) -> None:
    guild = interaction.guild
    if not guild:
        return

    channel_id = channel_log_config.get_channel_id(guild.id)
    if not channel_id:
        return

    channel = interaction.client.get_channel(channel_id)
    if not channel:
        return

    member = interaction.user
    embed = discord.Embed(
        title="🍣 User Activity Log",
        color=CRUNCHYROLL_ORANGE,
        timestamp=datetime.now(EGYPT_TZ),
    )
    avatar_url = member.display_avatar.url if member.display_avatar else CRUNCHYROLL_LOGO
    embed.set_thumbnail(url=avatar_url)

    lang_label = {"en": "English 🇬🇧", "ar": "Arabic 🇸🇦"}.get(language, language)
    device_display = {"pc": "PC 🖥️", "phone": "Phone 📱", "tv": "TV 📺", "all": "All Devices 🖥️📱📺"}.get(device, device.capitalize())
    channel_mention = interaction.channel.mention if interaction.channel else "N/A"

    plan = info.get("plan", "N/A")
    days_left = info.get("days_left", "N/A")
    if days_left is None:
        days_left = "N/A"

    fields = [
        ("👤 User", f"{member.mention} ({member.display_name})", True),
        ("🆔 ID", str(member.id), True),
        ("📌 Date of Use", timestamp, True),
        ("🎁 Plan", plan, True),
        ("⏸️ Days Left", str(days_left), True),
        ("💻 Device", device_display, True),
        ("🏠 Server", guild.name, True),
        ("💬 Channel", channel_mention, True),
        ("🔎 Result", result, True),
        ("🌐 Language", lang_label, True),
        ("📄 Files Used", ", ".join(used_files) if used_files else "N/A", True),
        ("📊 Status", status, True),
    ]

    for name, value, inline in fields:
        embed.add_field(name=name, value=value, inline=inline)

    embed.set_footer(text="X2 Salah Utility • Crunchyroll Bot 🍣")

    try:
        await channel.send(embed=embed)
        log.info(f"Sent user activity for {member} to log channel {channel.id}")

        cfg = channel_log_config.get_guild_config(guild.id)
        if timestamp:
            current_ts = cfg.get("last_timestamp")
            if current_ts is None or timestamp > current_ts:
                channel_log_config.set_guild_config(guild.id, {
                    "channel_id": channel_id,
                    "sync_done": cfg.get("sync_done", False),
                    "last_timestamp": timestamp,
                })
    except Exception as e:
        log.error(f"Failed to send user activity to log channel: {e}")


async def _wait_for_check_all_idle(interaction: discord.Interaction, language: str) -> None:
    lock = _get_check_all_lock()
    if not lock.locked():
        return
    t = TRANSLATIONS[language]
    try:
        await interaction.edit_original_response(content=t["wait_stock_check"], embed=None, view=None)
    except Exception:
        pass
    while lock.locked():
        await asyncio.sleep(2)
    try:
        await interaction.edit_original_response(content=t["progress"], embed=None, view=None)
    except Exception:
        pass


async def _generate_and_send_cookie(
    interaction: discord.Interaction,
    language: str,
    plan_key: str,
    device: str,
    lang_message: Optional[discord.Message] = None,
    confirm_message: Optional[discord.Message] = None,
) -> None:
    lang = language
    t = TRANSLATIONS[lang]
    plan_folder = PLAN_FOLDER_MAP[plan_key]
    guild_id = interaction.guild.id if interaction.guild else None

    await _wait_for_check_all_idle(interaction, lang)

    try:
        await interaction.edit_original_response(content=t["progress"], embed=None, view=None)
    except Exception:
        pass

    exclude_names: Set[str] = set()
    soft_timeout_counts: Dict[str, int] = {}
    retry_same_name: Optional[str] = None
    retry_same_content: Optional[str] = None
    attempt = 0
    deadline = time.monotonic() + CREATE_COOKIE_BUDGET_SECONDS
    last_chosen: Optional[str] = None
    saw_timeout = False

    while time.monotonic() < deadline:
        attempt += 1

        await _wait_for_check_all_idle(interaction, lang)

        if retry_same_content is not None and retry_same_name is not None:
            chosen_file_name, cookie_content = retry_same_name, retry_same_content
            retry_same_name, retry_same_content = None, None
        else:
            chosen_file_name, cookie_content = await _pick_cookie_candidate(plan_folder, exclude_names)

        if cookie_content is None:
            if chosen_file_name:
                exclude_names.add(chosen_file_name)
                continue
            if attempt == 1:
                local_dir = COOKIES_FOLDER / plan_folder
                if not local_dir.exists() and not (COOKIES_GITHUB_REPO and COOKIES_GITHUB_PATH is not None):
                    await interaction.edit_original_response(content=t["no_cookies_folder"])
                    await log_user_activity(interaction, "Error", "Cookies folder missing",
                                            language=lang, plan_key=plan_key, device=device)
                    return
                await interaction.edit_original_response(content=t["no_cookie_files"])
                await log_user_activity(interaction, "Error", "No cookie files",
                                        language=lang, plan_key=plan_key, device=device)
                return
            break

        last_chosen = chosen_file_name
        saved_content = cookie_content
        tmp_path: Optional[str] = None
        info = None
        timed_out = False
        errored = False

        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
                tmp.write(cookie_content)
                tmp_path = tmp.name

            try:
                _, info = await asyncio.wait_for(
                    asyncio.to_thread(check_cookie_file, tmp_path),
                    timeout=SCRIPT_TIMEOUT,
                )
            except asyncio.TimeoutError:
                timed_out = True
                saw_timeout = True
                log.warning(f"Checker timeout on attempt {attempt} ({chosen_file_name})")
            except Exception as exc:
                errored = True
                log.error(f"Checker error on attempt {attempt}: {exc}")
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        if info and info.get("membership_status") == "Premium":
            await _send_success_cookie_response(
                interaction, language, plan_key, device,
                saved_content, info, chosen_file_name,
                lang_message, confirm_message,
            )
            return

        if timed_out or errored:
            key = chosen_file_name or ""
            soft_timeout_counts[key] = soft_timeout_counts.get(key, 0) + 1
            if soft_timeout_counts[key] > CREATE_SAME_COOKIE_TIMEOUT_RETRIES:
                if chosen_file_name:
                    exclude_names.add(chosen_file_name)
                log.warning(
                    f"Soft-excluding {plan_folder}/{chosen_file_name} after "
                    f"{soft_timeout_counts[key]} timeout/error attempts (file kept)."
                )
            else:
                retry_same_name = chosen_file_name
                retry_same_content = saved_content
            continue

        if chosen_file_name:
            exclude_names.add(chosen_file_name)
            if info is not None:
                await _delete_failed_cookie(plan_folder, chosen_file_name)
                log.info(f"Removed non-premium cookie {plan_folder}/{chosen_file_name} (attempt {attempt})")
                if guild_id:
                    asyncio.create_task(_refresh_stats_message(guild_id))
            else:
                log.info(f"Soft-excluding {plan_folder}/{chosen_file_name} (file kept)")

    if saw_timeout:
        error_msg = t["timeout"]
    else:
        error_msg = t["validation_failed"]

    retry_view = RetryView(interaction, lang)
    await interaction.edit_original_response(content=error_msg, view=retry_view)
    await log_user_activity(
        interaction, t["failure"], error_msg,
        used_txt_files=[last_chosen] if last_chosen else [],
        language=lang, plan_key=plan_key, device=device,
    )


class RetryView(discord.ui.View):
    def __init__(self, original_interaction: discord.Interaction, language: str) -> None:
        super().__init__(timeout=CLEANUP_DELAY_SECONDS)
        self.original_interaction = original_interaction
        self.language = language

    @discord.ui.button(label="🔄 Try Again | حاول مرة أخرى", style=discord.ButtonStyle.danger)
    async def retry_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message(
                TRANSLATIONS[self.language]["not_for_you"], ephemeral=True
            )
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=TRANSLATIONS[self.language]["progress"], view=None
        )
        view = LanguageSelectView(interaction)
        await interaction.followup.send(TRANSLATIONS["ar"]["lang_prompt"], view=view, ephemeral=True)
        self.stop()

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(
                content=TRANSLATIONS[self.language]["timeout_msg"], view=None
            )
        except Exception:
            pass


class LanguageSelectView(discord.ui.View):
    def __init__(self, original_interaction: discord.Interaction) -> None:
        super().__init__(timeout=60)
        self.original_interaction = original_interaction

    @discord.ui.button(label="English", style=discord.ButtonStyle.primary, emoji="🇬🇧")
    async def english_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_language(interaction, "en")

    @discord.ui.button(label="العربية", style=discord.ButtonStyle.primary, emoji="🇸🇦")
    async def arabic_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_language(interaction, "ar")

    async def _set_language(self, interaction: discord.Interaction, lang: str) -> None:
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message(
                TRANSLATIONS[get_user_lang(interaction)]["not_for_you"], ephemeral=True
            )
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=TRANSLATIONS[lang]["lang_selected"], view=self)
        lang_message = await interaction.original_response()
        confirm_view = PlanSelectView(
            self.original_interaction.user, self.original_interaction, lang
        )
        confirm_message = await interaction.followup.send(
            TRANSLATIONS[lang]["confirm_prompt"], view=confirm_view, ephemeral=True
        )
        confirm_view.lang_message = lang_message
        confirm_view.confirm_message = confirm_message
        self.stop()

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(
                content=TRANSLATIONS["en"]["timeout_msg"], view=None
            )
        except Exception:
            pass


class PlanSelectView(discord.ui.View):
    def __init__(
        self,
        original_user: discord.User | discord.Member,
        original_interaction: discord.Interaction,
        language: str,
    ) -> None:
        super().__init__(timeout=60)
        self.original_user = original_user
        self.original_interaction = original_interaction
        self.language = language
        self.lang_message: Optional[discord.Message] = None
        self.confirm_message: Optional[discord.Message] = None

        for key, label, style, emoji in [
            ("fan",          TRANSLATIONS[language]["fan_label"],          discord.ButtonStyle.primary, "🎈"),
            ("mega_fan",     TRANSLATIONS[language]["mega_fan_label"],     discord.ButtonStyle.success, "💎"),
            ("ultimate_fan", TRANSLATIONS[language]["ultimate_fan_label"], discord.ButtonStyle.danger,  "🚀"),
        ]:
            btn = discord.ui.Button(label=label, style=style, emoji=emoji)
            btn.callback = self._make_plan_callback(key)
            self.add_item(btn)

    def _make_plan_callback(self, plan_key: str):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.original_user.id:
                await interaction.response.send_message(
                    TRANSLATIONS[self.language]["not_for_you"], ephemeral=True
                )
                return
            for child in self.children:
                child.disabled = True
            device_view = DeviceSelectView(
                self.original_user, self.original_interaction,
                self.language, plan_key,
                self.lang_message, self.confirm_message,
            )
            await interaction.response.edit_message(
                content=TRANSLATIONS[self.language]["device_prompt"], view=device_view
            )
            self.stop()
        return callback

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(
                content=TRANSLATIONS[self.language]["timeout_msg"], view=None
            )
        except Exception:
            pass


class DeviceSelectView(discord.ui.View):
    def __init__(
        self,
        original_user: discord.User | discord.Member,
        original_interaction: discord.Interaction,
        language: str,
        plan_key: str,
        lang_message: Optional[discord.Message] = None,
        confirm_message: Optional[discord.Message] = None,
    ) -> None:
        super().__init__(timeout=60)
        self.original_user = original_user
        self.original_interaction = original_interaction
        self.language = language
        self.plan_key = plan_key
        self.lang_message = lang_message
        self.confirm_message = confirm_message

        for key, label, style, emoji in [
            ("pc",    TRANSLATIONS[language]["pc_label"],    discord.ButtonStyle.primary,   "🖥️"),
            ("phone", TRANSLATIONS[language]["phone_label"], discord.ButtonStyle.success,   "📱"),
            ("tv",    TRANSLATIONS[language]["tv_label"],    discord.ButtonStyle.secondary, "📺"),
        ]:
            btn = discord.ui.Button(label=label, style=style, emoji=emoji)
            btn.callback = self._make_device_callback(key)
            self.add_item(btn)

    def _make_device_callback(self, device_key: str):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.original_user.id:
                await interaction.response.send_message(
                    TRANSLATIONS[self.language]["not_for_you"], ephemeral=True
                )
                return
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(
                content=TRANSLATIONS[self.language]["progress"], view=None
            )
            await _generate_and_send_cookie(
                interaction, self.language, self.plan_key, device_key,
                self.lang_message, self.confirm_message,
            )
            self.stop()
        return callback

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(
                content=TRANSLATIONS[self.language]["timeout_msg"], view=None
            )
        except Exception:
            pass


@bot.tree.command(name="create", description="🍣 Generate a Crunchyroll premium cookie")
async def create(interaction: discord.Interaction) -> None:
    await _start_create_flow(interaction)


@bot.tree.command(name="channel", description="📌 Set the text channel where the bot will work")
@app_commands.describe(channel="The text channel to designate as the bot's working channel")
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    lang = get_user_lang(interaction)
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    guild_id = interaction.guild.id
    guild_name = interaction.guild.name if interaction.guild else "Unknown"
    await config.set_allowed_channel(guild_id, channel.id, guild_name=guild_name, channel_name=channel.name)
    await interaction.followup.send(f"✅ Bot will now **only** respond in {channel.mention}.", ephemeral=True)
    await send_or_update_setup_messages(channel, guild_id)
    log.info(f"/channel set by {interaction.user} in guild {guild_id} → #{channel.name}")


@bot.tree.command(name="channel_log", description="📌 Set channel for Crunchyroll user activity log (Admin only)")
@app_commands.describe(channel="The text channel to receive activity posts")
async def channel_log(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    guild_id = interaction.guild.id
    now_iso = datetime.now(EGYPT_TZ).isoformat()
    channel_log_config.set_channel(guild_id, channel.id, sync_done=True, last_ts=now_iso)
    await interaction.response.send_message(
        f"✅ Log channel set to {channel.mention}. Only future activities will be posted.",
        ephemeral=True,
    )


@bot.tree.command(name="ban", description="🚫 Block a user by Discord ID (Admin only)")
@app_commands.describe(user_id="The Discord user ID to ban")
async def ban_user(interaction: discord.Interaction, user_id: str) -> None:
    lang = get_user_lang(interaction)
    if not is_admin(interaction.user.id) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.followup.send("❌ Invalid user ID.", ephemeral=True)
        return
    if is_user_banned(uid):
        await interaction.followup.send(f"⚠️ User `{uid}` is already banned.", ephemeral=True)
        return
    username = str(uid)
    try:
        target = await bot.fetch_user(uid)
        username = str(target)
    except Exception:
        pass
    _banned_user_ids.add(uid)
    _ban_attempt_counts.setdefault(uid, 0)
    ok = await asyncio.to_thread(add_ban_to_github, uid, username)
    msg = (
        f"✅ User `{username}` (ID: `{uid}`) has been **banned**."
        if ok else f"⚠️ User `{uid}` banned locally but **GitHub push failed**."
    )
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="unban", description="✅ Remove a bot ban (Admin only)")
@app_commands.describe(user_id="The Discord user ID to unban")
async def unban_user(interaction: discord.Interaction, user_id: str) -> None:
    lang = get_user_lang(interaction)
    if not is_admin(interaction.user.id) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.followup.send("❌ Invalid user ID.", ephemeral=True)
        return
    if not is_user_banned(uid):
        await interaction.followup.send(f"⚠️ User `{uid}` is not currently banned.", ephemeral=True)
        return
    _banned_user_ids.discard(uid)
    attempts = _ban_attempt_counts.pop(uid, 0)
    ok = await asyncio.to_thread(remove_ban_from_github, uid)
    msg = (
        f"✅ User `{uid}` has been **unbanned**. They had **{attempts}** blocked attempt(s)."
        if ok else f"⚠️ User `{uid}` unbanned locally but **GitHub push failed**."
    )
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="banserver", description="🚫 Ban a server from using the bot (Admin only)")
@app_commands.describe(guild_id="The Discord server ID to ban", reason="Reason for the ban")
async def ban_server(interaction: discord.Interaction, guild_id: str, reason: str = "Bot rule violation") -> None:
    lang = get_user_lang(interaction)
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        gid = int(guild_id.strip())
    except ValueError:
        await interaction.followup.send("❌ Invalid server ID.", ephemeral=True)
        return
    if is_server_banned(gid):
        await interaction.followup.send(f"⚠️ Server `{gid}` is already banned.", ephemeral=True)
        return
    guild_name = str(gid)
    target_guild = bot.get_guild(gid)
    if target_guild:
        guild_name = target_guild.name
    _banned_guild_ids.add(gid)
    ok = await asyncio.to_thread(add_server_ban_to_github, gid, guild_name, reason)
    msg = (
        f"✅ Server `{guild_name}` (ID: `{gid}`) has been **banned**.\n📋 Reason: {reason}"
        if ok else f"⚠️ Server `{gid}` banned locally but **GitHub push failed**."
    )
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="unbanserver", description="✅ Remove a bot ban from a server (Admin only)")
@app_commands.describe(guild_id="The Discord server ID to unban")
async def unban_server(interaction: discord.Interaction, guild_id: str) -> None:
    lang = get_user_lang(interaction)
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        gid = int(guild_id.strip())
    except ValueError:
        await interaction.followup.send("❌ Invalid server ID.", ephemeral=True)
        return
    if not is_server_banned(gid):
        await interaction.followup.send(f"⚠️ Server `{gid}` is not currently banned.", ephemeral=True)
        return
    _banned_guild_ids.discard(gid)
    ok = await asyncio.to_thread(remove_server_ban_from_github, gid)
    msg = (
        f"✅ Server `{gid}` has been **unbanned**."
        if ok else f"⚠️ Server `{gid}` unbanned locally but **GitHub push failed**."
    )
    await interaction.followup.send(msg, ephemeral=True)


admin_group = app_commands.Group(name="admin", description="👮 Manage bot admins")


@admin_group.command(name="add", description="👮 Add a bot admin")
@app_commands.describe(user_id="Discord user ID to grant admin access")
async def admin_add(interaction: discord.Interaction, user_id: str) -> None:
    lang = get_user_lang(interaction)
    if not is_owner(interaction.user.id):
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.followup.send("❌ Invalid user ID.", ephemeral=True)
        return
    if uid in _admin_registry:
        await interaction.followup.send(f"⚠️ User `{uid}` is already an admin.", ephemeral=True)
        return
    username = str(uid)
    try:
        target = await bot.fetch_user(uid)
        username = str(target)
    except Exception:
        pass
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    _admin_registry[uid] = {"username": username, "added_by": str(interaction.user), "added_at": now_str}
    ok = await asyncio.to_thread(save_admins_to_github, _admin_registry)
    msg = (
        f"✅ `{username}` (ID: `{uid}`) has been added as a **bot admin**."
        if ok else f"✅ `{uid}` added locally but **GitHub push failed**."
    )
    await interaction.followup.send(msg, ephemeral=True)


@admin_group.command(name="remove", description="👮 Remove a bot admin")
@app_commands.describe(user_id="Discord user ID to revoke admin access")
async def admin_remove(interaction: discord.Interaction, user_id: str) -> None:
    lang = get_user_lang(interaction)
    if interaction.user.id not in _PRIVILEGED_IDS:
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.followup.send("❌ Invalid user ID.", ephemeral=True)
        return
    if uid == BOT_OWNER_ID and interaction.user.id != BOT_OWNER_ID:
        await interaction.followup.send("❌ You cannot remove the bot owner.", ephemeral=True)
        return
    if uid not in _admin_registry:
        await interaction.followup.send(f"⚠️ User `{uid}` is not in the admin list.", ephemeral=True)
        return
    removed_info = _admin_registry.pop(uid)
    ok = await asyncio.to_thread(save_admins_to_github, _admin_registry)
    msg = (
        f"✅ `{removed_info['username']}` (ID: `{uid}`) has been **removed** from admins."
        if ok else f"✅ `{uid}` removed locally but **GitHub push failed**."
    )
    await interaction.followup.send(msg, ephemeral=True)


@admin_group.command(name="list", description="👮 List all current bot admins")
async def admin_list(interaction: discord.Interaction) -> None:
    lang = get_user_lang(interaction)
    if interaction.user.id not in _PRIVILEGED_IDS:
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    embed = discord.Embed(title="👮 Bot Admin List", color=CRUNCHYROLL_ORANGE, timestamp=datetime.now(EGYPT_TZ))
    embed.add_field(name=f"👑 X2 Salah (ID: {BOT_OWNER_ID})", value="Role: **Bot Owner** – permanent full access", inline=False)
    embed.add_field(name=f"⭐ HASHO_Z (ID: {BOT_COADMIN_ID})", value="Role: **Co-Admin**", inline=False)
    if _admin_registry:
        embed.add_field(name="─────────────", value="**Additional Admins:**", inline=False)
        for uid, info in _admin_registry.items():
            embed.add_field(
                name=f"{info['username']} (ID: {uid})",
                value=f"Added by: `{info['added_by']}`\nDate: `{info['added_at']}`",
                inline=False,
            )
    else:
        embed.add_field(name="─────────────", value="*No additional admins.*", inline=False)
    embed.set_footer(text=FOOTER_TEXT)
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(admin_group)


@bot.tree.command(name="stock", description="📊 Refresh the main message (Admin only)")
async def stock_refresh(interaction: discord.Interaction) -> None:
    lang = get_user_lang(interaction)
    if not is_admin(interaction.user.id) and not (
        interaction.guild and interaction.user.guild_permissions.administrator
    ):
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    guild_id = interaction.guild.id if interaction.guild else None
    if not guild_id:
        await interaction.response.send_message("❌ Must be used in a server.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await _refresh_stats_message(guild_id)
    await interaction.followup.send("✅ Main message has been refreshed.", ephemeral=True)


@bot.tree.command(
    name="check_all",
    description="Quick-validate all cookies (no login links), backup and delete invalids (Admin)",
)
async def check_all_cmd(interaction: discord.Interaction) -> None:
    lang = get_user_lang(interaction)
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    lock = _get_check_all_lock()
    if lock.locked():
        progress_msg = await interaction.followup.send(
            "⏳ Another check is already running. Waiting for it to finish, then starting yours…",
            ephemeral=True,
        )
    else:
        progress_msg = await interaction.followup.send(
            f"🔄 Quick-checking cookies (max {MAX_CONCURRENT_CHECKS} concurrent)...",
            ephemeral=True,
        )

    async def _progress(processed: int, total: int) -> None:
        try:
            await progress_msg.edit(content=f"🔄 Processing... {processed}/{total} files checked.")
        except Exception:
            pass

    stats = await run_check_all_pipeline(progress_callback=_progress)
    if stats["total"] == 0:
        await progress_msg.edit(content="❌ No cookie files found to check.")
        return
    if interaction.guild:
        await _refresh_stats_message(interaction.guild.id)
    await progress_msg.edit(content=None, embed=_build_check_all_report_embed(stats), view=None)
    log.info(f"/check_all completed by {interaction.user}: {stats}")


def is_allowed_channel(interaction: discord.Interaction) -> bool:
    guild_id = interaction.guild.id if interaction.guild else None
    if guild_id is None:
        return False
    channel_id = config.get_channel_for_guild(guild_id)
    if channel_id is None:
        return False
    return interaction.channel_id == channel_id


def _guild_allowed(guild_id: Optional[int]) -> bool:
    if not guild_id:
        return False
    if not ALLOWED_GUILD_IDS:
        return True
    return guild_id in ALLOWED_GUILD_IDS


async def global_interaction_check(interaction: discord.Interaction) -> bool:
    if is_user_banned(interaction.user.id):
        attempts = record_ban_attempt(interaction.user.id)
        msg = f"🚫 You have been banned from using this bot. (Attempt #{attempts})"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return False

    guild_id = interaction.guild.id if interaction.guild else None
    if guild_id and is_server_banned(guild_id):
        msg = "🚫 This server has been banned from using this bot."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return False

    if not _guild_allowed(guild_id):
        lang = get_user_lang(interaction)
        msg = TRANSLATIONS[lang]["wrong_guild"]
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return False

    return True


@bot.event
async def on_ready() -> None:
    log.info("━" * 60)
    log.info(f"Logged in as : {bot.user}  (ID: {bot.user.id})")
    log.info(f"Bot version  : {BOT_VERSION}")
    log.info(f"Cookie check limit : {COOKIE_CHECK_LIMIT} per {COOKIE_CHECK_WINDOW_HOURS}h (non-admins)")
    log.info(f"Cleanup delay      : {CLEANUP_DELAY_SECONDS}s ({CLEANUP_DELAY_SECONDS // 60} min)")
    log.info(f"Tutorial video URL : {TUTORIAL_VIDEO_URL}")
    if ALLOWED_GUILD_IDS:
        log.info(f"Guild restriction : {ALLOWED_GUILD_IDS}")
    else:
        log.info("Guild restriction : NONE (global bot)")

    if COOKIES_GITHUB_REPO:
        log.info(f"Cookie source : GitHub → {COOKIES_GITHUB_REPO}/{COOKIES_GITHUB_PATH} [{COOKIES_GITHUB_BRANCH}]")
    else:
        log.info(f"Cookie source : Local → {COOKIES_FOLDER.resolve()}")

    if BACKUP_GITHUB_REPO:
        log.info(f"Backup repository : {BACKUP_GITHUB_REPO}/{BACKUP_GITHUB_PATH} [{BACKUP_GITHUB_BRANCH}]")
    else:
        log.info("Backup repository : not configured")

    await config.init_db()

    global _banned_user_ids
    _banned_user_ids = await asyncio.to_thread(load_banned_users_from_github)
    log.info(f"Ban list: {len(_banned_user_ids)} banned user(s)")

    global _banned_guild_ids
    _banned_guild_ids = await asyncio.to_thread(load_banned_servers_from_github)
    log.info(f"Server ban list: {len(_banned_guild_ids)} banned server(s)")

    global _admin_registry
    _admin_registry = await asyncio.to_thread(load_admins_from_github)
    log.info(f"Admin list: {len(_admin_registry)} admin(s)")

    _load_setup_tracker()

    try:
        bot.add_view(MainMenuView())
        log.info("Registered persistent MainMenuView")
    except Exception as exc:
        log.warning(f"Could not register persistent MainMenuView: {exc}")

    if ALLOWED_GUILD_IDS:
        for gid in ALLOWED_GUILD_IDS:
            ch = config.get_channel_for_guild(gid)
            if ch:
                log.info(f"Guild {gid} → channel {ch}")
            else:
                log.warning(f"Guild {gid} → no channel configured (run /channel)")

    if GITHUB_REPO and GITHUB_FILE_PATH:
        log.info(f"GitHub log: {GITHUB_REPO}/{GITHUB_FILE_PATH}")
    log.info("━" * 60)

    bot.tree.interaction_check = global_interaction_check

    monitor.start()
    check_all_scheduler.start()

    if ALLOWED_GUILD_IDS:
        for guild_id in ALLOWED_GUILD_IDS:
            try:
                synced = await bot.tree.sync(guild=discord.Object(id=guild_id))
                log.info(f"Synced {len(synced)} command(s) to guild {guild_id}")
            except Exception as exc:
                log.error(f"Failed to sync commands to guild {guild_id}: {exc}")
    else:
        try:
            synced = await bot.tree.sync()
            log.info(f"Synced {len(synced)} command(s) globally")
        except Exception as exc:
            log.error(f"Failed to sync global commands: {exc}")

    guilds_to_refresh = ALLOWED_GUILD_IDS if ALLOWED_GUILD_IDS else [g.id for g in bot.guilds]
    for _gid in guilds_to_refresh:
        if config.get_channel_for_guild(_gid):
            try:
                await _refresh_stats_message(_gid)
            except Exception as _exc:
                log.warning(f"Startup stats refresh failed for guild {_gid}: {_exc}")


if __name__ == "__main__":
    COOKIES_FOLDER.mkdir(exist_ok=True)
    for folder in ("Fan", "Mega Fan", "Ultimate Fan"):
        (COOKIES_FOLDER / folder).mkdir(exist_ok=True)
    bot.run(DISCORD_BOT_TOKEN)
