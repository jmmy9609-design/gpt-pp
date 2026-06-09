from __future__ import annotations

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
from unittest.mock import patch
import json


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "webapp"))

from protocol_paypal_authorize import (  # noqa: E402
    CheckoutGuardError,
    NonZeroAmountError,
    build_confirm_payload,
    checkout_amount_guard,
    confirm_paypal_authorize,
    confirm_paypal,
    display_amounts,
    verify_zero_amount,
)
from webapp.server import (  # noqa: E402
    PublicApiError,
    _run_extraction_with_proxy_inner,
    billing_from_proxy_geo,
    collect_access_tokens,
    confirm_paypal_authorize_http,
    extract_access_token,
    extract_proxy,
    extract_proxy_candidates,
    fetch_proxy_provider_entries,
    parse_checkout_matrix,
    checkout_pairs_for_proxy,
    confirm_custom_paypal_authorize_http,
    mark_proxy_bad,
    mark_proxy_good,
    proxy_geo_priority,
    record_city_stat,
    sort_by_proxy_health,
    run_extraction,
    run_extraction_race,
    sanitize_error_value,
    schedule_background_redirect_poll,
    validate_billing_identity,
    PlusLinkHandler,
)


TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signature"
OPENAI_TOKEN = (
    "eyJhbGciOiJSUzI1NiJ9."
    "eyJhdWQiOlsiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS92MSJdLCJzdWIiOiJ1c2VyIn0."
    "signature"
)
OTHER_TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJhdWQiOlsiaHR0cHM6Ly9leGFtcGxlLmNvbSJdLCJzdWIiOiJ1c2VyIn0.signature"


def make_init(amount_due=0, *, currency="usd", methods=None):
    return {
        "invoice": {"amount_due": amount_due, "currency": currency},
        "currency": currency,
        "payment_method_types": methods if methods is not None else ["card", "paypal"],
        "init_checksum": "checksum",
        "customer_email": "buyer@example.test",
        "url": "https://pay.openai.com/c/pay/cs_test#fidkdWxOYHwnPyd1blpxYHZxWjA0S3A",
    }


