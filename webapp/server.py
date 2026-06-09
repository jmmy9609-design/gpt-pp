#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import uuid
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlsplit, urlunsplit


APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
STATIC_DIR = APP_DIR / "static"
COUNTER_FILE = APP_DIR / "counter.json"
COUNTER_LOCK = threading.Lock()
ACTIVE_VISITORS: dict[str, float] = {}
ACTIVE_VISITORS_LOCK = threading.Lock()
MAX_BODY_BYTES = 512 * 1024
MAX_PROXY_POOL_BYTES = 16 * 1024
MAX_PROXY_CANDIDATES = int(os.getenv("PLUS_LINK_MAX_PROXY_CANDIDATES", "128"))
PROXY_CITY_SAMPLES = int(os.getenv("PLUS_LINK_PROXY_CITY_SAMPLES", "32"))
PROXY_PROVIDER_FETCH_TIMEOUT_SECONDS = float(os.getenv("PLUS_LINK_PROXY_PROVIDER_FETCH_TIMEOUT_SECONDS", "10"))
PROXY_RACE_WORKERS = int(os.getenv("PLUS_LINK_PROXY_RACE_WORKERS", "8"))
# 单账号不再做多代理竞速：同一 AT 的 checkout/approve 会互相作废或触发 401/blocked。
# 批量并发仍在账号层执行；每个账号内部只串行换 lease。
CHECKOUT_RACE_ENABLED = False
PROXY_PREFLIGHT_WORKERS = int(os.getenv("PLUS_LINK_PROXY_PREFLIGHT_WORKERS", "8"))
PROXY_PREFLIGHT_TIMEOUT_SECONDS = float(os.getenv("PLUS_LINK_PROXY_PREFLIGHT_TIMEOUT_SECONDS", "12"))
PROXY_TARGET_PREFLIGHT = str(os.getenv("PLUS_LINK_PROXY_TARGET_PREFLIGHT", "1")).lower() in {"1", "true", "yes", "on"}
BATCH_TOKEN_LOCKS: dict[str, threading.Lock] = {}
BATCH_TOKEN_LOCKS_LOCK = threading.Lock()
PROXY_GEO_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
PROXY_GEO_CACHE_LOCK = threading.Lock()
PROXY_GEO_CACHE_TTL_SECONDS = float(os.getenv("PLUS_LINK_PROXY_GEO_CACHE_TTL_SECONDS", "300"))
PROVIDER_LEASED_PROXIES: set[str] = set()
PROVIDER_LEASED_PROXIES_LOCK = threading.Lock()
BATCH_WORKERS = int(os.getenv("PLUS_LINK_BATCH_WORKERS", "12"))
BATCH_PROXY_CANDIDATES = int(os.getenv("PLUS_LINK_BATCH_PROXY_CANDIDATES", "12"))
BATCH_RECOVERY_ROUNDS = int(os.getenv("PLUS_LINK_BATCH_RECOVERY_ROUNDS", "1"))
BATCH_RECOVERY_PROXY_CANDIDATES = int(os.getenv("PLUS_LINK_BATCH_RECOVERY_PROXY_CANDIDATES", str(BATCH_PROXY_CANDIDATES)))
BATCH_ACCOUNT_TIMEOUT_SECONDS = float(os.getenv("PLUS_LINK_BATCH_ACCOUNT_TIMEOUT_SECONDS", "120"))
REDIRECT_POLL_SECONDS = float(os.getenv("PLUS_LINK_REDIRECT_POLL_SECONDS", "4"))
BLOCKED_REDIRECT_POLL_SECONDS = float(os.getenv("PLUS_LINK_BLOCKED_REDIRECT_POLL_SECONDS", "18"))
APPROVE_BACKGROUND_POLL_SECONDS = float(os.getenv("PLUS_LINK_APPROVE_BACKGROUND_POLL_SECONDS", "45"))
APPROVE_BACKGROUND_WORKERS = int(os.getenv("PLUS_LINK_APPROVE_BACKGROUND_WORKERS", "12"))
CHATGPT_APPROVE_TIMEOUT_SECONDS = float(os.getenv("PLUS_LINK_CHATGPT_APPROVE_TIMEOUT_SECONDS", "3"))
STRIPE_CONFIRM_TIMEOUT_SECONDS = float(os.getenv("PLUS_LINK_STRIPE_CONFIRM_TIMEOUT_SECONDS", "7"))
STRIPE_INIT_TIMEOUT_SECONDS = float(os.getenv("PLUS_LINK_STRIPE_INIT_TIMEOUT_SECONDS", "8"))
STRIPE_PAYMENT_METHOD_TIMEOUT_SECONDS = float(os.getenv("PLUS_LINK_STRIPE_PAYMENT_METHOD_TIMEOUT_SECONDS", "5"))
CHECKOUT_TIMEOUT_SECONDS = float(os.getenv("PLUS_LINK_CHECKOUT_TIMEOUT_SECONDS", "14"))
TOKEN_UNAUTHORIZED_CONFIRMATIONS = int(os.getenv("PLUS_LINK_TOKEN_UNAUTHORIZED_CONFIRMATIONS", "2"))
TOKEN_UNAUTHORIZED_RECOVERY = str(os.getenv("PLUS_LINK_TOKEN_UNAUTHORIZED_RECOVERY", "1")).lower() in {"1", "true", "yes", "on"}
APPROVE_BLOCKED_CONFIRMATIONS = int(os.getenv("PLUS_LINK_APPROVE_BLOCKED_CONFIRMATIONS", "3"))
PROXY_UNSTABLE_CONFIRMATIONS = int(os.getenv("PLUS_LINK_PROXY_UNSTABLE_CONFIRMATIONS", "6"))
ACCOUNT_PROXY_MIN_REMAINING_SECONDS = float(os.getenv("PLUS_LINK_ACCOUNT_PROXY_MIN_REMAINING_SECONDS", "12"))
CHECKOUT_PAIR_LIMIT = int(os.getenv("PLUS_LINK_CHECKOUT_PAIR_LIMIT", "8"))
REQUIRE_ZERO_AMOUNT = str(os.getenv("PLUS_LINK_REQUIRE_ZERO", "0")).lower() in {"1", "true", "yes", "on"}
SUCCESS_CITY_HINTS = {
    item.strip().lower()
    for item in os.getenv("PLUS_LINK_SUCCESS_CITY_HINTS", "kawagoe,myohoji,myōhōji").split(",")
    if item.strip()
}
CURL_IMPERSONATE_PROFILE = os.getenv("PLUS_LINK_CURL_IMPERSONATE", "chrome").strip() or "chrome"
RACE_OVERALL_TIMEOUT_SECONDS = float(os.getenv("PLUS_LINK_RACE_TIMEOUT_SECONDS", "45"))
RATE_WINDOW_SECONDS = 60
RATE_LIMIT_PER_WINDOW = 120
CITY_STATS: dict[str, dict[str, Any]] = {}
CITY_STATS_LOCK = threading.Lock()
PROXY_BADNESS: dict[str, tuple[float, float]] = {}
PROXY_BADNESS_LOCK = threading.Lock()
PROXY_BADNESS_TTL_SECONDS = float(os.getenv("PLUS_LINK_PROXY_BADNESS_TTL_SECONDS", "600"))
PROXY_BADNESS_BLOCK_SCORE = float(os.getenv("PLUS_LINK_PROXY_BADNESS_BLOCK_SCORE", "5"))
LEASED_PROXY_PREFIX = "__leased_proxy__:"
BACKGROUND_LINKS_FILE = Path(os.getenv("PLUS_LINK_BACKGROUND_LINKS_FILE", "/tmp/pp_background_paypal_links.ndjson"))
BACKGROUND_LINKS_LOCK = threading.Lock()
BACKGROUND_JOB_KEYS: set[str] = set()
BACKGROUND_JOB_LOCK = threading.Lock()
BACKGROUND_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, APPROVE_BACKGROUND_WORKERS), thread_name_prefix="pp-approve-bg")


def load_counter() -> int:
    with COUNTER_LOCK:
        if COUNTER_FILE.exists():
            try:
                data = json.loads(COUNTER_FILE.read_text(encoding="utf-8"))
                return int(data.get("success_count", 0))
            except Exception:
                pass
        initial_val = 0
        try:
            COUNTER_FILE.write_text(json.dumps({"success_count": initial_val}), encoding="utf-8")
        except Exception:
            pass
        return initial_val


def increment_counter() -> int:
    with COUNTER_LOCK:
        current = 0
        if COUNTER_FILE.exists():
            try:
                data = json.loads(COUNTER_FILE.read_text(encoding="utf-8"))
                current = int(data.get("success_count", 0))
            except Exception:
                pass
        current += 1
        try:
            COUNTER_FILE.write_text(json.dumps({"success_count": current}), encoding="utf-8")
        except Exception:
            pass
        return current


def background_success_count() -> int:
    try:
        count = 0
        if not BACKGROUND_LINKS_FILE.exists():
            return 0
        with BACKGROUND_LINKS_FILE.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("ok") and REAL_PAYPAL_AUTHORIZE_RE.match(str(row.get("paypal_authorize_url") or "")):
                    count += 1
        return count
    except Exception:
        return 0


def append_background_link(row: dict[str, Any]) -> None:
    try:
        BACKGROUND_LINKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with BACKGROUND_LINKS_LOCK:
            with BACKGROUND_LINKS_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass


def record_visitor_active(ip: str) -> None:
    if not ip or ip == "unknown":
        return
    now = time.time()
    with ACTIVE_VISITORS_LOCK:
        ACTIVE_VISITORS[ip] = now


def get_active_visitors_count() -> int:
    now = time.time()
    cutoff = now - 60.0
    with ACTIVE_VISITORS_LOCK:
        expired = [ip for ip, ts in ACTIVE_VISITORS.items() if ts < cutoff]
        for ip in expired:
            del ACTIVE_VISITORS[ip]
        return len(ACTIVE_VISITORS)


def is_valid_host(host_header: str | None) -> bool:
    if not host_header:
        return False
    host = host_header.lower().split(":")[0]
    ALLOWED_DOMAINS = {"localhost", "127.0.0.1"}
    configured = os.getenv("ALLOWED_DOMAINS", "").strip()
    if configured:
        for item in configured.split(","):
            if item.strip():
                ALLOWED_DOMAINS.add(item.strip().lower())
    else:
        ALLOWED_DOMAINS.update({"yourdomain.com", "example.com"})
    return host in ALLOWED_DOMAINS

sys.path.insert(0, str(ROOT_DIR))

from plus_paypal_link_probe import mask_proxy, normalize_proxy, sanitize_url  # noqa: E402
from protocol_paypal_authorize import (  # noqa: E402
    CheckoutGuardError,
    build_confirm_payload,
    checkout_amount_guard,
    find_url,
)


TOKEN_RE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
REAL_PAYPAL_AUTHORIZE_RE = re.compile(r"^https://pm-redirects\.stripe\.com/authorize/")
STRIPE_INIT_VERSION = "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1"
STRIPE_RUNTIME_VERSION = "6f8494a281"
CHECKOUT_MATRIX_DEFAULT = "FR:EUR,DE:EUR,IE:EUR,NL:EUR,BE:EUR,ES:EUR,IT:EUR,PT:EUR,AT:EUR,FI:EUR,LU:EUR,GB:GBP,DK:DKK,SE:SEK,NO:NOK,US:USD,JP:JPY"
COUNTRY_CURRENCY = {
    "AT": "EUR",
    "BE": "EUR",
    "CH": "CHF",
    "DE": "EUR",
    "DK": "DKK",
    "ES": "EUR",
    "FI": "EUR",
    "FR": "EUR",
    "GB": "GBP",
    "IE": "EUR",
    "IT": "EUR",
    "JP": "JPY",
    "LU": "EUR",
    "NL": "EUR",
    "NO": "NOK",
    "PT": "EUR",
    "SE": "SEK",
    "US": "USD",
}
COUNTRY_NAME_TO_CODE = {
    "austria": "AT",
    "belgium": "BE",
    "denmark": "DK",
    "finland": "FI",
    "france": "FR",
    "germany": "DE",
    "ireland": "IE",
    "italy": "IT",
    "japan": "JP",
    "luxembourg": "LU",
    "netherlands": "NL",
    "norway": "NO",
    "portugal": "PT",
    "spain": "ES",
    "sweden": "SE",
    "switzerland": "CH",
    "united kingdom": "GB",
    "united states": "US",
}
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,63}$")
POSTAL_CODE_PATTERNS = {
    "CA": re.compile(r"^[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d$"),
    "GB": re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}$", re.I),
    "IE": re.compile(r"^[A-Za-z0-9]{3}\s?[A-Za-z0-9]{4}$"),
    "JP": re.compile(r"^\d{3}-?\d{4}$"),
    "US": re.compile(r"^\d{5}(?:-\d{4})?$"),
}
DEFAULT_POSTAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 -]{2,12}$")
COUNTRY_BILLING_NAMES = {
    "DE": ["Max Mueller", "Leon Schmidt", "Paul Fischer"],
    "FR": ["Jean Martin", "Lucas Bernard", "Louis Petit"],
    "GB": ["John Smith", "James Brown", "Oliver Wilson"],
    "IE": ["Sean Murphy", "Liam Kelly", "Conor Walsh"],
    "JP": ["Taro Yamada", "Ken Suzuki", "Haruto Tanaka"],
    "US": ["John Doe", "James Smith", "David Miller"],
}
COUNTRY_BILLING_TEMPLATES = {
    "AT": ("Kaerntner Strasse 1", "Vienna", "Vienna", "1010"),
    "BE": ("Rue Neuve 1", "Brussels", "Brussels", "1000"),
    "CH": ("Bahnhofstrasse 1", "Zurich", "Zurich", "8001"),
    "DE": ("Invalidenstrasse 1", "Berlin", "Berlin", "10115"),
    "DK": ("Nyhavn 1", "Copenhagen", "Capital Region", "1051"),
    "ES": ("Calle Mayor 1", "Madrid", "Madrid", "28013"),
    "FI": ("Mannerheimintie 1", "Helsinki", "Uusimaa", "00100"),
    "FR": ("10 Rue de Rivoli", "Paris", "Ile-de-France", "75001"),
    "GB": ("10 Downing Street", "London", "England", "SW1A 2AA"),
    "IE": ("1 O'Connell Street", "Dublin", "Dublin", "D01 F5P2"),
    "IT": ("Via Roma 1", "Rome", "Lazio", "00184"),
    "JP": ("1-1 Chiyoda", "Chiyoda-ku", "Tokyo", "100-0001"),
    "LU": ("Grand Rue 1", "Luxembourg", "Luxembourg", "1661"),
    "NL": ("Damrak 1", "Amsterdam", "North Holland", "1012"),
    "NO": ("Karl Johans gate 1", "Oslo", "Oslo", "0154"),
    "PT": ("Rua Augusta 1", "Lisbon", "Lisbon", "1100-048"),
    "SE": ("Drottninggatan 1", "Stockholm", "Stockholm", "111 51"),
    "US": ("350 5th Ave", "New York", "NY", "10001"),
}
JP_CITY_BILLING_TEMPLATES = {
    "fukuoka": ("4-2-8 Hakata", "Fukuoka-shi", "Fukuoka", "812-0011"),
    "hiroshima": ("1-1 Motomachi", "Hiroshima-shi", "Hiroshima", "730-0011"),
    "kobe": ("1-1 Kanocho", "Kobe-shi", "Hyogo", "650-0001"),
    "kyoto": ("1-1 Gionmachi", "Kyoto-shi", "Kyoto", "605-0074"),
    "nagoya": ("3-4-5 Sakae", "Nagoya-shi", "Aichi", "460-0008"),
    "osaka": ("2-1-1 Namba", "Osaka-shi", "Osaka", "542-0076"),
    "sapporo": ("1-1 Odori Nishi", "Sapporo-shi", "Hokkaido", "060-0042"),
    "sasebo": ("1-1 Miuracho", "Sasebo-shi", "Nagasaki", "857-0863"),
    "tokyo": ("1-1 Chiyoda", "Chiyoda-ku", "Tokyo", "100-0001"),
    "yokohama": ("1-1 Minato Mirai", "Yokohama-shi", "Kanagawa", "220-0012"),
}
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
ZERO_DECIMAL_CURRENCIES = {
    "bif",
    "clp",
    "djf",
    "gnf",
    "jpy",
    "kmf",
    "krw",
    "mga",
    "pyg",
    "rwf",
    "ugx",
    "vnd",
    "vuv",
    "xaf",
    "xof",
    "xpf",
}

