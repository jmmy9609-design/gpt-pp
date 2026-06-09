#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import string
from datetime import datetime
from pathlib import Path
from typing import Any

from inspect_hosted_checkout import latest_private_checkout_url, redact_text, start_local_proxy
from plus_paypal_link_probe import latest_codex_material, mask_proxy, sanitize_url


PM_AUTHORIZE_RE = re.compile(r"https://pm-redirects\.stripe\.com/authorize/[^\"'\\\s<>]+")
PAYPAL_RE = re.compile(r"https://(?:www\.|www\.sandbox\.|www-m\.)?paypal\.com/[^\"'\\\s<>]+", re.I)

EMAIL_SELECTORS = [
    "input#email",
    "input[name='login_email']",
    "input[name='email']",
    "input[type='email']",
    "input[autocomplete='username']",
]
NEXT_SELECTORS = [
    "button#btnNext",
    "input#btnNext",
    "button[name='btnNext']",
    "button:has-text('Next')",
    "input[value='Next']",
    "button:has-text('Continue')",
    "button:has-text('次へ')",
    "button:has-text('続行')",
]


def now_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def random_gmail_address() -> str:
    alphabet = string.ascii_lowercase + string.digits
    length = random.randint(13, 20)
    local = random.choice(string.ascii_lowercase) + "".join(random.choice(alphabet) for _ in range(length - 1))
    return f"{local}@gmail.com"


def mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 4:
        return f"{local[:1]}***@{domain}"
    return f"{local[:2]}***{local[-2:]}@{domain}"


