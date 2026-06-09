#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from plus_paypal_link_probe import (
    PM_REDIRECT_RE,
    create_checkout,
    latest_codex_material,
    mask_proxy,
    normalize_proxy,
    sanitize_url,
)
from inspect_hosted_checkout import latest_private_checkout_url


DEFAULT_OPENAI_STRIPE_PUBLISHABLE_KEY = (
    "pk_live_51HOrSwC6h1nxGoI3lTAgRjYVrz4dU3fVOabyCcKR3pbEJguCVAlqCxdxCUvoRh1XWwRacViovU3kLKvpkjh7IqkW00iXQsjo3n"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: dict[str, Any], private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if private:
        try:
            path.chmod(0o600)
        except Exception:
            pass


def find_url(value: Any, regex: re.Pattern[str] = PM_REDIRECT_RE) -> str:
    if isinstance(value, str):
        found = regex.search(value)
        return found.group(0) if found else ""
    if isinstance(value, dict):
        for child in value.values():
            found = find_url(child, regex)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = find_url(child, regex)
            if found:
                return found
    return ""


def load_latest_init_from_protocol_runs() -> tuple[str, str, dict[str, Any]]:
    runs = Path(__file__).resolve().parent / "runs"
    for path in sorted(runs.glob("protocol-*/protocol.private.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        pk = ""
        cs = ""
        init: dict[str, Any] = {}
        for event in data.get("events", []):
            url = str(event.get("url") or "")
            if "/payment_pages/" in url and "/init" in url and event.get("phase") == "request":
                post_data = parse_qs(str(event.get("post_data") or ""))
                pk = (post_data.get("key") or [""])[0] or pk
                match = re.search(r"/payment_pages/([^/]+)/init", url)
                if match:
                    cs = match.group(1)
            if "/payment_pages/" in url and "/init" in url and event.get("phase") == "response" and int(event.get("status") or 0) == 200:
                try:
                    init = json.loads(str(event.get("body") or "{}"))
                except Exception:
                    init = {}
        if pk and cs and init:
            return pk, cs, init
    return "", "", {}


class CheckoutGuardError(RuntimeError):
    code = "checkout_guard_failed"
    status = 422

    def __init__(self, message: str, *, amount_due: int | None = None, currency: str = "") -> None:
        super().__init__(message)
        self.amount_due = amount_due
        self.currency = currency

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": self.code,
            "message": str(self),
            "amount_due": self.amount_due,
            "currency": self.currency,
            "zero_verified": False,
        }


class NonZeroAmountError(CheckoutGuardError):
    code = "non_zero_amount"
    status = 409


def _coerce_amount_due(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("amount_due must not be boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value)
    raise ValueError("amount_due is not an integer")


def _optional_int(value: Any, default: int = 0) -> int:
    try:
        return _coerce_amount_due(value)
    except ValueError:
        return default


def display_amounts(init: dict[str, Any]) -> dict[str, int]:
    invoice = init.get("invoice") if isinstance(init.get("invoice"), dict) else {}
    total_summary = init.get("total_summary") if isinstance(init.get("total_summary"), dict) else {}
    lines = invoice.get("lines") if isinstance(invoice.get("lines"), dict) else {}
    line_items = lines.get("data") if isinstance(lines.get("data"), list) else []
    line_subtotal = sum(_optional_int(item.get("amount")) for item in line_items if isinstance(item, dict))

    subtotal = _optional_int(total_summary.get("subtotal"), line_subtotal)
    total = _optional_int(total_summary.get("total"), _optional_int(invoice.get("amount_due"), 0))
    due = _optional_int(total_summary.get("due"), _optional_int(invoice.get("amount_due"), total))
    discount_amount = max(subtotal - total, 0)
    return {
        "subtotal": subtotal,
        "total_exclusive_tax": 0,
        "total_inclusive_tax": total,
        "total_discount_amount": discount_amount,
        "shipping_rate_amount": 0,
        "due": due,
    }


def verify_zero_amount(init: dict[str, Any]) -> dict[str, Any]:
    """Fail closed unless Stripe init proves invoice.amount_due is exactly 0."""
    if not isinstance(init, dict):
        raise CheckoutGuardError("无法确认 Stripe 初始化响应，已取消提链")

    invoice = init.get("invoice")
    if not isinstance(invoice, dict) or "amount_due" not in invoice:
        raise CheckoutGuardError("无法确认 Stripe 发票应付金额，已取消提链")

    currency = str(init.get("currency") or invoice.get("currency") or "").lower()
    try:
        amount_due = _coerce_amount_due(invoice.get("amount_due"))
    except ValueError as exc:
        raise CheckoutGuardError("Stripe 发票金额格式异常，已取消提链", currency=currency) from exc

    if amount_due != 0:
        raise NonZeroAmountError(
            "检测到应付金额非 0，已取消提链",
            amount_due=amount_due,
            currency=currency,
        )

    payment_method_types = init.get("payment_method_types")
    if not isinstance(payment_method_types, list):
        raise CheckoutGuardError("无法确认当前 Stripe Checkout 支付方式，已取消提链", amount_due=amount_due, currency=currency)
    methods = {str(item).lower() for item in payment_method_types}
    if "paypal" not in methods:
        raise CheckoutGuardError("当前 Stripe Checkout 不支持 PayPal，已取消提链", amount_due=amount_due, currency=currency)

    return {
        "ok": True,
        "code": "zero_amount_verified",
        "amount_due": amount_due,
        "currency": currency,
        "zero_verified": True,
        "source": "stripe.invoice.amount_due",
    }


def checkout_amount_guard(init: dict[str, Any], *, require_zero: bool = True) -> dict[str, Any]:
    if require_zero:
        return verify_zero_amount(init)
    if not isinstance(init, dict):
        raise CheckoutGuardError("无法确认 Stripe 初始化响应，已取消提链")

    invoice = init.get("invoice") if isinstance(init.get("invoice"), dict) else {}
    total_summary = init.get("total_summary") if isinstance(init.get("total_summary"), dict) else {}
    currency = str(init.get("currency") or invoice.get("currency") or "").lower()
    amount_source = "stripe.invoice.amount_due"
    raw_amount = invoice.get("amount_due") if "amount_due" in invoice else None
    if raw_amount is None and "due" in total_summary:
        raw_amount = total_summary.get("due")
        amount_source = "stripe.total_summary.due"
    try:
        amount_due = _coerce_amount_due(raw_amount)
    except ValueError as exc:
        raise CheckoutGuardError("无法确认 Stripe 发票应付金额，已取消提链", currency=currency) from exc

    payment_method_types = init.get("payment_method_types")
    if not isinstance(payment_method_types, list):
        raise CheckoutGuardError("无法确认当前 Stripe Checkout 支付方式，已取消提链", amount_due=amount_due, currency=currency)
    methods = {str(item).lower() for item in payment_method_types}
    if "paypal" not in methods:
        raise CheckoutGuardError("当前 Stripe Checkout 不支持 PayPal，已取消提链", amount_due=amount_due, currency=currency)

    return {
        "ok": True,
        "code": "amount_verified_for_authorize_extract",
        "amount_due": amount_due,
        "currency": currency,
        "zero_verified": amount_due == 0,
        "source": amount_source,
    }


def fetch_checkout_init_fastpath(hosted_url: str, proxy: str) -> tuple[str, str, dict[str, Any]]:
    """Fetch Stripe init with a known publishable key; avoid loading hosted HTML assets."""
    try:
        # 1. 提取 cs_live ID
        cs_match = re.search(r"(cs_(?:live|test)_[a-zA-Z0-9]+)", hosted_url)
        if not cs_match:
            return "", "", {}
        cs = cs_match.group(1)
        
        session = make_session(proxy)
        headers = {
            "Accept": "application/json",
            "Origin": "https://pay.openai.com",
            "Referer": "https://pay.openai.com/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        pk_candidates: list[str] = []
        preferred = os.getenv("OPENAI_STRIPE_PUBLISHABLE_KEY", DEFAULT_OPENAI_STRIPE_PUBLISHABLE_KEY).strip()
        if preferred:
            pk_candidates.append(preferred)
        prefix = "pk_live_" if cs.startswith("cs_live_") else "pk_test_"
        pk_candidates = [item for item in pk_candidates if item.startswith(prefix)]
        if not pk_candidates:
            return "", "", {}

        init_url = f"https://api.stripe.com/v1/payment_pages/{cs}/init"
        for pk in pk_candidates:
            payload = {
                "key": pk,
                "eid": "NA",
                "browser_locale": "en-US",
                "browser_timezone": "Asia/Shanghai",
                "redirect_type": "url",
            }
            for referer in ("https://pay.openai.com/", hosted_url):
                init_headers = {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "Referer": referer,
                    "Origin": "https://pay.openai.com",
                    "User-Agent": headers["User-Agent"],
                }
                init_resp = session.post(init_url, headers=init_headers, data=payload, timeout=9)
                if init_resp.status_code == 200:
                    init_data = init_resp.json()
                    return pk, cs, init_data
            
    except Exception as exc:
        print(f"⚠️ [Stripe HTTP 通道] 纯 HTTP 提取失败: {type(exc).__name__} ({str(exc)})。", flush=True)
        
    return "", "", {}


def fetch_checkout_init(hosted_url: str, proxy: str, keep_artifacts: bool = True, allow_browser_fallback: bool = True) -> tuple[str, str, dict[str, Any], list[dict[str, Any]]]:
    """Use Playwright only to decode hosted URL fragment and record the init API response."""
    import asyncio
    from record_stripe_protocol import capture

    # 🌟 第一优先级：使用 100% 纯 HTTP 的 Stripe 极速通道 (0.2 秒内极速瞬发)
    pk_fast, cs_fast, init_fast = fetch_checkout_init_fastpath(hosted_url, proxy)
    if pk_fast and cs_fast and init_fast:
        print("⚡ [Stripe极速通道] 100% 纯 HTTP 高速提链成功！完美跳过 Playwright 浏览器冷启动！", flush=True)
        return pk_fast, cs_fast, init_fast, []

    if not allow_browser_fallback:
        return "", "", {}, []

    # 🌟 第二优先级（降级备用链路）：当纯 HTTP 遭遇 CF 拦截等阻碍，再平滑拉起无头浏览器作为护盾
    print("🔄 [降级避灾] 正在冷启动 Playwright 无头浏览器加载 Stripe 页面作为底层提取保障...", flush=True)
    out_dir = Path(__file__).resolve().parent / "runs" / ("protocol-init-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    asyncio.run(capture(hosted_url, proxy, out_dir, True, False, False, False, 4000))
    private = json.loads((out_dir / "protocol.private.json").read_text(encoding="utf-8"))
    pk = ""
    cs = ""
    init: dict[str, Any] = {}
    for event in private.get("events", []):
        url = str(event.get("url") or "")
        if "/payment_pages/" in url and "/init" in url and event.get("phase") == "request":
            post_data = parse_qs(str(event.get("post_data") or ""))
            pk = (post_data.get("key") or [""])[0] or pk
            match = re.search(r"/payment_pages/([^/]+)/init", url)
            if match:
                cs = match.group(1)
        if "/payment_pages/" in url and "/init" in url and event.get("phase") == "response" and int(event.get("status") or 0) == 200:
            init = json.loads(str(event.get("body") or "{}"))
    events = private.get("events", [])
    if not keep_artifacts:
        shutil.rmtree(out_dir, ignore_errors=True)
    return pk, cs, init, events


def make_session(proxy: str):
    try:
        from curl_cffi import requests as curl_requests
    except Exception as exc:
        raise RuntimeError(f"curl_cffi 不可用: {exc}") from exc
    return curl_requests.Session(impersonate="chrome", proxy=proxy, verify=True)


def billing_address(country: str) -> dict[str, str]:
    if (country or "").upper() == "JP":
        return {
            "country": "JP",
            "postal_code": "100-0001",
            "state": "東京都",
            "city": "千代田区",
            "line1": "1-1 Chiyoda",
            "line2": "Tokyo",
        }
    if (country or "").upper() == "DE":
        return {
            "country": "DE",
            "postal_code": "10115",
            "state": "Berlin",
            "city": "Berlin",
            "line1": "Invalidenstrasse 1",
            "line2": "Berlin",
        }
    return {
        "country": "US",
        "postal_code": "10001",
        "state": "NY",
        "city": "New York",
        "line1": "350 5th Ave",
        "line2": "New York",
    }


def build_confirm_payload(pk: str, init: dict[str, Any], return_url: str, *, require_zero: bool = True, country: str = "US") -> dict[str, str]:
    amount_gate = checkout_amount_guard(init, require_zero=require_zero)
    amount_due = int(amount_gate["amount_due"])
    amounts = display_amounts(init)
    address = billing_address(country)
    return {
        "eid": "NA",
        "key": pk,
        "init_checksum": str(init.get("init_checksum") or ""),
        "expected_amount": str(amount_due),
        "expected_payment_method_type": "paypal",
        "payment_method_data[type]": "paypal",
        "payment_method_data[billing_details][email]": str(init.get("customer_email") or ""),
        "payment_method_data[billing_details][address][country]": address["country"],
        "payment_method_data[billing_details][address][postal_code]": address["postal_code"],
        "payment_method_data[billing_details][address][state]": address["state"],
        "payment_method_data[billing_details][address][city]": address["city"],
        "payment_method_data[billing_details][address][line1]": address["line1"],
        "payment_method_data[billing_details][address][line2]": address["line2"],
        "consent[terms_of_service]": "accepted",
        "last_displayed_line_item_group_details[subtotal]": str(amounts["subtotal"]),
        "last_displayed_line_item_group_details[total_exclusive_tax]": str(amounts["total_exclusive_tax"]),
        "last_displayed_line_item_group_details[total_inclusive_tax]": str(amounts["total_inclusive_tax"]),
        "last_displayed_line_item_group_details[total_discount_amount]": str(amounts["total_discount_amount"]),
        "last_displayed_line_item_group_details[shipping_rate_amount]": str(amounts["shipping_rate_amount"]),
        "return_url": return_url,
    }


def confirm_paypal(proxy: str, pk: str, cs: str, init: dict[str, Any]) -> dict[str, Any]:
    # 🌟 智能动态识别：优先从 Stripe init 响应中自动提取真实的客户国家，自适应匹配最契合的账单地址
    customer_country = str(init.get("customer_country") or "US").upper()
    return confirm_paypal_authorize(proxy, pk, cs, init, require_zero=True, country=customer_country)


def confirm_paypal_authorize(proxy: str, pk: str, cs: str, init: dict[str, Any], *, require_zero: bool = True, country: str = "US") -> dict[str, Any]:
    amount_gate = checkout_amount_guard(init, require_zero=require_zero)
    session = make_session(proxy)
    hosted_url = str(init.get("url") or "")
    return_url = hosted_url or f"https://pay.openai.com/c/pay/{cs}"
    referer_url = return_url.split("#", 1)[0]
    endpoint = f"https://api.stripe.com/v1/payment_pages/{cs}/confirm"
    headers = {
        "Origin": "https://pay.openai.com",
        "Referer": referer_url,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    payload = build_confirm_payload(pk, init, return_url, require_zero=require_zero, country=country)
    try:
        response = session.post(endpoint, data=payload, headers=headers, timeout=12)
        status = int(getattr(response, "status_code", 0) or 0)
        text = getattr(response, "text", "") or ""
        try:
            body = response.json()
        except Exception:
            body = {"raw": text[:2000]}
        pm_url = find_url(body) or find_url(text)
        setup_intent = body.get("setup_intent") if isinstance(body, dict) else {}
        return {
            "status": status,
            "ok": 200 <= status < 300 and bool(pm_url),
            "endpoint": endpoint,
            "payload": payload,
            "response": body,
            "pm_authorize_url": pm_url,
            "zero_gate": amount_gate,
            "require_zero": require_zero,
            "setup_intent_status": setup_intent.get("status") if isinstance(setup_intent, dict) else "",
            "last_setup_error": setup_intent.get("last_setup_error") if isinstance(setup_intent, dict) else None,
        }
    finally:
        try:
            session.close()
        except Exception:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Protocol-level ChatGPT Plus Stripe-hosted PayPal authorize extractor.")
    parser.add_argument("--access-token", default="")
    parser.add_argument("--proxy", default="")
    parser.add_argument("--from-codex-session", action="store_true")
    parser.add_argument("--hosted-url", default="")
    parser.add_argument("--reuse-latest-init", action="store_true")
    parser.add_argument("--allow-non-zero", action="store_true", help="仅提取 Stripe PayPal authorize 时允许非 0 金额；默认仍要求 0 元。")
    parser.add_argument("--country", default="DE")
    parser.add_argument("--currency", default="EUR")
    parser.add_argument("--out-dir", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = str(args.access_token or "").strip()
    proxy = str(args.proxy or "").strip()
    if args.from_codex_session and (not token or not proxy):
        found_token, found_proxy, _ = latest_codex_material()
        token = token or found_token
        proxy = proxy or found_proxy
    if not proxy:
        print("ERROR: missing proxy")
        return 2
    proxy = normalize_proxy(proxy, "socks5h")

    hosted_url = str(args.hosted_url or "").strip() or latest_private_checkout_url()
    checkout_result: dict[str, Any] = {}
    if not hosted_url:
        if not token:
            print("ERROR: missing hosted URL and access token")
            return 2
        checkout_result = create_checkout(token, proxy, args.country, args.currency)
        hosted_url = str(checkout_result.get("hosted_checkout_url") or "")
    if not hosted_url:
        print("ERROR: hosted checkout URL not available")
        return 1

    if args.reuse_latest_init:
        pk, cs, init = load_latest_init_from_protocol_runs()
        init_events: list[dict[str, Any]] = []
    else:
        pk, cs, init, init_events = fetch_checkout_init(hosted_url, proxy)
    if not (pk and cs and init):
        print("ERROR: failed to recover Stripe publishable key / checkout session / init response")
        return 1

    try:
        zero_gate = checkout_amount_guard(init, require_zero=not args.allow_non_zero)
        result = confirm_paypal_authorize(proxy, pk, cs, init, require_zero=not args.allow_non_zero)
    except CheckoutGuardError as exc:
        zero_gate = exc.as_dict()
        result = {
            "status": exc.status,
            "ok": False,
            "endpoint": "",
            "payload": {},
            "response": {"error": exc.as_dict()},
            "pm_authorize_url": "",
            "zero_gate": zero_gate,
        }
    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent / "runs" / ("protocol-authorize-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    raw = {
        "created_at": utc_now(),
        "checkout": checkout_result,
        "hosted_checkout_url": hosted_url,
        "proxy": proxy,
        "stripe": {"publishable_key": pk, "checkout_session_id": cs, "init": init, "init_events": init_events},
        "confirm": result,
    }
    redacted = {
        "created_at": raw["created_at"],
        "proxy": mask_proxy(proxy),
        "hosted_checkout_url": sanitize_url(hosted_url),
        "stripe": {
            "publishable_key_present": bool(pk),
            "checkout_session_id": cs,
            "init_checksum_present": bool(init.get("init_checksum")),
            "payment_method_types": init.get("payment_method_types"),
            "amount_due": (init.get("invoice") or {}).get("amount_due") if isinstance(init.get("invoice"), dict) else None,
            "currency": init.get("currency"),
            "zero_gate": zero_gate,
        },
        "confirm": {
            "status": result.get("status"),
            "ok": result.get("ok"),
            "code": (result.get("zero_gate") or {}).get("code") if isinstance(result.get("zero_gate"), dict) else None,
            "zero_verified": (result.get("zero_gate") or {}).get("zero_verified") if isinstance(result.get("zero_gate"), dict) else None,
            "pm_authorize_url": sanitize_url(str(result.get("pm_authorize_url") or "")),
            "error": result.get("response", {}).get("error") if isinstance(result.get("response"), dict) else None,
        },
    }
    write_json(out_dir / "protocol-authorize.private.json", raw, private=True)
    write_json(out_dir / "protocol-authorize.redacted.json", redacted)
    print(json.dumps({
        "ok": redacted["confirm"]["ok"],
        "status": redacted["confirm"]["status"],
        "code": redacted["confirm"]["code"],
        "zero_verified": redacted["confirm"]["zero_verified"],
        "pm_authorize_url": redacted["confirm"]["pm_authorize_url"],
        "summary": str(out_dir / "protocol-authorize.redacted.json"),
        "private": str(out_dir / "protocol-authorize.private.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
