import re
import json
import base64
import requests
import pytz
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from collections import OrderedDict

LOGIN_URL = "https://beta-api.crunchyroll.com/auth/v1/token"
CLIENT_ID = "y2arvjb0h0rgvtizlovy"
CLIENT_SECRET = "JVLvwdIpXvxU-qIBvT1M8oQTr1qlQJX2"
AUTH_BASIC = "Basic bm9haWhkZXZtXzZpeWcwYThsMHE6"
DEVICE_ID = "0ea0d167-3c2e-4aa1-874c-cb876bde12da"
ETP_ANONYMOUS_ID = "4c5fa964-9cda-4893-af0b-f514ada8250a"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)

PLAN_DISPLAY_MAP = {
    "fan": "Fan",
    "ultimate_fan": "Ultimate Fan",
    "mega_fan": "Mega Fan",
}

COUNTRY_MAP = {
    "AF": "Afghanistan", "AL": "Albania", "DZ": "Algeria", "AD": "Andorra",
    "AO": "Angola", "AR": "Argentina", "AM": "Armenia", "AU": "Australia",
    "AT": "Austria", "AZ": "Azerbaijan", "BS": "Bahamas", "BH": "Bahrain",
    "BD": "Bangladesh", "BB": "Barbados", "BY": "Belarus", "BE": "Belgium",
    "BZ": "Belize", "BJ": "Benin", "BT": "Bhutan", "BO": "Bolivia",
    "BA": "Bosnia and Herzegovina", "BW": "Botswana", "BR": "Brazil",
    "BN": "Brunei", "BG": "Bulgaria", "BF": "Burkina Faso", "BI": "Burundi",
    "KH": "Cambodia", "CM": "Cameroon", "CA": "Canada", "CV": "Cape Verde",
    "KY": "Cayman Islands", "CF": "Central African Republic", "TD": "Chad",
    "CL": "Chile", "CN": "China", "CO": "Colombia", "KM": "Comoros",
    "CG": "Congo", "CD": "Democratic Republic of the Congo", "CR": "Costa Rica",
    "CI": "Ivory Coast", "HR": "Croatia", "CU": "Cuba", "CY": "Cyprus",
    "CZ": "Czech Republic", "DK": "Denmark", "DJ": "Djibouti", "DM": "Dominica",
    "DO": "Dominican Republic", "EC": "Ecuador", "EG": "Egypt", "SV": "El Salvador",
    "GQ": "Equatorial Guinea", "ER": "Eritrea", "EE": "Estonia", "SZ": "Eswatini",
    "ET": "Ethiopia", "FJ": "Fiji", "FI": "Finland", "FR": "France",
    "GA": "Gabon", "GM": "Gambia", "GE": "Georgia", "DE": "Germany",
    "GH": "Ghana", "GR": "Greece", "GD": "Grenada", "GT": "Guatemala",
    "GN": "Guinea", "GW": "Guinea-Bissau", "GY": "Guyana", "HT": "Haiti",
    "HN": "Honduras", "HU": "Hungary", "IS": "Iceland", "IN": "India",
    "ID": "Indonesia", "IR": "Iran", "IQ": "Iraq", "IE": "Ireland",
    "IL": "Israel", "IT": "Italy", "JM": "Jamaica", "JP": "Japan",
    "JO": "Jordan", "KZ": "Kazakhstan", "KE": "Kenya", "KI": "Kiribati",
    "KP": "North Korea", "KR": "South Korea", "KW": "Kuwait", "KG": "Kyrgyzstan",
    "LA": "Laos", "LV": "Latvia", "LB": "Lebanon", "LS": "Lesotho",
    "LR": "Liberia", "LY": "Libya", "LI": "Liechtenstein", "LT": "Lithuania",
    "LU": "Luxembourg", "MO": "Macau", "MK": "North Macedonia", "MG": "Madagascar",
    "MW": "Malawi", "MY": "Malaysia", "MV": "Maldives", "ML": "Mali",
    "MT": "Malta", "MH": "Marshall Islands", "MQ": "Martinique", "MR": "Mauritania",
    "MU": "Mauritius", "YT": "Mayotte", "MX": "Mexico", "FM": "Micronesia",
    "MD": "Moldova", "MC": "Monaco", "MN": "Mongolia", "ME": "Montenegro",
    "MS": "Montserrat", "MA": "Morocco", "MZ": "Mozambique", "MM": "Myanmar",
    "NA": "Namibia", "NR": "Nauru", "NP": "Nepal", "NL": "Netherlands",
    "NZ": "New Zealand", "NI": "Nicaragua", "NE": "Niger", "NG": "Nigeria",
    "NO": "Norway", "OM": "Oman", "PK": "Pakistan", "PW": "Palau",
    "PA": "Panama", "PG": "Papua New Guinea", "PY": "Paraguay", "PE": "Peru",
    "PH": "Philippines", "PL": "Poland", "PT": "Portugal", "PR": "Puerto Rico",
    "QA": "Qatar", "RO": "Romania", "RU": "Russia", "RW": "Rwanda",
    "RE": "Réunion", "ST": "São Tomé and Príncipe", "SA": "Saudi Arabia",
    "SN": "Senegal", "RS": "Serbia", "SC": "Seychelles", "SL": "Sierra Leone",
    "SG": "Singapore", "SK": "Slovakia", "SI": "Slovenia", "SB": "Solomon Islands",
    "SO": "Somalia", "ZA": "South Africa", "SS": "South Sudan", "ES": "Spain",
    "LK": "Sri Lanka", "SD": "Sudan", "SR": "Suriname", "SJ": "Svalbard and Jan Mayen",
    "SE": "Sweden", "CH": "Switzerland", "SY": "Syria", "TW": "Taiwan",
    "TJ": "Tajikistan", "TZ": "Tanzania", "TH": "Thailand", "TL": "Timor-Leste",
    "TG": "Togo", "TK": "Tokelau", "TO": "Tonga", "TT": "Trinidad and Tobago",
    "TN": "Tunisia", "TR": "Turkey", "TM": "Turkmenistan", "TC": "Turks and Caicos Islands",
    "TV": "Tuvalu", "UG": "Uganda", "UA": "Ukraine", "AE": "United Arab Emirates",
    "GB": "United Kingdom", "US": "United States", "UY": "Uruguay",
    "UZ": "Uzbekistan", "VU": "Vanuatu", "VE": "Venezuela", "VN": "Vietnam",
    "WF": "Wallis and Futuna", "EH": "Western Sahara", "YE": "Yemen",
    "ZM": "Zambia", "ZW": "Zimbabwe",
}