def write_json(path: Path, data: dict[str, Any], private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if private:
        try:
            path.chmod(0o600)
        except Exception:
            pass


def first_url(text: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(text or "")
    return match.group(0) if match else ""


def redacted_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in events[-250:]:
        out.append({k: redact_text(str(v)) if isinstance(v, str) else v for k, v in item.items()})
    return out


async def safe_screenshot(page, path: Path) -> str:
    try:
        await page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return ""


async def capture_text(page) -> str:
    try:
        return await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        return ""


async def click_in_any_frame(page, selectors: list[str], *, force: bool = False, timeout_ms: int = 3500) -> str:
    last_error = ""
    for frame in page.frames:
        for selector in selectors:
            try:
                locator = frame.locator(selector).first
                if await locator.count() < 1:
                    continue
                await locator.click(timeout=timeout_ms, force=force)
                return f"{frame.name or '<main>'}:{selector}"
            except Exception as exc:
                last_error = f"{selector}: {type(exc).__name__}: {exc}"
    return f"not-clicked ({last_error})" if last_error else "not-found"


async def js_click_paypal_radio(page) -> str:
    script = r"""
    () => {
      const input = document.querySelector('input[name="payment-method-accordion-item-title"][value="paypal"], input[value="paypal"]');
      if (!input) return 'paypal-radio-not-found';
      input.scrollIntoView({block: 'center', inline: 'center'});
      input.click();
      input.dispatchEvent(new Event('input', {bubbles: true}));
      input.dispatchEvent(new Event('change', {bubbles: true}));
      return 'paypal-radio-clicked';
    }
    """
    try:
        return await page.evaluate(script)
    except Exception as exc:
        return f"paypal-radio-js-error:{type(exc).__name__}"


async def js_accept_terms(page) -> str:
    script = r"""
    () => {
      const labelText = (el) => String(el.innerText || el.textContent || '');
      const labels = Array.from(document.querySelectorAll('label'));
      const targetLabel = labels.find((el) => {
        const text = labelText(el);
        if (/AI agent|Link CLI/i.test(text)) return false;
        return /charged the amount|agree to OpenAI|Terms of Use|terms until you cancel/i.test(text);
      });
      if (targetLabel) {
        targetLabel.scrollIntoView({block: 'center', inline: 'center'});
        targetLabel.click();
        return `terms-label=true checkboxes-clicked=0`;
      }
      const boxes = Array.from(document.querySelectorAll('input[type="checkbox"]'));
      let clicked = 0;
      for (const box of boxes) {
        const id = box.id ? `label[for="${CSS.escape(box.id)}"]` : '';
        const label = id ? document.querySelector(id) : box.closest('label');
        const text = label ? labelText(label) : '';
        if (/AI agent|Link CLI/i.test(text)) continue;
        if (!/charged the amount|agree to OpenAI|Terms of Use|terms until you cancel/i.test(text)) continue;
        if (!box.checked && !box.disabled) {
          box.scrollIntoView({block: 'center', inline: 'center'});
          box.click();
          box.dispatchEvent(new Event('input', {bubbles: true}));
          box.dispatchEvent(new Event('change', {bubbles: true}));
          clicked++;
        }
      }
      return `terms-label=${Boolean(targetLabel)} checkboxes-clicked=${clicked}`;
    }
    """
    try:
        return await page.evaluate(script)
    except Exception as exc:
        return f"terms-js-error:{type(exc).__name__}"


async def fill_jp_billing_address(page) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []

    async def force_input(name: str, value: str) -> str:
        try:
            locator = page.locator(f'[name="{name}"]').first
            await locator.scroll_into_view_if_needed(timeout=3000)
            await locator.fill(value, timeout=3000)
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
            return "filled"
        except Exception as exc:
            return f"failed:{type(exc).__name__}"

    async def force_select_prefecture() -> str:
        selectors = [
            '[name="billingAdministrativeArea"]',
            'select[name="billingAdministrativeArea"]',
            '[aria-label*="Prefecture"]',
            '[placeholder*="Prefecture"]',
        ]
        script = r"""
        (selectors) => {
          const wantedValues = ['Tokyo', '東京都', 'JP-13', '13'];
          const wantedText = /(東京都|Tokyo)/i;
          for (const selector of selectors) {
            const el = document.querySelector(selector);
            if (!el) continue;
            el.scrollIntoView({block: 'center', inline: 'center'});
            if (el.tagName === 'SELECT') {
              const option = Array.from(el.options || []).find((opt) => {
                return wantedValues.includes(opt.value) || wantedText.test(opt.textContent || '');
              });
              if (!option) continue;
              el.value = option.value;
              el.dispatchEvent(new Event('input', {bubbles: true}));
              el.dispatchEvent(new Event('change', {bubbles: true}));
              el.dispatchEvent(new Event('blur', {bubbles: true}));
              return `selected:${option.value}:${option.textContent}`;
            }
            const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
            if (setter) setter.call(el, 'Tokyo'); else el.value = 'Tokyo';
            el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: 'Tokyo'}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            el.dispatchEvent(new Event('blur', {bubbles: true}));
            return `filled:${selector}`;
          }
          return 'not-found';
        }
        """
        try:
            result = await page.evaluate(script, selectors)
            if result and result != "not-found":
                return str(result)
        except Exception as exc:
            actions.append({"step": "select_prefecture_js", "result": f"failed:{type(exc).__name__}"})

        for text in ("東京都 — Tokyo", "Tokyo", "東京都"):
            try:
                await page.locator('[name="billingAdministrativeArea"]').first.click(timeout=1500, force=True)
                await page.locator(f"text={text}").first.click(timeout=2500, force=True)
                return f"clicked-option:{text}"
            except Exception:
                continue
        return "failed:not-selected"

    try:
        await page.locator('a:has-text("Enter address manually")').first.click(timeout=2000)
        actions.append({"step": "manual_address", "result": "clicked"})
    except Exception:
        actions.append({"step": "manual_address", "result": "not-needed-or-not-found"})

    actions.append({"step": "fill_billingPostalCode", "result": await force_input("billingPostalCode", "100-0001")})
    actions.append({"step": "select_prefecture", "result": await force_select_prefecture()})

    for name, value in [
        ("billingLocality", "Chiyoda-ku"),
        ("billingAddressLine1", "1-1 Chiyoda"),
        ("billingAddressLine2", "Tokyo"),
    ]:
        actions.append({"step": f"fill_{name}", "result": await force_input(name, value)})
    return actions


async def collect_runtime_hooks(captured: dict[str, Any], pages: list[Any]) -> None:
    for idx, pg in enumerate(list(pages)):
        try:
            if pg.is_closed():
                continue
            data = await pg.evaluate(
                """() => ({
                  authorize: window.__ppCapturedAuthorize || '',
                  paypal: window.__ppCapturedPayPal || '',
                  events: Array.isArray(window.__ppHookEvents) ? window.__ppHookEvents.slice(-50) : []
                })"""
            )
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for item in data.get("events") or []:
            if isinstance(item, dict) and item.get("url"):
                captured["events"].append({"source": f"runtime-hook:{idx}:{item.get('kind','event')}", "status": 0, "url": str(item.get("url"))})
        pm_url = str(data.get("authorize") or "")
        paypal_url = str(data.get("paypal") or "")
        if pm_url and not captured.get("pm_authorize_url"):
            captured["pm_authorize_url"] = pm_url
        if paypal_url and not captured.get("paypal_url"):
            captured["paypal_url"] = paypal_url


async def fill_email_in_any_frame(page, email: str) -> str:
    for frame in page.frames:
        for selector in EMAIL_SELECTORS:
            try:
                locator = frame.locator(selector).first
                if await locator.count() < 1:
                    continue
                await locator.scroll_into_view_if_needed(timeout=2500)
                await locator.fill(email, timeout=3500)
                await locator.evaluate(
                    """(el, value) => {
                      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                      if (setter) setter.call(el, value); else el.value = value;
                      el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: value}));
                      el.dispatchEvent(new Event('change', {bubbles: true}));
                    }""",
                    email,
                )
                return f"{frame.name or '<main>'}:{selector}"
            except Exception:
                continue
    return ""


async def click_next_in_any_frame(page) -> str:
    return await click_in_any_frame(page, NEXT_SELECTORS, force=True, timeout_ms=5000)


async def fill_paypal_email_next(pages: list[Any], out_dir: Path, timeout_seconds: int = 45) -> dict[str, Any]:
    email = random_gmail_address()
    masked = mask_email(email)
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    seen_urls: list[str] = []

    while asyncio.get_running_loop().time() < deadline:
        for idx, pg in enumerate(list(pages)):
            try:
                if pg.is_closed():
                    continue
                current_url = str(pg.url or "")
                if current_url and current_url not in seen_urls:
                    seen_urls.append(current_url)
                page_text = (await capture_text(pg))[:800]
                is_candidate = "paypal.com/" in current_url.lower() or "paypal" in page_text.lower()
                if not is_candidate:
                    for frame in pg.frames:
                        for selector in EMAIL_SELECTORS:
                            try:
                                if await frame.locator(selector).first.count() > 0:
                                    is_candidate = True
                                    break
                            except Exception:
                                continue
                        if is_candidate:
                            break
                if not is_candidate:
                    continue

                await safe_screenshot(pg, out_dir / f"paypal-login-before-{idx}.png")
                filled = await fill_email_in_any_frame(pg, email)
                if not filled:
                    continue
                next_clicked = await click_next_in_any_frame(pg)
                await pg.wait_for_timeout(2500)
                after = str(pg.url or "")
                shot = await safe_screenshot(pg, out_dir / f"paypal-login-after-next-{idx}.png")
                return {
                    "status": "filled_next",
                    "page_index": idx,
                    "email": email,
                    "email_masked": masked,
                    "fill_selector": filled,
                    "next_selector": next_clicked,
                    "before_url": current_url,
                    "after_url": after,
                    "screenshot": shot,
                }
            except Exception:
                continue
        await asyncio.sleep(0.5)

    return {"status": "not_found", "email_masked": masked, "seen_urls": seen_urls[-12:]}


async def wait_for_authorize(captured: dict[str, Any], timeout_seconds: int) -> str:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        url = str(captured.get("pm_authorize_url") or captured.get("paypal_url") or "")
        if url:
            return url
        await asyncio.sleep(0.5)
    return ""


async def run_click_flow(hosted_url: str, proxy: str, headless: bool, timeout_seconds: int, out_dir: Path) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    captured: dict[str, Any] = {
        "pm_authorize_url": "",
        "paypal_url": "",
        "popup_url": "",
        "events": [],
        "actions": [],
        "screenshots": {},
    }
    pages = []
    tunnel_server = None

    def note_url(url: str, source: str, status: int = 0) -> None:
        if not url:
            return
        low = url.lower()
        if "stripe.com" in low or "paypal.com" in low or "pay.openai.com" in low or "pm-redirects" in low:
            captured["events"].append({"source": source, "status": status, "url": url})
        if "pm-redirects.stripe.com/authorize" in low and not captured["pm_authorize_url"]:
            captured["pm_authorize_url"] = url
        if "paypal.com/" in low and not captured["paypal_url"]:
            captured["paypal_url"] = url

    async with async_playwright() as p:
        tunnel_server, playwright_proxy = await start_local_proxy(proxy)
        browser = await p.chromium.launch(
            headless=headless,
            proxy=playwright_proxy,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1365, "height": 900},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        async def trim_static_assets(route, request):
            url = request.url.lower()
            if request.resource_type in {"image", "font", "media"}:
                await route.abort()
                return
            if "icon-pm-paypal" in url or "/fingerprinted/img/" in url or "applepay.html" in url:
                await route.abort()
                return
            await route.continue_()

        await context.route("**/*", trim_static_assets)
        await context.add_init_script(
            r"""
            (() => {
              if (window.__ppHookInstalled) return;
              window.__ppHookInstalled = true;
              window.__ppHookEvents = [];
              const rec = (kind, url) => {
                try {
                  const u = String(url || '');
                  if (!u) return;
                  window.__ppHookEvents.push({kind, url: u, ts: Date.now()});
                  if (/pm-redirects\.stripe\.com\/authorize/i.test(u)) window.__ppCapturedAuthorize = u;
                  if (/paypal\.com\//i.test(u)) window.__ppCapturedPayPal = u;
                } catch (_) {}
              };
              const oldOpen = window.open;
              window.open = function(url, target, features) {
                rec('window.open', url);
                try {
                  const u = String(url || '');
                  if (/pm-redirects\.stripe\.com\/authorize|paypal\.com\//i.test(u)) {
                    window.location.href = u;
                    return window;
                  }
                } catch (_) {}
                return oldOpen ? oldOpen.apply(this, arguments) : null;
              };
              const oldFetch = window.fetch;
              if (oldFetch) {
                window.fetch = async function(input, init) {
                  const resp = await oldFetch.apply(this, arguments);
                  try {
                    const u = String(typeof input === 'string' ? input : (input && input.url) || '');
                    if (/stripe\.com|pay\.openai\.com|pm-redirects|paypal\.com/i.test(u)) {
                      const body = await resp.clone().text();
                      const pm = body.match(/https:\/\/pm-redirects\.stripe\.com\/authorize\/[^"'\\\s<>]+/i);
                      const pp = body.match(/https:\/\/(?:www\.|www\.sandbox\.|www-m\.)?paypal\.com\/[^"'\\\s<>]+/i);
                      if (pm) rec('fetch-body:pm-authorize', pm[0]);
                      if (pp) rec('fetch-body:paypal', pp[0]);
                    }
                  } catch (_) {}
                  return resp;
                };
              }
            })();
            """
        )

        async def wire_page(pg, label: str) -> None:
            pages.append(pg)
            pg.on("request", lambda req: note_url(req.url, f"{label}:request"))
            pg.on("framenavigated", lambda frame: note_url(frame.url, f"{label}:frame"))

            async def on_response(response):
                url = response.url
                note_url(url, f"{label}:response", response.status)
                low = url.lower()
                if not any(x in low for x in ("stripe.com", "paypal.com", "pay.openai.com", "pm-redirects")):
                    return
                try:
                    content_type = response.headers.get("content-type", "")
                    if not any(kind in content_type for kind in ("json", "text", "javascript", "html")):
                        return
                    body = await response.text()
                except Exception:
                    return
                pm_url = first_url(body, PM_AUTHORIZE_RE)
                pp_url = first_url(body, PAYPAL_RE)
                if pm_url and not captured["pm_authorize_url"]:
                    captured["pm_authorize_url"] = pm_url
                    captured["events"].append({"source": f"{label}:body:pm-authorize", "status": response.status, "url": pm_url})
                if pp_url and not captured["paypal_url"]:
                    captured["paypal_url"] = pp_url
                    captured["events"].append({"source": f"{label}:body:paypal", "status": response.status, "url": pp_url})

            pg.on("response", on_response)

        context.on("page", lambda pg: asyncio.create_task(wire_page(pg, "popup")))
        page = await context.new_page()
        await wire_page(page, "main")

        try:
            await page.goto(hosted_url, timeout=70000, wait_until="domcontentloaded")
            await page.wait_for_timeout(9000)
            captured["screenshots"]["loaded"] = await safe_screenshot(page, out_dir / "01-loaded.png")

            action = await click_in_any_frame(
                page,
                [
                    'input[name="payment-method-accordion-item-title"][value="paypal"]',
                    'input[value="paypal"]',
                    'button[aria-label="Pay with PayPal"]',
                ],
                force=True,
            )
            captured["actions"].append({"step": "select_paypal", "result": action})
            if action.startswith("not-"):
                captured["actions"].append({"step": "select_paypal_js", "result": await js_click_paypal_radio(page)})
            await page.wait_for_timeout(1500)

            captured["actions"].extend(await fill_jp_billing_address(page))
            await page.wait_for_timeout(800)
            captured["actions"].append({"step": "accept_terms_js", "result": await js_accept_terms(page)})
            await page.wait_for_timeout(1000)
            captured["screenshots"]["paypal_selected"] = await safe_screenshot(page, out_dir / "02-paypal-selected.png")

            action = await click_in_any_frame(
                page,
                [
                    'button[aria-label="Pay with PayPal"]',
                    'button[type="submit"]',
                    'button:has-text("Subscribe")',
                    '[role="button"]:has-text("Subscribe")',
                ],
                force=True,
                timeout_ms=6000,
            )
            captured["actions"].append({"step": "submit_or_paypal", "result": action})

            found = await wait_for_authorize(captured, timeout_seconds)
            await collect_runtime_hooks(captured, pages)
            if not found:
                # Some Stripe builds need a second submit after wallet selection renders.
                action = await click_in_any_frame(
                    page,
                    [
                        'button[aria-label="Pay with PayPal"]',
                        'button[type="submit"]',
                        'button:has-text("Subscribe")',
                    ],
                    force=True,
                    timeout_ms=5000,
                )
                captured["actions"].append({"step": "submit_retry", "result": action})
                found = await wait_for_authorize(captured, min(30, timeout_seconds))
                await collect_runtime_hooks(captured, pages)

            if captured.get("pm_authorize_url") and not captured.get("paypal_url"):
                try:
                    target_page = pages[-1] if pages else page
                    await target_page.goto(str(captured["pm_authorize_url"]), timeout=70000, wait_until="domcontentloaded")
                    captured["actions"].append({"step": "force_pm_authorize_nav", "result": "navigated"})
                    await target_page.wait_for_timeout(5000)
                    await collect_runtime_hooks(captured, pages)
                except Exception as exc:
                    captured["actions"].append({"step": "force_pm_authorize_nav", "result": f"failed:{type(exc).__name__}"})

            captured["paypal_login"] = await fill_paypal_email_next(pages, out_dir, min(45, max(15, timeout_seconds)))

            for idx, pg in enumerate(pages):
                try:
                    note_url(pg.url, f"page-{idx}:final-url")
                    if "paypal.com/" in pg.url.lower():
                        captured["popup_url"] = pg.url
                    await safe_screenshot(pg, out_dir / f"final-page-{idx}.png")
                except Exception:
                    pass

            text = await capture_text(page)
            captured["page_text_excerpt"] = text[:4000]
            captured["status"] = "ok" if (captured["pm_authorize_url"] or captured["paypal_url"] or captured["popup_url"]) else "not_found"
        except Exception as exc:
            captured["status"] = "failed"
            captured["error"] = f"{type(exc).__name__}: {exc}"
            try:
                captured["screenshots"]["error"] = await safe_screenshot(page, out_dir / "error.png")
                captured["page_text_excerpt"] = (await capture_text(page))[:4000]
            except Exception:
                pass
        finally:
            await context.close()
            await browser.close()
            if tunnel_server:
                tunnel_server.close()
                await tunnel_server.wait_closed()

    raw = {
        "created_at": datetime.now().isoformat(),
            "hosted_checkout_url": hosted_url,
            "pm_authorize_url": captured.get("pm_authorize_url", ""),
            "paypal_url": captured.get("paypal_url", ""),
            "popup_url": captured.get("popup_url", ""),
            "paypal_login": captured.get("paypal_login", {}),
            "events": captured.get("events", [])[-250:],
        }
    redacted = {
        "created_at": raw["created_at"],
        "hosted_checkout_url": sanitize_url(hosted_url),
        "proxy": mask_proxy(proxy),
        "status": captured.get("status"),
        "pm_authorize_url": sanitize_url(str(captured.get("pm_authorize_url") or "")),
        "paypal_url": sanitize_url(str(captured.get("paypal_url") or "")),
        "popup_url": sanitize_url(str(captured.get("popup_url") or "")),
        "actions": captured.get("actions", []),
        "paypal_login": {
            k: (mask_email(v) if k == "email" else sanitize_url(v) if k.endswith("_url") and isinstance(v, str) else v)
            for k, v in dict(captured.get("paypal_login") or {}).items()
            if k != "email" or isinstance(v, str)
        },
        "screenshots": captured.get("screenshots", {}),
        "page_text_excerpt": captured.get("page_text_excerpt", ""),
        "events": redacted_events(captured.get("events", [])),
    }
    write_json(out_dir / "private.raw.json", raw, private=True)
    write_json(out_dir / "paypal-click.redacted.json", redacted)
    return redacted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Click Stripe hosted checkout into PayPal and capture pm-redirects authorize URL.")
    parser.add_argument("--hosted-url", default="")
    parser.add_argument("--proxy", default="")
    parser.add_argument("--from-codex-session", action="store_true")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--timeout-seconds", type=int, default=90)
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
    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent / "runs" / ("paypal-click-" + now_id())
    result = asyncio.run(run_click_flow(hosted_url, proxy, bool(args.headless), int(args.timeout_seconds), out_dir))
    print(json.dumps({
        "status": result.get("status"),
        "pm_authorize_url": result.get("pm_authorize_url"),
        "paypal_url": result.get("paypal_url"),
        "popup_url": result.get("popup_url"),
        "summary": str(out_dir / "paypal-click.redacted.json"),
        "private": str(out_dir / "private.raw.json"),
        "screenshots": result.get("screenshots"),
        "paypal_login": result.get("paypal_login"),
    }, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" and dict(result.get("paypal_login") or {}).get("status") == "filled_next" else 1


if __name__ == "__main__":
    raise SystemExit(main())
