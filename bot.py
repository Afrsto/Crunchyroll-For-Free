import os
import json
import time
import random
import asyncio
import logging
import tempfile
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from base64 import b64decode
from typing import Optional, List, Dict, Any, Tuple
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
import io

import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import requests
from github import Github, GithubException

try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

from crunchyroll_checker import check_cookie_file

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

if not DISCORD_BOT_TOKEN:
    raise ValueError("Missing DISCORD_TOKEN environment variable")

EGYPT_TZ = ZoneInfo("Africa/Cairo")
COOKIES_FOLDER = Path("cookies-2")
USER_LOG_FILE = Path("users.txt")
CONFIG_FILE = Path("config.json")
SETUP_TRACKER_FILE = Path("setup_messages.json")
GUILD_CONFIG_FILE = Path("guild_config.json")
SCRIPT_TIMEOUT = 30
CLEANUP_DELAY_SECONDS = 300
COOLDOWN_HOURS = 24

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
    branch = "main"
    path = ""
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

GITHUB_REPO, GITHUB_FILE_PATH = _parse_github_blob_url(REMOTE_LOG_URL)
CHANNEL_LOG_GITHUB_REPO, CHANNEL_LOG_GITHUB_PATH = _parse_github_blob_url(CHANNEL_LOG_URL)
BAN_USERS_GITHUB_REPO, BAN_USERS_GITHUB_PATH = _parse_github_blob_url(BAN_USERS_URL)
BAN_SERVERS_GITHUB_REPO, BAN_SERVERS_GITHUB_PATH = _parse_github_blob_url(BAN_SERVERS_URL)
ADMIN_USERS_GITHUB_REPO, ADMIN_USERS_GITHUB_PATH = _parse_github_blob_url(ADMIN_USERS_URL)
COOKIES_GITHUB_REPO, COOKIES_GITHUB_PATH, COOKIES_GITHUB_BRANCH = _parse_github_tree_url(COOKIES_REPO_URL)

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
        raw = b64decode(contents.content).decode("utf-8")
        return raw, contents.sha
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
_banned_user_ids: set[int] = set()
_ban_attempt_counts: Dict[int, int] = {}
_banned_guild_ids: set[int] = set()

def load_admins_from_github() -> Dict[int, Dict]:
    raw, _ = _read_github_file(ADMIN_USERS_GITHUB_REPO, ADMIN_USERS_GITHUB_PATH)
    registry = {}
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
    content = _serialize_admins(registry)
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M")
    return _write_github_file(
        ADMIN_USERS_GITHUB_REPO,
        ADMIN_USERS_GITHUB_PATH,
        content,
        f"👮 Update admin list [{now_str} EGY]",
    )

def load_banned_users_from_github() -> set[int]:
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
        BAN_USERS_GITHUB_REPO,
        BAN_USERS_GITHUB_PATH,
        content,
        f"🚫 Update ban list [{now_str} EGY]",
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
    if not removed:
        return True
    return _write_ban_list(new_lines)

def update_ban_attempts_on_github(user_id: int, attempts: int) -> None:
    if not BAN_USERS_GITHUB_REPO or not BAN_USERS_GITHUB_PATH:
        return
    raw, _ = _read_github_file(BAN_USERS_GITHUB_REPO, BAN_USERS_GITHUB_PATH)
    lines, updated = raw.splitlines(), False
    new_lines = []
    for ln in lines:
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

def load_banned_servers_from_github() -> set[int]:
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
        BAN_SERVERS_GITHUB_REPO,
        BAN_SERVERS_GITHUB_PATH,
        content,
        f"🚫 Update server ban list [{now_str} EGY]",
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
    safe_reason = reason.replace("|", "-")
    lines.append(
        f"{guild_id} | guild_name={guild_name} | reason={safe_reason} | banned_at={now_str}"
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
    if not removed:
        return True
    return _write_server_ban_list(new_lines)

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
    "ar-AE": "Asia/Dubai",        "ar-BH": "Asia/Bahrain",
    "ar-DZ": "Africa/Algiers",    "ar-EG": "Africa/Cairo",
    "ar-IQ": "Asia/Baghdad",      "ar-JO": "Asia/Amman",
    "ar-KW": "Asia/Kuwait",       "ar-LB": "Asia/Beirut",
    "ar-LY": "Africa/Tripoli",    "ar-MA": "Africa/Casablanca",
    "ar-OM": "Asia/Muscat",       "ar-QA": "Asia/Qatar",
    "ar-SA": "Asia/Riyadh",       "ar-SD": "Africa/Khartoum",
    "ar-SY": "Asia/Damascus",     "ar-TN": "Africa/Tunis",
    "ar-YE": "Asia/Aden",
    "en-US": "America/New_York",  "en-GB": "Europe/London",
    "en-AU": "Australia/Sydney",  "en-CA": "America/Toronto",
    "en-IN": "Asia/Kolkata",      "en-NZ": "Pacific/Auckland",
    "en-ZA": "Africa/Johannesburg",
    "fr-FR": "Europe/Paris",      "fr-BE": "Europe/Brussels",
    "fr-CH": "Europe/Zurich",     "fr-CA": "America/Toronto",
    "de-DE": "Europe/Berlin",     "de-AT": "Europe/Vienna",
    "de-CH": "Europe/Zurich",
    "es-ES": "Europe/Madrid",     "es-MX": "America/Mexico_City",
    "es-AR": "America/Argentina/Buenos_Aires",
    "tr-TR": "Europe/Istanbul",
    "ru-RU": "Europe/Moscow",
    "zh-CN": "Asia/Shanghai",     "zh-TW": "Asia/Taipei",
    "ja-JP": "Asia/Tokyo",
    "ko-KR": "Asia/Seoul",
    "pt-BR": "America/Sao_Paulo", "pt-PT": "Europe/Lisbon",
    "it-IT": "Europe/Rome",
    "nl-NL": "Europe/Amsterdam",  "nl-BE": "Europe/Brussels",
    "pl-PL": "Europe/Warsaw",
    "sv-SE": "Europe/Stockholm",
    "nb-NO": "Europe/Oslo",
    "da-DK": "Europe/Copenhagen",
    "fi-FI": "Europe/Helsinki",
    "cs-CZ": "Europe/Prague",
    "ro-RO": "Europe/Bucharest",
    "hu-HU": "Europe/Budapest",
    "el-GR": "Europe/Athens",
    "he-IL": "Asia/Jerusalem",
    "fa-IR": "Asia/Tehran",
    "hi-IN": "Asia/Kolkata",
    "id-ID": "Asia/Jakarta",
    "ms-MY": "Asia/Kuala_Lumpur",
    "th-TH": "Asia/Bangkok",
    "vi-VN": "Asia/Ho_Chi_Minh",
    "uk-UA": "Europe/Kiev",
}

def get_locale_info(locale_str: str) -> Tuple[str, str, str]:
    locale = str(locale_str)
    country = LOCALE_TO_COUNTRY.get(locale) or LOCALE_TO_COUNTRY.get(locale.split("-")[0], "Unknown")
    tz_key = LOCALE_TO_TZ.get(locale) or LOCALE_TO_TZ.get(locale.split("-")[0])
    if tz_key:
        try:
            tz = ZoneInfo(tz_key)
            local_time_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            local_time_str = "N/A"
            tz_key = "Unknown"
    else:
        local_time_str = "N/A"
        tz_key = "Unknown"
    return country, local_time_str, tz_key

TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "en": {
        "lang_prompt": "🌐 **Please select your language:**\n🌐 **الرجاء اختيار اللغة:**",
        "lang_selected": "✅ Language selected: **English**",
        "confirm_prompt": "🍣 **Please choose your Crunchyroll plan**\n",
        "device_prompt": "📱 **Choose your device:**",
        "pc_label": "PC",
        "phone_label": "Phone",
        "tv_label": "TV",
        "progress": "⏳ **Generating your Crunchyroll cookie… please wait.**",
        "no_cookies_folder": "❌ Cookies folder not found. Please contact the administrator.",
        "no_cookie_files": "❌ No accounts available right now. Please try again later.",
        "timeout": "⌛ Validation took too long. Please try again later.",
        "unexpected_error": "⚠️ An unexpected error occurred. Please try again.",
        "cookie_invalid": "❌ The selected session is invalid or expired. Please try again.",
        "success_title": "✅ 🍣 Crunchyroll Premium Cookie Ready!",
        "success_desc": "🍪 Your cookie is below. Copy it and import it into your browser.",
        "footer": "⚠️ This cookie is for personal use only – do not share it.",
        "fan_label": "Fan 🎈",
        "ultimate_fan_label": "Ultimate Fan 🚀",
        "mega_fan_label": "Mega Fan 💎",
        "cancelled": "🚫 Process cancelled.",
        "not_for_you": "🚫 You cannot interact with this menu.",
        "timeout_msg": "⏰ Request timed out due to inactivity.",
        "wrong_channel_no_config": "⚠️ No channel configured. Admins must run `/channel` first.",
        "wrong_channel_with_config": "❌ This command can only be used in {channel}.",
        "wrong_guild": "❌ This bot is not available in this server.",
        "not_admin": "❌ You do not have permission to use this command.",
        "cooldown": "⏳ You already generated a cookie recently.\n\n⌛ Please wait **{hours}h {minutes}m** before creating another one.",
        "retry_prompt": "❌ The attempt failed. Please try again.\n\n❌ فشلت المحاولة. يرجى المحاولة مرة أخرى.\n\n🔄 **Try Again | حاول مرة أخرى**",
        "retry_button": "🔄 Try Again | حاول مرة أخرى",
        "setup_desc": (
            "Welcome! 👋 Use the `/create` command to generate a Crunchyroll premium cookie.\n\n"
            "**📋 How to use:**\n"
            "1️⃣  Type `/create` in this channel.\n"
            "2️⃣  Select your preferred language.\n"
            "3️⃣  Choose your plan: **Fan**, **Mega Fan**, or **Ultimate Fan**.\n"
            "4️⃣  Choose your device: **PC**, **Phone**, or **TV**.\n"
            "5️⃣  Wait a few seconds for your cookie.\n\n"
            "*⚠️ Note: Cookies are single-use. Messages auto-delete after 5 minutes for privacy.*"
        ),
        "account_inactive": "❌ This account is not currently active or cannot generate a valid cookie. It may be unsubscribed or expired.",
        "validation_failed": "❌ Could not validate the account. Please try again later.",
        "failure": "❌ Failure",
        "tv_instruction": (
            "📺 **TV Activation Instructions**\n\n"
            "1️⃣  Open **https://www.crunchyroll.com/activate** on your browser.\n"
            "2️⃣  Enter the activation code shown on your TV screen.\n"
            "3️⃣  The cookie will be automatically linked to your TV session.\n\n"
            "💡 If you don't see a code, make sure your TV app is up to date."
        ),
        "cookie_editor_instruction": (
            "🍪 **Copy the cookie header below** and use it to authenticate.\n"
            "You can add these cookies manually using a browser extension like **Cookie-Editor**.\n"
            "🔗 **Download it from:** <https://cookie-editor.com/>"
        ),
    },
    "ar": {
        "lang_prompt": "🌐 **Please select your language:**\n🌐 **الرجاء اختيار اللغة:**",
        "lang_selected": "\u200f✅ تم اختيار اللغة: **العربية**",
        "confirm_prompt": "\u200f🍣 **يرجى اختيار باقة كرانشيرول**\n",
        "device_prompt": "\u200f📱 **اختر جهازك:**",
        "pc_label": "PC",
        "phone_label": "Phone",
        "tv_label": "TV",
        "progress": "\u200f⏳ **جاري إنشاء الكوكي الخاص بك… يرجى الانتظار.**",
        "no_cookies_folder": "\u200f❌ مجلد الكوكيز غير موجود. يرجى الاتصال بالمسؤول.",
        "no_cookie_files": "\u200f❌ لا توجد حسابات متاحة حالياً. حاول لاحقاً.",
        "timeout": "\u200f⌛ استغرق التحقق وقتاً طويلاً. يرجى المحاولة مرة أخرى.",
        "unexpected_error": "\u200f⚠️ حدث خطأ غير متوقع.",
        "cookie_invalid": "\u200f❌ الحساب المختار غير صالح أو منتهي الصلاحية. حاول مجدداً.",
        "success_title": "\u200f✅ 🍣 كوكي كرانشيرول بريميوم جاهز!",
        "success_desc": "\u200f🍪 الكوكي موجود أدناه. انسخه واستورده في متصفحك.",
        "footer": "\u200f⚠️ هذا الكوكي للاستخدام الشخصي فقط – يُمنع مشاركته.",
        "fan_label": "Fan 🎈",
        "ultimate_fan_label": "Ultimate Fan 🚀",
        "mega_fan_label": "Mega Fan 💎",
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
        "setup_desc": (
            "مرحباً! 👋 استخدم أمر `/create` للحصول على كوكي كرانشيرول بريميوم.\n\n"
            "**📋 طريقة الاستخدام:**\n"
            "1️⃣  اكتب `/create` في هذه القناة.\n"
            "2️⃣  اختر لغتك المفضلة.\n"
            "3️⃣  اختر الباقة: **Fan** أو **Mega Fan** أو **Ultimate Fan**.\n"
            "4️⃣  اختر جهازك: **PC** أو **Phone** أو **TV**.\n"
            "5️⃣  انتظر بضع ثوانٍ للحصول على الكوكي.\n\n"
            "\u200f*⚠️ ملاحظة: الكوكيز للاستخدام مرة واحدة. يتم حذف الرسائل تلقائياً بعد 5 دقائق.*"
        ),
        "account_inactive": "❌ هذا الحساب غير نشط حاليًا أو لا يمكن إنشاء كوكي صالح. قد يكون غير مشترك أو منتهي الصلاحية.",
        "validation_failed": "❌ تعذر التحقق من الحساب. يرجى المحاولة مرة أخرى لاحقًا.",
        "failure": "❌ فشل",
        "tv_instruction": (
            "\u200f📺 **تعليمات تفعيل التلفاز**\n\n"
            "1️⃣  افتح **https://www.crunchyroll.com/activate** في متصفحك.\n"
            "2️⃣  أدخل رمز التفعيل الظاهر على شاشة التلفاز.\n"
            "3️⃣  سيتم ربط الكوكي تلقائياً بجلسة التلفاز الخاصة بك.\n\n"
            "💡 إذا لم يظهر رمز، تأكد من تحديث تطبيق التلفاز."
        ),
        "cookie_editor_instruction": (
            "🍪 **انسخ رأس الكوكي أدناه** واستخدمه للمصادقة.\n"
            "يمكنك إضافة هذه الكوكيز يدويًا باستخدام إضافة متصفح مثل **Cookie-Editor**.\n"
            "🔗 **حمّلها من:** <https://cookie-editor.com/>"
        ),
    }
}

def get_user_lang(interaction: discord.Interaction) -> str:
    return "ar" if str(interaction.locale).startswith("ar") else "en"

def parse_netscape_to_cookie_header(content: str) -> str:
    """Convert Netscape cookie file content to a Cookie header string."""
    pairs = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split('\t')
        # Netscape format: domain, flag, path, secure, expiration, name, value
        if len(parts) >= 7:
            name = parts[5].strip()
            value = parts[6].strip()
            if name and value:
                pairs.append(f"{name}={value}")
        else:
            # Fallback: try to parse as key=value pairs separated by semicolons
            for token in line.split(';'):
                token = token.strip()
                if '=' in token:
                    k, v = token.split('=', 1)
                    if k and v:
                        pairs.append(f"{k.strip()}={v.strip()}")
    return "; ".join(pairs)