RATE_BUCKET: dict[str, list[float]] = {}
RATE_LOCK = threading.Lock()


@dataclass
class PublicApiError(Exception):
    code: str
    message: str
    status: int = HTTPStatus.BAD_REQUEST
    details: dict[str, Any] | None = None


def shutdown_executor(executor: ThreadPoolExecutor, *, wait: bool = False, cancel_futures: bool = False) -> None:
    if cancel_futures:
        try:
            executor.shutdown(wait=wait, cancel_futures=True)
            return
        except TypeError:
            pass
    executor.shutdown(wait=wait)


def amount_display(amount_due: int | None, currency: str) -> str:
    if amount_due is None:
        return "unknown"
    code = str(currency or "").upper() or "UNKNOWN"
    if str(currency or "").lower() in ZERO_DECIMAL_CURRENCIES:
        value = str(amount_due)
    else:
        value = f"{amount_due / 100:.2f}"
    return f"{value} {code}"


def decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        segment = token.split(".", 2)[1]
        segment += "=" * ((4 - len(segment) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(segment.encode("utf-8")).decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def is_openai_access_token(token: str) -> bool:
    payload = decode_jwt_payload(token)
    audience = payload.get("aud") or []
    if isinstance(audience, str):
        audience = [audience]
    return "https://api.openai.com/v1" in audience


def sanitize_error_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value
        text = re.sub(
            r"(socks5h?|https?)://([^:@/\s]+):([^@/\s]+)@",
            r"\1://<redacted>:<redacted>@",
            text,
        )
        text = re.sub(
            r"(?<![\w.-])([\w.-]+):(\d{2,5}):([^:\s@]+):([^\s@]+)",
            r"\1:\2:<redacted>:<redacted>",
            text,
        )
        return text
    if isinstance(value, dict):
        return {k: sanitize_error_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_error_value(v) for v in value]
    return value


def extract_access_token(raw_value: str) -> str:
    text = str(raw_value or "").strip()
    if not text:
        raise PublicApiError("missing_credential", "请填写 accessToken 或 /api/auth/session JSON")
        if len(text.encode("utf-8")) > MAX_BODY_BYTES:
            raise PublicApiError("credential_too_large", "Session 内容过大，已拒绝处理")

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PublicApiError("invalid_session_json", "Session JSON 无法解析") from exc
        candidates = [
            data.get("accessToken") if isinstance(data, dict) else "",
            data.get("access_token") if isinstance(data, dict) else "",
            data.get("token") if isinstance(data, dict) else "",
            data.get("session", {}).get("accessToken") if isinstance(data.get("session"), dict) else "",
            data.get("session", {}).get("access_token") if isinstance(data.get("session"), dict) else "",
        ]
        direct = [str(item).strip() for item in candidates if str(item or "").strip()]
        token = next((item for item in direct if is_openai_access_token(item)), "")
        if not token:
            nested = collect_access_tokens(data)
            token = next((item for item in nested if is_openai_access_token(item)), "")
        if not token:
            token = direct[0] if direct else ""
    else:
        token = text

    if not TOKEN_RE.fullmatch(token):
        raise PublicApiError("invalid_access_token", "未识别到合法 JWT 格式 accessToken")
    return token


def collect_access_tokens(value: Any) -> list[str]:
    found: list[str] = []

    def add_token(candidate: Any) -> None:
        text = str(candidate or "").strip()
        if TOKEN_RE.fullmatch(text):
            found.append(text)

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key in ("accessToken", "access_token", "token"):
                add_token(item.get(key))
            for child in item.values():
                walk(child)
            return
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if isinstance(item, str):
            for match in TOKEN_RE.finditer(item):
                add_token(match.group(0))

    if isinstance(value, (dict, list)):
        walk(value)
    else:
        text = str(value or "").strip()
        if not text:
            return []
        if text.startswith(("{", "[")):
            try:
                walk(json.loads(text))
            except Exception:
                pass
        walk(text)
    deduped = list(dict.fromkeys(found))
    access_tokens = [item for item in deduped if is_openai_access_token(item)]
    return access_tokens or deduped


def extract_proxy(raw_value: str) -> str:
    return extract_proxy_candidates(raw_value)[0]


def is_proxy_provider_url(value: str) -> bool:
    parsed = urlsplit(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and "pickdynamicips" in parsed.path.lower()


def provider_proxy_scheme(provider_url: str) -> str:
    params = dict(parse_qsl(urlsplit(provider_url).query, keep_blank_values=True))
    protocol = str(params.get("p") or params.get("protocol") or "").strip().lower()
    if protocol in {"socks5", "socks5h"}:
        return "socks5h"
    if protocol in {"http", "https"}:
        return protocol
    return ""


def with_provider_scheme(entry: str, scheme: str) -> str:
    entry = str(entry or "").strip()
    if not entry or not scheme or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", entry):
        return entry
    return f"{scheme}://{entry}"


def mark_provider_leased(proxy: str) -> None:
    if not proxy:
        return
    with PROVIDER_LEASED_PROXIES_LOCK:
        PROVIDER_LEASED_PROXIES.add(proxy)


def is_provider_leased(proxy: str) -> bool:
    with PROVIDER_LEASED_PROXIES_LOCK:
        return proxy in PROVIDER_LEASED_PROXIES


def fetch_proxy_provider_entries(provider_url: str, samples: int) -> list[str]:
    from curl_cffi import requests as curl_requests

    sample_count = max(1, min(samples, MAX_PROXY_CANDIDATES))
    scheme = provider_proxy_scheme(provider_url)
    worker_count = max(1, min(PROXY_PREFLIGHT_WORKERS, sample_count))
    entries: list[str] = []
    entries_lock = threading.Lock()

    def fetch_once() -> None:
        response = curl_requests.get(
            provider_url,
            impersonate="chrome",
            timeout=PROXY_PROVIDER_FETCH_TIMEOUT_SECONDS,
            verify=True,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"provider_http_{response.status_code}")
        lines = [item.strip() for item in re.split(r"[\r\n]+", response.text or "") if item.strip()]
        with entries_lock:
            entries.extend(with_provider_scheme(item, scheme) for item in lines)

    executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="pp-proxy-provider")
    futures = [executor.submit(fetch_once) for _ in range(sample_count)]
    errors: list[str] = []
    try:
        for future in as_completed(futures, timeout=max(PROXY_PROVIDER_FETCH_TIMEOUT_SECONDS + 2.0, 3.0)):
            try:
                future.result()
            except PublicApiError:
                raise
            except Exception as exc:
                errors.append(type(exc).__name__)
    except TimeoutError:
        errors.append("Timeout")
    finally:
        shutdown_executor(executor, wait=False, cancel_futures=True)

    deduped = [item for item in dict.fromkeys(entries) if item]
    if not deduped:
        raise PublicApiError(
            "proxy_provider_empty",
            "代理池接口未返回可用代理",
            HTTPStatus.BAD_GATEWAY,
            {"errors": errors[-6:]},
        )
    for proxy in deduped:
        mark_provider_leased(proxy)
    return deduped


def lease_proxy_key(proxy: str) -> str:
    return f"{LEASED_PROXY_PREFIX}{proxy}"


def get_proxy_lease(proxy: str) -> dict[str, Any] | None:
    return get_cached_proxy_geo(lease_proxy_key(proxy))


def set_proxy_lease(proxy: str, geo: dict[str, Any]) -> None:
    set_cached_proxy_geo(lease_proxy_key(proxy), geo)


def proxy_badness(proxy: str) -> float:
    now = time.monotonic()
    with PROXY_BADNESS_LOCK:
        item = PROXY_BADNESS.get(proxy)
        if not item:
            return 0.0
        updated_at, score = item
        if now - updated_at > PROXY_BADNESS_TTL_SECONDS:
            PROXY_BADNESS.pop(proxy, None)
            return 0.0
        return float(score)


def mark_proxy_bad(proxy: str, weight: float = 1.0) -> None:
    if not proxy:
        return
    now = time.monotonic()
    with PROXY_BADNESS_LOCK:
        _, current = PROXY_BADNESS.get(proxy, (0.0, 0.0))
        PROXY_BADNESS[proxy] = (now, min(PROXY_BADNESS_BLOCK_SCORE + 2.0, float(current) + weight))


def mark_proxy_good(proxy: str) -> None:
    if not proxy:
        return
    with PROXY_BADNESS_LOCK:
        PROXY_BADNESS.pop(proxy, None)


def sort_by_proxy_health(candidates: list[str]) -> list[str]:
    return sorted(candidates, key=lambda proxy: proxy_badness(proxy))


def split_proxy_entries(raw_value: str) -> list[str]:
    raw_proxy = str(raw_value or os.getenv("PLUS_LINK_PROXY") or "").strip()
    if not raw_proxy:
        raise PublicApiError("missing_proxy", "请填写代理，或设置 PLUS_LINK_PROXY")
    if len(raw_proxy.encode("utf-8")) > MAX_PROXY_POOL_BYTES:
        raise PublicApiError("proxy_too_large", "代理池配置过长，已拒绝处理")
    entries: list[str] = []
    for line in [item.strip() for item in re.split(r"[\n\r]+", raw_proxy) if item.strip()]:
        if is_proxy_provider_url(line):
            entries.append(line)
            continue
        entries.extend(item.strip() for item in re.split(r"[,;]+", line) if item.strip())
    if not entries:
        raise PublicApiError("missing_proxy", "请填写代理，或设置 PLUS_LINK_PROXY")
    resolved: list[str] = []
    for entry in entries:
        if is_proxy_provider_url(entry):
            resolved.extend(fetch_proxy_provider_entries(entry, PROXY_CITY_SAMPLES))
        else:
            resolved.append(entry)
    return list(dict.fromkeys(resolved))


def normalize_proxy_entry(raw_proxy: str) -> list[str]:
    raw_proxy = str(raw_proxy or "").strip()
    explicit_scheme = ""
    scheme_match = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*)://(.+)$", raw_proxy)
    if scheme_match:
        explicit_scheme = scheme_match.group(1).lower()
        raw_proxy = scheme_match.group(2).strip()
        if os.getenv("PLUS_LINK_FORCE_HTTP", "0") == "1":
            explicit_scheme = ""

    # 兼容代理商常见格式：host:port:user:pass -> scheme://user:pass@host:port
    # libcurl 只接受标准 URL 代理格式；不转换会把第二个冒号后的账号当成端口，触发 curl(5)。
    if "@" not in raw_proxy:
        parts = raw_proxy.split(":")
        if len(parts) >= 4 and parts[1].isdigit():
            host = parts[0].strip()
            port = parts[1].strip()
            user = parts[2].strip()
            password = ":".join(parts[3:]).strip()
            if host and port and user and password:
                raw_proxy = f"{user}:{password}@{host}:{port}"

    # 防止用户输入漏掉冒号和端口号。
    parts = raw_proxy.split("@")
    if len(parts) > 1:
        host_part = parts[-1]
        # 如果不含端口号，或者含有冒号但冒号后面不是有效的数字端口
        if ":" not in host_part:
            raise PublicApiError(
                "proxy_missing_port",
                "【日区动态家宽】配置中未检测到端口号！请补充完整的端口（格式需为: user:pass@host:port）"
            )
        else:
            # 进一步核验冒号后是否是纯数字端口
            port_str = host_part.split(":")[-1].split("/")[0]
            if not port_str.isdigit():
                raise PublicApiError(
                    "proxy_invalid_port",
                    "【日区动态家宽】配置中的端口号必须为纯数字！请重新核对（格式需为: user:pass@host:port）"
                )

    if explicit_scheme:
        if explicit_scheme not in {"socks5", "socks5h", "http", "https"}:
            raise PublicApiError("invalid_proxy", "代理协议不正确")
        return [normalize_proxy(raw_proxy, explicit_scheme)]

    configured = os.getenv("PLUS_LINK_PROXY_SCHEMES", "").strip()
    if not configured:
        configured = "socks5h" if "kookeey" in raw_proxy.lower() else "http"
    schemes = [item.strip().lower() for item in configured.split(",") if item.strip()]
    schemes = [item for item in schemes if item in {"http", "https", "socks5h", "socks5"}]
    if not schemes:
        schemes = ["http"]
    # Kookeey 的 1000 端口实测只稳定走 socks5h；其它代理默认 HTTP CONNECT。
    return [normalize_proxy(raw_proxy, scheme) for scheme in schemes]


def rotate_kookeey_session(proxy_url: str, nonce: int) -> str:
    suffix = f"{nonce % 100000000:08d}"
    if re.search(r"-[A-Za-z]{2}-\d{6,12}(?=@)", proxy_url):
        return re.sub(r"(-[A-Za-z]{2})-\d{6,12}(?=@)", rf"\1-{suffix}", proxy_url)
    if re.search(r"-[A-Za-z]{2}(?=@)", proxy_url):
        return re.sub(r"(-[A-Za-z]{2})(?=@)", rf"\1-{suffix}", proxy_url)
    return proxy_url


def expand_proxy_candidate(proxy_url: str) -> list[str]:
    if "kookeey" not in proxy_url.lower():
        return [proxy_url]
    if is_provider_leased(proxy_url):
        return [proxy_url]
    if re.search(r"-[A-Za-z]{2}-\d{6,12}(?=@)", proxy_url):
        return [proxy_url]
    expanded = [proxy_url]
    base_nonce = int(time.time() * 1000)
    for idx in range(1, max(1, PROXY_CITY_SAMPLES)):
        expanded.append(rotate_kookeey_session(proxy_url, base_nonce + idx))
    return expanded


def extract_proxy_candidates(raw_value: str) -> list[str]:
    candidates: list[str] = []
    for entry in split_proxy_entries(raw_value):
        for normalized in normalize_proxy_entry(entry):
            candidates.extend(expand_proxy_candidate(normalized))
    deduped = list(dict.fromkeys(candidates))
    return deduped[:MAX_PROXY_CANDIDATES]


def token_lock_key(access_token: str) -> str:
    return access_token[-32:] if len(access_token) > 32 else access_token


def lock_for_token(access_token: str) -> threading.Lock:
    key = token_lock_key(access_token)
    with BATCH_TOKEN_LOCKS_LOCK:
        return BATCH_TOKEN_LOCKS.setdefault(key, threading.Lock())


def preflight_proxy_candidates(candidates: list[str], *, refresh: bool = False) -> list[str]:
    if len(candidates) <= 1:
        if candidates:
            try:
                geo = probe_proxy_geo(candidates[0], timeout=3, use_cache=not refresh)
                set_proxy_lease(candidates[0], geo)
            except Exception:
                pass
        return candidates

    worker_count = max(1, min(PROXY_PREFLIGHT_WORKERS, len(candidates)))
    deadline = time.monotonic() + PROXY_PREFLIGHT_TIMEOUT_SECONDS
    scored: list[tuple[int, int, int, str]] = []

    def probe(index: int, proxy: str) -> tuple[int, int, int, str] | None:
        try:
            geo = probe_proxy_geo(proxy, timeout=3, use_cache=not refresh)
            if PROXY_TARGET_PREFLIGHT:
                target = check_proxy_target_reachability(proxy, timeout=4.0)
                if not target.get("ok"):
                    mark_proxy_bad(proxy, 2.0)
                    return None
                geo["target_preflight"] = target
        except Exception:
            return None
        country = country_code_from_geo(geo)
        city = str(geo.get("city") or "").strip()
        if not country or not city:
            return None
        latency = int(geo.get("latency_ms") or 999999)
        set_proxy_lease(proxy, geo)
        # 已拿到国家/城市的代理才进入 checkout；同 AT checkout 后续串行，避免互相作废。
        return (proxy_geo_priority(geo) + city_success_priority(geo), latency, index, proxy)

    executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="pp-proxy-preflight")
    futures = [executor.submit(probe, idx, proxy) for idx, proxy in enumerate(candidates)]
    try:
        pending = set(futures)
        while pending and time.monotonic() < deadline:
            done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            for future in done:
                item = future.result()
                if item is not None:
                    scored.append(item)
        for future in pending:
            future.cancel()
    finally:
        shutdown_executor(executor, wait=False, cancel_futures=True)

    if not scored:
        return candidates
    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    healthy = [proxy for _, _, _, proxy in scored]
    healthy_set = set(healthy)
    rest = [proxy for proxy in candidates if proxy not in healthy_set]
    return healthy + rest


