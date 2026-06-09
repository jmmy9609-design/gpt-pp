#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import json
import os
import sys
from typing import Any

from plus_paypal_link_probe import create_checkout, find_nested_url, PM_REDIRECT_RE
from protocol_paypal_authorize import confirm_paypal_authorize, fetch_checkout_init


def amount_display(init: dict[str, Any]) -> tuple[int | None, str, str]:
    invoice = init.get("invoice") if isinstance(init.get("invoice"), dict) else {}
    currency = str(init.get("currency") or invoice.get("currency") or "").upper()
    raw = invoice.get("amount_due")
    amount = raw if isinstance(raw, int) else None
    if amount is None:
        return None, currency, "unknown"
    return amount, currency, f"{amount / 100:.2f} {currency or 'EUR'}"


def main() -> int:
    token = os.environ.get("PP_TOKEN", "").strip()
    proxy = os.environ.get("PP_PROXY", "").strip()
    country = os.environ.get("PP_COUNTRY", "DE").strip() or "DE"
    currency = os.environ.get("PP_CURRENCY", "EUR").strip() or "EUR"
    if not token or not proxy:
        print(json.dumps({"ok": False, "code": "fallback_missing_input", "message": "missing token/proxy"}))
        return 2

    try:
        hosted = os.environ.get("PP_HOSTED_URL", "").strip()
        if not hosted:
            with contextlib.redirect_stdout(sys.stderr):
                checkout = create_checkout(token, proxy, country, currency)
            hosted = str(checkout.get("hosted_checkout_url") or "")
            if not hosted:
                print(json.dumps({
                    "ok": False,
                    "code": "fallback_checkout_failed",
                    "message": str(checkout.get("error") or f"checkout status={checkout.get('status')}")[:300],
                }, ensure_ascii=False))
                return 1

        with contextlib.redirect_stdout(sys.stderr):
            pk, cs, init, _events = fetch_checkout_init(hosted, proxy, keep_artifacts=False, allow_browser_fallback=False)
        if not (pk and cs and init):
            print(json.dumps({"ok": False, "code": "fallback_init_failed", "message": "failed to recover Stripe init"}, ensure_ascii=False))
            return 1

        amount_due, cur, display = amount_display(init)
        existing_pm = find_nested_url(init, PM_REDIRECT_RE)
        if existing_pm:
            print(json.dumps({
                "ok": True,
                "code": "paypal_authorize_extracted",
                "amount_due": amount_due,
                "currency": cur,
                "amount_display": display,
                "hosted_checkout_url": hosted,
                "paypal_authorize_url": existing_pm,
            }, ensure_ascii=False))
            return 0

        with contextlib.redirect_stdout(sys.stderr):
            result = confirm_paypal_authorize(proxy, pk, cs, init, require_zero=False)
        pm_url = str(result.get("pm_authorize_url") or "")
        if not result.get("ok") or not pm_url:
            print(json.dumps({
                "ok": False,
                "code": "fallback_confirm_failed",
                "message": f"confirm status={result.get('status')} no paypal redirect",
                "amount_due": amount_due,
                "currency": cur,
                "amount_display": display,
            }, ensure_ascii=False))
            return 1
        print(json.dumps({
            "ok": True,
            "code": "paypal_authorize_extracted",
            "amount_due": amount_due,
            "currency": cur,
            "amount_display": display,
            "hosted_checkout_url": hosted,
            "paypal_authorize_url": pm_url,
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "code": "fallback_exception", "message": f"{type(exc).__name__}: {str(exc)[:240]}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