def get_country_name(code: str) -> str:
    return COUNTRY_MAP.get(code, code or "Unknown")


def format_display_date(value) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%B %d, %Y")
    except Exception:
        return str(value)


def format_member_since(value) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%B %Y")
    except Exception:
        return str(value)


def compute_days_left(expiry_date_str: Optional[str]) -> Optional[int]:
    if not expiry_date_str:
        return None

    expiry_str = str(expiry_date_str).strip()
    if not expiry_str:
        return None

    now = datetime.now(pytz.UTC)

    try:
        renewal_date = datetime.strptime(
            expiry_str.replace("Z", "+00:00"),
            "%Y-%m-%dT%H:%M:%S%z",
        )
        return max(0, (renewal_date - now).days)
    except Exception:
        pass

    try:
        renewal_date = datetime.strptime(
            expiry_str.replace("Z", "+00:00"),
            "%Y-%m-%dT%H:%M:%S.%f%z",
        )
        return max(0, (renewal_date - now).days)
    except Exception:
        pass

    try:
        dt = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.UTC)
        return max(0, (dt - now).days)
    except Exception:
        pass

    try:
        dt = datetime.strptime(expiry_str[:10], "%Y-%m-%d").replace(tzinfo=pytz.UTC)
        return max(0, (dt - now).days)
    except Exception:
        return None


def extract_cookies_dict(content: str) -> OrderedDict:
    cookies: "OrderedDict[str, str]" = OrderedDict()
    known_names = {
        "etp_rt", "session_id", "cf_clearance", "cr_exp", "c_locale",
        "device_id", "anonymous_consent_tos", "OptanonConsent",
        "OptanonAlertBoxClosed",
    }

    for line in content.splitlines():
        line = line.strip()
        if line.startswith((".crunchyroll.com", "#HttpOnly_.crunchyroll.com", ".www.crunchyroll.com")):
            parts = line.split()
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]

    for name in known_names:
        matches = re.findall(rf"\b{re.escape(name)}\s*=\s*([^;\n]+)", content, re.IGNORECASE)
        if matches:
            cookies[name] = matches[-1].strip()

    for match in re.finditer(r"\b([a-zA-Z0-9_-]+)=([^;\n]+)", content):
        name, value = match.group(1), match.group(2).strip()
        if name in known_names or name.startswith(("cr_", "etp_")):
            cookies[name] = value

    if "etp_rt" not in cookies:
        jwt_match = re.search(r"etp_jwt[=\s]+([^\s;]+)", content, re.IGNORECASE)
        if not jwt_match:
            jwt_match = re.search(r'["\']etp_jwt["\']\s*:\s*["\']([^"\']+)', content, re.IGNORECASE)
        if jwt_match:
            jwt = jwt_match.group(1)
            if jwt.startswith("eyJ") and "." in jwt:
                try:
                    parts = jwt.split(".")
                    if len(parts) >= 2:
                        payload = parts[1]
                        payload += "=" * (4 - len(payload) % 4)
                        data = json.loads(base64.b64decode(payload))
                        if data.get("rt_id"):
                            cookies["etp_rt"] = data["rt_id"]
                        if data.get("anonymous_id"):
                            cookies["ajs_anonymous_id"] = data["anonymous_id"]
                        if data.get("device_id"):
                            cookies["device_id"] = data["device_id"]
                except Exception:
                    pass

    return OrderedDict((k, v) for k, v in cookies.items() if v and len(v) < 5000)


