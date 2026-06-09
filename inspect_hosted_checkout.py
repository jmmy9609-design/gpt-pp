#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import re
import socket
import struct
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from plus_paypal_link_probe import (
    JWT_RE,
    latest_codex_material,
    mask_proxy,
    normalize_proxy,
    sanitize_url,
)


MARKERS = ("paypal", "payment_method", "payment_method_types", "pay.openai", "pm-redirects", "stripe")


def latest_private_checkout_url() -> str:
    base = Path(__file__).resolve().parent / "runs"
    files = sorted(base.glob("*/private.raw.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        url = str((data.get("checkout") or {}).get("hosted_checkout_url") or "").strip()
        if url:
            return url
    return ""


def redact_text(value: str) -> str:
    text = sanitize_url(str(value or ""))
    text = JWT_RE.sub("<access-token-redacted>", text)
    return text


def parse_playwright_proxy(proxy: str) -> dict[str, str]:
    normalized = normalize_proxy(proxy, "socks5h")
    parsed = urlsplit(normalized)
    host = parsed.hostname or ""
    port = parsed.port or 1000
    username = parsed.username or ""
    password = parsed.password or ""
    scheme = "socks5" if parsed.scheme.startswith("socks") else parsed.scheme
    return {
        "server": f"{scheme}://{host}:{port}",
        "username": username,
        "password": password,
    }


async def pipe_stream(reader, writer) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def handle_socks_http_tunnel(client_reader, client_writer, proxy_host: str, proxy_port: int, proxy_user: str, proxy_pass: str) -> None:
    try:
        header = await client_reader.readuntil(b"\r\n\r\n")
        first_line = header.split(b"\r\n", 1)[0].decode("latin1", "replace")
        parts = first_line.split()
        if len(parts) < 3 or parts[0].upper() != "CONNECT":
            client_writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            await client_writer.drain()
            return
        target_host, target_port_s = parts[1].rsplit(":", 1)
        target_port = int(target_port_s)

        remote_reader, remote_writer = await asyncio.open_connection(proxy_host, proxy_port)
        remote_writer.write(b"\x05\x01\x02")
        await remote_writer.drain()
        resp = await remote_reader.readexactly(2)
        if resp != b"\x05\x02":
            raise RuntimeError(f"SOCKS auth method rejected: {resp!r}")
        user_b = proxy_user.encode()
        pass_b = proxy_pass.encode()
        remote_writer.write(b"\x01" + bytes([len(user_b)]) + user_b + bytes([len(pass_b)]) + pass_b)
        await remote_writer.drain()
        auth_resp = await remote_reader.readexactly(2)
        if auth_resp != b"\x01\x00":
            raise RuntimeError("SOCKS username/password rejected")
        host_b = target_host.encode("idna")
        remote_writer.write(b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b + struct.pack("!H", target_port))
        await remote_writer.drain()
        conn_resp = await remote_reader.readexactly(4)
        if conn_resp[1] != 0:
            raise RuntimeError(f"SOCKS connect failed: {conn_resp!r}")
        atyp = conn_resp[3]
        if atyp == 1:
            await remote_reader.readexactly(4)
        elif atyp == 3:
            ln = await remote_reader.readexactly(1)
            await remote_reader.readexactly(ln[0])
        elif atyp == 4:
            await remote_reader.readexactly(16)
        await remote_reader.readexactly(2)

        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_writer.drain()
        await asyncio.gather(
            pipe_stream(client_reader, remote_writer),
            pipe_stream(remote_reader, client_writer),
        )
    except Exception:
        try:
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_writer.drain()
        except Exception:
            pass
    finally:
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except Exception:
            pass


async def start_local_proxy(proxy: str):
    parsed = urlsplit(normalize_proxy(proxy, "socks5h"))
    host = parsed.hostname or "gate.kookeey.info"
    port = parsed.port or 1000
    user = parsed.username or ""
    password = parsed.password or ""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    local_port = sock.getsockname()[1]
    sock.close()
    server = await asyncio.start_server(
        lambda r, w: handle_socks_http_tunnel(r, w, host, port, user, password),
        "127.0.0.1",
        local_port,
    )
    return server, {"server": f"http://127.0.0.1:{local_port}"}


def slim_event(url: str, status: int = 0, note: str = "") -> dict[str, Any]:
    return {
        "url": redact_text(url),
        "status": status,
        "note": note,
    }


async def inspect(hosted_url: str, proxy: str, headless: bool, out_dir: Path) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    network_events: list[dict[str, Any]] = []
    marker_hits: list[dict[str, Any]] = []
    paypal_authorize_url = ""

    tunnel_server = None
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
        page = await context.new_page()

        async def on_response(response):
            nonlocal paypal_authorize_url
            url = response.url
            if not any(host in url for host in ("stripe.com", "pay.openai.com", "chatgpt.com", "pm-redirects")):
                return
            event = slim_event(url, response.status)
            network_events.append(event)
            if "pm-redirects.stripe.com/authorize" in url:
                paypal_authorize_url = url
                marker_hits.append(slim_event(url, response.status, "paypal-authorize-url-response"))
            try:
                headers = response.headers
                content_type = headers.get("content-type", "")
                if not any(kind in content_type for kind in ("json", "text", "javascript", "html")):
                    return
                body = await response.text()
            except Exception:
                return
            lower = body.lower()
            if "pm-redirects.stripe.com/authorize" in lower:
                match = re.search(r"https://pm-redirects\.stripe\.com/authorize/[^\"'\\\s<>]+", body)
                if match:
                    paypal_authorize_url = match.group(0)
                    marker_hits.append(slim_event(paypal_authorize_url, response.status, "paypal-authorize-url-body"))
            if any(marker in lower for marker in MARKERS):
                snippet = body[:2500]
                marker_hits.append({
                    "url": redact_text(url),
                    "status": response.status,
                    "content_type": content_type,
                    "snippet": redact_text(snippet),
                })

        page.on("response", on_response)
        await page.goto(hosted_url, timeout=70000, wait_until="domcontentloaded")
        await page.wait_for_timeout(12000)

        page_text = ""
        buttons: list[dict[str, str]] = []
        frames: list[dict[str, str]] = []
        try:
            page_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            page_text = ""
        try:
            buttons = await page.evaluate(
                """() => Array.from(document.querySelectorAll('button, [role=button], label, input, a')).slice(0, 220).map((el) => ({
                    tag: el.tagName,
                    type: el.getAttribute('type') || '',
                    role: el.getAttribute('role') || '',
                    name: el.getAttribute('name') || '',
                    value: el.getAttribute('value') || '',
                    aria: el.getAttribute('aria-label') || '',
                    text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 200)
                }))"""
            )
        except Exception:
            buttons = []
        try:
            frames = [
                {"url": redact_text(frame.url), "name": frame.name or ""}
                for frame in page.frames
            ]
        except Exception:
            frames = []

        paypal_visible = "paypal" in page_text.lower() or any(
            "paypal" in " ".join(str(v) for v in item.values()).lower() for item in buttons
        )

        screenshot_path = out_dir / "hosted-checkout.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)

        await context.close()
        await browser.close()
        if tunnel_server:
            tunnel_server.close()
            await tunnel_server.wait_closed()

    return {
        "created_at": datetime.now().isoformat(),
        "hosted_checkout_url": sanitize_url(hosted_url),
        "proxy": mask_proxy(proxy),
        "paypal_visible": paypal_visible,
        "paypal_authorize_url": sanitize_url(paypal_authorize_url),
        "page_text_excerpt": page_text[:4000],
        "buttons": buttons,
        "frames": frames,
        "network_events": network_events[-120:],
        "marker_hits": marker_hits[-80:],
        "screenshot": str(out_dir / "hosted-checkout.png"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect Stripe hosted checkout for PayPal availability and network markers.")
    parser.add_argument("--hosted-url", default="")
    parser.add_argument("--proxy", default="")
    parser.add_argument("--from-codex-session", action="store_true")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
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
    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent / "runs" / ("inspect-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    result = asyncio.run(inspect(hosted_url, proxy, bool(args.headless), out_dir))
    (out_dir / "inspect.redacted.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "paypal_visible": result["paypal_visible"],
        "paypal_authorize_url": result["paypal_authorize_url"],
        "screenshot": result["screenshot"],
        "summary": str(out_dir / "inspect.redacted.json"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