def proxy_error_type(result: dict[str, Any], proxy: str) -> str:
    scheme = proxy.split("://", 1)[0] if "://" in proxy else "unknown"
    status = int(result.get("status") or 0)
    if status:
        return f"{scheme}://HTTP{status}"
    error_type = str(result.get("error_type") or "ProxyError")
    return f"{scheme}://{error_type}"


def normalize_transport_error(value: Any) -> str:
    text = str(value or "").lower()
    if not text:
        return "unknown"
    if "wrong_version_number" in text or "tls connect error" in text or "ssl" in text:
        return "tls_error"
    if "connection closed abruptly" in text or "connection reset" in text or "reset by peer" in text:
        return "connection_reset"
    if "timeout" in text or "timed out" in text or "operation timed" in text:
        return "timeout"
    if "could not resolve" in text or "dns" in text or "lookup" in text:
        return "dns_error"
    if "407" in text or "auth" in text and "proxy" in text:
        return "proxy_auth_error"
    if "failed to connect" in text or "connection refused" in text:
        return "connect_failed"
    return "network_error"


def check_proxy_target_reachability(proxy: str, timeout: float = 6.0) -> dict[str, Any]:
    from curl_cffi import requests as curl_requests

    targets = (
        ("chatgpt", "https://chatgpt.com/backend-api/accounts/check"),
        ("chatgpt_ping", "https://chatgpt.com/backend-api/sentinel/ping"),
        ("stripe", "https://api.stripe.com/v1/payment_pages/healthcheck"),
    )
    result: dict[str, Any] = {"ok": True, "targets": []}
    for name, url in targets:
        started = time.monotonic()
        try:
            if name == "chatgpt_ping":
                resp = curl_requests.post(
                    url,
                    proxy=proxy,
                    impersonate="chrome",
                    timeout=timeout,
                    verify=True,
                    json={},
                    headers={
                        "Origin": "https://chatgpt.com",
                        "Referer": "https://chatgpt.com/",
                        "x-openai-target-path": "/backend-api/sentinel/ping",
                        "x-openai-target-route": "/backend-api/sentinel/ping",
                    },
                )
            else:
                resp = curl_requests.get(
                    url,
                    proxy=proxy,
                    impersonate="chrome",
                    timeout=timeout,
                    verify=True,
                )
            status = int(getattr(resp, "status_code", 0) or 0)
            # 401/403/404 都说明 TLS 和代理隧道可到目标；真正要过滤的是 TLS/timeout/DNS/connect。
            target_ok = status > 0 and status < 600
            result["targets"].append(
                {
                    "name": name,
                    "status": status,
                    "ok": target_ok,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                }
            )
            if not target_ok:
                result["ok"] = False
        except Exception as exc:
            result["ok"] = False
            result["targets"].append(
                {
                    "name": name,
                    "ok": False,
                    "code": normalize_transport_error(exc),
                    "error_type": type(exc).__name__,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                }
            )
    return result


def city_key_from_info(info: dict[str, Any] | None) -> str:
    if not info:
        return "unknown"
    country = str(info.get("country") or info.get("country_name") or "unknown")
    region = str(info.get("region") or info.get("regionName") or "")
    city = str(info.get("city") or "unknown")
    return " / ".join(item for item in (country, region, city) if item)


def record_city_stat(info: dict[str, Any] | None, success: bool, elapsed_ms: int) -> None:
    key = city_key_from_info(info)
    with CITY_STATS_LOCK:
        bucket = CITY_STATS.setdefault(
            key,
            {
                "city": key,
                "attempts": 0,
                "success": 0,
                "fail": 0,
                "elapsed_ms": [],
            },
        )
        bucket["attempts"] += 1
        bucket["success"] += int(success)
        bucket["fail"] += int(not success)
        bucket["elapsed_ms"].append(elapsed_ms)
        bucket["elapsed_ms"] = bucket["elapsed_ms"][-200:]


def city_stats_snapshot() -> list[dict[str, Any]]:
    with CITY_STATS_LOCK:
        rows = []
        for item in CITY_STATS.values():
            attempts = int(item.get("attempts") or 0)
            success = int(item.get("success") or 0)
            if success <= 0:
                continue
            elapsed = list(item.get("elapsed_ms") or [])
            rows.append(
                {
                    "city": item.get("city") or "unknown",
                    "attempts": attempts,
                    "success": success,
                    "fail": int(item.get("fail") or 0),
                    "success_rate": round(success / max(1, attempts) * 100, 1),
                    "avg_ms": int(sum(elapsed) / max(1, len(elapsed))),
                }
            )
    rows.sort(key=lambda item: (-float(item["success_rate"]), -int(item["success"]), -int(item["attempts"]), int(item["avg_ms"])))
    return rows[:20]


def proxy_geo_priority(geo: dict[str, Any] | None) -> int:
    if not geo:
        return 50
    haystack = " ".join(
        str(geo.get(key) or "").strip().lower()
        for key in ("country", "region", "city", "org")
    )
    if any(item and item in haystack for item in SUCCESS_CITY_HINTS):
        return 0
    return 20


def city_success_priority(geo: dict[str, Any] | None) -> int:
    key = city_key_from_info(geo)
    with CITY_STATS_LOCK:
        item = CITY_STATS.get(key)
        if not item:
            return 500
        attempts = int(item.get("attempts") or 0)
        success = int(item.get("success") or 0)
        if success <= 0:
            return 500
        fail = int(item.get("fail") or 0)
        rate = success / max(1, attempts)
        return int((1.0 - rate) * 100) - success * 10 + fail * 5


def get_cached_proxy_geo(proxy: str) -> dict[str, Any] | None:
    now = time.monotonic()
    with PROXY_GEO_CACHE_LOCK:
        item = PROXY_GEO_CACHE.get(proxy)
        if not item:
            return None
        cached_at, geo = item
        if now - cached_at > PROXY_GEO_CACHE_TTL_SECONDS:
            PROXY_GEO_CACHE.pop(proxy, None)
            return None
        return dict(geo)


def set_cached_proxy_geo(proxy: str, geo: dict[str, Any]) -> None:
    if not country_code_from_geo(geo) or not str(geo.get("city") or "").strip():
        return
    with PROXY_GEO_CACHE_LOCK:
        PROXY_GEO_CACHE[proxy] = (time.monotonic(), dict(geo))


def probe_proxy_geo(proxy: str, timeout: float = 5.0, *, use_cache: bool = True) -> dict[str, Any]:
    from curl_cffi import requests as curl_requests

    if use_cache:
        cached = get_cached_proxy_geo(proxy)
        if cached:
            return cached

    started = time.monotonic()
    sources = (
        ("ipinfo", "https://ipinfo.io/json"),
        ("ipapi", "http://ip-api.com/json/?fields=status,country,countryCode,regionName,city,query,isp,org"),
        ("ipwho", "https://ipwho.is/"),
    )
    errors: list[str] = []

    def fetch(source: str, url: str) -> tuple[str, dict[str, Any] | None, str | None]:
        try:
            resp = curl_requests.get(
                url,
                proxy=proxy,
                impersonate="chrome",
                timeout=timeout,
                verify=True,
            )
            if resp.status_code != 200:
                return source, None, f"{source}:http{resp.status_code}"
            raw = resp.json() or {}
            if source == "ipapi":
                if raw.get("status") != "success":
                    return source, None, f"{source}:bad_status"
                data = {
                    "ip": raw.get("query"),
                    "country": raw.get("countryCode"),
                    "country_name": raw.get("country"),
                    "region": raw.get("regionName"),
                    "city": raw.get("city"),
                    "org": raw.get("org") or raw.get("isp"),
                }
            elif source == "ipwho":
                if raw.get("success") is False:
                    return source, None, f"{source}:bad_status"
                data = {
                    "ip": raw.get("ip"),
                    "country": raw.get("country_code"),
                    "country_name": raw.get("country"),
                    "region": raw.get("region"),
                    "city": raw.get("city"),
                    "org": (raw.get("connection") or {}).get("org") if isinstance(raw.get("connection"), dict) else "",
                }
            else:
                data = raw
            data["source"] = source
            data["latency_ms"] = int((time.monotonic() - started) * 1000)
            data["proxy_protocol"] = proxy.split("://", 1)[0] if "://" in proxy else "unknown"
            if country_code_from_geo(data) and str(data.get("city") or "").strip():
                return source, data, None
            return source, None, f"{source}:missing_geo"
        except Exception as exc:
            return source, None, f"{source}:{type(exc).__name__}"

    executor = ThreadPoolExecutor(max_workers=len(sources), thread_name_prefix="pp-geo")
    futures = [executor.submit(fetch, source, url) for source, url in sources]
    try:
        try:
            for future in as_completed(futures, timeout=max(timeout + 1.0, 2.0)):
                _, data, err = future.result()
                if data:
                    set_cached_proxy_geo(proxy, data)
                    for item in futures:
                        item.cancel()
                    return data
                if err:
                    errors.append(err)
        except TimeoutError:
            errors.append("geo:Timeout")
    finally:
        shutdown_executor(executor, wait=False, cancel_futures=True)

    raise PublicApiError(
        "proxy_geo_unavailable",
        "代理出口国家/城市识别失败，已跳过该节点避免使用假账单地址",
        HTTPStatus.BAD_GATEWAY,
        {"errors": errors[-6:]},
    )


def country_code_from_geo(info: dict[str, Any] | None) -> str:
    if not info:
        return ""
    for key in ("country", "country_code"):
        value = str(info.get(key) or "").strip().upper()
        if re.fullmatch(r"[A-Z]{2}", value):
            return value
    country_name = str(info.get("country_name") or info.get("countryName") or "").strip().lower()
    if country_name in COUNTRY_NAME_TO_CODE:
        return COUNTRY_NAME_TO_CODE[country_name]
    return ""


def checkout_pairs_for_proxy(payload: dict[str, Any], geo: dict[str, Any] | None) -> list[tuple[str, str]]:
    pairs = parse_checkout_matrix(payload)
    limit = int(payload.get("checkout_pair_limit") or CHECKOUT_PAIR_LIMIT or len(pairs))
    return pairs[: max(1, min(limit, len(pairs)))]