def get_access_token_from_cookies(cookies_dict: dict) -> Optional[str]:
    session = requests.Session()
    for name, value in cookies_dict.items():
        session.cookies.set(name, value, domain=".crunchyroll.com", path="/")
        session.cookies.set(name, value, domain="www.crunchyroll.com", path="/")

    payload = (
        f"device_id={DEVICE_ID}"
        "&device_type=Chrome%20on%20Windows"
        "&grant_type=etp_rt_cookie"
    )
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": AUTH_BASIC,
        "Content-Type": "application/x-www-form-urlencoded",
        "Etp-Anonymous-Id": ETP_ANONYMOUS_ID,
        "Origin": "https://www.crunchyroll.com",
        "Referer": "https://www.crunchyroll.com/account/membership",
    }
    try:
        resp = session.post(LOGIN_URL, headers=headers, data=payload, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("access_token")
    except Exception:
        pass
    return None


def get_account_info_from_token(token: str, cookies_dict: dict) -> Optional[Dict[str, Any]]:
    session = requests.Session()
    for name, value in cookies_dict.items():
        session.cookies.set(name, value, domain=".crunchyroll.com", path="/")
        session.cookies.set(name, value, domain="www.crunchyroll.com", path="/")

    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {token}",
        "Etp-Anonymous-Id": ETP_ANONYMOUS_ID,
        "Referer": "https://www.crunchyroll.com/account/membership",
    }
    try:
        resp = session.get(
            "https://www.crunchyroll.com/accounts/v1/me",
            headers=headers, timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "account_id": data.get("account_id"),
                "external_id": data.get("external_id"),
                "email": data.get("email"),
                "username": data.get("username"),
                "created": data.get("created"),
                "verified": data.get("email_verified", False),
                "has_password": data.get("has_password", False),
            }
    except Exception:
        pass
    return None


def _extract_expiry_from_sub(sub: Dict[str, Any]) -> Optional[str]:
    candidates = (
        "nextRenewalDate",
        "next_renewal_date",
        "expirationDate",
        "expiration_date",
        "renewalDate",
        "renewal_date",
    )
    for key in candidates:
        value = sub.get(key)
        if value:
            return value
    return None


def get_subscription_details(token: str, account_id: str, cookies_dict: dict) -> Optional[Dict]:
    session = requests.Session()
    for name, value in cookies_dict.items():
        session.cookies.set(name, value, domain=".crunchyroll.com", path="/")
        session.cookies.set(name, value, domain="www.crunchyroll.com", path="/")

    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {token}",
        "Etp-Anonymous-Id": ETP_ANONYMOUS_ID,
        "Referer": "https://www.crunchyroll.com/account/membership",
    }
    try:
        resp = session.get(
            f"https://www.crunchyroll.com/subs/v4/accounts/{account_id}/subscriptions",
            headers=headers, timeout=15,
        )
        if resp.status_code != 200:
            return None
        subscriptions = resp.json().get("subscriptions", [])
        if not subscriptions:
            return None

        sub = subscriptions[0]
        plan_obj = sub.get("plan", {})
        tier = plan_obj.get("tier", {})
        plan_code = tier.get("value", "unknown")
        plan_label = tier.get("text", "") or plan_obj.get("name", {}).get("value", "")
        plan_label = plan_label.replace("cr_", "").replace("_", " ").title()

        is_free = plan_code == "free" or "free" in plan_label.lower()

        if is_free:
            return {
                "is_free": True,
                "country": sub.get("countryCode", "Unknown"),
                "expiration_date": None,
                "plan_code": "free",
                "plan_label": "Free",
                "status": sub.get("status", "Unknown"),
                "payment_method": sub.get("paymentMethod", {}).get("source", "Unknown"),
                "price": sub.get("price", {}).get("amount", ""),
                "free_trial": sub.get("activeFreeTrial", False),
                "on_hold": sub.get("onHold", False),
                "days_left": None,
            }

        expiry = _extract_expiry_from_sub(sub)
        return {
            "is_free": False,
            "country": sub.get("countryCode", "Unknown"),
            "expiration_date": expiry,
            "days_left": compute_days_left(expiry),
            "plan_code": plan_code,
            "plan_label": plan_label,
            "status": sub.get("status", "Unknown"),
            "payment_method": sub.get("paymentMethod", {}).get("source", "Unknown"),
            "price": sub.get("price", {}).get("amount", ""),
            "free_trial": sub.get("activeFreeTrial", False),
            "on_hold": sub.get("onHold", False),
        }
    except Exception:
        return None


