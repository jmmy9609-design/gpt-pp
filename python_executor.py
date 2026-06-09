#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import time
from typing import Any

from plus_paypal_link_probe import create_checkout, normalize_proxy
from protocol_paypal_authorize import CheckoutGuardError, confirm_paypal_authorize


JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def extract_access_token(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if value.startswith("{") or value.startswith("["):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = None
        found = find_token(parsed)
        if found:
            return found
    match = JWT_RE.search(value)
    return match.group(0) if match else ""


def find_token(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("accessToken", "access_token"):
            token = str(value.get(key) or "").strip()
            if JWT_RE.fullmatch(token):
                return token
        for child in value.values():
            token = find_token(child)
            if token:
                return token
    if isinstance(value, list):
        for child in value:
            token = find_token(child)
            if token:
                return token
    return ""


def amount_display(amount: Any, currency: str) -> str:
    try:
        cents = int(amount)
    except Exception:
        return "unknown"
    code = (currency or "UNKNOWN").upper()
    if (currency or "").lower() in {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf"}:
        return f"{cents} {code}"
    return f"{cents / 100:.2f} {code}"


def checkout_hosted_url(checkout: dict[str, Any]) -> str:
    hosted = str(checkout.get("hosted_checkout_url") or "")
    if hosted:
        return hosted
    raw = checkout.get("raw_response")
    if not isinstance(raw, dict):
        return ""
    for key in ("url", "stripe_hosted_url", "checkout_url", "hosted_checkout_url"):
        value = str(raw.get(key) or "")
        if "/c/pay/" in value:
            return value
    cs = str(raw.get("checkout_session_id") or raw.get("id") or "")
    if not cs.startswith("cs_"):
        return ""
    secret = str(raw.get("client_secret") or "")
    frag = ""
    marker = "_secret_"
    if marker in secret:
        frag = secret.split(marker, 1)[1]
    return f"https://pay.openai.com/c/pay/{cs}" + (f"#{frag}" if frag else "")


def fetch_init_http(hosted: str, proxy: str, preferred_pk: str = "") -> tuple[str, str, dict[str, Any], str]:
    try:
        from curl_cffi import requests as curl_requests
    except Exception as exc:
        return "", "", {}, f"curl_cffi_missing:{type(exc).__name__}"
    cs_match = re.search(r"(cs_(?:live|test)_[A-Za-z0-9]+)", hosted)
    if not cs_match:
        return "", "", {}, "cs_missing"
    cs = cs_match.group(1)
    session = curl_requests.Session(impersonate="chrome", proxy=proxy, verify=True)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Origin": "https://pay.openai.com",
        "Referer": "https://pay.openai.com/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    try:
        resp = session.get(hosted, headers=headers, timeout=9)
        if resp.status_code != 200:
            return "", cs, {}, f"hosted_http_{resp.status_code}"
        html = resp.text or ""
        pk_candidates: list[str] = []
        for match in re.finditer(r"(pk_(?:live|test)_[A-Za-z0-9_]{20,})", html):
            value = match.group(1)
            if value not in pk_candidates:
                pk_candidates.append(value)
        preferred = preferred_pk.strip()
        if preferred and preferred not in pk_candidates:
            pk_candidates.append(preferred)
        prefix = "pk_live_" if cs.startswith("cs_live_") else "pk_test_"
        pk_candidates = [item for item in pk_candidates if item.startswith(prefix)]
        if not pk_candidates:
            return "", cs, {}, "pk_missing"
        init_url = f"https://api.stripe.com/v1/payment_pages/{cs}/init"
        last_error = ""
        for pk in pk_candidates:
            form = {
                "key": pk,
                "eid": "NA",
                "browser_locale": "en-US",
                "browser_timezone": "Asia/Shanghai",
                "redirect_type": "url",
            }
            for referer in ("https://pay.openai.com/", hosted):
                init_headers = {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "Origin": "https://pay.openai.com",
                    "Referer": referer,
                    "User-Agent": headers["User-Agent"],
                }
                init_resp = session.post(init_url, headers=init_headers, data=form, timeout=9)
                if init_resp.status_code != 200:
                    last_error = f"init_http_{init_resp.status_code}"
                    continue
                try:
                    init = init_resp.json()
                    try:
                        session.get(
                            "https://api.stripe.com/v1/payment_pages/allowed_origins",
                            params={"key": pk, "session_id": cs},
                            headers={"Accept": "application/json", "Referer": "https://js.stripe.com/", "User-Agent": headers["User-Agent"]},
                            timeout=5,
                        )
                    except Exception:
                        pass
                    return pk, cs, init, ""
                except Exception as exc:
                    last_error = f"init_json_{type(exc).__name__}"
        return pk_candidates[0], cs, {}, last_error or "init_failed"
    except Exception as exc:
        return "", cs, {}, f"init_network_{type(exc).__name__}"
    finally:
        try:
            session.close()
        except Exception:
            pass


def fail(code: str, message: str, status: int = 502, **extra: Any) -> int:
    payload = {
        "ok": False,
        "code": code,
        "message": message,
        "status": status,
        "amount_display": extra.pop("amount_display", "unknown"),
    }
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 1


def main() -> int:
    started = time.time()
    try:
        req = json.load(sys.stdin)
    except Exception as exc:
        return fail("invalid_json", f"invalid stdin json: {exc}", 400)

    token = extract_access_token(str(req.get("access_token") or req.get("credential") or ""))
    proxy = normalize_proxy(str(req.get("proxy") or ""), "socks5h")
    country = str(req.get("country") or "DE")
    currency = str(req.get("currency") or "EUR")
    allow_non_zero = bool(req.get("allow_non_zero", True))
    if not token:
        return fail("invalid_access_token", "未识别到合法 JWT 格式 accessToken", 400)
    if not proxy:
        return fail("missing_proxy", "请填写代理", 400)

    try:
        checkout: dict[str, Any] = {}
        hosted = str(req.get("hosted_checkout_url") or "").strip()
        if not hosted:
            checkout = create_checkout(token, proxy, country, currency)
            hosted = checkout_hosted_url(checkout)
        if not hosted:
            return fail("checkout_failed", f"ChatGPT checkout failed: HTTP {checkout.get('status') or 0}", 502)

        pk, cs, init, init_err = fetch_init_http(hosted, proxy, str(req.get("publishable_key") or ""))
        if not (pk and cs and init):
            return fail("stripe_init_failed", f"Stripe init failed: {init_err}", 502, hosted_checkout_url=hosted)

        result: dict[str, Any] = {}
        for confirm_country in dict.fromkeys([country.upper(), "US"]):
            result = confirm_paypal_authorize(proxy, pk, cs, init, require_zero=not allow_non_zero, country=confirm_country)
            if result.get("ok") and result.get("pm_authorize_url"):
                break
            if "#" in hosted:
                # Two historical HTTP-only captures differ only by return_url shape.
                # Try the no-fragment variant on the same exit IP before rotating IP.
                init_no_fragment = dict(init)
                init_no_fragment["url"] = hosted.split("#", 1)[0]
                alt = confirm_paypal_authorize(proxy, pk, cs, init_no_fragment, require_zero=not allow_non_zero, country=confirm_country)
                if alt.get("ok") and alt.get("pm_authorize_url"):
                    result = alt
                    break
        invoice = init.get("invoice") if isinstance(init.get("invoice"), dict) else {}
        amount = invoice.get("amount_due")
        cur = str(init.get("currency") or invoice.get("currency") or "")
        pm_url = str(result.get("pm_authorize_url") or "")
        if not result.get("ok") or not pm_url:
            return fail(
                "stripe_confirm_failed",
                f"Stripe confirm failed: HTTP {result.get('status') or 0}",
                502,
                hosted_checkout_url=hosted,
                amount_due=amount,
                currency=cur,
                amount_display=amount_display(amount, cur),
                zero_verified=amount == 0,
            )
        print(json.dumps({
            "ok": True,
            "code": "paypal_authorize_extracted",
            "zero_verified": amount == 0,
            "amount_due": amount,
            "currency": cur,
            "amount_display": amount_display(amount, cur),
            "hosted_checkout_url": hosted,
            "paypal_authorize_url": pm_url,
            "elapsed_ms": int((time.time() - started) * 1000),
        }, ensure_ascii=False, separators=(",", ":")))
        return 0
    except CheckoutGuardError as exc:
        return fail(getattr(exc, "code", "checkout_guard_failed"), str(exc), getattr(exc, "status", 422))
    except Exception as exc:
        return fail("python_executor_error", f"{type(exc).__name__}: {str(exc)[:300]}", 502)


if __name__ == "__main__":
    raise SystemExit(main())
