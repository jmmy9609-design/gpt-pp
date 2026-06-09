#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
KOOKEEY_RE = re.compile(
    r"(?:(?:socks5h?|https?)://)?[A-Za-z0-9_-]+:[^\s@]+@gate\.kookeey\.info:1000"
)
PM_REDIRECT_RE = re.compile(r"https://pm-redirects\.stripe\.com/authorize/[^\"'\s<>]+")
PAY_OPENAI_RE = re.compile(r"https://pay\.openai\.com/c/pay/[^\"'\s<>]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask_token(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    return f"<access-token-present len={len(value)}>"


def normalize_proxy(value: str, scheme: str = "socks5h") -> str:
    proxy = str(value or "").strip()
    if not proxy:
        return ""
    if proxy.startswith(("socks5://", "socks5h://", "http://", "https://")):
        return proxy
    if "@" not in proxy:
        parts = proxy.split(":")
        if len(parts) >= 4 and parts[1].isdigit():
            host, port, user = parts[0].strip(), parts[1].strip(), parts[2].strip()
            password = ":".join(parts[3:]).strip()
            if host and port and user and password:
                proxy = f"{user}:{password}@{host}:{port}"
    scheme = scheme.rstrip(":/") or "socks5h"
    return f"{scheme}://{proxy}"


def mask_proxy(value: str) -> str:
    proxy = normalize_proxy(value)
    return re.sub(r"(://)[^:@/\s]+:[^@/\s]+@", r"\1<redacted>:<redacted>@", proxy)


def sanitize_url(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = re.sub(
        r"([?&](?:ba_token|token|ssrt|session|code|state|setup_intent|setup_intent_client_secret)=)[^&]+",
        r"\1<redacted>",
        text,
    )
    text = re.sub(r"(https://pay\.openai\.com/c/pay/)[^\s?#]+", r"\1<redacted>", text)
    text = re.sub(
        r"(https://pm-redirects\.stripe\.com/authorize/[^/\s]+/)[^\s?#]+",
        r"\1<redacted>",
        text,
    )
    return text


def b64url_json(segment: str) -> dict[str, Any]:
    segment = segment + "=" * (-len(segment) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(segment.encode()).decode())
    except Exception:
        return {}


def token_claims(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    return b64url_json(parts[1]) if len(parts) >= 2 else {}


def latest_codex_material() -> tuple[str, str, str]:
    """Extract the latest token/proxy from this Codex thread files without echoing secrets."""
    sessions_dir = Path.home() / ".codex" / "sessions"
    if not sessions_dir.exists():
        return "", "", ""
    files = sorted(
        (p for p in sessions_dir.glob("**/*.jsonl") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:40]
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "gate.kookeey.info:1000" not in text and "plus-link" not in text:
            continue
        tokens = [item for item in JWT_RE.findall(text) if "redacted" not in item.lower()]
        proxies = [
            item for item in KOOKEEY_RE.findall(text)
            if "redacted" not in item.lower() and "<" not in item and ">" not in item
        ]
        if tokens and proxies:
            return tokens[-1], proxies[-1], str(path)
    return "", "", ""


def find_nested_url(value: Any, pattern: re.Pattern[str]) -> str:
    seen: set[int] = set()

    def walk(item: Any) -> str:
        if isinstance(item, str):
            match = pattern.search(item)
            return match.group(0) if match else ""
        if isinstance(item, (dict, list, tuple)):
            marker = id(item)
            if marker in seen:
                return ""
            seen.add(marker)
        if isinstance(item, dict):
            for key in ("url", "redirect_url", "authorize_url", "hosted_checkout_url", "checkout_url"):
                found = walk(item.get(key))
                if found:
                    return found
            for child in item.values():
                found = walk(child)
                if found:
                    return found
        elif isinstance(item, (list, tuple)):
            for child in item:
                found = walk(child)
                if found:
                    return found
        return ""

    return walk(value)


def make_http_client(proxy: str):
    try:
        from curl_cffi import requests as curl_requests
    except Exception as exc:
        raise RuntimeError(f"curl_cffi 不可用，无法稳定使用 socks5h 代理: {exc}") from exc
    kwargs: dict[str, Any] = {"impersonate": "chrome", "verify": True}
    if proxy:
        kwargs["proxy"] = proxy
    return curl_requests.Session(**kwargs)


def create_checkout(access_token: str, proxy: str, country: str, currency: str, timeout_seconds: int = 30) -> dict[str, Any]:
    session = make_http_client(proxy)
    path = "/backend-api/payments/checkout"
    payload = {
        "plan_name": "chatgptplusplan",
        "billing_details": {
            "country": country,
            "currency": currency,
        },
        "entry_point": "all_plans_pricing_modal",
        "promo_campaign": {
            "promo_campaign_id": "plus-1-month-free",
            "is_coupon_from_query_param": False,
        },
        "checkout_ui_mode": "hosted",
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "X-OpenAI-Target-Path": path,
        "X-OpenAI-Target-Route": path,
    }
    try:
        response = session.post("https://chatgpt.com" + path, headers=headers, json=payload, timeout=timeout_seconds)
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
        if not hosted_url:
            hosted_url = find_nested_url(data, PAY_OPENAI_RE)
        if not hosted_url and isinstance(data, dict):
            cs = str(data.get("checkout_session_id") or data.get("id") or "")
            client_secret = str(data.get("client_secret") or "")
            if cs.startswith("cs_") and "_secret_" in client_secret:
                hosted_url = "https://pay.openai.com/c/pay/" + cs + "#" + client_secret.split("_secret_", 1)[1]
        paypal_authorize_url = find_nested_url(data, PM_REDIRECT_RE)
        return {
            "ok": 200 <= status < 300 and bool(hosted_url or data.get("checkout_session_id")),
            "status": status,
            "payload": payload,
            "checkout_session_id": data.get("checkout_session_id") if isinstance(data, dict) else "",
            "processor_entity": data.get("processor_entity") if isinstance(data, dict) else "",
            "publishable_key": data.get("publishable_key") if isinstance(data, dict) else "",
            "publishable_key_present": bool(data.get("publishable_key")) if isinstance(data, dict) else False,
            "checkout_ui_mode": data.get("checkout_ui_mode") if isinstance(data, dict) else "",
            "requires_manual_approval": data.get("requires_manual_approval") if isinstance(data, dict) else None,
            "hosted_checkout_url": hosted_url or str(data.get("url") or data.get("stripe_hosted_url") or data.get("checkout_url") or ""),
            "paypal_authorize_url": paypal_authorize_url,
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
        try:
            session.close()
        except Exception:
            pass


async def extract_paypal_authorize_with_existing_automator(
    hosted_url: str,
    access_token: str,
    proxy: str,
    headless: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[1] / "chatgpt2api"
    sys.path.insert(0, str(repo))
    from services.checkout_automator import checkout_automator
    from services.checkout_protocol import build_checkout_protocol_config, public_automation_result

    protocol_config = build_checkout_protocol_config(
        {},
        {
            "checkout_proxy": proxy,
            "checkout_browser": "chromium",
            "checkout_headless": headless,
            "checkout_timeout_seconds": timeout_seconds,
            "sms_phone": "",
            "sms_api_url": "",
        },
    )
    result = await checkout_automator.run_automation_task(
        hosted_url,
        access_token=access_token,
        paypal_authorize_url="",
        protocol_config=protocol_config,
    )
    return public_automation_result(result)


def redact_result(value: Any) -> Any:
    if isinstance(value, str):
        if "pay.openai.com/c/pay/" in value or "pm-redirects.stripe.com/authorize/" in value:
            return sanitize_url(value)
        if JWT_RE.search(value):
            return JWT_RE.sub("<access-token-redacted>", value)
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in {"raw_response"}:
                continue
            if "token" in k.lower() and isinstance(v, str):
                out[k] = "<redacted>"
            elif "proxy" in k.lower() and isinstance(v, str):
                out[k] = mask_proxy(v)
            else:
                out[k] = redact_result(v)
        return out
    if isinstance(value, list):
        return [redact_result(v) for v in value]
    return value


def write_json(path: Path, payload: dict[str, Any], private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if private:
        try:
            path.chmod(0o600)
        except Exception:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate ChatGPT Plus hosted checkout and extract Stripe PayPal authorize URL.")
    parser.add_argument("--country", default=os.getenv("PLUS_COUNTRY", "DE"))
    parser.add_argument("--currency", default=os.getenv("PLUS_CURRENCY", "EUR"))
    parser.add_argument("--access-token", default=os.getenv("OPENAI_ACCESS_TOKEN", ""))
    parser.add_argument("--proxy", default=os.getenv("CHECKOUT_PROXY", ""))
    parser.add_argument("--proxy-scheme", default=os.getenv("CHECKOUT_PROXY_SCHEME", "auto"), choices=["auto", "socks5h", "socks5", "http", "https"])
    parser.add_argument("--from-codex-session", action="store_true", help="Read latest token/proxy from local Codex session logs.")
    parser.add_argument("--extract-paypal", action="store_true", help="Open hosted checkout with Playwright/Chromium and capture pm-redirects Stripe PayPal URL.")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "runs"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = str(args.access_token or "").strip()
    proxy = str(args.proxy or "").strip()
    material_source = "env-or-arg"
    if args.from_codex_session and (not token or not proxy):
        found_token, found_proxy, source = latest_codex_material()
        token = token or found_token
        proxy = proxy or found_proxy
        material_source = source or "codex-session-not-found"
    raw_proxy = proxy
    if not token:
        print("ERROR: missing access token; set OPENAI_ACCESS_TOKEN or use --from-codex-session", file=sys.stderr)
        return 2
    if not proxy:
        print("ERROR: missing checkout proxy; set CHECKOUT_PROXY or use --from-codex-session", file=sys.stderr)
        return 2

    claims = token_claims(token)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) / run_id
    summary: dict[str, Any] = {
        "created_at": utc_now(),
        "material_source": material_source,
        "token": {
            "present": True,
            "masked": mask_token(token),
            "email": claims.get("https://api.openai.com/profile", {}).get("email") if isinstance(claims.get("https://api.openai.com/profile"), dict) else "",
            "exp": claims.get("exp"),
            "client_id": claims.get("client_id"),
        },
        "proxy": mask_proxy(normalize_proxy(raw_proxy, "socks5h")),
        "request": {
            "country": args.country,
            "currency": args.currency,
            "checkout_ui_mode": "hosted",
            "promo_campaign_id": "plus-1-month-free",
        },
    }

    schemes = ["socks5h", "socks5", "http"] if args.proxy_scheme == "auto" else [args.proxy_scheme]
    print(f"[1/2] using token={summary['token']['masked']} proxy={summary['proxy']} country={args.country} currency={args.currency}")
    checkout: dict[str, Any] = {}
    used_proxy = ""
    attempts: list[dict[str, Any]] = []
    for scheme in schemes:
        candidate_proxy = normalize_proxy(raw_proxy, scheme)
        print(f"[proxy] trying {scheme}://<redacted>@gate.kookeey.info:1000")
        candidate_result = create_checkout(token, candidate_proxy, args.country, args.currency, args.timeout_seconds)
        attempts.append({
            "scheme": scheme,
            "ok": bool(candidate_result.get("ok")),
            "status": candidate_result.get("status"),
            "error": candidate_result.get("error", ""),
            "error_type": candidate_result.get("error_type", ""),
            "hosted_checkout_url": candidate_result.get("hosted_checkout_url", ""),
        })
        if candidate_result.get("ok"):
            checkout = candidate_result
            used_proxy = candidate_proxy
            break
        if not checkout:
            checkout = candidate_result
            used_proxy = candidate_proxy
    summary["proxy_attempts"] = redact_result(attempts)
    summary["checkout"] = redact_result(checkout)
    private_payload = {
        "created_at": utc_now(),
        "checkout": {
            "hosted_checkout_url": checkout.get("hosted_checkout_url", ""),
            "paypal_authorize_url": checkout.get("paypal_authorize_url", ""),
            "checkout_session_id": checkout.get("checkout_session_id", ""),
        },
    }
    print(f"[2/2] checkout status={checkout.get('status')} hosted={sanitize_url(str(checkout.get('hosted_checkout_url') or ''))}")

    if args.extract_paypal and checkout.get("hosted_checkout_url"):
        print("[paypal] launching Chromium with checkout proxy to capture Stripe confirm redirect...")
        try:
            paypal_result = asyncio.run(
                extract_paypal_authorize_with_existing_automator(
                    str(checkout["hosted_checkout_url"]),
                    token,
                    used_proxy,
                    bool(args.headless),
                    int(args.timeout_seconds),
                )
            )
        except Exception as exc:
            paypal_result = {"status": "failed", "stage": "extract_exception", "error": str(exc)}
        summary["paypal_extraction"] = redact_result(paypal_result)
        raw_paypal = str(paypal_result.get("paypal_authorize_url") or "")
        if raw_paypal:
            private_payload["checkout"]["paypal_authorize_url"] = raw_paypal
        print(
            "[paypal] status={} stage={} authorize={}".format(
                paypal_result.get("status"),
                paypal_result.get("stage"),
                sanitize_url(str(paypal_result.get("paypal_authorize_url") or "")),
            )
        )

    write_json(out_dir / "summary.redacted.json", summary)
    write_json(out_dir / "private.raw.json", private_payload, private=True)
    print(f"[saved] redacted={out_dir / 'summary.redacted.json'}")
    print(f"[saved] private={out_dir / 'private.raw.json'}")
    return 0 if checkout.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