def extract_info(cookies_dict: dict) -> Dict[str, Any]:
    token = get_access_token_from_cookies(cookies_dict)
    if not token:
        return {}

    account_info = get_account_info_from_token(token, cookies_dict)
    if not account_info or not account_info.get("account_id"):
        return {}

    account_id = account_info["account_id"]
    sub_info = get_subscription_details(token, account_id, cookies_dict) or {}

    plan_code = str(sub_info.get("plan_code", "")).lower()
    plan_label = sub_info.get("plan_label", "Unknown")
    if "ultimate" in plan_code or "ultimate" in plan_label.lower():
        plan_display = "Ultimate Fan"
    elif "mega" in plan_code or "mega" in plan_label.lower():
        plan_display = "Mega Fan"
    elif "fan" in plan_code or "fan" in plan_label.lower() or "premium" in plan_code:
        plan_display = "Fan"
    else:
        plan_display = plan_label or "Unknown"

    is_free = sub_info.get("is_free", False)
    membership_status = "Free" if is_free else "Premium"

    raw_days = sub_info.get("days_left")
    if raw_days is None:
        days_left_display: Any = "N/A"
    else:
        try:
            days_left_display = int(raw_days)
        except (TypeError, ValueError):
            days_left_display = raw_days

    return {
        "name": account_info.get("username") or "Unknown",
        "email": account_info.get("email") or "Unknown",
        "country": get_country_name(sub_info.get("country", "Unknown")),
        "plan": plan_display,
        "plan_code": plan_code,
        "member_since": format_member_since(account_info.get("created")),
        "next_billing": format_display_date(sub_info.get("expiration_date")),
        "days_left": days_left_display,
        "phone": "N/A",
        "membership_status": membership_status,
        "profiles": "N/A",
        "payment_method": sub_info.get("payment_method", "N/A"),
        "price": sub_info.get("price", ""),
        "free_trial": sub_info.get("free_trial", False),
        "on_hold": sub_info.get("on_hold", False),
        "status": sub_info.get("status", "Unknown"),
        "verified": account_info.get("verified", False),
        "has_password": account_info.get("has_password", False),
        "created": account_info.get("created"),
        "expiration_date_raw": sub_info.get("expiration_date"),
    }


def check_cookie_content(content: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    if not content:
        return None, None
    cookies_dict = extract_cookies_dict(content)
    if not cookies_dict:
        return None, None
    info = extract_info(cookies_dict)
    if not info:
        return None, None
    info["raw_cookies"] = content
    return None, info


def check_cookie_file(file_path: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None, None
    return check_cookie_content(content)


def quick_check_cookie_content(content: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    if not content:
        return False, None
    cookies_dict = extract_cookies_dict(content)
    if not cookies_dict:
        return False, None

    token = get_access_token_from_cookies(cookies_dict)
    if not token:
        return False, None

    account_info = get_account_info_from_token(token, cookies_dict)
    if not account_info or not account_info.get("account_id"):
        return False, None

    sub_info = get_subscription_details(token, account_info["account_id"], cookies_dict)
    if not sub_info or sub_info.get("is_free"):
        return False, account_info

    return True, account_info


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        _, info = check_cookie_file(sys.argv[1])
        if info and info.get("membership_status") == "Premium":
            print("✅ Valid Crunchyroll Premium account:")
            for k, v in info.items():
                if k != "raw_cookies":
                    print(f"{k}: {v}")
        elif info:
            print("❌ Invalid or free account")
        else:
            print("❌ Could not authenticate cookie")