def validate_billing_identity(billing: dict[str, str]) -> dict[str, str]:
    name = str(billing.get("name") or "").strip()
    email = str(billing.get("email") or "").strip()
    country = str(billing.get("country") or "").strip().upper()
    postal_code = str(billing.get("postal_code") or "").strip()
    if not NAME_RE.fullmatch(name):
        raise PublicApiError("billing_invalid_name", "账单姓名格式异常", HTTPStatus.BAD_GATEWAY, {"country": country})
    if not EMAIL_RE.fullmatch(email):
        raise PublicApiError("billing_invalid_email", "账单邮箱格式异常", HTTPStatus.BAD_GATEWAY, {"country": country})
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise PublicApiError("billing_invalid_country", "账单国家格式异常", HTTPStatus.BAD_GATEWAY, {"country": country})
    postal_re = POSTAL_CODE_PATTERNS.get(country, DEFAULT_POSTAL_RE)
    if not postal_re.fullmatch(postal_code):
        raise PublicApiError(
            "billing_invalid_postal_code",
            "账单邮编格式异常",
            HTTPStatus.BAD_GATEWAY,
            {"country": country, "postal_code": postal_code},
        )
    return billing


def billing_email_for_name(name: str) -> str:
    local = re.sub(r"[^a-z0-9]+", ".", name.lower()).strip(".")
    return f"{local}.{uuid.uuid4().hex[:6]}@example.com"


def billing_from_proxy_geo(info: dict[str, Any] | None, fallback_country: str = "US") -> dict[str, str]:
    country = country_code_from_geo(info) or normalize_country_for_billing(fallback_country)
    city = str((info or {}).get("city") or "").strip().lower()
    if country == "JP":
        template = next((value for key, value in JP_CITY_BILLING_TEMPLATES.items() if key in city), COUNTRY_BILLING_TEMPLATES["JP"])
    else:
        template = COUNTRY_BILLING_TEMPLATES.get(country, COUNTRY_BILLING_TEMPLATES["US"])
        if country not in COUNTRY_BILLING_TEMPLATES:
            country = "US"
    names = COUNTRY_BILLING_NAMES.get(country, COUNTRY_BILLING_NAMES["US"])
    name = names[uuid.uuid4().int % len(names)]
    line1, template_city, state, postal_code = template
    return validate_billing_identity(
        {
            "name": name,
            "email": billing_email_for_name(name),
            "country": country,
            "line1": line1,
            "city": template_city,
            "state": state,
            "postal_code": postal_code,
        }
    )


def normalize_country_for_billing(country: str) -> str:
    country = str(country or "").strip().upper()
    return country if re.fullmatch(r"[A-Z]{2}", country) else "US"


def proxy_requests_session(proxy: str):
    from curl_cffi.requests import Session as CurlCffiSession

    session = CurlCffiSession(impersonate=CURL_IMPERSONATE_PROFILE)
    session.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session


def chatgpt_requests_session(proxy: str, access_token: str):
    device_id = str(uuid.uuid4())
    session = proxy_requests_session(proxy)
    session.headers.update(
        {
            "Authorization": f"Bearer {access_token}",
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
            "oai-device-id": device_id,
            "oai-language": "en-US",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "Cookie": f"oai-did={device_id}",
        }
    )
    return session


def create_checkout(
    access_token: str,
    proxy: str,
    country: str,
    currency: str,
    timeout_seconds: int = 30,
    chatgpt_session: Any | None = None,
) -> dict[str, Any]:
    session = chatgpt_session or chatgpt_requests_session(proxy, access_token)
    path = "/backend-api/payments/checkout"
    payload = {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {
            "country": country,
            "currency": currency,
        },
        "promo_campaign": {
            "promo_campaign_id": "plus-1-month-free",
            "is_coupon_from_query_param": False,
        },
        "checkout_ui_mode": "hosted",
    }
    try:
        response = session.post(
            "https://chatgpt.com" + path,
            json=payload,
            headers={
                "Referer": "https://chatgpt.com/",
                "x-openai-target-path": path,
                "x-openai-target-route": path,
            },
            timeout=timeout_seconds,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        text = getattr(response, "text", "") or ""
        try:
            data = response.json()
        except Exception:
            data = {"raw": text[:1000]}
        hosted_url = ""
        if isinstance(data, dict):
            for key in ("stripe_hosted_url", "hosted_checkout_url", "url", "checkout_url"):
                candidate = str(data.get(key) or "")
                if "/c/pay/" in candidate:
                    hosted_url = candidate
                    break
        if not hosted_url and isinstance(data, dict):
            checkout_session_id = str(data.get("checkout_session_id") or data.get("id") or "")
            client_secret = str(data.get("client_secret") or "")
            if checkout_session_id.startswith("cs_") and "_secret_" in client_secret:
                hosted_url = "https://pay.openai.com/c/pay/" + checkout_session_id + "#" + client_secret.split("_secret_", 1)[1]
        data = data if isinstance(data, dict) else {}
        return {
            "ok": 200 <= status < 300 and bool(hosted_url or data.get("checkout_session_id")),
            "status": status,
            "payload": payload,
            "checkout_session_id": data.get("checkout_session_id") or data.get("session_id") or data.get("id") or "",
            "processor_entity": data.get("processor_entity") or data.get("processorEntity") or "",
            "publishable_key": data.get("publishable_key") or "",
            "publishable_key_present": bool(data.get("publishable_key")),
            "checkout_ui_mode": data.get("checkout_ui_mode") or "",
            "requires_manual_approval": data.get("requires_manual_approval"),
            "hosted_checkout_url": hosted_url or str(data.get("url") or data.get("stripe_hosted_url") or data.get("checkout_url") or ""),
            "paypal_authorize_url": find_url(data),
            "raw_response": data,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "payload": payload,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "hosted_checkout_url": "",
            "paypal_authorize_url": "",
        }
    finally:
        if chatgpt_session is None:
            try:
                session.close()
            except Exception:
                pass


def parse_checkout_matrix(payload: dict[str, Any]) -> list[tuple[str, str]]:
    configured = str(payload.get("checkout_matrix") or os.getenv("PLUS_LINK_CHECKOUT_MATRIX") or "").strip()
    if not configured:
        country = str(payload.get("country") or os.getenv("PLUS_LINK_CHECKOUT_COUNTRY") or "").upper()
        currency = str(payload.get("currency") or os.getenv("PLUS_LINK_CHECKOUT_CURRENCY") or "").upper()
        configured = f"{country}:{currency}" if country and currency else CHECKOUT_MATRIX_DEFAULT

    pairs: list[tuple[str, str]] = []
    for item in re.split(r"[\n,;]+", configured):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            country, currency = item.split(":", 1)
        elif "/" in item:
            country, currency = item.split("/", 1)
        else:
            continue
        country = country.strip().upper()
        currency = currency.strip().upper()
        if len(country) == 2 and 3 <= len(currency) <= 4:
            pairs.append((country, currency))
    return list(dict.fromkeys(pairs)) or [("US", "USD")]


def stripe_init_http(proxy: str, publishable_key: str, checkout_session_id: str) -> dict[str, Any]:
    if not publishable_key:
        raise PublicApiError("stripe_publishable_key_missing", "checkout 未返回 Stripe publishable key，无法纯 HTTP init", HTTPStatus.BAD_GATEWAY)
    session = proxy_requests_session(proxy)
    try:
        body = {
            "browser_locale": "en-US",
            "browser_timezone": "Asia/Shanghai",
            "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
            "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
            "elements_session_client[elements_init_source]": "custom_checkout",
            "elements_session_client[referrer_host]": "chatgpt.com",
            "elements_session_client[stripe_js_id]": str(uuid.uuid4()),
            "elements_session_client[locale]": "en",
            "elements_session_client[is_aggregation_expected]": "false",
            "elements_options_client[saved_payment_method][enable_save]": "never",
            "elements_options_client[saved_payment_method][enable_redisplay]": "never",
            "key": publishable_key,
            "_stripe_version": STRIPE_INIT_VERSION,
        }
        response = session.post(
            f"https://api.stripe.com/v1/payment_pages/{checkout_session_id}/init",
            data=body,
            headers={
                "Origin": "https://pay.openai.com",
                "Referer": "https://pay.openai.com/",
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=STRIPE_INIT_TIMEOUT_SECONDS,
        )
        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:500]}
        if response.status_code >= 400:
            raise PublicApiError(
                "stripe_init_failed",
                "Stripe init 失败",
                HTTPStatus.BAD_GATEWAY,
                {"status": response.status_code, "raw_error": data},
            )
        return data
    finally:
        session.close()


def confirm_paypal_authorize_http(
    proxy: str,
    publishable_key: str,
    checkout_session_id: str,
    init: dict[str, Any],
    *,
    country: str,
) -> dict[str, Any]:
    amount_gate = checkout_amount_guard(init, require_zero=REQUIRE_ZERO_AMOUNT)
    session = proxy_requests_session(proxy)
    try:
        hosted_url = str(init.get("url") or init.get("stripe_hosted_url") or "")
        return_url = hosted_url or f"https://pay.openai.com/c/pay/{checkout_session_id}"
        endpoint = f"https://api.stripe.com/v1/payment_pages/{checkout_session_id}/confirm"
        payload = build_confirm_payload(
            publishable_key,
            init,
            return_url,
            require_zero=REQUIRE_ZERO_AMOUNT,
            country=country,
        )
        response = session.post(
            endpoint,
            data=payload,
            headers={
                "Origin": "https://pay.openai.com",
                "Referer": return_url.split("#", 1)[0],
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=STRIPE_INIT_TIMEOUT_SECONDS,
        )
        text = response.text or ""
        try:
            body = response.json()
        except Exception:
            body = {"raw": text[:1000]}
        authorize_url = find_url(body) or find_url(text)
        ok = 200 <= response.status_code < 300 and bool(REAL_PAYPAL_AUTHORIZE_RE.match(authorize_url))
        return {
            "ok": ok,
            "status": response.status_code,
            "response": body,
            "pm_authorize_url": authorize_url if ok else "",
            "zero_gate": amount_gate,
            "billing_country": country,
        }
    finally:
        session.close()


def extract_redirect_to_url(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    next_action = payload.get("next_action")
    if isinstance(next_action, dict) and next_action.get("type") == "redirect_to_url":
        redirect_to_url = next_action.get("redirect_to_url")
        if isinstance(redirect_to_url, dict):
            url = str(redirect_to_url.get("url") or "").strip()
            if url:
                return url
    for key in ("setup_intent", "payment_intent"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            found = extract_redirect_to_url(nested)
            if found:
                return found
    return find_url(payload)


def stripe_context(init: dict[str, Any]) -> dict[str, str]:
    stripe_js_id = str(uuid.uuid4())
    return {
        "stripe_js_id": stripe_js_id,
        "elements_session_id": f"elements_session_{uuid.uuid4().hex[:11]}",
        "elements_session_config_id": str(init.get("config_id") or uuid.uuid4()),
        "config_id": str(init.get("config_id") or ""),
        "init_checksum": str(init.get("init_checksum") or ""),
        "locale": "en",
        "runtime_version": STRIPE_RUNTIME_VERSION,
    }


def payment_method_billing(country: str, proxy_geo: dict[str, Any] | None = None) -> dict[str, str]:
    return billing_from_proxy_geo(proxy_geo, fallback_country=country)


def stripe_create_paypal_payment_method(
    proxy: str,
    publishable_key: str,
    checkout_session_id: str,
    init: dict[str, Any],
    billing_country: str,
    ctx: dict[str, str],
    proxy_geo: dict[str, Any] | None = None,
) -> str:
    billing = payment_method_billing(billing_country, proxy_geo)
    session = proxy_requests_session(proxy)
    try:
        body = {
            "billing_details[name]": billing["name"],
            "billing_details[email]": billing["email"],
            "billing_details[address][country]": billing["country"],
            "billing_details[address][line1]": billing["line1"],
            "billing_details[address][city]": billing["city"],
            "billing_details[address][postal_code]": billing["postal_code"],
            "billing_details[address][state]": billing["state"],
            "type": "paypal",
            "payment_user_agent": f"stripe.js/{ctx['runtime_version']}; stripe-js-v3/{ctx['runtime_version']}; payment-element; deferred-intent",
            "referrer": "https://chatgpt.com",
            "time_on_page": "35000",
            "client_attribution_metadata[checkout_session_id]": checkout_session_id,
            "client_attribution_metadata[client_session_id]": ctx["stripe_js_id"],
            "client_attribution_metadata[checkout_config_id]": ctx.get("config_id") or "",
            "client_attribution_metadata[elements_session_id]": ctx["elements_session_id"],
            "client_attribution_metadata[elements_session_config_id]": ctx["elements_session_config_id"],
            "client_attribution_metadata[merchant_integration_source]": "elements",
            "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
            "client_attribution_metadata[merchant_integration_version]": "2021",
            "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
            "client_attribution_metadata[payment_method_selection_flow]": "automatic",
            "client_attribution_metadata[merchant_integration_additional_elements][0]": "payment",
            "client_attribution_metadata[merchant_integration_additional_elements][1]": "address",
            "key": publishable_key,
            "_stripe_version": STRIPE_INIT_VERSION,
        }
        response = session.post("https://api.stripe.com/v1/payment_methods", data=body, timeout=STRIPE_PAYMENT_METHOD_TIMEOUT_SECONDS)
        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:500]}
        if response.status_code >= 400:
            raise PublicApiError(
                "stripe_payment_method_failed",
                "Stripe PayPal payment_method 创建失败",
                HTTPStatus.BAD_GATEWAY,
                {"status": response.status_code, "raw_error": data},
            )
        pm_id = str(data.get("id") or "")
        if not pm_id.startswith("pm_"):
            raise PublicApiError("stripe_payment_method_bad_response", "Stripe PayPal payment_method 响应异常", HTTPStatus.BAD_GATEWAY)
        return pm_id
    finally:
        session.close()


def stripe_confirm_custom_paypal(
    proxy: str,
    publishable_key: str,
    checkout_session_id: str,
    init: dict[str, Any],
    payment_method_id: str,
    ctx: dict[str, str],
    *,
    billing_country: str,
    processor_entity: str,
) -> dict[str, Any]:
    amount_gate = checkout_amount_guard(init, require_zero=REQUIRE_ZERO_AMOUNT)
    session = proxy_requests_session(proxy)
    try:
        return_url = stripe_confirm_return_url(checkout_session_id, init, billing_country, processor_entity)
        body = {
            "guid": uuid.uuid4().hex,
            "muid": uuid.uuid4().hex,
            "sid": uuid.uuid4().hex,
            "payment_method": payment_method_id,
            "init_checksum": str(init.get("init_checksum") or ctx.get("init_checksum") or ""),
            "version": ctx["runtime_version"],
            "expected_amount": str(amount_gate["amount_due"]),
            "expected_payment_method_type": "paypal",
            "return_url": return_url,
            "elements_session_client[session_id]": ctx["elements_session_id"],
            "elements_session_client[locale]": ctx["locale"],
            "elements_session_client[referrer_host]": "chatgpt.com",
            "elements_session_client[is_aggregation_expected]": "false",
            "elements_session_client[elements_init_source]": "custom_checkout",
            "elements_session_client[stripe_js_id]": ctx["stripe_js_id"],
            "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
            "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
            "elements_options_client[saved_payment_method][enable_save]": "never",
            "elements_options_client[saved_payment_method][enable_redisplay]": "never",
            "client_attribution_metadata[client_session_id]": ctx["stripe_js_id"],
            "client_attribution_metadata[checkout_session_id]": checkout_session_id,
            "client_attribution_metadata[checkout_config_id]": ctx.get("config_id") or "",
            "client_attribution_metadata[elements_session_id]": ctx["elements_session_id"],
            "client_attribution_metadata[elements_session_config_id]": ctx["elements_session_config_id"],
            "client_attribution_metadata[merchant_integration_source]": "checkout",
            "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
            "client_attribution_metadata[merchant_integration_version]": "custom",
            "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
            "client_attribution_metadata[payment_method_selection_flow]": "automatic",
            "client_attribution_metadata[merchant_integration_additional_elements][0]": "payment",
            "client_attribution_metadata[merchant_integration_additional_elements][1]": "address",
            "consent[terms_of_service]": "accepted",
            "key": publishable_key,
            "_stripe_version": STRIPE_INIT_VERSION,
        }
        response = session.post(f"https://api.stripe.com/v1/payment_pages/{checkout_session_id}/confirm", data=body, timeout=STRIPE_CONFIRM_TIMEOUT_SECONDS)
        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:1000]}
        if response.status_code >= 400:
            raise PublicApiError(
                "stripe_confirm_failed",
                "Stripe PayPal confirm 失败",
                HTTPStatus.BAD_GATEWAY,
                {"status": response.status_code, "raw_error": data},
            )
        data["_zero_gate"] = amount_gate
        return data
    finally:
        session.close()


