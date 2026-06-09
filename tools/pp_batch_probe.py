#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib import error
from urllib import request


JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def http_json(url: str, payload: dict | None = None, timeout: float = 30.0) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"

    req = request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        if raw:
            return json.loads(raw)
        raise
    except Exception:
        args = [
            "curl",
            "-sS",
            "--connect-timeout",
            "5",
            "--max-time",
            str(int(timeout)),
            "-H",
            "Accept: application/json",
        ]
        input_data = None
        if payload is not None:
            input_data = json.dumps(payload).encode()
            args += ["-X", "POST", "-H", "Content-Type: application/json", "--data-binary", "@-"]
        args.append(url)
        out = subprocess.check_output(args, input=input_data)
        return json.loads(out.decode())


def load_tokens(source: str, limit: int) -> list[str]:
    if source.startswith("file://"):
        raw = Path(source[7:]).read_text()
    else:
        data = http_json(source, timeout=15)
        raw = json.dumps(data)
    tokens: list[str] = []
    seen: set[str] = set()
    for token in JWT_RE.findall(raw):
        if not is_access_token(token):
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if limit and len(tokens) >= limit:
            break
    return tokens


def is_access_token(token: str) -> bool:
    try:
        payload_part = token.split(".")[1]
        payload_part += "=" * ((4 - len(payload_part) % 4) % 4)
        payload = json.loads(__import__("base64").urlsafe_b64decode(payload_part))
    except Exception:
        return False
    aud = payload.get("aud") or []
    if isinstance(aud, str):
        aud = [aud]
    scopes = payload.get("scp") or payload.get("scope") or []
    if isinstance(scopes, str):
        scopes = scopes.split()
    return "https://api.openai.com/v1" in aud and (
        "model.request" in scopes or "offline_access" in scopes
    )


def normalize_proxy_pool(raw: str, per_city_samples: int) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []

    candidates = [line.strip() for line in re.split(r"[\n\r,;]+", raw) if line.strip()]
    expanded: list[str] = []
    base_nonce = int(time.time() * 1000) % 100000000
    for item in candidates:
        expanded.append(item)
        for i in range(1, max(1, per_city_samples)):
            suffix = f"{(base_nonce + i) % 100000000:08d}"
            if re.search(r"-[A-Za-z]{2}-\d{6,12}(@|$)", item):
                expanded.append(re.sub(r"-\d{6,12}(@|$)", f"-{suffix}\\1", item))
            elif re.search(r"-[A-Za-z]{2}(@|$)", item):
                expanded.append(re.sub(r"(-[A-Za-z]{2})(@|$)", f"\\1-{suffix}\\2", item))
    return list(dict.fromkeys(expanded))


def city_key(ipinfo: dict) -> str:
    country = ipinfo.get("country") or ipinfo.get("country_name") or "unknown"
    region = ipinfo.get("region") or ipinfo.get("regionName") or ""
    city = ipinfo.get("city") or "unknown"
    org = ipinfo.get("org") or ipinfo.get("isp") or ""
    return " / ".join([x for x in [country, region, city, org] if x])


def probe_city(proxy: str, gateway: str) -> dict:
    data = http_json(f"{gateway.rstrip('/')}/api/test-proxy", {"proxy": proxy}, timeout=25)
    if data.get("ok"):
        return data
    return {"ok": False, "city_key": data.get("message") or data.get("code") or "proxy_failed"}


def extract_one(gateway: str, token: str, proxy_pool: str, city: str, index: int) -> dict:
    started = time.time()
    try:
        result = http_json(
            f"{gateway.rstrip('/')}/api/extract",
            {"credential": token, "proxy": proxy_pool},
            timeout=180,
        )
        result_city = " / ".join(
            x
            for x in [
                result.get("proxy_country"),
                result.get("proxy_region"),
                result.get("proxy_city"),
                result.get("proxy_org"),
            ]
            if x
        )
        ok = bool(result.get("ok") and result.get("paypal_authorize_url"))
        return {
            "index": index,
            "suffix": token[-8:],
            "ok": ok,
            "code": result.get("code"),
            "amount": result.get("amount_display"),
            "elapsed_ms": int((time.time() - started) * 1000),
            "paypal_url_present": bool(result.get("paypal_authorize_url")),
            "message": result.get("message", ""),
            "city": result_city or city,
        }
    except Exception as exc:
        return {
            "index": index,
            "suffix": token[-8:],
            "ok": False,
            "code": "request_error",
            "amount": "unknown",
            "elapsed_ms": int((time.time() - started) * 1000),
            "paypal_url_present": False,
            "message": str(exc),
            "city": city,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accounts-url", default="http://127.0.0.1:8000/api/accounts")
    parser.add_argument("--gateway", default="http://127.0.0.1:8787")
    parser.add_argument("--proxy-file", default="/tmp/pp_proxy_url.txt")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--per-city-samples", type=int, default=6)
    parser.add_argument("--out", default="/tmp/pp_batch_probe_results.jsonl")
    args = parser.parse_args()

    tokens = load_tokens(args.accounts_url, args.limit)
    if not tokens:
        print(json.dumps({"ok": False, "error": "no_tokens"}, ensure_ascii=False))
        return 1

    proxy_raw = Path(args.proxy_file).read_text().strip()
    proxy_candidates = normalize_proxy_pool(proxy_raw, args.per_city_samples)
    if not proxy_candidates:
        print(json.dumps({"ok": False, "error": "no_proxy"}, ensure_ascii=False))
        return 1

    geo_cache: dict[str, str] = {}
    for proxy in proxy_candidates:
        city_probe = probe_city(proxy, args.gateway)
        geo_cache[proxy] = city_key(city_probe) if city_probe.get("ok") else city_probe.get("city_key", "proxy_failed")

    stats = defaultdict(lambda: {"attempts": 0, "success": 0, "fail": 0, "elapsed_ms": []})
    out_path = Path(args.out)
    out_path.write_text("")
    rows: list[dict] = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = []
        for i, token in enumerate(tokens):
            start_proxy = proxy_candidates[i % len(proxy_candidates)]
            rotated = proxy_candidates[i % len(proxy_candidates) :] + proxy_candidates[: i % len(proxy_candidates)]
            proxy_pool = "\n".join(rotated)
            city = geo_cache.get(start_proxy, "unknown")
            futures.append(pool.submit(extract_one, args.gateway, token, proxy_pool, city, i + 1))

        for fut in as_completed(futures):
            row = fut.result()
            rows.append(row)
            city = row.get("city") or "unknown"
            bucket = stats[city]
            bucket["attempts"] += 1
            bucket["success"] += int(row["ok"])
            bucket["fail"] += int(not row["ok"])
            bucket["elapsed_ms"].append(row["elapsed_ms"])
            with out_path.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps(row, ensure_ascii=False), flush=True)

    summary = []
    for name, item in stats.items():
        attempts = item["attempts"]
        success = item["success"]
        avg_ms = int(sum(item["elapsed_ms"]) / max(1, len(item["elapsed_ms"])))
        summary.append(
            {
                "city": name,
                "attempts": attempts,
                "success": success,
                "fail": item["fail"],
                "success_rate": round(success / attempts * 100, 1) if attempts else 0,
                "avg_ms": avg_ms,
            }
        )
    summary.sort(key=lambda x: (-x["success_rate"], x["avg_ms"]))
    print(json.dumps({"summary": summary, "tokens": len(tokens), "out": str(out_path)}, ensure_ascii=False), flush=True)
    return 0 if all(row["ok"] for row in rows) else 2


if __name__ == "__main__":
    sys.exit(main())