class ChannelLogConfig:
    def __init__(self, file_path: Path = GUILD_CONFIG_FILE):
        self.file_path = file_path
        self.data: Dict[str, Any] = {}
        self._load()

    def _load(self):
        if self.file_path.exists():
            try:
                with open(self.file_path, 'r') as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}
        else:
            self.data = {"guilds": {}, "last_fetch": None}

    def _save(self):
        try:
            with open(self.file_path, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            log.error(f"Failed to save guild_config: {e}")

    def get_guild_config(self, guild_id: int) -> Dict[str, Any]:
        return self.data["guilds"].get(str(guild_id), {})

    def set_guild_config(self, guild_id: int, config: Dict[str, Any]):
        self.data["guilds"][str(guild_id)] = config
        self._save()

    def get_channel_id(self, guild_id: int) -> Optional[int]:
        cfg = self.get_guild_config(guild_id)
        return cfg.get("channel_id")

    def set_channel(self, guild_id: int, channel_id: int, sync_done: bool = False, last_ts: Optional[str] = None):
        cfg = {
            "channel_id": channel_id,
            "sync_done": sync_done,
            "last_timestamp": last_ts,
        }
        self.set_guild_config(guild_id, cfg)

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
    files_used: str

    @classmethod
    def from_line(cls, line: str) -> Optional["LogEntry"]:
        pattern = re.compile(
            r"^\[(?P<timestamp>[^\]]+)\]\s+"
            r"👤\s+User:\s+(?P<user>[^\s(]+)\s+\(Display:\s+(?P<display>[^)]+)\)\s+\|\s+"
            r"🆔\s+ID:\s+(?P<user_id>\d+)\s+\|\s+"
            r"🎁\s+Plan:\s+(?P<plan>[^|]+)\|\s+"
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
            files_used=data["files_used"].strip(),
        )

class LogFetcher:
    def __init__(self, url: str = ""):
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
                    entries = []
                    for line in text.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        entry = LogEntry.from_line(line)
                        if entry:
                            entries.append(entry)
                    log.info(f"Fetched {len(entries)} entries from remote log")
                    return entries
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning(f"Attempt {attempt+1}/{retries} failed: {e}")
                await asyncio.sleep(2 ** attempt)
        log.error("Failed to fetch remote log after retries")
        return []

class CrunchyrollMonitor:
    def __init__(self, bot: commands.Bot, config: ChannelLogConfig, remote_log_url: Optional[str] = None):
        self.bot = bot
        self.config = config
        self.remote_log_url = remote_log_url or ""
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
        if not self.remote_log_url:
            return
        async with LogFetcher(self.remote_log_url) as fetcher:
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

        new_entries = []
        if last_fetch:
            for e in all_entries:
                if e.timestamp > last_fetch:
                    new_entries.append(e)
        else:
            new_entries = all_entries

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

            guild_new = []
            for e in new_entries:
                if guild_last is None or e.timestamp > guild_last:
                    guild_new.append(e)

            if not guild_new:
                continue

            for entry in guild_new:
                embed = self._build_embed(entry)
                try:
                    await channel.send(embed=embed)
                    log.info(f"Posted log entry for {entry.user} in guild {guild_id}")
                except discord.Forbidden:
                    log.warning(f"No permission to post in channel {channel_id}")
                    break
                except Exception as e:
                    log.error(f"Failed to post embed: {e}")

            if guild_new:
                latest = max(e.timestamp for e in guild_new)
                self.config.set_guild_config(guild_id, {
                    "channel_id": channel_id,
                    "sync_done": True,
                    "last_timestamp": latest.isoformat()
                })

        if all_entries:
            latest_all = max(e.timestamp for e in all_entries)
            self.config.set_last_fetch(latest_all.isoformat())

    def _build_embed(self, entry: LogEntry) -> discord.Embed:
        embed = discord.Embed(
            title="🍣 User Activity Log",
            color=self._embed_color,
            timestamp=entry.timestamp,
        )
        embed.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/commons/thumb/7/7b/Crunchyroll_Logo.png/320px-Crunchyroll_Logo.png")

        fields = [
            ("👤 User", f"{entry.user} ({entry.display_name})", True),
            ("🆔 ID", str(entry.user_id), True),
            ("📌 Date of Use", entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"), True),
            ("🎁 Plan", entry.plan, True),
            ("🏠 Server", entry.server, True),
            ("🌐 Language", entry.language, True),
            ("Days Left", entry.days_left if hasattr(entry, 'days_left') else "N/A", True),
            ("💬 Channel", f"#{entry.channel}", True),
            ("📄 Files Used", entry.files_used, True),
            ("💻 Device", entry.device, True),
            ("🔎 Result", entry.result, True),
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
monitor = CrunchyrollMonitor(bot, channel_log_config, remote_log_url=REMOTE_LOG_URL)

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
                log.info(f"Restored from GitHub logs.txt: guild {gid} → channel {cid}")

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
            log.error(f"Failed to read config.json: {exc}")

    def _save_to_file(self) -> None:
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump({"guilds": self.guilds}, f, indent=2)
        except Exception as exc:
            log.warning(f"Could not save config.json: {exc}")

    def get_channel_for_guild(self, guild_id: int) -> Optional[int]:
        guild_key = str(guild_id)
        if guild_key in self.guilds:
            return self.guilds[guild_key]
        if self.allowed_channel_id:
            return self.allowed_channel_id
        return DEFAULT_CHANNEL_ID

    async def set_allowed_channel(
        self,
        guild_id: int,
        channel_id: int,
        guild_name: str = "Unknown",
        channel_name: str = "Unknown",
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
        CHANNEL_LOG_GITHUB_REPO,
        CHANNEL_LOG_GITHUB_PATH,
        "".join(lines),
        f"📌 Update channel link: guild {guild_id} → channel {channel_id}",
    )

def load_channel_links_from_github() -> Dict[str, int]:
    if not CHANNEL_LOG_GITHUB_REPO or not CHANNEL_LOG_GITHUB_PATH:
        return {}
    raw, _ = _read_github_file(CHANNEL_LOG_GITHUB_REPO, CHANNEL_LOG_GITHUB_PATH)
    result = {}
    for line in raw.splitlines():
        if not line.startswith("CHANNEL_LINK"):
            continue
        try:
            parts = {kv.split("=", 1)[0].strip(): kv.split("=", 1)[1].strip()
                     for kv in line.split("|")[1:] if "=" in kv}
            result[parts["guild_id"]] = int(parts["channel_id"])
        except Exception:
            continue
    log.info(f"Loaded {len(result)} channel link(s) from GitHub logs.txt")
    return result

config = Config()

PLAN_FOLDER_MAP = {
    "fan": "Fan",
    "mega_fan": "Mega Fan",
    "ultimate_fan": "Ultimate Fan",
}

def _count_txt_files_in_folder(plan_folder: str) -> int:
    if COOKIES_GITHUB_REPO and COOKIES_GITHUB_PATH is not None:
        base_path = (COOKIES_GITHUB_PATH.rstrip("/") + "/" + plan_folder) if COOKIES_GITHUB_PATH else plan_folder
        try:
            names = _fetch_github_cookie_list_in_path(base_path)
            return len(names)
        except Exception:
            pass
    local_dir = COOKIES_FOLDER / plan_folder
    if not local_dir.exists():
        return 0
    return len(list(local_dir.glob("*.txt")))

async def _build_stats_embed() -> discord.Embed:
    fan_count, mega_count, ultimate_count = await asyncio.gather(
        asyncio.to_thread(_count_txt_files_in_folder, "Fan"),
        asyncio.to_thread(_count_txt_files_in_folder, "Mega Fan"),
        asyncio.to_thread(_count_txt_files_in_folder, "Ultimate Fan"),
    )
    embed = discord.Embed(
        title="📊 Account Stock  |  المخزون الحالي",
        color=discord.Color.from_rgb(247, 120, 23),
        timestamp=datetime.now(EGYPT_TZ),
    )
    embed.add_field(
        name="🎈 Fan",
        value=f"**{fan_count}** account(s) available",
        inline=True,
    )
    embed.add_field(
        name="💎 Mega Fan",
        value=f"**{mega_count}** account(s) available",
        inline=True,
    )
    embed.add_field(
        name="🚀 Ultimate Fan",
        value=f"**{ultimate_count}** account(s) available",
        inline=True,
    )
    embed.set_footer(text="⚡ X2 Salah Utility  •  Crunchyroll Bot 🍣")
    return embed

_used_cookie_files: List[Path] = []
_used_github_cookie_names: List[str] = []
_cookies_repo_cache = None

def pick_cookie_file(txt_files: List[Path]) -> Path:
    global _used_cookie_files
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

async def log_user_activity(
    interaction: discord.Interaction,
    condition: str,
    result: str,
    used_txt_files: Optional[List[str]] = None,
    language: Optional[str] = None,
    plan_key: Optional[str] = None,
    device: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> None:
    if timestamp is None:
        timestamp = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    now_egypt = timestamp

    user = interaction.user
    guild = interaction.guild
    locale_str = str(interaction.locale) if interaction.locale else "en"
    country_name, local_time, local_tz = get_locale_info(locale_str)

    channel_name = getattr(interaction.channel, "name", "N/A")
    lang_label = {"ar": "Arabic 🇸🇦", "en": "English 🇬🇧"}.get(language or "", language or "N/A")
    device_label = {"pc": "PC", "phone": "Phone", "tv": "TV"}.get(device or "", "N/A")
    plan_display = PLAN_FOLDER_MAP.get(plan_key or "", "N/A")

    files_used_display = ", ".join(used_txt_files) if used_txt_files else "N/A"

    line = (
        f"[{now_egypt} EGY] "
        f"👤 User: {user} (Display: {user.display_name}) | "
        f"🆔 ID: {user.id} | "
        f"🎁 Plan: {plan_display} | "
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
        GITHUB_REPO,
        GITHUB_FILE_PATH,
        content,
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
        remaining = COOLDOWN_HOURS - elapsed_hours
        return True, remaining
    return False, 0.0

_setup_message_ids: Dict[int, Dict[str, int]] = {}

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
FOOTER_TEXT = "⚡ X2 Salah Utility  •  Crunchyroll Bot 🍣"
CRUNCHYROLL_LOGO = "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7b/Crunchyroll_Logo.png/320px-Crunchyroll_Logo.png"

def _build_welcome_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🍣 Crunchyroll Cookie Generator  |  مولد كوكيز كرانشيرول",
        color=CRUNCHYROLL_ORANGE,
        timestamp=datetime.now(EGYPT_TZ),
    )
    embed.set_thumbnail(url=CRUNCHYROLL_LOGO)
    embed.add_field(
        name="🇬🇧 Welcome | مرحباً 🇸🇦",
        value=(
            "Welcome to the **Crunchyroll Cookie Generator**! 👋\n"
            "This bot generates a personal Crunchyroll premium cookie on demand.\n\n"
            "مرحباً بك في **مولد كوكيز كرانشيرول**! 👋\n"
            "يقوم هذا البوت بإنشاء كوكي شخصي لكرانشيرول عند الطلب."
        ),
        inline=False,
    )
    embed.add_field(
        name="📋 How to use | طريقة الاستخدام",
        value=(
            "**🇬🇧 English Steps:**\n"
            "1️⃣  Type `/create` in this channel\n"
            "2️⃣  Select your language\n"
            "3️⃣  Choose your plan (**Fan**, **Mega Fan**, or **Ultimate Fan**)\n"
            "4️⃣  Choose your device (**PC**, **Phone**, or **TV**)\n"
            "5️⃣  Receive your cookie and use it to log in\n\n"
            "**🇸🇦 الخطوات بالعربي:**\n"
            "1️⃣  اكتب `/create` في هذه القناة\n"
            "2️⃣  اختر لغتك\n"
            "3️⃣  اختر الباقة (**Fan** أو **Mega Fan** أو **Ultimate Fan**)\n"
            "4️⃣  اختر جهازك (**PC** أو **Phone** أو **TV**)\n"
            "5️⃣  احصل على الكوكي واستخدمه لتسجيل الدخول"
        ),
        inline=False,
    )
    embed.add_field(
        name="🛠️ Available Commands | الأوامر المتاحة",
        value=(
            "`/create` – Generate a Crunchyroll cookie | إنشاء كوكي كرانشيرول\n"
            "`/ban` – Block a user (Admin) | حظر مستخدم (مسؤول)\n"
            "`/unban` – Unblock a user (Admin) | رفع الحظر (مسؤول)\n"
            "`/banserver` – Ban a server (Admin) | حظر سيرفر (مسؤول)\n"
            "`/unbanserver` – Unban a server (Admin) | رفع حظر سيرفر (مسؤول)\n"
            "`/channel` – Set bot channel | تعيين قناة البوت\n"
            "`/channel_log` – Set activity log channel | تعيين قناة سجل النشاط\n"
            "`/admin` – Manage bot admins (Admin) | إدارة المشرفين (مسؤول)"
        ),
        inline=False,
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed

def _build_rules_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📜 Rules & Guidelines  |  القواعد والإرشادات",
        color=discord.Color.from_rgb(230, 126, 34),
        timestamp=datetime.now(EGYPT_TZ),
    )
    embed.add_field(
        name="🇬🇧 Rules (English)",
        value=(
            "🚫 **Rule 1:** Any suspicious activity will result in a permanent ban.\n\n"
            "✅ **Rule 2:** Cookies are for personal use only – do **not** share them.\n\n"
            "🔄 **Rule 3:** Messages auto-delete after **5 minutes** for privacy."
        ),
        inline=False,
    )
    embed.add_field(
        name="🇸🇦 القواعد (العربية)",
        value=(
            "🚫 **القاعدة 1:** أي نشاط مشبوه يؤدي إلى حظر دائم.\n\n"
            "✅ **القاعدة 2:** الكوكيز للاستخدام الشخصي فقط – يُمنع مشاركتها.\n\n"
            "🔄 **القاعدة 3:** يتم حذف الرسائل تلقائياً بعد **5 دقائق** لحماية الخصوصية."
        ),
        inline=False,
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed

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
                log.info(f"Found existing '{title_prefix}' message {msg.id} via history scan")
                return msg
    except Exception as exc:
        log.warning(f"History scan failed: {exc}")
    return None

async def send_or_update_setup_messages(channel: discord.TextChannel, guild_id: int) -> None:
    stored = _setup_message_ids.get(guild_id, {})
    welcome_embed = _build_welcome_embed()
    rules_embed = _build_rules_embed()
    stats_embed = await _build_stats_embed()

    welcome_msg = await _fetch_or_scan(channel, stored.get("welcome"), "🍣 Crunchyroll Cookie Generator")
    if welcome_msg:
        try:
            await welcome_msg.edit(embed=welcome_embed)
            log.info(f"Updated welcome message {welcome_msg.id}")
        except Exception as exc:
            log.warning(f"Could not update welcome message: {exc}")
            welcome_msg = None
    if welcome_msg is None:
        try:
            welcome_msg = await channel.send(embed=welcome_embed)
            await welcome_msg.pin()
            log.info(f"Pinned welcome message {welcome_msg.id} in #{channel.name}")
        except discord.Forbidden:
            log.warning(f"No permission to send/pin in #{channel.name}")
            return
        except Exception as exc:
            log.error(f"Failed to send welcome message: {exc}")
            return

    rules_msg = await _fetch_or_scan(channel, stored.get("rules"), "📜 Rules & Guidelines")
    if rules_msg:
        try:
            await rules_msg.edit(embed=rules_embed)
            log.info(f"Updated rules message {rules_msg.id}")
        except Exception as exc:
            log.warning(f"Could not update rules message: {exc}")
            rules_msg = None
    if rules_msg is None:
        try:
            rules_msg = await channel.send(embed=rules_embed)
            await rules_msg.pin()
            log.info(f"Pinned rules message {rules_msg.id} in #{channel.name}")
        except Exception as exc:
            log.error(f"Failed to send rules message: {exc}")

    stats_msg = await _fetch_or_scan(channel, stored.get("stats"), "📊 Account Stock")
    if stats_msg:
        try:
            await stats_msg.edit(embed=stats_embed)
            log.info(f"Updated stats message {stats_msg.id}")
        except Exception as exc:
            log.warning(f"Could not update stats message: {exc}")
            stats_msg = None
    if stats_msg is None:
        try:
            stats_msg = await channel.send(embed=stats_embed)
            try:
                await stats_msg.pin()
            except Exception:
                pass
            log.info(f"Sent stats message {stats_msg.id} in #{channel.name}")
        except Exception as exc:
            log.error(f"Failed to send stats message: {exc}")

    _setup_message_ids[guild_id] = {
        "welcome": welcome_msg.id if welcome_msg else None,
        "rules": rules_msg.id if rules_msg else None,
        "stats": stats_msg.id if stats_msg else None,
    }
    _save_setup_tracker()

async def _refresh_stats_message(guild_id: int) -> None:
    channel_id = config.get_channel_for_guild(guild_id)
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if not channel:
        return
    stored = _setup_message_ids.get(guild_id, {})
    stats_msg_id = stored.get("stats")
    stats_embed = await _build_stats_embed()
    if stats_msg_id:
        try:
            stats_msg = await channel.fetch_message(stats_msg_id)
            await stats_msg.edit(embed=stats_embed)
            log.info(f"Refreshed stats message {stats_msg_id} for guild {guild_id}")
            return
        except discord.NotFound:
            log.info(f"Tracked stats message {stats_msg_id} gone – scanning history")
            _setup_message_ids.setdefault(guild_id, {})["stats"] = None
        except Exception as exc:
            log.warning(f"Could not refresh stats message: {exc}")
            return
    try:
        async for msg in channel.history(limit=50):
            if msg.author != bot.user:
                continue
            if msg.embeds and msg.embeds[0].title and msg.embeds[0].title.startswith("📊 Account Stock"):
                await msg.edit(embed=stats_embed)
                _setup_message_ids.setdefault(guild_id, {})["stats"] = msg.id
                _save_setup_tracker()
                log.info(f"Found and refreshed existing stats message {msg.id} in guild {guild_id}")
                return
    except Exception as exc:
        log.warning(f"History scan failed for guild {guild_id}: {exc}")
    try:
        new_msg = await channel.send(embed=stats_embed)
        try:
            await new_msg.pin()
        except Exception:
            pass
        _setup_message_ids.setdefault(guild_id, {})["stats"] = new_msg.id
        _save_setup_tracker()
        log.info(f"Re-sent and pinned stats message {new_msg.id} for guild {guild_id}")
    except Exception as exc:
        log.error(f"Failed to re-send stats message: {exc}")

_cleanup_tasks: Dict[int, asyncio.Task] = {}

async def schedule_cleanup(
    interaction: discord.Interaction,
    messages: List[discord.WebhookMessage],
    delay: int = CLEANUP_DELAY_SECONDS
) -> None:
    if interaction.id in _cleanup_tasks:
        _cleanup_tasks[interaction.id].cancel()
        del _cleanup_tasks[interaction.id]

    async def _cleanup():
        await asyncio.sleep(delay)
        try:
            await interaction.delete_original_response()
        except Exception:
            pass
        for msg in messages:
            try:
                await msg.delete()
            except Exception:
                pass
        _cleanup_tasks.pop(interaction.id, None)
        log.debug(f"Cleanup done for interaction {interaction.id}")

    task = asyncio.create_task(_cleanup())
    _cleanup_tasks[interaction.id] = task

async def send_activity_log(
    interaction: discord.Interaction,
    plan_display: str,
    device: str,
    files_used: List[str],
    info: Dict[str, Any],
    status: str = "Success",
    result: str = "Cookie generated"
) -> None:
    guild = interaction.guild
    if not guild:
        return
    channel_id = channel_log_config.get_channel_id(guild.id)
    if not channel_id:
        return
    log_channel = bot.get_channel(channel_id)
    if not log_channel:
        return

    days_left_str = str(info.get("days_left", "N/A"))

    embed = discord.Embed(
        title="🍣 User Activity Log",
        color=CRUNCHYROLL_ORANGE,
        timestamp=datetime.now(EGYPT_TZ),
    )

    embed.set_thumbnail(url=interaction.user.display_avatar.url)

    embed.add_field(
        name="👤 User",
        value=f"{interaction.user.mention} ({interaction.user.display_name})",
        inline=True
    )
    embed.add_field(name="🆔 ID", value=str(interaction.user.id), inline=True)
    embed.add_field(
        name="📌 Date of Use",
        value=datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        inline=True
    )
    embed.add_field(name="🎁 Plan", value=plan_display, inline=True)
    embed.add_field(name="🏠 Server", value=guild.name, inline=True)
    embed.add_field(name="🌐 Language", value=get_user_lang(interaction).upper(), inline=True)
    embed.add_field(name="Days Left", value=days_left_str, inline=True)
    embed.add_field(
        name="💬 Channel",
        value=interaction.channel.mention,
        inline=True
    )
    embed.add_field(
        name="📄 Files Used",
        value=", ".join(files_used) if files_used else "N/A",
        inline=True
    )
    embed.add_field(name="💻 Device", value=device.capitalize(), inline=True)
    embed.add_field(name="🔎 Result", value=result, inline=True)
    embed.add_field(name="📊 Status", value=status, inline=True)

    embed.set_footer(
        text=f"X2 Salah Utility • User Activity • {datetime.now(EGYPT_TZ).strftime('%I:%M %p')}",
        icon_url=interaction.user.display_avatar.url
    )

    try:
        await log_channel.send(embed=embed)
        log.info(f"Activity log sent to {log_channel.name} for user {interaction.user}")
    except Exception as e:
        log.error(f"Failed to send activity log: {e}")

async def _delete_failed_cookie(plan_folder: str, filename: str) -> None:
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
                return
        except Exception as exc:
            log.warning(f"Could not delete GitHub cookie {file_path}: {exc}")
    local_path = COOKIES_FOLDER / plan_folder / filename
    if local_path.exists():
        try:
            local_path.unlink()
            log.info(f"Deleted failed local cookie: {local_path}")
        except Exception as exc:
            log.warning(f"Could not delete local cookie {local_path}: {exc}")

async def _delete_and_refresh(plan_folder: str, filename: str, guild_id: Optional[int]) -> None:
    await _delete_failed_cookie(plan_folder, filename)
    if guild_id:
        await _refresh_stats_message(guild_id)

async def _generate_and_send_cookie(
    interaction: discord.Interaction,
    language: str,
    plan_key: str,
    device: str,
    messages_to_delete: List[discord.WebhookMessage],
    lang_message: Optional[discord.Message] = None,
    confirm_message: Optional[discord.Message] = None,
) -> None:
    lang = language
    t = TRANSLATIONS[lang]
    plan_folder = PLAN_FOLDER_MAP[plan_key]

    chosen_file_name: Optional[str] = None
    cookie_content: Optional[str] = None
    tmp_path: Optional[str] = None

    if COOKIES_GITHUB_REPO and COOKIES_GITHUB_PATH is not None:
        base_path = (COOKIES_GITHUB_PATH.rstrip("/") + "/" + plan_folder) if COOKIES_GITHUB_PATH else plan_folder
        github_names = await asyncio.to_thread(_fetch_github_cookie_list_in_path, base_path)
        if github_names:
            chosen_file_name = await asyncio.to_thread(pick_github_cookie_rotation, github_names)
            cookie_content = await asyncio.to_thread(_fetch_github_cookie_content_in_path, base_path, chosen_file_name)
            if cookie_content is None:
                chosen_file_name = None

    if cookie_content is None:
        local_dir = COOKIES_FOLDER / plan_folder
        if not local_dir.exists():
            local_dir = COOKIES_FOLDER
        if not local_dir.exists():
            await interaction.edit_original_response(content=t["no_cookies_folder"])
            await log_user_activity(interaction, "Error", "Cookies folder missing", language=lang, device=device)
            await schedule_cleanup(interaction, messages_to_delete)
            return
        txt_files = list(local_dir.glob("*.txt"))
        if not txt_files:
            await interaction.edit_original_response(content=t["no_cookie_files"])
            await log_user_activity(interaction, "Error", "No cookie files", language=lang, device=device)
            await schedule_cleanup(interaction, messages_to_delete)
            return
        chosen_path = pick_cookie_file(txt_files)
        chosen_file_name = chosen_path.name
        try:
            cookie_content = chosen_path.read_text(encoding="utf-8")
        except Exception as exc:
            log.error(f"Failed to read local cookie: {exc}")
            await interaction.edit_original_response(content=t["unexpected_error"])
            await schedule_cleanup(interaction, messages_to_delete)
            return

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
            tmp.write(cookie_content)
            tmp_path = tmp.name
    except Exception as exc:
        log.error(f"Failed to write temp cookie file: {exc}")
        await interaction.edit_original_response(content=t["unexpected_error"])
        await schedule_cleanup(interaction, messages_to_delete)
        return

    try:
        _, info = await asyncio.wait_for(
            asyncio.to_thread(check_cookie_file, tmp_path),
            timeout=SCRIPT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        await interaction.edit_original_response(content=t["timeout"])
        await log_user_activity(
            interaction, "Timeout", "Validation timeout",
            used_txt_files=[chosen_file_name], language=lang, plan_key=plan_key, device=device,
        )
        await schedule_cleanup(interaction, messages_to_delete)
        return
    except Exception as exc:
        log.error(f"Checker error: {exc}")
        await interaction.edit_original_response(content=t["unexpected_error"])
        await log_user_activity(
            interaction, "Error", f"Exception: {str(exc)[:80]}",
            used_txt_files=[chosen_file_name], language=lang, plan_key=plan_key, device=device,
        )
        await schedule_cleanup(interaction, messages_to_delete)
        return
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    if info:
        # Edit the original response to indicate success
        try:
            await interaction.edit_original_response(
                content="✅ **Cookie generated successfully!** The cookie header is below (only visible to you).",
                embed=None,
                view=None,
            )
        except Exception as e:
            log.error(f"Failed to edit original response: {e}")

        raw_cookie = info.get("raw_cookies", "")
        if raw_cookie:
            cookie_header = parse_netscape_to_cookie_header(raw_cookie)
            if cookie_header:
                block = f"```text\nCookie:\n{cookie_header}\n```"
                try:
                    followup_msg = await interaction.followup.send(
                        f"🍪 **Your Crunchyroll cookie header:**\n{block}",
                        ephemeral=True
                    )
                    messages_to_delete.append(followup_msg)
                except Exception as e:
                    log.error(f"Failed to send cookie header: {e}")
                    try:
                        await interaction.followup.send(
                            "⚠️ Could not send cookie header due to an error. Please try again.",
                            ephemeral=True
                        )
                    except Exception:
                        pass
            else:
                try:
                    followup_msg = await interaction.followup.send(
                        "⚠️ Cookie content could not be parsed into a valid header.",
                        ephemeral=True
                    )
                    messages_to_delete.append(followup_msg)
                except Exception as e:
                    log.error(f"Failed to send parse error: {e}")
        else:
            try:
                followup_msg = await interaction.followup.send(
                    "⚠️ Cookie content not available.",
                    ephemeral=True
                )
                messages_to_delete.append(followup_msg)
            except Exception as e:
                log.error(f"Failed to send missing content error: {e}")

        # Send cookie editor instruction
        ce_msg = t.get("cookie_editor_instruction",
            "🍪 **Copy the cookie header below** and use it to authenticate.\n"
            "You can add these cookies manually using a browser extension like Cookie-Editor.\n"
            "🔗 **Download it from:** <https://cookie-editor.com/>")
        try:
            ce_followup = await interaction.followup.send(ce_msg, ephemeral=True)
            messages_to_delete.append(ce_followup)
        except Exception as e:
            log.error(f"Failed to send cookie editor instruction: {e}")

        # Send TV instruction
        tv_msg = t.get("tv_instruction",
            "📺 Open **https://www.crunchyroll.com/activate** and enter the code displayed on your TV.")
        try:
            tv_followup = await interaction.followup.send(tv_msg, ephemeral=True)
            messages_to_delete.append(tv_followup)
        except Exception as e:
            log.error(f"Failed to send TV instruction: {e}")

        activity_timestamp = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
        status_label = "✅ Success"
        result_label = "Cookie generated"
        used_files = [chosen_file_name] if chosen_file_name else []
        asyncio.create_task(
            log_user_activity(
                interaction, status_label, result_label,
                used_txt_files=used_files, language=lang, plan_key=plan_key, device=device,
                timestamp=activity_timestamp
            )
        )

        plan_display = info.get("plan", "Unknown")
        await send_activity_log(
            interaction=interaction,
            plan_display=plan_display,
            device=device,
            files_used=used_files,
            info=info,
            status="Success",
            result="Cookie generated"
        )

        if interaction.guild:
            asyncio.create_task(_refresh_stats_message(interaction.guild.id))

        await schedule_cleanup(interaction, messages_to_delete)

    else:
        error_msg = t["validation_failed"]
        if chosen_file_name:
            guild_id = interaction.guild.id if interaction.guild else None
            asyncio.create_task(_delete_and_refresh(plan_folder, chosen_file_name, guild_id))
            log.info(f"Scheduled deletion of failed cookie: {plan_folder}/{chosen_file_name} with stock refresh")

        retry_view = RetryView(interaction, lang, messages_to_delete)
        await interaction.edit_original_response(content=error_msg, view=retry_view)
        await log_user_activity(
            interaction,
            t["failure"],
            error_msg,
            used_txt_files=[chosen_file_name] if chosen_file_name else [],
            language=lang,
            plan_key=plan_key,
            device=device,
        )
        await schedule_cleanup(interaction, messages_to_delete)

class RetryView(discord.ui.View):
    def __init__(self, original_interaction: discord.Interaction, language: str, messages: List[discord.WebhookMessage]) -> None:
        super().__init__(timeout=CLEANUP_DELAY_SECONDS)
        self.original_interaction = original_interaction
        self.language = language
        self.messages = messages

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
        if self.original_interaction.id in _cleanup_tasks:
            _cleanup_tasks[self.original_interaction.id].cancel()
            del _cleanup_tasks[self.original_interaction.id]

        view = LanguageSelectView(interaction, self.messages)
        lang_msg = await interaction.followup.send(
            TRANSLATIONS["en"]["lang_prompt"], view=view, ephemeral=True
        )
        self.messages.append(lang_msg)
        view.original_interaction = interaction
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
        await schedule_cleanup(self.original_interaction, self.messages)

class LanguageSelectView(discord.ui.View):
    def __init__(self, original_interaction: discord.Interaction, messages: List[discord.WebhookMessage]) -> None:
        super().__init__(timeout=60)
        self.original_interaction = original_interaction
        self.messages = messages

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
        self.messages.append(lang_message)

        confirm_view = PlanSelectView(
            self.original_interaction.user,
            self.original_interaction,
            lang,
            self.messages
        )
        confirm_msg = await interaction.followup.send(
            TRANSLATIONS[lang]["confirm_prompt"], view=confirm_view, ephemeral=True
        )
        self.messages.append(confirm_msg)
        confirm_view.lang_message = lang_message
        confirm_view.confirm_message = confirm_msg
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
        await schedule_cleanup(self.original_interaction, self.messages)

class PlanSelectView(discord.ui.View):
    def __init__(
        self,
        original_user: discord.User | discord.Member,
        original_interaction: discord.Interaction,
        language: str,
        messages: List[discord.WebhookMessage],
    ) -> None:
        super().__init__(timeout=60)
        self.original_user = original_user
        self.original_interaction = original_interaction
        self.language = language
        self.messages = messages
        self.lang_message: Optional[discord.Message] = None
        self.confirm_message: Optional[discord.Message] = None
        self.plan_key: Optional[str] = None

        for key, label, emoji in [
            ("fan", TRANSLATIONS[language]["fan_label"], "🎈"),
            ("mega_fan", TRANSLATIONS[language]["mega_fan_label"], "💎"),
            ("ultimate_fan", TRANSLATIONS[language]["ultimate_fan_label"], "🚀"),
        ]:
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, emoji=emoji)
            btn.callback = self._make_plan_callback(key)
            self.add_item(btn)

    def _make_plan_callback(self, plan_key: str):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.original_user.id:
                await interaction.response.send_message(
                    TRANSLATIONS[self.language]["not_for_you"], ephemeral=True
                )
                return
            self.plan_key = plan_key
            for child in self.children:
                child.disabled = True
            device_view = DeviceSelectView(
                self.original_user,
                self.original_interaction,
                self.language,
                plan_key,
                self.lang_message,
                self.confirm_message,
                self.messages,
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
        await schedule_cleanup(self.original_interaction, self.messages)

class DeviceSelectView(discord.ui.View):
    def __init__(
        self,
        original_user: discord.User | discord.Member,
        original_interaction: discord.Interaction,
        language: str,
        plan_key: str,
        lang_message: Optional[discord.Message] = None,
        confirm_message: Optional[discord.Message] = None,
        messages: Optional[List[discord.WebhookMessage]] = None,
    ) -> None:
        super().__init__(timeout=60)
        self.original_user = original_user
        self.original_interaction = original_interaction
        self.language = language
        self.plan_key = plan_key
        self.lang_message = lang_message
        self.confirm_message = confirm_message
        self.messages = messages if messages is not None else []

        for key, label, style, emoji in [
            ("pc",    TRANSLATIONS[language]["pc_label"],    discord.ButtonStyle.primary,  "🖥️"),
            ("phone", TRANSLATIONS[language]["phone_label"], discord.ButtonStyle.success,  "📱"),
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
                interaction,
                self.language,
                self.plan_key,
                device_key,
                self.messages,
                self.lang_message,
                self.confirm_message,
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
        await schedule_cleanup(self.original_interaction, self.messages)

@bot.tree.command(name="create", description="🍣 Generate a Crunchyroll premium cookie")
async def create(interaction: discord.Interaction) -> None:
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
                TRANSLATIONS[user_lang]["wrong_channel_with_config"].format(channel=mention), ephemeral=True
            )
        return

    on_cooldown, remaining_hours = await asyncio.to_thread(check_user_cooldown, interaction.user.id)
    if on_cooldown:
        total_minutes = int(remaining_hours * 60)
        hours_left = total_minutes // 60
        minutes_left = total_minutes % 60
        msg = TRANSLATIONS[user_lang]["cooldown"].format(hours=hours_left, minutes=minutes_left)
        await interaction.response.send_message(msg, ephemeral=True)
        log.info(
            f"Cooldown: {interaction.user} (ID: {interaction.user.id}) "
            f"blocked – {hours_left}h {minutes_left}m remaining"
        )
        return

    messages_to_delete = []
    view = LanguageSelectView(interaction, messages_to_delete)
    await interaction.response.send_message(TRANSLATIONS["en"]["lang_prompt"], view=view, ephemeral=True)

@bot.tree.command(
    name="channel",
    description="📌 Set the text channel where the bot will work",
)
@app_commands.describe(channel="The text channel to designate as the bot's working channel")
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    lang = get_user_lang(interaction)
    await interaction.response.defer(ephemeral=True)
    guild_id = interaction.guild.id
    guild_name = interaction.guild.name if interaction.guild else "Unknown"
    await config.set_allowed_channel(guild_id, channel.id, guild_name=guild_name, channel_name=channel.name)
    msg = (
        f"✅ Bot will now **only** respond in {channel.mention}."
        if lang == "en"
        else f"\u200f✅ البوت سيعمل الآن **فقط** في {channel.mention}."
    )
    await interaction.followup.send(msg, ephemeral=True)
    await send_or_update_setup_messages(channel, guild_id)
    log.info(f"/channel set by {interaction.user} in guild {guild_id} → #{channel.name}")

@bot.tree.command(
    name="channel_log",
    description="📌 Set channel for Crunchyroll user activity log (Admin only)",
)
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
        ephemeral=True
    )
    log.info(f"/channel_log set by {interaction.user} in guild {guild_id} → #{channel.name} (last_ts={now_iso})")

@bot.tree.command(name="ban", description="🚫 Block a user from using the bot by their Discord ID (Admin only)")
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
        await interaction.followup.send("❌ Invalid user ID – must be a numeric Discord ID.", ephemeral=True)
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
    success = await asyncio.to_thread(add_ban_to_github, uid, username)
    if success:
        log.info(f"Admin {interaction.user} banned {username} (ID: {uid})")
        msg = (
            f"✅ User `{username}` (ID: `{uid}`) has been **banned**."
            if lang == "en"
            else f"\u200f✅ تم حظر المستخدم `{username}` (ID: `{uid}`)."
        )
    else:
        msg = (
            f"⚠️ User `{uid}` banned locally but **GitHub push failed**."
            if lang == "en"
            else f"\u200f⚠️ تم الحظر محليًا لكن **فشل الرفع إلى GitHub**."
        )
    await interaction.followup.send(msg, ephemeral=True)

@bot.tree.command(name="unban", description="✅ Remove a bot ban for a user by their Discord ID (Admin only)")
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
        await interaction.followup.send("❌ Invalid user ID – must be a numeric Discord ID.", ephemeral=True)
        return
    if not is_user_banned(uid):
        await interaction.followup.send(f"⚠️ User `{uid}` is not currently banned.", ephemeral=True)
        return
    _banned_user_ids.discard(uid)
    attempts = _ban_attempt_counts.pop(uid, 0)
    success = await asyncio.to_thread(remove_ban_from_github, uid)
    if success:
        log.info(f"Admin {interaction.user} unbanned user {uid} (had {attempts} attempt(s))")
        msg = (
            f"✅ User `{uid}` has been **unbanned**. They had **{attempts}** blocked attempt(s)."
            if lang == "en"
            else f"\u200f✅ تم رفع الحظر عن المستخدم `{uid}`. كان لديه **{attempts}** محاولة محظورة."
        )
    else:
        msg = (
            f"⚠️ User `{uid}` unbanned locally but **GitHub push failed**."
            if lang == "en"
            else f"\u200f⚠️ تم رفع الحظر محليًا لكن **فشل الرفع إلى GitHub**."
        )
    await interaction.followup.send(msg, ephemeral=True)

@bot.tree.command(
    name="banserver",
    description="🚫 Ban a server from using the bot due to a bot-rule violation (Admin only)",
)
@app_commands.describe(
    guild_id="The Discord server (guild) ID to ban",
    reason="Reason for the server ban (e.g. bot rule violation)",
)
async def ban_server(
    interaction: discord.Interaction,
    guild_id: str,
    reason: str = "Bot rule violation",
) -> None:
    lang = get_user_lang(interaction)
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        gid = int(guild_id.strip())
    except ValueError:
        await interaction.followup.send("❌ Invalid server ID – must be a numeric Discord guild ID.", ephemeral=True)
        return
    if is_server_banned(gid):
        await interaction.followup.send(f"⚠️ Server `{gid}` is already banned.", ephemeral=True)
        return
    guild_name = str(gid)
    target_guild = bot.get_guild(gid)
    if target_guild:
        guild_name = target_guild.name
    _banned_guild_ids.add(gid)
    success = await asyncio.to_thread(add_server_ban_to_github, gid, guild_name, reason)
    if success:
        log.info(f"Admin {interaction.user} banned server '{guild_name}' (ID: {gid}) – reason: {reason}")
        msg = (
            f"✅ Server `{guild_name}` (ID: `{gid}`) has been **banned**.\n📋 Reason: {reason}"
            if lang == "en"
            else f"\u200f✅ تم حظر السيرفر `{guild_name}` (ID: `{gid}`).\n📋 السبب: {reason}"
        )
    else:
        msg = (
            f"⚠️ Server `{gid}` banned locally but **GitHub push failed**."
            if lang == "en"
            else f"\u200f⚠️ تم الحظر محليًا لكن **فشل الرفع إلى GitHub**."
        )
    await interaction.followup.send(msg, ephemeral=True)

@bot.tree.command(
    name="unbanserver",
    description="✅ Remove a bot ban from a server by its guild ID (Admin only)",
)
@app_commands.describe(guild_id="The Discord server (guild) ID to unban")
async def unban_server(interaction: discord.Interaction, guild_id: str) -> None:
    lang = get_user_lang(interaction)
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        gid = int(guild_id.strip())
    except ValueError:
        await interaction.followup.send("❌ Invalid server ID – must be a numeric Discord guild ID.", ephemeral=True)
        return
    if not is_server_banned(gid):
        await interaction.followup.send(f"⚠️ Server `{gid}` is not currently banned.", ephemeral=True)
        return
    _banned_guild_ids.discard(gid)
    success = await asyncio.to_thread(remove_server_ban_from_github, gid)
    if success:
        log.info(f"Admin {interaction.user} unbanned server {gid}")
        msg = (
            f"✅ Server `{gid}` has been **unbanned**."
            if lang == "en"
            else f"\u200f✅ تم رفع الحظر عن السيرفر `{gid}`."
        )
    else:
        msg = (
            f"⚠️ Server `{gid}` unbanned locally but **GitHub push failed**."
            if lang == "en"
            else f"\u200f⚠️ تم رفع الحظر محليًا لكن **فشل الرفع إلى GitHub**."
        )
    await interaction.followup.send(msg, ephemeral=True)

admin_group = app_commands.Group(
    name="admin",
    description="👮 Manage bot admins",
)

@admin_group.command(name="add", description="👮 Add a user to the bot admin list")
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
        await interaction.followup.send("❌ Invalid user ID – must be numeric.", ephemeral=True)
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
    _admin_registry[uid] = {
        "username": username,
        "added_by": str(interaction.user),
        "added_at": now_str,
    }
    success = await asyncio.to_thread(save_admins_to_github, _admin_registry)
    log.info(f"Owner {interaction.user} added admin {username} (ID: {uid})")
    msg = (
        f"✅ `{username}` (ID: `{uid}`) has been added as a **bot admin**."
        if lang == "en"
        else f"\u200f✅ تمت إضافة `{username}` (ID: `{uid}`) كـ **مسؤول بوت**."
    ) if success else (
        f"✅ `{uid}` added locally but **GitHub push failed**."
        if lang == "en"
        else f"\u200f✅ تمت الإضافة محليًا لكن **فشل الرفع إلى GitHub**."
    )
    await interaction.followup.send(msg, ephemeral=True)

@admin_group.command(name="remove", description="👮 Remove a user from the bot admin list")
@app_commands.describe(user_id="Discord user ID to revoke admin access")
async def admin_remove(interaction: discord.Interaction, user_id: str) -> None:
    lang = get_user_lang(interaction)
    invoker = interaction.user.id
    if invoker not in _PRIVILEGED_IDS:
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.followup.send("❌ Invalid user ID – must be numeric.", ephemeral=True)
        return
    if uid == BOT_OWNER_ID and invoker != BOT_OWNER_ID:
        await interaction.followup.send("❌ You cannot remove the bot owner.", ephemeral=True)
        return
    if uid not in _admin_registry:
        await interaction.followup.send(f"⚠️ User `{uid}` is not in the admin list.", ephemeral=True)
        return
    removed_info = _admin_registry.pop(uid)
    success = await asyncio.to_thread(save_admins_to_github, _admin_registry)
    log.info(f"{interaction.user} removed admin {removed_info['username']} (ID: {uid})")
    msg = (
        f"✅ `{removed_info['username']}` (ID: `{uid}`) has been **removed** from admins."
        if lang == "en"
        else f"\u200f✅ تمت إزالة `{removed_info['username']}` (ID: `{uid}`) من المسؤولين."
    ) if success else (
        f"✅ `{uid}` removed locally but **GitHub push failed**."
        if lang == "en"
        else f"\u200f✅ تمت الإزالة محليًا لكن **فشل الرفع إلى GitHub**."
    )
    await interaction.followup.send(msg, ephemeral=True)

@admin_group.command(name="list", description="👮 List all current bot admins")
async def admin_list(interaction: discord.Interaction) -> None:
    lang = get_user_lang(interaction)
    if interaction.user.id not in _PRIVILEGED_IDS:
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    embed = discord.Embed(
        title="👮 Bot Admin List  |  قائمة المسؤولين",
        color=discord.Color.gold(),
        timestamp=datetime.now(EGYPT_TZ),
    )
    embed.add_field(
        name=f"👑 X2 Salah (ID: {BOT_OWNER_ID})",
        value="Role: **Bot Owner** – permanent full access",
        inline=False,
    )
    embed.add_field(
        name=f"⭐ HASHO_Z (ID: {BOT_COADMIN_ID})",
        value="Role: **Co-Admin** – can remove admins (except owner)",
        inline=False,
    )
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

@bot.tree.command(
    name="stock",
    description="📊 Refresh the Account Stock embed in the bot channel (Admin only)",
)
async def stock_refresh(interaction: discord.Interaction) -> None:
    lang = get_user_lang(interaction)
    if not is_admin(interaction.user.id) and not (
        interaction.guild and interaction.user.guild_permissions.administrator
    ):
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return
    guild_id = interaction.guild.id if interaction.guild else None
    if not guild_id:
        await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await _refresh_stats_message(guild_id)
    msg = (
        "✅ Account Stock message has been refreshed."
        if lang == "en"
        else "\u200f✅ تم تحديث رسالة مخزون الحسابات."
    )
    await interaction.followup.send(msg, ephemeral=True)
    log.info(f"/stock used by {interaction.user} in guild {guild_id}")

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
        lang = get_user_lang(interaction)
        msg = (
            f"🚫 You have been banned from using this bot. (Attempt #{attempts})"
            if lang == "en"
            else f"\u200f🚫 تم حظرك من استخدام هذا البوت. (المحاولة رقم #{attempts})"
        )
        cmd_name = interaction.command.name if interaction.command else "?"
        log.warning(f"Banned user {interaction.user} tried /{cmd_name} – attempt #{attempts}")
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return False

    guild_id = interaction.guild.id if interaction.guild else None
    if guild_id and is_server_banned(guild_id):
        lang = get_user_lang(interaction)
        msg = (
            "🚫 This server has been banned from using this bot due to a violation of the bot rules."
            if lang == "en"
            else "\u200f🚫 تم حظر هذا السيرفر من استخدام البوت بسبب انتهاك قواعد الاستخدام."
        )
        cmd_name = interaction.command.name if interaction.command else "?"
        log.warning(f"Banned guild {guild_id} tried /{cmd_name} via {interaction.user}")
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
    if ALLOWED_GUILD_IDS:
        log.info(f"Guild restriction : {ALLOWED_GUILD_IDS}")
    else:
        log.info("Guild restriction : NONE (global bot)")

    if COOKIES_GITHUB_REPO:
        log.info(f"Cookie source : GitHub → {COOKIES_GITHUB_REPO}/{COOKIES_GITHUB_PATH} [{COOKIES_GITHUB_BRANCH}]")
    else:
        log.info(f"Cookie source : Local → {COOKIES_FOLDER.resolve()}")

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
        _ch_id = config.get_channel_for_guild(_gid)
        if _ch_id:
            try:
                await _refresh_stats_message(_gid)
            except Exception as _exc:
                log.warning(f"Startup stats refresh failed for guild {_gid}: {_exc}")

if __name__ == "__main__":
    COOKIES_FOLDER.mkdir(exist_ok=True)
    for folder in ["Fan", "Mega Fan", "Ultimate Fan"]:
        (COOKIES_FOLDER / folder).mkdir(exist_ok=True)
    bot.run(DISCORD_BOT_TOKEN)