def processor_entity_for_country(country: str, processor_entity: str = "") -> str:
    if processor_entity:
        return processor_entity
    return "openai_llc" if (country or "").upper() == "US" else "openai_ie"


def to_openai_pay_url(stripe_hosted_url: str) -> str:
    url = str(stripe_hosted_url or "").strip()
    if not url:
        return ""
    if url.startswith("https://checkout.stripe.com"):
        return "https://pay.openai.com" + url[len("https://checkout.stripe.com") :]
    parsed = urlsplit(url)
    if parsed.netloc.lower() == "checkout.stripe.com":
        return urlunsplit((parsed.scheme or "https", "pay.openai.com", parsed.path, parsed.query, parsed.fragment))
    return url


def chatgpt_success_return_url(checkout_session_id: str, country: str, processor_entity: str = "") -> str:
    entity = processor_entity_for_country(country, processor_entity)
    return f"https://chatgpt.com/checkout/verify?stripe_session_id={checkout_session_id}&processor_entity={entity}&plan_type=plus"


def stripe_checkout_long_url(checkout_session_id: str, country: str, processor_entity: str = "") -> str:
    return (
        f"https://checkout.stripe.com/c/pay/{checkout_session_id}"
        f"?returned_from_redirect=true&ui_mode=custom&return_url="
        f"{quote(chatgpt_success_return_url(checkout_session_id, country, processor_entity), safe='')}"
    )


def stripe_confirm_return_url(
    checkout_session_id: str,
    init: dict[str, Any],
    country: str,
    processor_entity: str = "",
) -> str:
    hosted_url = to_openai_pay_url(str(init.get("url") or init.get("stripe_hosted_url") or ""))
    if not hosted_url:
        hosted_url = stripe_checkout_long_url(checkout_session_id, country, processor_entity)
    if "pay.openai.com/" in hosted_url or "checkout.stripe.com/" in hosted_url:
        parsed = urlsplit(hosted_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.setdefault("success_return_url", chatgpt_success_return_url(checkout_session_id, country, processor_entity))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    return hosted_url


def chatgpt_approve_checkout(
    proxy: str,
    access_token: str,
    checkout_session_id: str,
    processor_entity: str,
    chatgpt_session: Any | None = None,
) -> dict[str, Any]:
    session = chatgpt_session or chatgpt_requests_session(proxy, access_token)
    try:
        try:
            session.post(
                "https://chatgpt.com/backend-api/sentinel/ping",
                json={},
                headers={
                    "x-openai-target-path": "/backend-api/sentinel/ping",
                    "x-openai-target-route": "/backend-api/sentinel/ping",
                },
                timeout=4,
            )
        except Exception:
            pass
        path = "/backend-api/payments/checkout/approve"
        response = session.post(
            "https://chatgpt.com" + path,
            json={"checkout_session_id": checkout_session_id, "processor_entity": processor_entity},
            headers={
                "Referer": f"https://chatgpt.com/checkout/{processor_entity}/{checkout_session_id}",
                "x-openai-target-path": path,
                "x-openai-target-route": path,
            },
            timeout=CHATGPT_APPROVE_TIMEOUT_SECONDS,
        )
        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:500]}
        if response.status_code >= 400:
            raise PublicApiError(
                "chatgpt_approve_failed",
                "ChatGPT checkout approve 失败",
                HTTPStatus.BAD_GATEWAY,
                {"status": response.status_code, "raw_error": data},
            )
        if not isinstance(data, dict):
            return {}
        result = data.get("result")
        if result not in {"approved", None, ""}:
            raise PublicApiError(
                "chatgpt_approve_rejected",
                "ChatGPT checkout approve 未通过",
                HTTPStatus.BAD_GATEWAY,
                {"status": response.status_code, "raw_error": data},
            )
        return data
    finally:
        if chatgpt_session is None:
            session.close()


def stripe_poll_redirect_url(
    proxy: str,
    publishable_key: str,
    checkout_session_id: str,
    *,
    timeout_seconds: float = 25.0,
) -> str:
    session = proxy_requests_session(proxy)
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    params = {
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[session_id]": f"elements_session_{uuid.uuid4().hex[:11]}",
        "elements_session_client[stripe_js_id]": str(uuid.uuid4()),
        "elements_session_client[locale]": "en",
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_options_client[saved_payment_method][enable_save]": "never",
        "elements_options_client[saved_payment_method][enable_redisplay]": "never",
        "key": publishable_key,
        "_stripe_version": STRIPE_INIT_VERSION,
    }
    try:
        last_status = 0
        while time.monotonic() < deadline:
            response = session.get(
                f"https://api.stripe.com/v1/payment_pages/{checkout_session_id}",
                params=params,
                timeout=5,
            )
            last_status = response.status_code
            if response.status_code == 200:
                try:
                    data = response.json()
                except Exception:
                    data = {}
                redirect_url = extract_redirect_to_url(data)
                if redirect_url:
                    return redirect_url
            time.sleep(0.75)
        raise PublicApiError(
            "stripe_redirect_poll_timeout",
            "Stripe redirect 轮询超时",
            HTTPStatus.BAD_GATEWAY,
            {"status": last_status},
        )
    finally:
        session.close()


def schedule_background_redirect_poll(
    *,
    proxy: str,
    publishable_key: str,
    checkout_session_id: str,
    token_hash: str,
    proxy_geo: dict[str, Any] | None,
    amount_gate: dict[str, Any],
    billing: dict[str, str],
    processor_entity: str,
    reason: str,
) -> None:
    if not checkout_session_id or not publishable_key or APPROVE_BACKGROUND_POLL_SECONDS <= 0:
        return
    job_key = hashlib.sha256(f"{token_hash}:{checkout_session_id}:{publishable_key[:16]}".encode("utf-8")).hexdigest()
    with BACKGROUND_JOB_LOCK:
        if job_key in BACKGROUND_JOB_KEYS:
            return
        BACKGROUND_JOB_KEYS.add(job_key)

    def job() -> None:
        try:
            redirect_url = stripe_poll_redirect_url(
                proxy,
                publishable_key,
                checkout_session_id,
                timeout_seconds=APPROVE_BACKGROUND_POLL_SECONDS,
            )
            if not REAL_PAYPAL_AUTHORIZE_RE.match(redirect_url):
                return
            elapsed_ms = int(APPROVE_BACKGROUND_POLL_SECONDS * 1000)
            record_city_stat(proxy_geo, True, elapsed_ms)
            increment_counter()
            append_background_link(
                {
                    "ok": True,
                    "code": "paypal_authorize_ready_background",
                    "token_hash": token_hash,
                    "checkout_session_id": checkout_session_id,
                    "processor_entity": processor_entity,
                    "amount_due": amount_gate.get("amount_due"),
                    "currency": amount_gate.get("currency"),
                    "amount_display": amount_display(int(amount_gate.get("amount_due") or 0), str(amount_gate.get("currency") or "")),
                    "paypal_authorize_url": redirect_url,
                    "proxy": mask_proxy(proxy),
                    "proxy_ip": (proxy_geo or {}).get("ip") or "",
                    "proxy_country": (proxy_geo or {}).get("country") or "",
                    "proxy_region": (proxy_geo or {}).get("region") or "",
                    "proxy_city": (proxy_geo or {}).get("city") or "",
                    "billing_country": billing.get("country") or "",
                    "billing_city": billing.get("city") or "",
                    "reason": reason,
                    "created_at": int(time.time()),
                }
            )
        except Exception:
            return
        finally:
            with BACKGROUND_JOB_LOCK:
                BACKGROUND_JOB_KEYS.discard(job_key)

    BACKGROUND_EXECUTOR.submit(job)


def confirm_custom_paypal_authorize_http(
    proxy: str,
    access_token: str,
    publishable_key: str,
    checkout_session_id: str,
    init: dict[str, Any],
    *,
    country: str,
    processor_entity: str,
    proxy_geo: dict[str, Any] | None = None,
    chatgpt_session: Any | None = None,
) -> dict[str, Any]:
    amount_gate = checkout_amount_guard(init, require_zero=REQUIRE_ZERO_AMOUNT)
    ctx = stripe_context(init)
    billing = payment_method_billing(country, proxy_geo)
    payment_method_id = stripe_create_paypal_payment_method(
        proxy,
        publishable_key,
        checkout_session_id,
        init,
        country,
        ctx,
        proxy_geo,
    )
    confirm = stripe_confirm_custom_paypal(
        proxy,
        publishable_key,
        checkout_session_id,
        init,
        payment_method_id,
        ctx,
        billing_country=billing["country"],
        processor_entity=processor_entity,
    )
    redirect_url = extract_redirect_to_url(confirm)
    submission = confirm.get("submission_attempt") if isinstance(confirm, dict) else None
    confirm_state = str(submission.get("state") or "") if isinstance(submission, dict) else ""
    approve_error: dict[str, Any] | None = None
    approve_raw_result = ""
    if not redirect_url and confirm_state == "requires_approval":
        try:
            approve = chatgpt_approve_checkout(proxy, access_token, checkout_session_id, processor_entity, chatgpt_session=chatgpt_session)
            redirect_url = extract_redirect_to_url(approve)
        except PublicApiError as exc:
            # 部分节点 approve 会返回 blocked，但 Stripe confirm 侧可能已经生成 redirect；
            # 不能在这里直接熔断账号，先继续轮询 Stripe，再把该节点归类为可换节点失败。
            approve_error = {
                "code": exc.code,
                "message": exc.message,
                "details": sanitize_error_value(exc.details or {}),
            }
            raw_result = ""
            raw_error = (exc.details or {}).get("raw_error") if isinstance(exc.details, dict) else None
            if isinstance(raw_error, dict):
                raw_result = str(raw_error.get("result") or "").lower()
            approve_raw_result = raw_result
            # approve 返回 blocked/expired 不立刻判死；confirm 侧可能已经异步生成 redirect。
            # 继续轮询 payment_pages，只有没有 redirect 时才把该节点归类为可换节点失败。
        except Exception as exc:
            # approve 请求在 TLS/timeout/connection reset 时也可能已经被上游接收；
            # 先继续同步轮询 Stripe，避免把可出链的节点提前判死。
            normalized_code = normalize_transport_error(exc)
            approve_error = {
                "code": normalized_code,
                "message": translate_exception(exc),
                "details": {
                    "error_type": type(exc).__name__,
                    "raw_error": sanitize_error_value(str(exc)[:240]),
                },
            }
    if not redirect_url:
        try:
            if approve_error:
                poll_seconds = max(BLOCKED_REDIRECT_POLL_SECONDS, REDIRECT_POLL_SECONDS)
            else:
                poll_seconds = REDIRECT_POLL_SECONDS
            redirect_url = stripe_poll_redirect_url(
                proxy,
                publishable_key,
                checkout_session_id,
                timeout_seconds=poll_seconds,
            )
        except PublicApiError as exc:
            if approve_error:
                approve_failure = classify_failure(
                    str(approve_error.get("code") or ""),
                    approve_error.get("details") if isinstance(approve_error.get("details"), dict) else {},
                )
                schedule_background_redirect_poll(
                    proxy=proxy,
                    publishable_key=publishable_key,
                    checkout_session_id=checkout_session_id,
                    token_hash=hashlib.sha256(access_token.encode("utf-8")).hexdigest()[:16],
                    proxy_geo=proxy_geo,
                    amount_gate=amount_gate,
                    billing=billing,
                    processor_entity=processor_entity,
                    reason=str(approve_error.get("code") or "approve_blocked"),
                )
                if approve_failure == "proxy_unstable":
                    raise PublicApiError(
                        "proxy_unstable",
                        "approve 阶段出现 TLS/连接/超时，已同步等候 Stripe redirect 后切换下一个代理节点",
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "confirm_state": confirm_state,
                            "poll_seconds": poll_seconds,
                            "approve_error": approve_error,
                            "poll_error": {
                                "code": exc.code,
                                "message": exc.message,
                                "details": sanitize_error_value(exc.details or {}),
                            },
                        },
                    ) from exc
                raise PublicApiError(
                    "paypal_authorize_approve_blocked",
                    "ChatGPT approve blocked/expired 且 Stripe redirect 未就绪，已切换下一个代理节点",
                    HTTPStatus.BAD_GATEWAY,
                    {
                        "confirm_state": confirm_state,
                        "poll_seconds": poll_seconds,
                        "background_poll_seconds": APPROVE_BACKGROUND_POLL_SECONDS,
                        "approve_error": approve_error,
                        "poll_error": {
                            "code": exc.code,
                            "message": exc.message,
                            "details": sanitize_error_value(exc.details or {}),
                        },
                    },
                ) from exc
            raise
    ok = bool(REAL_PAYPAL_AUTHORIZE_RE.match(redirect_url))
    return {
        "ok": ok,
        "status": 200 if ok else 502,
        "payment_method_id": payment_method_id,
        "pm_authorize_url": redirect_url if ok else "",
        "zero_gate": amount_gate,
        "billing_country": billing["country"],
        "billing_city": billing["city"],
        "billing_postal_code": billing["postal_code"],
        "confirm_state": confirm_state,
        **({"approve_error": approve_error} if approve_error else {}),
    }