class ZeroGateTests(unittest.TestCase):
    def test_zero_amount_passes(self):
        gate = verify_zero_amount(make_init(0))
        self.assertTrue(gate["zero_verified"])
        self.assertEqual(gate["amount_due"], 0)

    def test_non_zero_amount_is_blocked(self):
        with self.assertRaises(NonZeroAmountError) as ctx:
            verify_zero_amount(make_init(2000))
        self.assertEqual(ctx.exception.code, "non_zero_amount")
        self.assertEqual(ctx.exception.amount_due, 2000)

    def test_missing_amount_fails_closed(self):
        with self.assertRaises(CheckoutGuardError):
            verify_zero_amount({"invoice": {}, "payment_method_types": ["paypal"]})

    def test_paypal_method_required_when_present(self):
        with self.assertRaises(CheckoutGuardError):
            verify_zero_amount(make_init(0, methods=["card"]))

    def test_missing_payment_methods_fail_closed(self):
        init = make_init(0)
        init.pop("payment_method_types")
        with self.assertRaises(CheckoutGuardError):
            verify_zero_amount(init)

    def test_confirm_payload_uses_verified_zero_amount(self):
        init = make_init(0)
        init["total_summary"] = {"subtotal": 2000, "total": 0, "due": 0}
        payload = build_confirm_payload("pk_test", init, "https://pay.openai.com/c/pay/cs_test")
        self.assertEqual(payload["expected_amount"], "0")
        self.assertEqual(payload["last_displayed_line_item_group_details[subtotal]"], "2000")
        self.assertEqual(payload["last_displayed_line_item_group_details[total_discount_amount]"], "2000")

    def test_allow_non_zero_mode_builds_confirm_payload_from_current_amount(self):
        init = make_init(2200)
        init["total_summary"] = {"subtotal": 2000, "total": 2200, "due": 2200}

        gate = checkout_amount_guard(init, require_zero=False)
        payload = build_confirm_payload("pk_test", init, "https://pay.openai.com/c/pay/cs_test", require_zero=False)

        self.assertFalse(gate["zero_verified"])
        self.assertEqual(gate["amount_due"], 2200)
        self.assertEqual(payload["expected_amount"], "2200")

    def test_display_amounts_come_from_init_summary(self):
        init = make_init(0)
        init["total_summary"] = {"subtotal": 3000, "total": 0, "due": 0}
        self.assertEqual(display_amounts(init)["subtotal"], 3000)
        self.assertEqual(display_amounts(init)["total_discount_amount"], 3000)

    def test_confirm_paypal_blocks_before_network_on_non_zero(self):
        with patch("protocol_paypal_authorize.make_session") as make_session:
            with self.assertRaises(NonZeroAmountError):
                confirm_paypal("socks5h://proxy.invalid:1000", "pk_test", "cs_test", make_init(2000))
        make_session.assert_not_called()

    def test_confirm_authorize_allow_non_zero_posts_and_extracts_pm_redirect(self):
        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "next_action": {
                        "redirect_to_url": {
                            "url": "https://pm-redirects.stripe.com/authorize/acct_test/sa_nonce_test?useWebAuthSession=true"
                        }
                    }
                }

        class FakeSession:
            def __init__(self):
                self.posted = None

            def post(self, endpoint, data, headers, timeout):
                self.posted = data
                return FakeResponse()

            def close(self):
                pass

        fake = FakeSession()
        with patch("protocol_paypal_authorize.make_session", return_value=fake):
            result = confirm_paypal_authorize(
                "socks5h://proxy.invalid:1000",
                "pk_test",
                "cs_test",
                make_init(2200),
                require_zero=False,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(fake.posted["expected_amount"], "2200")
        self.assertIn("pm-redirects.stripe.com/authorize", result["pm_authorize_url"])


class WebAppTests(unittest.TestCase):
    def test_extract_access_token_from_session_json(self):
        token = extract_access_token('{"accessToken":"%s"}' % TOKEN)
        self.assertEqual(token, TOKEN)

    def test_collect_access_tokens_prefers_openai_access_token_from_session_blob(self):
        raw = json.dumps({"idToken": OTHER_TOKEN, "accessToken": OPENAI_TOKEN, "nested": {"token": OTHER_TOKEN}})
        self.assertEqual(collect_access_tokens(raw), [OPENAI_TOKEN])
        self.assertEqual(extract_access_token(raw), OPENAI_TOKEN)

    def test_extract_proxy_accepts_host_port_user_pass_format(self):
        proxy = extract_proxy("gate1.ipweb.cc:7778:B_53345_JP___5_uQGIrAl7:123456")
        self.assertEqual(proxy, "http://B_53345_JP___5_uQGIrAl7:123456@gate1.ipweb.cc:7778")

    def test_extract_proxy_candidates_auto_detects_protocols(self):
        proxies = extract_proxy_candidates("gate1.ipweb.cc:7778:B_53345_JP___5_uQGIrAl7:123456")
        self.assertEqual(proxies, [
            "http://B_53345_JP___5_uQGIrAl7:123456@gate1.ipweb.cc:7778",
        ])

    def test_extract_proxy_candidates_uses_socks5h_for_kookeey(self):
        proxies = extract_proxy_candidates("u:p@gate-jp.kookeey.info:1000")
        self.assertEqual(proxies, ["socks5h://u:p@gate-jp.kookeey.info:1000"])

    def test_extract_proxy_candidates_keeps_kookeey_sticky_session(self):
        proxies = extract_proxy_candidates("u:p-JP-12345678@gate-jp.kookeey.info:1000")
        self.assertEqual(proxies, ["socks5h://u:p-JP-12345678@gate-jp.kookeey.info:1000"])

    def test_proxy_provider_entries_apply_socks5h_scheme(self):
        class FakeResponse:
            status_code = 200
            text = "user:pass-JP@gate.kookeey.info:1000\n"

        with patch("curl_cffi.requests.get", return_value=FakeResponse()) as get:
            proxies = fetch_proxy_provider_entries("https://provider.test/pickdynamicips?p=socks5", 1)
        self.assertEqual(proxies, ["socks5h://user:pass-JP@gate.kookeey.info:1000"])
        self.assertEqual(get.call_count, 1)
        self.assertEqual(
            extract_proxy_candidates("socks5h://user:pass-JP@gate.kookeey.info:1000"),
            ["socks5h://user:pass-JP@gate.kookeey.info:1000"],
        )

    def test_extract_proxy_keeps_explicit_scheme(self):
        proxies = extract_proxy_candidates("socks5h://u:p@gate1.ipweb.cc:7778")
        self.assertEqual(proxies, ["socks5h://u:p@gate1.ipweb.cc:7778"])

    def test_sanitize_error_value_redacts_proxy_credentials(self):
        safe = sanitize_error_value({
            "checkout_error": "Unsupported proxy syntax in 'socks5h://gate1.ipweb.cc:7778:B_53345_JP___5_uQGIrAl7:123456'"
        })
        self.assertNotIn("B_53345", safe["checkout_error"])
        self.assertNotIn("123456", safe["checkout_error"])

    def test_default_matrix_prefers_europe_before_us_and_japan(self):
        self.assertEqual(
            parse_checkout_matrix({})[:5],
            [("FR", "EUR"), ("DE", "EUR"), ("IE", "EUR"), ("NL", "EUR"), ("BE", "EUR")],
        )

    def test_checkout_pairs_are_limited_per_proxy(self):
        pairs = checkout_pairs_for_proxy({"checkout_pair_limit": 3}, {"country": "JP", "city": "Tokyo"})
        self.assertEqual(pairs, [("FR", "EUR"), ("DE", "EUR"), ("IE", "EUR")])

    def test_proxy_badness_pushes_bad_lease_back(self):
        good = "http://u:p@good.invalid:1000"
        bad = "http://u:p@bad.invalid:1000"
        mark_proxy_good(good)
        mark_proxy_good(bad)
        mark_proxy_bad(bad, 2.0)
        self.assertEqual(sort_by_proxy_health([bad, good]), [good, bad])
        mark_proxy_good(bad)

    def test_proxy_geo_priority_does_not_bias_city_ranking(self):
        self.assertEqual(
            proxy_geo_priority({"country": "JP", "region": "Osaka", "city": "Sakai"}),
            proxy_geo_priority({"country": "JP", "region": "Tokyo", "city": "Tokyo"}),
        )

    def test_city_ranking_only_returns_successful_cities(self):
        import webapp.server as server

        with server.CITY_STATS_LOCK:
            server.CITY_STATS.clear()
        record_city_stat({"country": "JP", "region": "Tokyo", "city": "Tokyo"}, False, 1000)
        record_city_stat({"country": "JP", "region": "Osaka", "city": "Sakai"}, True, 2000)
        rows = server.city_stats_snapshot()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["city"], "JP / Osaka / Sakai")
        self.assertEqual(rows[0]["success_rate"], 100.0)

    def test_proxy_geo_billing_uses_jp_sasebo_postal_code(self):
        billing = billing_from_proxy_geo({"country": "JP", "city": "Sasebo", "region": "Nagasaki"}, fallback_country="FR")
        self.assertEqual(billing["country"], "JP")
        self.assertEqual(billing["city"], "Sasebo-shi")
        self.assertEqual(billing["postal_code"], "857-0863")
        validate_billing_identity(billing)

    def test_confirm_http_allows_non_zero_by_default(self):
        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "next_action": {
                        "redirect_to_url": {
                            "url": "https://pm-redirects.stripe.com/authorize/acct_test/sa_nonce_test"
                        }
                    }
                }

        class FakeSession:
            def post(self, endpoint, data, headers, timeout):
                return FakeResponse()

            def close(self):
                pass

        with patch("webapp.server.proxy_requests_session", return_value=FakeSession()) as session:
            result = confirm_paypal_authorize_http(
                "http://user:pass@proxy.invalid:1000",
                "pk_test",
                "cs_test",
                make_init(2000),
                country="DE",
            )
        self.assertTrue(result["ok"])
        session.assert_called_once()

    def test_confirm_http_requires_zero_when_strict_flag_enabled(self):
        with patch("webapp.server.REQUIRE_ZERO_AMOUNT", True):
            with patch("webapp.server.proxy_requests_session") as session:
                with self.assertRaises(NonZeroAmountError):
                    confirm_paypal_authorize_http(
                        "http://user:pass@proxy.invalid:1000",
                        "pk_test",
                        "cs_test",
                        make_init(2000),
                        country="DE",
                    )
        session.assert_not_called()

    def test_confirm_http_rejects_non_pm_redirect(self):
        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "next_action": {
                        "redirect_to_url": {
                            "url": "https://checkout.stripe.com/c/pay/cs_test"
                        }
                    }
                }

        class FakeSession:
            def post(self, endpoint, data, headers, timeout):
                return FakeResponse()

            def close(self):
                pass

        with patch("webapp.server.proxy_requests_session", return_value=FakeSession()):
            result = confirm_paypal_authorize_http(
                "http://user:pass@proxy.invalid:1000",
                "pk_test",
                "cs_test",
                make_init(0),
                country="DE",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["pm_authorize_url"], "")

    def test_web_flow_non_zero_can_still_confirm_by_default(self):
        with patch("webapp.server.create_checkout") as create_checkout:
            with patch("webapp.server.stripe_init_http") as stripe_init:
                with patch("webapp.server.confirm_custom_paypal_authorize_http") as confirm:
                    create_checkout.return_value = {
                        "ok": True,
                        "status": 200,
                        "checkout_session_id": "cs_test",
                        "publishable_key": "pk_test",
                        "checkout_ui_mode": "custom",
                        "requires_manual_approval": True,
                    }
                    stripe_init.return_value = make_init(1500)
                    confirm.return_value = {
                        "ok": True,
                        "status": 200,
                        "pm_authorize_url": "https://pm-redirects.stripe.com/authorize/acct_test/sa_nonce_test",
                        "billing_country": "JP",
                    }
                    result = run_extraction({"credential": TOKEN, "proxy": "user:pass@proxy.invalid:1000", "checkout_matrix": "DE:EUR"})
        self.assertFalse(result["zero_verified"])
        self.assertEqual(result["amount_due"], 1500)
        confirm.assert_called_once()

    def test_web_flow_returns_url_after_zero_verification(self):
        with patch("webapp.server.create_checkout") as create_checkout:
            with patch("webapp.server.stripe_init_http") as stripe_init:
                with patch("webapp.server.confirm_custom_paypal_authorize_http") as confirm:
                    create_checkout.return_value = {
                        "ok": True,
                        "status": 200,
                        "checkout_session_id": "cs_test",
                        "publishable_key": "pk_test",
                        "processor_entity": "stripe/openai",
                    }
                    stripe_init.return_value = make_init(0)
                    confirm.return_value = {
                        "ok": True,
                        "status": 200,
                        "pm_authorize_url": "https://pm-redirects.stripe.com/authorize/acct_test/sa_nonce_test",
                        "billing_country": "DE",
                    }
                    result = run_extraction({"credential": TOKEN, "proxy": "user:pass@proxy.invalid:1000", "checkout_matrix": "DE:EUR"})
        self.assertTrue(result["zero_verified"])
        self.assertEqual(result["amount_due"], 0)
        self.assertEqual(result["hosted_checkout_url"], "")
        self.assertIn("pm-redirects.stripe.com/authorize", result["paypal_authorize_url"])

    def test_web_flow_uses_proxy_geo_billing_for_europe_checkout(self):
        with patch("webapp.server.create_checkout") as create_checkout:
            with patch("webapp.server.stripe_init_http") as stripe_init:
                with patch("webapp.server.confirm_custom_paypal_authorize_http") as confirm:
                    with patch("webapp.server.probe_proxy_geo", return_value={"country": "JP", "city": "Sasebo", "region": "Nagasaki"}):
                        create_checkout.return_value = {
                            "ok": True,
                            "status": 200,
                            "checkout_session_id": "cs_test",
                            "publishable_key": "pk_test",
                            "processor_entity": "openai_ie",
                        }
                        stripe_init.return_value = make_init(0, currency="eur")
                        confirm.return_value = {
                            "ok": True,
                            "status": 200,
                            "pm_authorize_url": "https://pm-redirects.stripe.com/authorize/acct_test/sa_nonce_test",
                            "billing_country": "JP",
                        }
                        result = run_extraction({"credential": TOKEN, "proxy": "http://user:pass@proxy.invalid:1000", "checkout_matrix": "FR:EUR"})
        self.assertEqual(create_checkout.call_args.args[2], "FR")
        self.assertEqual(confirm.call_args.kwargs["country"], "JP")
        self.assertEqual(confirm.call_args.kwargs["proxy_geo"]["city"], "Sasebo")
        self.assertEqual(result["billing_country"], "JP")
        self.assertIn("pm-redirects.stripe.com/authorize", result["paypal_authorize_url"])

    def test_web_flow_skips_confirm_when_proxy_geo_missing_city(self):
        with patch("webapp.server.create_checkout") as create_checkout:
            with patch("webapp.server.confirm_custom_paypal_authorize_http") as confirm:
                with patch("webapp.server.probe_proxy_geo", return_value={"country": "JP"}):
                    with self.assertRaises(PublicApiError) as ctx:
                        run_extraction({"credential": TOKEN, "proxy": "http://user:pass@proxy.invalid:1000", "checkout_matrix": "FR:EUR"})
        self.assertEqual(ctx.exception.code, "proxy_geo_unavailable")
        create_checkout.assert_not_called()
        confirm.assert_not_called()

    def test_web_flow_rejects_proxy_when_preheated_ip_changes(self):
        with patch("webapp.server.get_proxy_lease", return_value={"ip": "1.1.1.1", "country": "JP", "region": "Tokyo", "city": "Tokyo"}):
            with patch("webapp.server.probe_proxy_geo", return_value={"ip": "2.2.2.2", "country": "JP", "region": "Osaka", "city": "Osaka"}):
                with patch("webapp.server.create_checkout") as create_checkout:
                    with self.assertRaises(PublicApiError) as ctx:
                        _run_extraction_with_proxy_inner(TOKEN, "http://user:pass@proxy.invalid:1000", {"checkout_matrix": "FR:EUR"})
        self.assertEqual(ctx.exception.code, "proxy_ip_changed")
        create_checkout.assert_not_called()

    def test_web_flow_skips_init_failures_without_browser_fallback(self):
        with patch("webapp.server.create_checkout") as create_checkout:
            with patch("webapp.server.stripe_init_http") as stripe_init:
                with patch("webapp.server.probe_proxy_geo", return_value={"country": "JP", "city": "Sasebo"}):
                    create_checkout.return_value = {
                        "ok": True,
                        "status": 200,
                        "checkout_session_id": "cs_test",
                        "publishable_key": "pk_test",
                    }
                    stripe_init.side_effect = PublicApiError("stripe_init_failed", "Stripe init 失败")
                    with self.assertRaises(Exception):
                        run_extraction({"credential": TOKEN, "proxy": "http://user:pass@proxy.invalid:1000", "checkout_matrix": "DE:EUR"})
        self.assertEqual(create_checkout.call_count, 1)
        self.assertEqual(stripe_init.call_count, 1)

    def test_web_flow_checkout_401_stops_as_token_unauthorized(self):
        with patch("webapp.server.create_checkout") as create_checkout:
            with patch("webapp.server.probe_proxy_geo", return_value={"country": "JP", "city": "Sasebo"}):
                create_checkout.return_value = {
                    "ok": False,
                    "status": 401,
                    "raw_response": {"detail": "unauthorized"},
                }
                with self.assertRaises(PublicApiError) as ctx:
                    run_extraction({"credential": TOKEN, "proxy": "http://user:pass@proxy.invalid:1000", "checkout_matrix": "DE:EUR"})
        self.assertEqual(ctx.exception.code, "token_unauthorized")

    def test_web_flow_checkout_transport_failure_is_proxy_unstable(self):
        with patch("webapp.server.create_checkout") as create_checkout:
            with patch("webapp.server.probe_proxy_geo", return_value={"country": "JP", "city": "Sasebo"}):
                create_checkout.return_value = {
                    "ok": False,
                    "status": 0,
                    "error": "Connection closed abruptly",
                    "error_type": "ConnectionError",
                }
                with self.assertRaises(PublicApiError) as ctx:
                    run_extraction({"credential": TOKEN, "proxy": "http://user:pass@proxy.invalid:1000", "checkout_matrix": "DE:EUR"})
        self.assertEqual(ctx.exception.code, "proxy_unstable")

    def test_custom_confirm_polls_stripe_after_approve_blocked(self):
        with patch("webapp.server.stripe_create_paypal_payment_method", return_value="pm_test"):
            with patch(
                "webapp.server.stripe_confirm_custom_paypal",
                return_value={"submission_attempt": {"state": "requires_approval"}},
            ):
                with patch(
                    "webapp.server.chatgpt_approve_checkout",
                    side_effect=PublicApiError(
                        "chatgpt_approve_rejected",
                        "blocked",
                        details={"raw_error": {"result": "pending"}},
                    ),
                ):
                    with patch(
                        "webapp.server.stripe_poll_redirect_url",
                        return_value="https://pm-redirects.stripe.com/authorize/acct_test/sa_nonce_test",
                    ) as poll:
                        result = confirm_custom_paypal_authorize_http(
                            "http://user:pass@proxy.invalid:1000",
                            TOKEN,
                            "pk_test",
                            "cs_test",
                            make_init(0),
                            country="JP",
                            processor_entity="openai_ie",
                            proxy_geo={"country": "JP", "city": "Tokyo"},
                        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["confirm_state"], "requires_approval")
        self.assertIn("approve_error", result)
        poll.assert_called_once()

    def test_custom_confirm_polls_even_after_terminal_approve_blocked(self):
        with patch("webapp.server.stripe_create_paypal_payment_method", return_value="pm_test"):
            with patch(
                "webapp.server.stripe_confirm_custom_paypal",
                return_value={"submission_attempt": {"state": "requires_approval"}},
            ):
                with patch(
                    "webapp.server.chatgpt_approve_checkout",
                    side_effect=PublicApiError(
                        "chatgpt_approve_rejected",
                        "blocked",
                        details={"raw_error": {"result": "blocked"}},
                    ),
                ):
                    with patch(
                        "webapp.server.stripe_poll_redirect_url",
                        return_value="https://pm-redirects.stripe.com/authorize/acct_test/sa_nonce_test",
                    ) as poll:
                        result = confirm_custom_paypal_authorize_http(
                            "http://user:pass@proxy.invalid:1000",
                            TOKEN,
                            "pk_test",
                            "cs_test",
                            make_init(0),
                            country="JP",
                            processor_entity="openai_ie",
                            proxy_geo={"country": "JP", "city": "Tokyo"},
                        )
        self.assertTrue(result["ok"])
        poll.assert_called_once()
        self.assertEqual(poll.call_args.kwargs["timeout_seconds"], 18.0)

    def test_custom_confirm_schedules_background_poll_when_blocked_poll_times_out(self):
        with patch("webapp.server.stripe_create_paypal_payment_method", return_value="pm_test"):
            with patch(
                "webapp.server.stripe_confirm_custom_paypal",
                return_value={"submission_attempt": {"state": "requires_approval"}},
            ):
                with patch(
                    "webapp.server.chatgpt_approve_checkout",
                    side_effect=PublicApiError(
                        "chatgpt_approve_rejected",
                        "blocked",
                        details={"raw_error": {"result": "blocked"}},
                    ),
                ):
                    with patch(
                        "webapp.server.stripe_poll_redirect_url",
                        side_effect=PublicApiError("stripe_redirect_poll_timeout", "timeout"),
                    ):
                        with patch("webapp.server.schedule_background_redirect_poll") as bg:
                            with self.assertRaises(PublicApiError) as ctx:
                                confirm_custom_paypal_authorize_http(
                                    "http://user:pass@proxy.invalid:1000",
                                    TOKEN,
                                    "pk_test",
                                    "cs_test",
                                    make_init(0),
                                    country="JP",
                                    processor_entity="openai_ie",
                                    proxy_geo={"country": "JP", "city": "Tokyo"},
                                )
        self.assertEqual(ctx.exception.code, "paypal_authorize_approve_blocked")
        self.assertEqual((ctx.exception.details or {}).get("background_poll_seconds"), 45)
        bg.assert_called_once()

    def test_background_redirect_poll_records_late_success(self):
        import webapp.server as server

        with patch("webapp.server.stripe_poll_redirect_url", return_value="https://pm-redirects.stripe.com/authorize/acct_test/sa_nonce_test"):
            with patch("webapp.server.record_city_stat") as record:
                with patch("webapp.server.increment_counter") as counter:
                    with patch("webapp.server.append_background_link") as append:
                        class ImmediateExecutor:
                            def submit(self, fn):
                                fn()

                        with patch.object(server, "BACKGROUND_EXECUTOR", ImmediateExecutor()):
                            schedule_background_redirect_poll(
                                proxy="http://user:pass@proxy.invalid:1000",
                                publishable_key="pk_test",
                                checkout_session_id="cs_test_bg",
                                token_hash="tokenhash",
                                proxy_geo={"country": "JP", "region": "Tokyo", "city": "Tokyo"},
                                amount_gate={"amount_due": 0, "currency": "eur"},
                                billing={"country": "JP", "city": "Tokyo"},
                                processor_entity="openai_ie",
                                reason="approve_blocked",
                            )
        record.assert_called_once()
        counter.assert_called_once()
        append.assert_called_once()

    def test_same_token_checkout_attempts_are_serialized(self):
        active = 0
        max_active = 0

        def fake_run(_token, _proxy, _payload):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            time.sleep(0.05)
            active -= 1
            raise PublicApiError("paypal_authorize_not_ready", "not ready")

        def invoke():
            with self.assertRaises(PublicApiError):
                run_extraction_race(TOKEN, ["http://u:p@proxy.invalid:1000"], {})

        with patch("webapp.server.preflight_proxy_candidates", side_effect=lambda items: items):
            with patch("webapp.server.CHECKOUT_RACE_ENABLED", False):
                with patch("webapp.server.run_extraction_with_proxy", side_effect=fake_run):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        list(pool.map(lambda _: invoke(), range(2)))

        self.assertEqual(max_active, 1)

    def test_proxy_race_runs_leases_concurrently_without_shared_context(self):
        active = 0
        max_active = 0

        def fake_run(_token, _proxy, _payload):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            time.sleep(0.05)
            active -= 1
            raise PublicApiError("paypal_authorize_not_ready", "not ready")

        with patch("webapp.server.CHECKOUT_RACE_ENABLED", True):
            with patch("webapp.server.run_extraction_with_proxy", side_effect=fake_run):
                with self.assertRaises(PublicApiError):
                    run_extraction_race(TOKEN, ["http://u:p@proxy1.invalid:1000", "http://u:p@proxy2.invalid:1000"], {})

        self.assertGreaterEqual(max_active, 2)

    def test_token_unauthorized_rechecks_next_serial_proxy(self):
        calls: list[str] = []

        def fake_run(_token, proxy, _payload):
            calls.append(proxy)
            details = {"errors": [{"details": {"attempts": [{"checkout_status": 401}]}}]}
            raise PublicApiError("paypal_authorize_not_ready", "not ready", details=details)

        with patch("webapp.server.preflight_proxy_candidates", side_effect=lambda items: items):
            with patch("webapp.server.CHECKOUT_RACE_ENABLED", False):
                with patch("webapp.server.TOKEN_UNAUTHORIZED_CONFIRMATIONS", 3):
                    with patch("webapp.server.run_extraction_with_proxy", side_effect=fake_run):
                        with self.assertRaises(PublicApiError) as ctx:
                            run_extraction_race(
                                TOKEN,
                                [
                                    "http://u:p@proxy1.invalid:1000",
                                    "http://u:p@proxy2.invalid:1000",
                                    "http://u:p@proxy3.invalid:1000",
                                ],
                                {},
                            )

        self.assertEqual(ctx.exception.code, "token_unauthorized")
        self.assertEqual(
            calls,
            [
                "http://u:p@proxy1.invalid:1000",
                "http://u:p@proxy2.invalid:1000",
                "http://u:p@proxy3.invalid:1000",
            ],
        )

    def test_token_unauthorized_exhausts_configured_serial_confirmations(self):
        calls: list[str] = []

        def fake_run(_token, proxy, _payload):
            calls.append(proxy)
            raise PublicApiError("token_unauthorized", "401", details={"attempts": [{"checkout_status": 401}]})

        proxies = [f"http://u:p@proxy{i}.invalid:1000" for i in range(10)]
        with patch("webapp.server.preflight_proxy_candidates", side_effect=lambda items: items):
            with patch("webapp.server.CHECKOUT_RACE_ENABLED", False):
                with patch("webapp.server.TOKEN_UNAUTHORIZED_CONFIRMATIONS", 2):
                    with patch("webapp.server.run_extraction_with_proxy", side_effect=fake_run):
                        with self.assertRaises(PublicApiError) as ctx:
                            run_extraction_race(TOKEN, proxies, {})

        self.assertEqual(ctx.exception.code, "token_unauthorized")
        self.assertEqual(calls, proxies[:2])

    def test_token_unauthorized_can_recover_on_next_proxy(self):
        calls: list[str] = []

        def fake_run(_token, proxy, _payload):
            calls.append(proxy)
            if len(calls) == 1:
                raise PublicApiError("token_unauthorized", "401", details={"attempts": [{"checkout_status": 401}]})
            return {"ok": True, "paypal_authorize_url": "https://pm-redirects.stripe.com/authorize/acct/test"}

        with patch("webapp.server.preflight_proxy_candidates", side_effect=lambda items: items):
            with patch("webapp.server.CHECKOUT_RACE_ENABLED", False):
                with patch("webapp.server.APPROVE_BLOCKED_CONFIRMATIONS", 2):
                    with patch("webapp.server.run_extraction_with_proxy", side_effect=fake_run):
                        result = run_extraction_race(TOKEN, ["http://u:p@proxy1.invalid:1000", "http://u:p@proxy2.invalid:1000"], {})

        self.assertTrue(result["ok"])
        self.assertEqual(calls, ["http://u:p@proxy1.invalid:1000", "http://u:p@proxy2.invalid:1000"])

    def test_transport_error_continues_serial_proxy_fallback(self):
        calls: list[str] = []

        def fake_run(_token, proxy, _payload):
            calls.append(proxy)
            if len(calls) == 1:
                raise TimeoutError("Connection timed out")
            return {"ok": True, "paypal_authorize_url": "https://pm-redirects.stripe.com/authorize/acct/test"}

        with patch("webapp.server.preflight_proxy_candidates", side_effect=lambda items: items):
            with patch("webapp.server.CHECKOUT_RACE_ENABLED", False):
                with patch("webapp.server.APPROVE_BLOCKED_CONFIRMATIONS", 2):
                    with patch("webapp.server.run_extraction_with_proxy", side_effect=fake_run):
                        result = run_extraction_race(TOKEN, ["http://u:p@proxy1.invalid:1000", "http://u:p@proxy2.invalid:1000"], {})

        self.assertEqual(calls, ["http://u:p@proxy1.invalid:1000", "http://u:p@proxy2.invalid:1000"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["checkout_mode"], "serial_per_token")

    def test_serial_extraction_can_skip_duplicate_preflight_after_batch_preheat(self):
        with patch("webapp.server.preflight_proxy_candidates") as preflight:
            with patch("webapp.server.CHECKOUT_RACE_ENABLED", False):
                with patch(
                    "webapp.server.run_extraction_with_proxy",
                    return_value={"ok": True, "paypal_authorize_url": "https://pm-redirects.stripe.com/authorize/acct/test"},
                ):
                    result = run_extraction_race(TOKEN, ["http://u:p@proxy1.invalid:1000"], {"_preflight_done": True})

        preflight.assert_not_called()
        self.assertTrue(result["ok"])

    def test_checkout_not_active_session_continues_serial_proxy_fallback(self):
        calls: list[str] = []

        def fake_run(_token, proxy, _payload):
            calls.append(proxy)
            if len(calls) == 1:
                raise PublicApiError(
                    "paypal_authorize_not_ready",
                    "not ready",
                    details={"attempts": [{"confirm_errors": [{"details": {"raw_error": {"error": {"code": "checkout_not_active_session"}}}}]}]},
                )
            return {"ok": True, "paypal_authorize_url": "https://pm-redirects.stripe.com/authorize/acct/test"}

        with patch("webapp.server.preflight_proxy_candidates", side_effect=lambda items: items):
            with patch("webapp.server.CHECKOUT_RACE_ENABLED", False):
                with patch("webapp.server.APPROVE_BLOCKED_CONFIRMATIONS", 2):
                    with patch("webapp.server.run_extraction_with_proxy", side_effect=fake_run):
                        result = run_extraction_race(TOKEN, ["http://u:p@proxy1.invalid:1000", "http://u:p@proxy2.invalid:1000"], {})

        self.assertEqual(calls, ["http://u:p@proxy1.invalid:1000", "http://u:p@proxy2.invalid:1000"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["checkout_mode"], "serial_per_token")

    def test_approve_blocked_continues_serial_proxy_fallback(self):
        calls: list[str] = []

        def fake_run(_token, proxy, _payload):
            calls.append(proxy)
            if len(calls) == 1:
                raise PublicApiError(
                    "paypal_authorize_approve_blocked",
                    "blocked",
                    details={"approve_error": {"code": "chatgpt_approve_rejected", "details": {"raw_error": {"result": "blocked"}}}},
                )
            return {"ok": True, "paypal_authorize_url": "https://pm-redirects.stripe.com/authorize/acct/test"}

        with patch("webapp.server.preflight_proxy_candidates", side_effect=lambda items: items):
            with patch("webapp.server.CHECKOUT_RACE_ENABLED", False):
                with patch("webapp.server.APPROVE_BLOCKED_CONFIRMATIONS", 2):
                    with patch("webapp.server.run_extraction_with_proxy", side_effect=fake_run):
                        result = run_extraction_race(TOKEN, ["http://u:p@proxy1.invalid:1000", "http://u:p@proxy2.invalid:1000"], {})

        self.assertEqual(calls, ["http://u:p@proxy1.invalid:1000", "http://u:p@proxy2.invalid:1000"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["checkout_mode"], "serial_per_token")

    def test_approve_blocked_stops_after_configured_confirmations(self):
        calls: list[str] = []

        def fake_run(_token, proxy, _payload):
            calls.append(proxy)
            raise PublicApiError(
                "paypal_authorize_approve_blocked",
                "blocked",
                details={"approve_error": {"code": "chatgpt_approve_rejected", "details": {"raw_error": {"result": "blocked"}}}},
            )

        proxies = [f"http://u:p@proxy{i}.invalid:1000" for i in range(8)]
        with patch("webapp.server.preflight_proxy_candidates", side_effect=lambda items: items):
            with patch("webapp.server.CHECKOUT_RACE_ENABLED", False):
                with patch("webapp.server.APPROVE_BLOCKED_CONFIRMATIONS", 1):
                    with patch("webapp.server.run_extraction_with_proxy", side_effect=fake_run):
                        with self.assertRaises(PublicApiError) as ctx:
                            run_extraction_race(TOKEN, proxies, {})

        self.assertEqual(ctx.exception.code, "approve_blocked")
        self.assertEqual(calls, proxies[:1])
        self.assertEqual((ctx.exception.details or {}).get("attempt_count"), 1)
        self.assertEqual((ctx.exception.details or {}).get("candidate_count"), len(proxies))

    def test_proxy_unstable_stops_after_configured_confirmations(self):
        calls: list[str] = []

        def fake_run(_token, proxy, _payload):
            calls.append(proxy)
            raise TimeoutError("Operation timed out")

        proxies = [f"http://u:p@proxy{i}.invalid:1000" for i in range(10)]
        with patch("webapp.server.preflight_proxy_candidates", side_effect=lambda items: items):
            with patch("webapp.server.CHECKOUT_RACE_ENABLED", False):
                with patch("webapp.server.PROXY_UNSTABLE_CONFIRMATIONS", 3):
                    with patch("webapp.server.run_extraction_with_proxy", side_effect=fake_run):
                        with self.assertRaises(PublicApiError) as ctx:
                            run_extraction_race(TOKEN, proxies, {})

        self.assertEqual(ctx.exception.code, "proxy_unstable")
        self.assertEqual(calls, proxies[:3])
        self.assertEqual((ctx.exception.details or {}).get("attempt_count"), 3)
        self.assertEqual((ctx.exception.details or {}).get("candidate_count"), len(proxies))

    def test_batch_timeout_does_not_count_queued_tokens(self):
        payload = {
            "tokens": [TOKEN + str(i) for i in range(3)],
            "proxy": "http://u:p@proxy.invalid:1000",
        }
        rows = []

        def fake_run(_token, _candidates, _payload):
            time.sleep(0.02)
            return {"ok": True, "paypal_authorize_url": "https://pm-redirects.stripe.com/authorize/acct/test"}

        handler = object.__new__(PlusLinkHandler)
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = {}
        handler.read_body_json = lambda: payload

        def capture(stream_rows):
            rows.extend(stream_rows)

        handler.send_ndjson_stream = capture

        with patch("webapp.server.allow_request", return_value=True):
            with patch("webapp.server.extract_proxy_candidates", return_value=["http://u:p@proxy.invalid:1000"]):
                with patch("webapp.server.preflight_proxy_candidates", side_effect=lambda items, refresh=False: items):
                    with patch("webapp.server.get_cached_proxy_geo", return_value={"country": "JP", "city": "Tokyo"}):
                        with patch("webapp.server.BATCH_WORKERS", 1):
                            with patch("webapp.server.BATCH_ACCOUNT_TIMEOUT_SECONDS", 0.03):
                                with patch("webapp.server.run_extraction_race", side_effect=fake_run):
                                    PlusLinkHandler.stream_extract_batch(handler)

        result_rows = [row for row in rows if "index" in row]
        self.assertEqual(len(result_rows), 3)
        self.assertTrue(all(row["ok"] for row in result_rows))

    def test_batch_stream_does_not_emit_outer_timeout_for_running_account(self):
        payload = {
            "tokens": [TOKEN + str(i) for i in range(2)],
            "proxy": "http://u:p@proxy.invalid:1000",
        }
        rows = []

        def fake_run(_token, _candidates, _payload):
            time.sleep(0.04)
            return {"ok": True, "paypal_authorize_url": "https://pm-redirects.stripe.com/authorize/acct/test"}

        handler = object.__new__(PlusLinkHandler)
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = {}
        handler.read_body_json = lambda: payload
        handler.send_ndjson_stream = lambda stream_rows: rows.extend(stream_rows)

        with patch("webapp.server.allow_request", return_value=True):
            with patch("webapp.server.extract_proxy_candidates", return_value=["http://u:p@proxy.invalid:1000"]):
                with patch("webapp.server.preflight_proxy_candidates", side_effect=lambda items, refresh=False: items):
                    with patch("webapp.server.get_cached_proxy_geo", return_value={"country": "JP", "city": "Tokyo"}):
                        with patch("webapp.server.BATCH_WORKERS", 2):
                            with patch("webapp.server.BATCH_ACCOUNT_TIMEOUT_SECONDS", 0.01):
                                with patch("webapp.server.run_extraction_race", side_effect=fake_run):
                                    PlusLinkHandler.stream_extract_batch(handler)

        result_rows = [row for row in rows if "index" in row]
        self.assertEqual(len(result_rows), 2)
        self.assertTrue(all(row["ok"] for row in result_rows))
        self.assertFalse(any((row.get("result") or {}).get("code") == "account_timeout_isolated" for row in result_rows))

    def test_batch_preheated_proxy_geo_skips_duplicate_live_geo_probe(self):
        with patch("webapp.server.get_proxy_lease", return_value={"ip": "1.1.1.1", "country": "JP", "region": "Tokyo", "city": "Tokyo"}):
            with patch("webapp.server.probe_proxy_geo") as geo:
                with patch(
                    "webapp.server.create_checkout",
                    side_effect=PublicApiError("proxy_unstable", "network"),
                ):
                    with self.assertRaises(PublicApiError):
                        _run_extraction_with_proxy_inner(
                            TOKEN,
                            "http://user:pass@proxy.invalid:1000",
                            {"checkout_matrix": "FR:EUR", "_preflight_done": True},
                        )
        geo.assert_not_called()

    def test_batch_recovery_retries_recoverable_failures_only(self):
        payload = {
            "tokens": [TOKEN + "1", TOKEN + "2"],
            "proxy": "http://u:p@proxy.invalid:1000",
        }
        rows = []
        calls: dict[str, int] = {}

        def fake_run(token, _candidates, _payload):
            calls[token] = calls.get(token, 0) + 1
            if token.endswith("1"):
                return {"ok": True, "paypal_authorize_url": "https://pm-redirects.stripe.com/authorize/acct/test1"}
            if calls[token] == 1:
                raise PublicApiError("proxy_unstable", "network")
            return {"ok": True, "paypal_authorize_url": "https://pm-redirects.stripe.com/authorize/acct/test2"}

        handler = object.__new__(PlusLinkHandler)
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = {}
        handler.read_body_json = lambda: payload
        handler.send_ndjson_stream = lambda stream_rows: rows.extend(stream_rows)

        with patch("webapp.server.allow_request", return_value=True):
            with patch("webapp.server.extract_proxy_candidates", return_value=["http://u:p@proxy1.invalid:1000", "http://u:p@proxy2.invalid:1000"]):
                with patch("webapp.server.preflight_proxy_candidates", side_effect=lambda items, refresh=False: items):
                    with patch("webapp.server.get_cached_proxy_geo", return_value={"country": "JP", "city": "Kawagoe"}):
                        with patch("webapp.server.BATCH_WORKERS", 2):
                            with patch("webapp.server.BATCH_RECOVERY_ROUNDS", 1):
                                with patch("webapp.server.run_extraction_race", side_effect=fake_run):
                                    PlusLinkHandler.stream_extract_batch(handler)

        result_rows = [row for row in rows if "index" in row]
        self.assertEqual([row["index"] for row in result_rows], [1, 2])
        self.assertTrue(all(row["ok"] for row in result_rows))
        self.assertEqual(calls[payload["tokens"][0]], 1)
        self.assertEqual(calls[payload["tokens"][1]], 2)

    def test_batch_recovery_does_not_emit_stale_first_round_failure(self):
        payload = {
            "tokens": [TOKEN + "1"],
            "proxy": "http://u:p@proxy.invalid:1000",
        }
        rows = []

        def fake_run(_token, _candidates, _payload):
            raise PublicApiError("token_unauthorized", "401", details={"attempts": [{"checkout_status": 401}]})

        handler = object.__new__(PlusLinkHandler)
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = {}
        handler.read_body_json = lambda: payload
        handler.send_ndjson_stream = lambda stream_rows: rows.extend(stream_rows)

        with patch("webapp.server.allow_request", return_value=True):
            with patch("webapp.server.extract_proxy_candidates", return_value=["http://u:p@proxy1.invalid:1000", "http://u:p@proxy2.invalid:1000"]):
                with patch("webapp.server.preflight_proxy_candidates", side_effect=lambda items, refresh=False: items):
                    with patch("webapp.server.get_cached_proxy_geo", return_value={"country": "JP", "city": "Kawagoe"}):
                        with patch("webapp.server.BATCH_WORKERS", 1):
                            with patch("webapp.server.BATCH_RECOVERY_ROUNDS", 1):
                                with patch("webapp.server.run_extraction_race", side_effect=fake_run):
                                    PlusLinkHandler.stream_extract_batch(handler)

        result_rows = [row for row in rows if "index" in row]
        self.assertEqual(len(result_rows), 1)
        self.assertEqual(result_rows[0]["round"], 1)
        self.assertFalse(result_rows[0]["ok"])

    def test_checkout_race_env_cannot_enable_single_account_race(self):
        import webapp.server as server

        self.assertFalse(server.CHECKOUT_RACE_ENABLED)


if __name__ == "__main__":
    unittest.main()
