#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from click_paypal_authorize import click_in_any_frame, js_accept_terms
from inspect_hosted_checkout import latest_private_checkout_url, redact_text, start_local_proxy
from plus_paypal_link_probe import latest_codex_material, mask_proxy, sanitize_url


def now_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def write_json(path: Path, data: dict[str, Any], private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if private:
        try:
            path.chmod(0o600)
        except Exception:
            pass


def redacted(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redacted(v) for v in value]
    if isinstance(value, dict):
        return {k: redacted(v) for k, v in value.items()}
    return value


async def body_text(response) -> str:
    try:
        headers = response.headers
        ctype = headers.get("content-type", "")
        if not any(kind in ctype for kind in ("json", "text", "javascript", "html", "x-www-form-urlencoded")):
            return ""
        return await response.text()
    except Exception:
        return ""


async def fill_jp_billing_address(page) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []

    async def force_input(name: str, value: str) -> str:
        try:
            locator = page.locator(f'[name="{name}"]').first
            await locator.scroll_into_view_if_needed(timeout=3500)
            await locator.fill(value, timeout=3500)
            await locator.evaluate(
                """(el, value) => {
                  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                  if (setter) setter.call(el, value); else el.value = value;
                  el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: value}));
                  el.dispatchEvent(new Event('change', {bubbles: true}));
                  el.dispatchEvent(new Event('blur', {bubbles: true}));
                }""",
                value,
            )
            await page.wait_for_timeout(250)
            actual = await locator.input_value(timeout=2000)
            return f"filled:{actual}"
        except Exception as exc:
            return f"failed:{type(exc).__name__}"

    try:
        await page.locator('a:has-text("Enter address manually")').first.click(timeout=2500)
        actions.append({"step": "manual_address", "result": "clicked"})
    except Exception:
        actions.append({"step": "manual_address", "result": "not-needed-or-not-found"})

    try:
        actions.append({"step": "fill_billingPostalCode", "result": await force_input("billingPostalCode", "100-0001")})
    except Exception:
        pass

    try:
        select = page.locator('[name="billingAdministrativeArea"]').first
        await select.select_option(label="東京都 — Tokyo", timeout=3500)
        actions.append({"step": "select_prefecture", "result": "東京都 — Tokyo"})
    except Exception:
        try:
            await page.locator('[name="billingAdministrativeArea"]').first.select_option(value="Tokyo", timeout=3500)
            actions.append({"step": "select_prefecture", "result": "Tokyo"})
        except Exception as exc:
            actions.append({"step": "select_prefecture", "result": f"failed:{type(exc).__name__}"})

    # Stripe re-renders dependent address inputs after prefecture selection; fill these last.
    for name, value in [
        ("billingLocality", "千代田区"),
        ("billingAddressLine1", "1-1 Chiyoda"),
        ("billingAddressLine2", "Tokyo"),
    ]:
        actions.append({"step": f"fill_{name}", "result": await force_input(name, value)})

    try:
        values = await page.evaluate(
            """() => Object.fromEntries(
              ['billingPostalCode','billingAdministrativeArea','billingLocality','billingAddressLine1','billingAddressLine2']
                .map((name) => [name, document.querySelector(`[name="${name}"]`)?.value || ''])
            )"""
        )
        actions.append({"step": "address_values", "result": json.dumps(values, ensure_ascii=False)})
    except Exception as exc:
        actions.append({"step": "address_values", "result": f"failed:{type(exc).__name__}"})

    return actions


async def capture(
    hosted_url: str,
    proxy: str,
    out_dir: Path,
    headless: bool,
    click_paypal: bool,
    fill_address: bool,
    submit: bool,
    wait_ms: int,
) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    private_events: list[dict[str, Any]] = []
    actions: list[dict[str, str]] = []
    tunnel_server = None

    async with async_playwright() as p:
        tunnel_server, playwright_proxy = await start_local_proxy(proxy)
        browser = await p.chromium.launch(
            headless=headless,
            proxy=playwright_proxy,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1365, "height": 900},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        page.on(
            "request",
            lambda req: private_events.append(
                {
                    "phase": "request",
                    "method": req.method,
                    "url": req.url,
                    "post_data": req.post_data or "",
                    "headers": {
                        k: v
                        for k, v in req.headers.items()
                        if k.lower()
                        in {
                            "content-type",
                            "origin",
                            "referer",
                            "stripe-version",
                            "user-agent",
                        }
                    },
                }
            )
            if any(host in req.url for host in ("api.stripe.com", "pay.openai.com", "pm-redirects", "paypal.com"))
            else None,
        )

        async def on_response(resp) -> None:
            if not any(host in resp.url for host in ("api.stripe.com", "pay.openai.com", "pm-redirects", "paypal.com")):
                return
            text = await body_text(resp)
            private_events.append(
                {
                    "phase": "response",
                    "status": resp.status,
                    "url": resp.url,
                    "headers": {
                        k: v
                        for k, v in resp.headers.items()
                        if k.lower() in {"content-type", "location", "request-id", "stripe-version"}
                    },
                    "body": text,
                }
            )

        page.on("response", on_response)
        try:
            await page.goto(hosted_url, timeout=70000, wait_until="domcontentloaded")
            await page.wait_for_timeout(9000)
            await page.screenshot(path=str(out_dir / "01-loaded.png"), full_page=True)

            if click_paypal:
                action = await click_in_any_frame(
                    page,
                    [
                        'input[name="payment-method-accordion-item-title"][value="paypal"]',
                        'input[value="paypal"]',
                        'button[aria-label="Pay with PayPal"]',
                    ],
                    force=True,
                )
                actions.append({"step": "select_paypal", "result": action})
                await page.wait_for_timeout(1500)
                if fill_address:
                    actions.extend(await fill_jp_billing_address(page))
                    await page.wait_for_timeout(1000)
                actions.append({"step": "accept_terms", "result": await js_accept_terms(page)})
                await page.wait_for_timeout(1000)
                await page.screenshot(path=str(out_dir / "02-paypal-selected.png"), full_page=True)

            if submit:
                action = await click_in_any_frame(
                    page,
                    [
                        'button[aria-label="Pay with PayPal"]',
                        'button[type="submit"]',
                        'button:has-text("Subscribe")',
                    ],
                    force=True,
                    timeout_ms=6000,
                )
                actions.append({"step": "submit", "result": action})
                await page.wait_for_timeout(wait_ms)
                await page.screenshot(path=str(out_dir / "03-after-submit.png"), full_page=True)
            else:
                await page.wait_for_timeout(wait_ms)
        finally:
            await context.close()
            await browser.close()
            if tunnel_server:
                tunnel_server.close()
                await tunnel_server.wait_closed()

    interesting = [
        item
        for item in private_events
        if any(
            marker in str(item.get("url", ""))
            for marker in (
                "/payment_pages/",
                "/elements/sessions",
                "allowed_origins",
                "pm-redirects.stripe.com/authorize",
                "paypal.com",
            )
        )
    ]
    raw = {
        "created_at": datetime.now().isoformat(),
        "hosted_checkout_url": hosted_url,
        "proxy": proxy,
        "actions": actions,
        "events": private_events,
        "interesting": interesting,
    }
    summary = {
        "created_at": raw["created_at"],
        "hosted_checkout_url": sanitize_url(hosted_url),
        "proxy": mask_proxy(proxy),
        "actions": actions,
        "counts": {
            "events": len(private_events),
            "interesting": len(interesting),
            "confirm_requests": len(
                [
                    e
                    for e in private_events
                    if e.get("phase") == "request" and "/confirm" in str(e.get("url", ""))
                ]
            ),
            "pre_confirm_requests": len(
                [
                    e
                    for e in private_events
                    if e.get("phase") == "request" and "/pre_confirm" in str(e.get("url", ""))
                ]
            ),
        },
        "interesting": redacted(interesting[-80:]),
        "screenshots": {
            "loaded": str(out_dir / "01-loaded.png"),
            "paypal_selected": str(out_dir / "02-paypal-selected.png") if click_paypal else "",
            "after_submit": str(out_dir / "03-after-submit.png") if submit else "",
        },
    }
    write_json(out_dir / "protocol.private.json", raw, private=True)
    write_json(out_dir / "protocol.redacted.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record Stripe hosted checkout protocol requests/responses.")
    parser.add_argument("--hosted-url", default="")
    parser.add_argument("--proxy", default="")
    parser.add_argument("--from-codex-session", action="store_true")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--click-paypal", action="store_true")
    parser.add_argument("--fill-jp-address", action="store_true")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--wait-ms", type=int, default=5000)
    parser.add_argument("--out-dir", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    hosted_url = str(args.hosted_url or "").strip() or latest_private_checkout_url()
    if not hosted_url:
        print("ERROR: hosted checkout URL not found")
        return 2
    proxy = str(args.proxy or "").strip()
    if args.from_codex_session and not proxy:
        _, proxy, _ = latest_codex_material()
    if not proxy:
        print("ERROR: proxy not found")
        return 2
    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent / "runs" / ("protocol-" + now_id())
    result = asyncio.run(
        capture(
            hosted_url,
            proxy,
            out_dir,
            bool(args.headless),
            bool(args.click_paypal),
            bool(args.fill_jp_address),
            bool(args.submit),
            int(args.wait_ms),
        )
    )
    print(json.dumps({
        "counts": result["counts"],
        "summary": str(out_dir / "protocol.redacted.json"),
        "private": str(out_dir / "protocol.private.json"),
        "screenshots": result["screenshots"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