def init_amount_summary(init: dict[str, Any]) -> dict[str, Any]:
    try:
        gate = checkout_amount_guard(init, require_zero=False)
    except CheckoutGuardError as exc:
        return exc.as_dict()
    return gate


def compact_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    item = {
        "country": attempt.get("country"),
        "currency": attempt.get("currency"),
        "checkout_status": attempt.get("checkout_status"),
        "init_status": attempt.get("init_status"),
        "amount_display": attempt.get("amount_display"),
        "zero_verified": attempt.get("zero_verified"),
        "paypal_present": attempt.get("paypal_present"),
        "checkout_ui_mode": attempt.get("checkout_ui_mode"),
        "requires_manual_approval": attempt.get("requires_manual_approval"),
        "processor_entity": attempt.get("processor_entity"),
        "code": attempt.get("code"),
    }
    if attempt.get("confirm_status") is not None:
        item["confirm_status"] = attempt.get("confirm_status")
    if attempt.get("confirm_errors"):
        item["confirm_errors"] = sanitize_error_value(attempt.get("confirm_errors"))
    if attempt.get("error_type"):
        item["error_type"] = sanitize_error_value(attempt.get("error_type"))
    if attempt.get("raw_error"):
        item["raw_error"] = sanitize_error_value(str(attempt.get("raw_error"))[:240])
    return item


def classify_failure(code: str, details: dict[str, Any] | None = None) -> str:
    text = json.dumps(sanitize_error_value(details or {}), ensure_ascii=False).lower()
    normalized = str(code or "").lower()
    if "checkout_status\": 401" in text or "http401" in text or "unauthorized" in text:
        return "token_unauthorized"
    if normalized in {"chatgpt_approve_rejected", "paypal_authorize_approve_blocked"} or "chatgpt_approve_rejected" in text or "result\": \"blocked" in text:
        return "approve_blocked"
    if "checkout_not_active_session" in text:
        return "proxy_unstable"
    if REQUIRE_ZERO_AMOUNT and ("amount_display\": \"19." in text or "zero_verified\": false" in text or "non_zero_amount" in text):
        return "non_zero_checkout"
    if normalized in {
        "timeout",
        "sslerror",
        "tlserror",
        "tls_error",
        "connection_reset",
        "connect_failed",
        "connectionerror",
        "dns_error",
        "network_error",
        "proxy_geo_unavailable",
        "proxy_ip_changed",
    } or any(marker in text for marker in ("timeout", "sslerror", "tls_error", "connection reset", "closed abruptly", "bad record mac")):
        return "proxy_unstable"
    if normalized == "paypal_authorize_ready":
        return "ready"
    return normalized or "unknown"


def is_proxy_transport_failure(code: str, details: dict[str, Any] | None = None) -> bool:
    return classify_failure(code, details or {}) == "proxy_unstable"


def is_batch_recoverable_failure(code: str, details: dict[str, Any] | None = None) -> bool:
    failure = classify_failure(code, details or {})
    if failure == "token_unauthorized":
        return TOKEN_UNAUTHORIZED_RECOVERY
    return failure in {"proxy_unstable", "approve_blocked", "paypal_authorize_not_ready"}


def serial_final_error(errors: list[dict[str, Any]], *, exhausted: bool = True) -> tuple[str, str, dict[str, Any] | None]:
    if not errors:
        return "paypal_authorize_failed", "本次未拿到有效 PayPal 授权链接", None
    failures = [str(item.get("failure") or classify_failure(str(item.get("code") or ""), item.get("details") if isinstance(item.get("details"), dict) else {})) for item in errors]
    if all(str(item.get("code") or "") == "proxy_geo_unavailable" for item in errors):
        return "proxy_geo_unavailable", "代理出口国家/城市识别失败，已跳过该节点避免使用假账单地址", None
    if failures and all(item == "proxy_unstable" for item in failures):
        return "proxy_unstable", "本账号已串行切换全部候选节点，均为 TLS/连接/超时类网络失败", None
    if failures and all(item == "token_unauthorized" for item in failures):
        return "token_unauthorized", "多个独立代理节点 checkout 均返回 401，已判定该 AT 当前不可用于 checkout", None
    if "approve_blocked" in failures:
        blocked = next(
            (
                item
                for item in reversed(errors)
                if str(item.get("failure") or classify_failure(str(item.get("code") or ""), item.get("details") if isinstance(item.get("details"), dict) else {})) == "approve_blocked"
            ),
            None,
        )
        return (
            "approve_blocked",
            "上游 checkout approve 返回 blocked/expired；已串行换节点，仍未放行 PayPal authorize",
            blocked.get("details") if isinstance(blocked, dict) and isinstance(blocked.get("details"), dict) else None,
        )
    informative = next(
        (item for item in reversed(errors) if str(item.get("code") or "") == "paypal_authorize_not_ready"),
        None,
    )
    last = informative or errors[-1]
    return (
        str(last.get("code") or "paypal_authorize_failed"),
        str(last.get("message") or "本次未拿到有效 PayPal 授权链接"),
        last.get("details") if isinstance(last.get("details"), dict) else None,
    )


def get_client_ip(handler: BaseHTTPRequestHandler) -> str:
    forwarded = str(handler.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    return handler.client_address[0] if handler.client_address else "unknown"


def allow_request(ip: str) -> bool:
    now = time.monotonic()
    with RATE_LOCK:
        bucket = [ts for ts in RATE_BUCKET.get(ip, []) if now - ts < RATE_WINDOW_SECONDS]
        if len(bucket) >= RATE_LIMIT_PER_WINDOW:
            RATE_BUCKET[ip] = bucket
            return False
        bucket.append(now)
        RATE_BUCKET[ip] = bucket
        return True


def translate_exception(exc: Exception) -> str:
    err_str = str(exc)
    # 检查最典型的代理及解析问题
    if "Could not resolve host" in err_str or "DNSError" in err_str:
        return "代理服务器域名解析失败。请检查[日区动态家宽]格式是否漏掉了端口号（格式需为: user:pass@host:port）"
    if "SSL_ERROR_SYSCALL" in err_str or "Connection reset by peer" in err_str:
        return "代理通道已连上但被对端重置。常见原因是代理协议类型不匹配、该节点不支持 HTTPS CONNECT/OpenAI、或节点被目标站阻断；网关会自动尝试 HTTP/SOCKS5 协议。"
    if "Connection refused" in err_str or "Failed to connect" in err_str or "socks connection failed" in err_str.lower():
        return "代理服务器连接拒绝。请确认该代理IP和端口是否有效，或此动态家宽代理已失效过时"
    if "SOCKS username/password rejected" in err_str or "auth failed" in err_str.lower() or "rejected by the SOCKS5 server" in err_str:
        server_ip = os.getenv("SERVER_PUBLIC_IP") or "您的网关服务器公网 IP"
        return f"家宽代理鉴权失败。请核对[代理密码]；若此代理为【免密 IP 白名单授权】，请务必登录代理商后台，将本网关服务器的公网 IP <code>{server_ip}</code> 完整添加到该家宽代理的【IP 白名单】中，否则代理会拒绝入网！"
    if "SOCKS auth method rejected" in err_str:
        return "代理认证模式不支持。请确保您的网络代理是标准 SOCKS5/socks5h 格式"
    if "timeout" in err_str.lower() or "timed out" in err_str.lower():
        return "请求代理超时。日区住宅链路延迟偏高，请更换为更流畅的动态家宽节点"
    
    # 默认兜底
    return f"服务端异常：{type(exc).__name__} ({err_str})"


def _run_extraction_with_proxy_inner(access_token: str, proxy: str, payload: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    attempts: list[dict[str, Any]] = []
    geo: dict[str, Any] = {}
    preheated_geo: dict[str, Any] = get_proxy_lease(proxy) or get_cached_proxy_geo(proxy) or {}
    verify_live_geo = str(os.getenv("PLUS_LINK_VERIFY_LIVE_GEO", "0")).lower() in {"1", "true", "yes", "on"}
    if preheated_geo and payload.get("_preflight_done") and not verify_live_geo:
        geo = dict(preheated_geo)
    else:
        try:
            geo = probe_proxy_geo(proxy, timeout=5, use_cache=not verify_live_geo)
        except PublicApiError:
            raise
        except Exception as exc:
            raise PublicApiError(
                "proxy_geo_unavailable",
                "代理出口国家/城市识别失败，已跳过该节点避免使用假账单地址",
                HTTPStatus.BAD_GATEWAY,
                {"error_type": type(exc).__name__},
            ) from exc
    preheated_ip = str(preheated_geo.get("ip") or "")
    live_ip = str(geo.get("ip") or "")
    if preheated_ip and live_ip and preheated_ip != live_ip:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        record_city_stat(preheated_geo or geo, False, elapsed_ms)
        raise PublicApiError(
            "proxy_ip_changed",
            "预热出口 IP 与实际使用 IP 不一致，已丢弃该粘性代理并继续采样",
            HTTPStatus.BAD_GATEWAY,
            {
                "preheated_ip": preheated_ip,
                "live_ip": live_ip,
                "preheated_city": city_key_from_info(preheated_geo),
                "live_city": city_key_from_info(geo),
            },
        )
    proxy_country = country_code_from_geo(geo)
    if not proxy_country or not str(geo.get("city") or "").strip():
        raise PublicApiError(
            "proxy_geo_unavailable",
            "代理出口国家/城市识别失败，已跳过该节点避免使用假账单地址",
            HTTPStatus.BAD_GATEWAY,
            {"geo": sanitize_error_value(geo)},
        )
    checkout_pairs = checkout_pairs_for_proxy(payload, geo)

    chatgpt_session = chatgpt_requests_session(proxy, access_token)
    try:
        for country, currency in checkout_pairs:
            checkout = create_checkout(access_token, proxy, country, currency, timeout_seconds=CHECKOUT_TIMEOUT_SECONDS, chatgpt_session=chatgpt_session)
            attempt: dict[str, Any] = {
                "country": country,
                "currency": currency,
                "checkout_status": checkout.get("status"),
                "checkout_ui_mode": checkout.get("checkout_ui_mode"),
                "requires_manual_approval": checkout.get("requires_manual_approval"),
                "processor_entity": checkout.get("processor_entity") or "",
            }
            if not checkout.get("ok"):
                attempt.update(
                    {
                        "code": "checkout_create_failed",
                        "error_type": proxy_error_type(checkout, proxy),
                        "raw_error": (
                            checkout.get("raw_response", {}).get("detail")
                            if isinstance(checkout.get("raw_response"), dict)
                            else None
                        )
                        or checkout.get("error"),
                    }
                )
                attempts.append(attempt)
                status = int(checkout.get("status") or 0)
                compact = compact_attempt(attempt)
                if status == 401:
                    raise PublicApiError(
                        "token_unauthorized",
                        "AT 已到达上游但 checkout 返回 401：切换独立代理复核",
                        HTTPStatus.UNAUTHORIZED,
                        {"attempts": [compact]},
                    )
                if status == 0 or status in {408, 425, 429, 500, 502, 503, 504, 520, 522, 524}:
                    raise PublicApiError(
                        "proxy_unstable",
                        "代理到 ChatGPT/Stripe checkout 关键域不稳定，已串行切换下一个节点",
                        HTTPStatus.BAD_GATEWAY,
                        {"attempts": [compact]},
                    )
                continue

            checkout_session_id = str(checkout.get("checkout_session_id") or "")
            publishable_key = str(checkout.get("publishable_key") or "")
            hosted_url = str(checkout.get("hosted_checkout_url") or "")

            if not (checkout_session_id and publishable_key):
                attempt.update({"code": "checkout_missing_fields", "hosted_url_present": bool(hosted_url)})
                attempts.append(attempt)
                continue

            try:
                init = stripe_init_http(proxy, publishable_key, checkout_session_id)
                attempt["init_status"] = 200
            except PublicApiError as exc:
                attempt.update({"code": exc.code, "details": sanitize_error_value(exc.details or {})})
                attempts.append(attempt)
                continue

            amount_gate = init_amount_summary(init)
            amount_due_raw = amount_gate.get("amount_due")
            currency_actual = str(amount_gate.get("currency") or init.get("currency") or currency).lower()
            methods = init.get("payment_method_types") if isinstance(init.get("payment_method_types"), list) else []
            attempt.update(
                {
                    "code": amount_gate.get("code"),
                    "amount_due": amount_due_raw,
                    "currency_actual": currency_actual,
                    "amount_display": amount_display(int(amount_due_raw), currency_actual) if isinstance(amount_due_raw, int) else "unknown",
                    "zero_verified": bool(amount_gate.get("zero_verified")),
                    "paypal_present": "paypal" in {str(item).lower() for item in methods},
                }
            )
            attempts.append(attempt)

            if (REQUIRE_ZERO_AMOUNT and not attempt["zero_verified"]) or not attempt["paypal_present"]:
                continue

            billing_country = proxy_country or str((init.get("geocoding") or {}).get("country_code") or "").upper() or country
            country_candidates = [billing_country]
            confirm: dict[str, Any] = {}
            confirm_errors: list[dict[str, Any]] = []
            for billing_country in [item for item in country_candidates if item]:
                try:
                    confirm = confirm_custom_paypal_authorize_http(
                        proxy,
                        access_token,
                        publishable_key,
                        checkout_session_id,
                        init,
                        country=billing_country,
                        processor_entity=processor_entity_for_country(country, str(checkout.get("processor_entity") or "")),
                        proxy_geo=geo,
                        chatgpt_session=chatgpt_session,
                    )
                except PublicApiError as exc:
                    confirm_errors.append(
                        {
                            "billing_country": billing_country,
                            "code": exc.code,
                            "details": sanitize_error_value(exc.details or {}),
                        }
                    )
                    continue
                authorize_url = str(confirm.get("pm_authorize_url") or "")
                if confirm.get("ok") and REAL_PAYPAL_AUTHORIZE_RE.match(authorize_url):
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    record_city_stat(geo, True, elapsed_ms)
                    return {
                        "ok": True,
                        "code": "paypal_authorize_ready",
                        "message": "真实 PayPal authorize 链已生成",
                        "zero_verified": bool(attempt["zero_verified"]),
                        "amount_due": amount_due_raw,
                        "currency": currency_actual,
                        "amount_display": attempt["amount_display"],
                        "payment_method": "paypal",
                        "checkout_session_id": checkout_session_id,
                        "processor_entity": checkout.get("processor_entity") or "",
                        "proxy": mask_proxy(proxy),
                        "proxy_protocol": proxy.split("://", 1)[0] if "://" in proxy else "unknown",
                        "proxy_ip": geo.get("ip") or "",
                        "preheated_proxy_ip": preheated_ip or geo.get("ip") or "",
                        "proxy_ip_verified": bool(live_ip and (not preheated_ip or preheated_ip == live_ip)),
                        "proxy_session_locked": True,
                        "proxy_country": geo.get("country") or "",
                        "proxy_region": geo.get("region") or "",
                        "proxy_city": geo.get("city") or "",
                        "proxy_org": geo.get("org") or "",
                        "billing_country": billing_country,
                        "elapsed_ms": elapsed_ms,
                        "hosted_checkout_url": "",
                        "paypal_authorize_url": authorize_url,
                        "checkout_attempts": [compact_attempt(item) for item in attempts],
                    }
            attempt.update(
                {
                    "code": "paypal_authorize_failed",
                    "confirm_status": confirm.get("status") if confirm else None,
                    "confirm_errors": confirm_errors[-3:],
                }
            )
    finally:
        chatgpt_session.close()

    elapsed_ms = int((time.monotonic() - started) * 1000)
    record_city_stat(geo, False, elapsed_ms)
    raise PublicApiError(
        "paypal_authorize_not_ready",
        "本次未拿到真实 $0 PayPal authorize 链，已跳过 Hosted/Link 假链；网络类失败会串行换节点继续",
        HTTPStatus.BAD_GATEWAY,
        {
            "proxy": mask_proxy(proxy),
            "preheated_proxy_ip": str(preheated_geo.get("ip") or ""),
            "proxy_ip": str(geo.get("ip") or ""),
            "proxy_ip_verified": bool(str(geo.get("ip") or "") and (not str(preheated_geo.get("ip") or "") or str(preheated_geo.get("ip") or "") == str(geo.get("ip") or ""))),
            "elapsed_ms": elapsed_ms,
            "attempts": [compact_attempt(item) for item in attempts[-8:]],
        },
    )


def run_extraction_with_proxy(access_token: str, proxy: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _run_extraction_with_proxy_inner(access_token, proxy, payload)


def run_extraction_race(access_token: str, candidates: list[str], payload: dict[str, Any]) -> dict[str, Any]:
    if not candidates:
        raise PublicApiError("missing_proxy", "请填写代理，系统不会直连")

    if not CHECKOUT_RACE_ENABLED:
        ordered = list(candidates) if payload.get("_preflight_done") else preflight_proxy_candidates(candidates)
        errors: list[dict[str, Any]] = []
        attempted: list[str] = []
        token_unauthorized_count = 0
        token_unauthorized_threshold = max(1, min(TOKEN_UNAUTHORIZED_CONFIRMATIONS, len(ordered)))
        approve_blocked_count = 0
        approve_blocked_threshold = max(1, min(APPROVE_BLOCKED_CONFIRMATIONS, len(ordered)))
        proxy_unstable_count = 0
        proxy_unstable_threshold = max(1, min(PROXY_UNSTABLE_CONFIRMATIONS, len(ordered)))
        with lock_for_token(access_token):
            for proxy in ordered:
                attempted.append(proxy)
                try:
                    result = run_extraction_with_proxy(access_token, proxy, payload)
                    if result.get("ok") and result.get("paypal_authorize_url"):
                        result["proxy_attempts"] = len(attempted)
                        result["candidate_count"] = len(ordered)
                        result["checkout_mode"] = "serial_per_token"
                        mark_proxy_good(proxy)
                        return result
                except PublicApiError as exc:
                    failure = classify_failure(exc.code, exc.details or {})
                    item = (
                        {
                            "proxy": mask_proxy(proxy),
                            "code": exc.code,
                            "message": exc.message,
                            "failure": failure,
                            "details": sanitize_error_value(exc.details or {}),
                        }
                    )
                    errors.append(item)
                    if failure == "proxy_unstable":
                        proxy_unstable_count += 1
                        mark_proxy_bad(proxy, 2.0)
                        if proxy_unstable_count >= proxy_unstable_threshold:
                            break
                        continue
                    if failure == "token_unauthorized":
                        token_unauthorized_count += 1
                        mark_proxy_bad(proxy, 0.25)
                        if token_unauthorized_count >= token_unauthorized_threshold:
                            break
                        continue
                    elif failure in {"approve_blocked", "non_zero_checkout"}:
                        mark_proxy_bad(proxy, 0.5)
                        if failure == "approve_blocked":
                            approve_blocked_count += 1
                            if approve_blocked_count >= approve_blocked_threshold:
                                break
                        continue
                except Exception as exc:
                    normalized_code = normalize_transport_error(exc)
                    failure = classify_failure(normalized_code, {"error": str(exc)})
                    errors.append(
                        {
                            "proxy": mask_proxy(proxy),
                            "code": normalized_code,
                            "error_type": type(exc).__name__,
                            "message": translate_exception(exc),
                            "failure": failure,
                        }
                    )
                    mark_proxy_bad(proxy, 2.0)
                    if failure == "proxy_unstable":
                        proxy_unstable_count += 1
                        if proxy_unstable_count >= proxy_unstable_threshold:
                            break
        if errors:
            final_code, final_message, final_details = serial_final_error(errors, exhausted=True)
            raise PublicApiError(
                final_code,
                final_message,
                HTTPStatus.BAD_GATEWAY,
                {
                    "proxy_attempts": [mask_proxy(item) for item in attempted[:12]],
                    "attempt_count": len(attempted),
                    "candidate_count": len(ordered),
                    "checkout_mode": "serial_per_token",
                    "errors": errors[-8:],
                    **({"last_details": sanitize_error_value(final_details)} if final_details else {}),
                },
            )
        raise PublicApiError("paypal_authorize_failed", "本次未拿到有效 PayPal 授权链接", HTTPStatus.BAD_GATEWAY)

    worker_count = max(1, min(PROXY_RACE_WORKERS, len(candidates)))
    executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="pp-proxy-race")
    futures = {executor.submit(run_extraction_with_proxy, access_token, proxy, payload): proxy for proxy in candidates}
    errors: list[dict[str, Any]] = []
    pending = set(futures)
    deadline = time.monotonic() + RACE_OVERALL_TIMEOUT_SECONDS
    try:
        while pending and time.monotonic() < deadline:
            done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                proxy = futures[future]
                try:
                    result = future.result()
                    if result.get("ok") and result.get("paypal_authorize_url"):
                        result["proxy_attempts"] = len(candidates)
                        result["checkout_mode"] = "race_per_token"
                        mark_proxy_good(proxy)
                        return result
                except PublicApiError as exc:
                    errors.append(
                        {
                            "proxy": mask_proxy(proxy),
                            "code": exc.code,
                            "message": exc.message,
                            "details": sanitize_error_value(exc.details or {}),
                        }
                    )
                    failure = classify_failure(exc.code, exc.details or {})
                    if failure == "proxy_unstable":
                        mark_proxy_bad(proxy, 2.0)
                    elif failure in {"approve_blocked", "non_zero_checkout"}:
                        mark_proxy_bad(proxy, 0.5)
                except Exception as exc:
                    normalized_code = normalize_transport_error(exc)
                    errors.append(
                        {
                            "proxy": mask_proxy(proxy),
                            "code": normalized_code,
                            "error_type": type(exc).__name__,
                            "message": translate_exception(exc),
                            "failure": classify_failure(normalized_code, {"error": str(exc)}),
                        }
                    )
                    mark_proxy_bad(proxy, 2.0)
        if pending:
            errors.append({"code": "proxy_race_timeout", "message": "代理竞速达到总超时，慢节点已隔离，不再拖住本次响应"})
    finally:
        shutdown_executor(executor, wait=False, cancel_futures=True)

    if errors:
        informative = next(
            (item for item in reversed(errors) if str(item.get("code") or "") == "paypal_authorize_not_ready"),
            None,
        )
        last = informative or errors[-1]
        raise PublicApiError(
            str(last.get("code") or "paypal_authorize_failed"),
            str(last.get("message") or "本次未拿到有效 PayPal 授权链接"),
            HTTPStatus.BAD_GATEWAY,
            {
                "proxy_attempts": [mask_proxy(item) for item in candidates[:12]],
                "attempt_count": len(candidates),
                "errors": errors[-8:],
            },
        )
    raise PublicApiError("paypal_authorize_failed", "本次未拿到有效 PayPal 授权链接", HTTPStatus.BAD_GATEWAY)


def run_extraction(payload: dict[str, Any]) -> dict[str, Any]:
    access_token = extract_access_token(str(payload.get("credential") or payload.get("accessToken") or ""))
    candidates = extract_proxy_candidates(str(payload.get("proxy") or ""))
    return run_extraction_race(access_token, candidates, payload)


class PlusLinkHandler(BaseHTTPRequestHandler):
    server_version = "PlusPayPalZeroGate/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stdout.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), fmt % args))

    def add_common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; object-src 'none';",
        )

    def send_json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.add_common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_ndjson_stream(self, rows: Any) -> None:
        self.send_response(HTTPStatus.OK)
        self.add_common_headers()
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        for row in rows:
            try:
                self.wfile.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
                self.wfile.flush()
            except BrokenPipeError:
                break

    def send_static(self, request_path: str) -> None:
        parsed_path = unquote(urlparse(request_path).path)
        rel = "index.html" if parsed_path in {"", "/"} else parsed_path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        static_root = STATIC_DIR.resolve()
        try:
            target.relative_to(static_root)
        except ValueError:
            self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "code": "not_found", "message": "Not found"})
            return
        if not target.is_file():
            self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "code": "not_found", "message": "Not found"})
            return
        content = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.add_common_headers()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def read_body_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            raise PublicApiError("empty_body", "请求体为空")
        if length > MAX_BODY_BYTES:
            raise PublicApiError("body_too_large", "请求体过大，已拒绝处理", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise PublicApiError("invalid_json", "请求 JSON 无法解析") from exc
        if not isinstance(data, dict):
            raise PublicApiError("invalid_json_shape", "请求体必须是 JSON object")
        return data

    def do_GET(self) -> None:
        if not is_valid_host(self.headers.get("Host")):
            self.send_json(HTTPStatus.FORBIDDEN, {"ok": False, "code": "domain_blocked", "message": "Domain access unauthorized. Anti-cloning shield active."})
            return
        client_ip = get_client_ip(self)
        record_visitor_active(client_ip)
        if urlparse(self.path).path == "/api/health":
            self.send_json(HTTPStatus.OK, {"ok": True, "service": "plus-paypal-zero-gate"})
            return
        if urlparse(self.path).path == "/api/stats":
            self.send_json(HTTPStatus.OK, {
                "ok": True,
                "success_count": load_counter(),
                "background_success_count": background_success_count(),
                "online_count": get_active_visitors_count(),
                "city_stats": city_stats_snapshot(),
            })
            return
        if urlparse(self.path).path == "/api/background-links":
            rows: list[dict[str, Any]] = []
            try:
                if BACKGROUND_LINKS_FILE.exists():
                    with BACKGROUND_LINKS_FILE.open("r", encoding="utf-8", errors="ignore") as fh:
                        for line in fh:
                            try:
                                row = json.loads(line)
                            except Exception:
                                continue
                            if row.get("ok") and REAL_PAYPAL_AUTHORIZE_RE.match(str(row.get("paypal_authorize_url") or "")):
                                rows.append(row)
                rows = rows[-200:]
            except Exception:
                rows = []
            self.send_json(HTTPStatus.OK, {"ok": True, "links": rows, "count": len(rows)})
            return
        self.send_static(self.path)

    def handle_test_proxy(self) -> None:
        try:
            payload = self.read_body_json()
            raw_proxy = payload.get("proxy", "")
            proxy_candidates = extract_proxy_candidates(raw_proxy)[:4]

            from curl_cffi import requests as curl_requests

            # 阶段 1: 测试基础代理连通度与 IP 归属
            start_time = time.monotonic()
            proxy = ""
            ip_data: dict[str, Any] = {}
            last_error: Exception | None = None
            try:
                def probe(candidate: str) -> tuple[str, Any]:
                    resp = curl_requests.get(
                        "https://ipinfo.io/json",
                        proxy=candidate,
                        impersonate="chrome",
                        timeout=5,
                        verify=True,
                    )
                    return candidate, resp

                base_resp = None
                with ThreadPoolExecutor(max_workers=max(1, len(proxy_candidates))) as pool:
                    futures = [pool.submit(probe, candidate) for candidate in proxy_candidates]
                    for future in as_completed(futures, timeout=6):
                        try:
                            candidate, resp = future.result()
                            if resp.status_code == 200:
                                proxy = candidate
                                base_resp = resp
                                break
                            base_resp = resp
                        except Exception as exc:
                            last_error = exc
                    for future in futures:
                        future.cancel()
                if base_resp is None:
                    raise last_error or RuntimeError("all proxy schemes failed")
                latency = int((time.monotonic() - start_time) * 1000)
                if base_resp.status_code != 200:
                    self.send_json(HTTPStatus.OK, {
                        "ok": True,
                        "ip": "unknown",
                        "country": "unknown",
                        "org": "unknown",
                        "city": "unknown",
                        "latency_ms": latency,
                        "proxy_protocol": proxy.split("://", 1)[0] if "://" in proxy else "unknown",
                        "checkout_ready": True,
                        "candidates": len(proxy_candidates),
                        "warning": f"IP 归属接口限频或返回 HTTP {base_resp.status_code}；正式提链将继续使用代理并发竞速。",
                    })
                    return
                ip_data = base_resp.json()
            except Exception as e:
                friendly_msg = translate_exception(e)
                self.send_json(HTTPStatus.OK, {
                    "ok": False,
                    "message": f"代理基础连接建立失败: {friendly_msg}",
                    "candidates": len(proxy_candidates),
                })
                return

            # 基础出口可用即可返回；关键域名由正式提链链路并发竞速，不在检测按钮里串行阻塞。
            self.send_json(HTTPStatus.OK, {
                "ok": True,
                "ip": ip_data.get("ip", "unknown"),
                "country": ip_data.get("country", "unknown"),
                "org": ip_data.get("org", "unknown"),
                "city": ip_data.get("city", "unknown"),
                "latency_ms": latency,
                "proxy_protocol": proxy.split("://", 1)[0] if "://" in proxy else "unknown",
                "checkout_ready": True,
                "candidates": len(proxy_candidates),
                "warning": "",
            })
            return

            # 旧的关键域名串行探测路径保留在下面，但不再执行，避免测试阶段拖慢真实提链。
            try:
                failed_checks = []
                checks = [
                    ("ChatGPT", "https://chatgpt.com"),
                    ("Pay OpenAI", "https://pay.openai.com"),
                    ("Stripe API", "https://api.stripe.com"),
                ]
                for label, url in checks:
                    try:
                        check_resp = curl_requests.get(
                            url,
                            proxy=proxy,
                            impersonate="chrome",
                            timeout=4,
                            verify=True
                        )
                        if check_resp.status_code not in range(200, 500):
                            failed_checks.append(f"{label}=HTTP {check_resp.status_code}")
                    except Exception as check_exc:
                        failed_checks.append(f"{label}={type(check_exc).__name__}")
            except Exception as e:
                # 专门捕获诸如 User was rejected by the SOCKS5 server 这种由于代理商拦截或没有开通 OpenAI 权限引发的错误
                err_str = str(e)
                if "rejected by the SOCKS5 server" in err_str or "SOCKS5 server" in err_str:
                    self.send_json(HTTPStatus.OK, {
                        "ok": False,
                        "message": "基础代理虽通，但<b>该家宽代理未开通 OpenAI (ChatGPT) 访问权限</b>！请在代理商控制台中开通 OpenAI (ChatGPT) 通行策略，或者联系代理客服解决。"
                    })
                else:
                    friendly_msg = translate_exception(e)
                    self.send_json(HTTPStatus.OK, {
                        "ok": False,
                        "message": f"基础代理虽通，但在连接 ChatGPT (OpenAI) 专用域名时失败: {friendly_msg}。请确保该代理能正常出网访问 OpenAI。"
                    })
                return

            # 两阶段全部通航，返回最顶配的成功卡片！
            self.send_json(HTTPStatus.OK, {
                "ok": True,
                "ip": ip_data.get("ip", "unknown"),
                "country": ip_data.get("country", "unknown"),
                "org": ip_data.get("org", "unknown"),
                "city": ip_data.get("city", "unknown"),
                "latency_ms": latency,
                "proxy_protocol": proxy.split("://", 1)[0] if "://" in proxy else "unknown",
                "checkout_ready": len(failed_checks) == 0,
                "warning": ("关键域名探测有超时，但基础 IP 可用，转链会继续尝试浏览器兜底：" + "，".join(failed_checks)) if failed_checks else "",
            })
                
        except PublicApiError as exc:
            self.send_json(HTTPStatus.OK, {
                "ok": False,
                "message": exc.message
            })
        except Exception as exc:
            import traceback
            traceback.print_exc()
            friendly_msg = translate_exception(exc)
            self.send_json(HTTPStatus.OK, {
                "ok": False,
                "message": friendly_msg
            })

    def stream_extract_batch(self) -> None:
        client_ip = get_client_ip(self)
        if not allow_request(client_ip):
            self.send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"ok": False, "code": "rate_limited", "message": "请求过快，请稍后再试"},
            )
            return

        payload = self.read_body_json()
        raw_tokens = payload.get("tokens")
        if isinstance(raw_tokens, list):
            tokens: list[str] = []
            for item in raw_tokens:
                tokens.extend(collect_access_tokens(item))
            tokens = list(dict.fromkeys(tokens))
        else:
            tokens = collect_access_tokens(payload.get("credential") or payload.get("accessToken") or "")
        if not tokens:
            raise PublicApiError("invalid_access_token", "未解析到任何合法 JWT accessToken")

        raw_proxy = str(payload.get("proxy") or "")
        if not raw_proxy.strip():
            raise PublicApiError("missing_proxy", "请填写代理，系统不会直连")

        max_tokens = int(os.getenv("PLUS_LINK_BATCH_MAX_TOKENS", "200"))
        tokens = tokens[:max_tokens]
        worker_count = max(1, min(BATCH_WORKERS, len(tokens)))

        def task(index: int, token: str, healthy_candidates: list[str], per_token_proxy_count: int, round_no: int = 0) -> dict[str, Any]:
            task_started = time.monotonic()

            def with_elapsed(row: dict[str, Any]) -> dict[str, Any]:
                elapsed_ms = int((time.monotonic() - task_started) * 1000)
                result = row.get("result")
                if isinstance(result, dict):
                    result.setdefault("elapsed_ms", elapsed_ms)
                else:
                    row["elapsed_ms"] = elapsed_ms
                return row

            healthy_candidates = sort_by_proxy_health(healthy_candidates)
            offset = ((index - 1) + round_no * per_token_proxy_count) % len(healthy_candidates)
            rotated_all = healthy_candidates[offset:] + healthy_candidates[:offset]
            rotated = rotated_all[:per_token_proxy_count]
            try:
                task_payload = dict(payload)
                task_payload["_preflight_done"] = True
                result = run_extraction_race(token, rotated, task_payload)
                if result.get("ok"):
                    increment_counter()
                return with_elapsed({
                    "index": index,
                    "ok": bool(result.get("ok")),
                    "round": round_no,
                    "result": result,
                })
            except PublicApiError as exc:
                return with_elapsed({
                    "index": index,
                    "ok": False,
                    "round": round_no,
                    "error": exc.message,
                    "result": {
                        "ok": False,
                        "code": exc.code,
                        "message": exc.message,
                        "details": sanitize_error_value(exc.details or {}),
                    },
                })
            except Exception as exc:
                return with_elapsed({
                    "index": index,
                    "ok": False,
                    "round": round_no,
                    "error": translate_exception(exc),
                    "result": {"ok": False, "code": type(exc).__name__, "message": translate_exception(exc)},
                })

        def rows() -> Any:
            yield {
                "type": "progress",
                "stage": "proxy_provider",
                "message": f"正在从粘性代理接口采样城市面，共 {len(tokens)} 个账号等待转化...",
                "total": len(tokens),
            }
            try:
                candidates = extract_proxy_candidates(raw_proxy)
                if not candidates:
                    raise PublicApiError("missing_proxy", "请填写代理，系统不会直连")
                yield {
                    "type": "progress",
                    "stage": "proxy_preflight",
                    "message": f"已取得 {len(candidates)} 个粘性代理，正在预热并锁定出口 IP...",
                    "candidate_count": len(candidates),
                }
                healthy_candidates = preflight_proxy_candidates(candidates, refresh=True)
                healthy_candidates = [proxy for proxy in healthy_candidates if get_cached_proxy_geo(proxy)] or healthy_candidates
                healthy_candidates = [proxy for proxy in healthy_candidates if proxy_badness(proxy) < PROXY_BADNESS_BLOCK_SCORE] or healthy_candidates
                healthy_candidates = sort_by_proxy_health(healthy_candidates)
                per_token_proxy_count = max(1, min(BATCH_PROXY_CANDIDATES, len(healthy_candidates)))
                yield {
                    "type": "progress",
                    "stage": "proxy_ready",
                    "message": (
                        f"粘性代理预热完成：{len(healthy_candidates)} 个候选，"
                        f"单账号{'并发' if CHECKOUT_RACE_ENABLED else '串行最多'} {per_token_proxy_count} 个 lease。"
                    ),
                    "candidate_count": len(healthy_candidates),
                    "city_stats": city_stats_snapshot(),
                }
            except PublicApiError as exc:
                for idx, _token in enumerate(tokens, 1):
                    yield {
                        "index": idx,
                        "ok": False,
                        "error": exc.message,
                        "result": {
                            "ok": False,
                            "code": exc.code,
                            "message": exc.message,
                            "details": sanitize_error_value(exc.details or {}),
                        },
                    }
                return
            except Exception as exc:
                message = translate_exception(exc)
                for idx, _token in enumerate(tokens, 1):
                    yield {
                        "index": idx,
                        "ok": False,
                        "error": message,
                        "result": {"ok": False, "code": type(exc).__name__, "message": message},
                    }
                return

            executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="pp-batch")
            try:
                final_failures: dict[int, dict[str, Any]] = {}
                current_indices = list(range(1, len(tokens) + 1))
                recovery_rounds = max(0, BATCH_RECOVERY_ROUNDS)
                recovery_proxy_count = max(1, min(BATCH_RECOVERY_PROXY_CANDIDATES, len(healthy_candidates)))
                for round_no in range(recovery_rounds + 1):
                    if not current_indices:
                        break
                    round_proxy_count = per_token_proxy_count if round_no == 0 else recovery_proxy_count
                    if round_no > 0:
                        yield {
                            "type": "progress",
                            "stage": "batch_recovery",
                            "message": f"第 {round_no} 轮补偿：{len(current_indices)} 个失败账号换下一批健康 lease 继续转化...",
                            "remaining": len(current_indices),
                            "round": round_no,
                            "city_stats": city_stats_snapshot(),
                        }
                    pending: set[Any] = {
                        executor.submit(task, idx, tokens[idx - 1], healthy_candidates, round_proxy_count, round_no)
                        for idx in current_indices
                    }
                    next_retry: list[int] = []
                    while pending:
                        done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
                        for future in done:
                            row = future.result()
                            if row.get("ok"):
                                final_failures.pop(int(row.get("index") or 0), None)
                                yield row
                                continue
                            result = row.get("result") if isinstance(row.get("result"), dict) else {}
                            code = str(result.get("code") or "")
                            details = result.get("details") if isinstance(result.get("details"), dict) else {}
                            recoverable = round_no < recovery_rounds and is_batch_recoverable_failure(code, details)
                            if recoverable:
                                idx = int(row.get("index") or 0)
                                if idx:
                                    next_retry.append(idx)
                                    final_failures[idx] = row
                                continue
                            final_failures.pop(int(row.get("index") or 0), None)
                            yield row
                    current_indices = sorted(set(next_retry))
                for idx in sorted(final_failures):
                    yield final_failures[idx]
            finally:
                shutdown_executor(executor, wait=False, cancel_futures=True)
            yield {"type": "stats", "city_stats": city_stats_snapshot()}

        self.send_ndjson_stream(rows())

    def do_POST(self) -> None:
        if not is_valid_host(self.headers.get("Host")):
            self.send_json(HTTPStatus.FORBIDDEN, {"ok": False, "code": "domain_blocked", "message": "Domain access unauthorized. Anti-cloning shield active."})
            return
        
        path = urlparse(self.path).path
        if path == "/api/test-proxy":
            self.handle_test_proxy()
            return
        if path == "/api/extract-batch":
            try:
                self.stream_extract_batch()
            except PublicApiError as exc:
                self.send_json(exc.status, {"ok": False, "code": exc.code, "message": exc.message})
            except Exception as exc:
                import traceback
                traceback.print_exc()
                self.send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "code": "internal_error", "message": translate_exception(exc)},
                )
            return

        if path != "/api/extract":
            self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "code": "not_found", "message": "Not found"})
            return
        client_ip = get_client_ip(self)
        if not allow_request(client_ip):
            self.send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"ok": False, "code": "rate_limited", "message": "请求过快，请稍后再试"},
            )
            return
        try:
            payload = self.read_body_json()
            result = run_extraction(payload)
            if isinstance(result, dict) and result.get("ok"):
                increment_counter()
            self.send_json(HTTPStatus.OK, result)
        except CheckoutGuardError as exc:
            self.send_json(
                exc.status,
                {
                    "ok": False,
                    "code": exc.code,
                    "message": str(exc),
                    "amount_due": exc.amount_due,
                    "currency": exc.currency,
                    "zero_verified": False,
                },
            )
        except PublicApiError as exc:
            safe_details = sanitize_error_value(exc.details) if exc.details else None
            print(f"DEBUG PublicApiError: code={exc.code}, message={exc.message}, details={safe_details}", flush=True)
            msg = exc.message
            if exc.details and isinstance(exc.details, dict):
                checkout_err = str(exc.details.get("checkout_error") or "")
                if "Could not resolve" in checkout_err or "DNSError" in checkout_err:
                    msg = "代理服务器域名解析失败。请检查[日区动态家宽]格式是否漏掉了端口号（格式需为: user:pass@host:port）"
                elif "Connection refused" in checkout_err or "Failed to connect" in checkout_err:
                    msg = "代理服务器连接拒绝。请确认该代理IP和端口是否有效，或此动态家宽代理已失效过时"
                elif "timeout" in checkout_err.lower() or "timed out" in checkout_err.lower():
                    msg = "请求代理超时。日区住宅链路延迟偏高，请更换为更流畅的动态家宽节点"
            
            data = {"ok": False, "code": exc.code, "message": msg}
            if safe_details:
                data["details"] = safe_details
            self.send_json(exc.status, data)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            friendly_msg = translate_exception(exc)
            self.send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "code": "internal_error", "message": friendly_msg},
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Commercial zero-amount Plus PayPal link extractor website.")
    parser.add_argument("--host", default=os.getenv("PLUS_LINK_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PLUS_LINK_PORT", "8888")))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), PlusLinkHandler)
    print(f"Plus PayPal 0 元门禁网站已启动：http://{args.host}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
