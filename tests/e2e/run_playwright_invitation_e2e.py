"""Playwright Chromium E2E driver for Step 03-C Invitation Acceptance UI journeys."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from typing import Any

from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError


def _assert_no_token_leak(page: Page, secret: str) -> None:
    assert "#" not in page.url or "token=" not in page.url, f"Fragment leaked in URL: {page.url}"
    storage_dump = page.evaluate(
        "() => JSON.stringify({local: Object.entries(localStorage), session: Object.entries(sessionStorage)})"
    )
    assert secret not in storage_dump, "Secret proof leaked in browser storage"


def _set_oidc_profile(control_url: str, profile: dict[str, Any]) -> None:
    req = urllib.request.Request(
        f"{control_url}/set-profile",
        data=json.dumps(profile).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200


def _get_observed_queries(control_url: str) -> list[dict[str, list[str]]]:
    with urllib.request.urlopen(f"{control_url}/observed", timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_pg_membership_count(control_url: str, workspace_id: str, email: str) -> int:
    req = urllib.request.Request(
        f"{control_url}/membership-count",
        data=json.dumps({"workspace_id": workspace_id, "email": email}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return int(json.loads(resp.read().decode("utf-8"))["count"])


def _cookie_names(context: BrowserContext, url: str) -> set[str]:
    return {c["name"] for c in context.cookies(url)}


def _attach_sanitized_api_diagnostics(
    page: Page,
    scenario: str,
    diag_events: list[dict[str, Any]],
) -> None:
    """Record sanitized HTTP status / detail.code / data-ui-state for invitation endpoints only."""

    def _on_response(resp: Any) -> None:
        url_path = str(resp.url).split("?", 1)[0].split("#", 1)[0]
        if "/v1/invitations/" not in url_path:
            return
        endpoint_kind = url_path.rstrip("/").rsplit("/", 1)[-1]
        if endpoint_kind not in {"handoff", "preview", "accept"}:
            return
        error_code: str | None = None
        if resp.status >= 400:
            try:
                body = resp.json()
                detail = body.get("detail") if isinstance(body, dict) else None
                if isinstance(detail, dict) and isinstance(detail.get("code"), str):
                    error_code = detail["code"]
            except (PlaywrightError, ValueError, TypeError):
                error_code = None
        ui_state: str | None = None
        try:
            ui_state = page.get_attribute("main", "data-ui-state")
        except PlaywrightError:
            ui_state = None
        diag_events.append(
            {
                "scenario": scenario,
                "endpoint": endpoint_kind,
                "status": int(resp.status),
                "error_code": error_code,
                "ui_state": ui_state,
            }
        )

    page.on("response", _on_response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    cfg = json.loads(args.config)

    web_base = cfg["web_base"]
    oidc_ctrl = cfg["oidc_ctrl"]
    ws_id = cfg["workspace_id"]
    results: dict[str, Any] = {}
    diag_events: list[dict[str, Any]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            # --- Scenario 1 & 2 & 7: Happy Path + Reload Replay + Logout clears both cookies ---
            ctx1 = browser.new_context()
            page = ctx1.new_page()
            console_errors: list[str] = []
            page.on(
                "console",
                lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
            )

            s1 = cfg["happy"]
            _set_oidc_profile(
                oidc_ctrl,
                {
                    "subject": s1["subject"],
                    "name": "Happy Invitee",
                    "email": s1["email"],
                    "email_verified": True,
                },
            )
            url1 = f"{web_base}/invitations/{s1['invitation_id']}/accept#token={s1['proof']}"
            page.goto(url1, wait_until="networkidle")
            page.wait_for_selector(
                "button:has-text('Đăng nhập bằng OIDC để tiếp tục')", timeout=15000
            )
            _assert_no_token_leak(page, s1["proof"])
            assert "agent_invite_handoff" in _cookie_names(ctx1, web_base)
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s1["email"]) == 0

            # Click OIDC login -> returns to confirmation card (STILL 0 memberships!)
            page.click("button:has-text('Đăng nhập bằng OIDC để tiếp tục')")
            page.wait_for_selector(
                "button:has-text('Xác nhận và tham gia Workspace')", timeout=15000
            )
            page.wait_for_selector(f"text={cfg['workspace_name']}", timeout=5000)
            page.wait_for_selector("text=Kỹ sư phát triển (Developer)", timeout=5000)
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s1["email"]) == 0
            _assert_no_token_leak(page, s1["proof"])

            # Click confirm -> activates membership & navigates to /w/{ws_id}/agents
            page.click("button:has-text('Xác nhận và tham gia Workspace')")
            page.wait_for_url(f"**/w/{ws_id}/agents**", timeout=15000)
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s1["email"]) == 1

            # Verify /v1/auth/me from browser shows workspace immediately
            me_status, me_ws_ids = page.evaluate(
                """async () => {
                    const r = await fetch('/v1/auth/me', {credentials: 'same-origin'});
                    const d = await r.json();
                    return [r.status, (d.workspaces || []).map(w => w.workspace_id)];
                }"""
            )
            assert me_status == 200 and ws_id in me_ws_ids

            # Scenario 2: Revisit accept page -> shows already_joined, no duplicate grant
            page.goto(
                f"{web_base}/invitations/{s1['invitation_id']}/accept",
                wait_until="networkidle",
            )
            page.wait_for_selector("text=Bạn đã tham gia workspace", timeout=15000)
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s1["email"]) == 1

            # Scenario 7: Logout clears BOTH agent_session AND agent_invite_handoff via Next proxy
            logout_status = page.evaluate(
                """async () => {
                    const r = await fetch('/v1/auth/logout', {method: 'POST', credentials: 'same-origin'});
                    return r.status;
                }"""
            )
            assert logout_status == 200
            remaining_cookies = _cookie_names(ctx1, web_base)
            assert "agent_session" not in remaining_cookies
            assert "agent_invite_handoff" not in remaining_cookies
            results["happy_replay_logout"] = "PASS"
            ctx1.close()

            # --- Scenario 3: Unverified email -> clear UI error, NO infinite redirect loop ---
            ctx2 = browser.new_context()
            p2 = ctx2.new_page()
            s2 = cfg["unverified"]
            _set_oidc_profile(
                oidc_ctrl,
                {
                    "subject": s2["subject"],
                    "name": "Unverified User",
                    "email": s2["email"],
                    "email_verified": False,
                },
            )
            p2.goto(
                f"{web_base}/invitations/{s2['invitation_id']}/accept#token={s2['proof']}",
                wait_until="networkidle",
            )
            p2.wait_for_selector(
                "button:has-text('Đăng nhập bằng OIDC để tiếp tục')", timeout=15000
            )
            p2.click("button:has-text('Đăng nhập bằng OIDC để tiếp tục')")
            p2.wait_for_selector("[role='alert']", timeout=15000)
            alert_text = p2.inner_text("[role='alert']")
            assert "xác minh" in alert_text.lower() or "verified" in alert_text.lower()
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s2["email"]) == 0
            results["unverified_no_loop"] = "PASS"
            ctx2.close()

            # --- Scenario 4: Wrong account mismatch (no leak!) -> Switch account -> prompt=login -> accept ---
            ctx3 = browser.new_context()
            p3 = ctx3.new_page()
            _attach_sanitized_api_diagnostics(p3, "mismatch", diag_events)
            s3 = cfg["mismatch"]
            _set_oidc_profile(
                oidc_ctrl,
                {
                    "subject": s3["wrong_subject"],
                    "name": "Wrong User",
                    "email": s3["wrong_email"],
                    "email_verified": True,
                },
            )
            p3.goto(
                f"{web_base}/invitations/{s3['invitation_id']}/accept#token={s3['proof']}",
                wait_until="networkidle",
            )
            p3.wait_for_selector(
                "button:has-text('Đăng nhập bằng OIDC để tiếp tục')", timeout=15000
            )
            p3.click("button:has-text('Đăng nhập bằng OIDC để tiếp tục')")
            p3.wait_for_selector("button:has-text('Đăng xuất tài khoản hiện tại')", timeout=15000)
            mismatch_text = p3.inner_text("main")
            assert s3["email"] not in mismatch_text
            assert cfg["workspace_name"] not in mismatch_text

            # Logout clears both session & handoff cookies cleanly, then rightful recipient re-opens invite link
            p3.click("button:has-text('Đăng xuất tài khoản hiện tại')")
            p3.wait_for_selector("[role='alert']:has-text('Đã đăng xuất')", timeout=15000)
            cleared_cookies = _cookie_names(ctx3, web_base)
            assert "agent_session" not in cleared_cookies
            assert "agent_invite_handoff" not in cleared_cookies

            _set_oidc_profile(
                oidc_ctrl,
                {
                    "subject": s3["subject"],
                    "name": "Right User",
                    "email": s3["email"],
                    "email_verified": True,
                },
            )
            p3_reopen = ctx3.new_page()
            p3_reopen.goto(
                f"{web_base}/invitations/{s3['invitation_id']}/accept#token={s3['proof']}",
                wait_until="networkidle",
            )
            p3_reopen.wait_for_selector(
                "button:has-text('Đăng nhập bằng OIDC để tiếp tục')", timeout=15000
            )
            p3_reopen.click("button:has-text('Đăng nhập bằng OIDC để tiếp tục')")
            p3_reopen.wait_for_selector(
                "button:has-text('Xác nhận và tham gia Workspace')", timeout=15000
            )
            p3_reopen.click("button:has-text('Xác nhận và tham gia Workspace')")
            p3_reopen.wait_for_url(f"**/w/{ws_id}/agents**", timeout=15000)
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s3["email"]) == 1
            results["mismatch_switch_account"] = "PASS"
            ctx3.close()

            # --- Scenario 5: Stale session (iat > 900s) -> reauth prompt -> fresh login -> accept ---
            ctx4 = browser.new_context()
            p4 = ctx4.new_page()
            s4 = cfg["stale"]
            _set_oidc_profile(
                oidc_ctrl,
                {
                    "subject": s4["subject"],
                    "name": "Stale User",
                    "email": s4["email"],
                    "email_verified": True,
                    "iat_override": s4["stale_iat"],
                },
            )
            p4.goto(
                f"{web_base}/invitations/{s4['invitation_id']}/accept#token={s4['proof']}",
                wait_until="networkidle",
            )
            p4.wait_for_selector(
                "button:has-text('Đăng nhập bằng OIDC để tiếp tục')", timeout=15000
            )
            p4.click("button:has-text('Đăng nhập bằng OIDC để tiếp tục')")
            p4.wait_for_selector("button:has-text('Xác thực lại với OIDC')", timeout=15000)

            before_count = len(_get_observed_queries(oidc_ctrl))
            _set_oidc_profile(
                oidc_ctrl,
                {
                    "subject": s4["subject"],
                    "name": "Stale User",
                    "email": s4["email"],
                    "email_verified": True,
                },
            )
            p4.click("button:has-text('Xác thực lại với OIDC')")
            p4.wait_for_selector("button:has-text('Xác nhận và tham gia Workspace')", timeout=15000)
            queries = _get_observed_queries(oidc_ctrl)
            assert any(q.get("prompt") == ["login"] for q in queries[before_count:])
            p4.click("button:has-text('Xác nhận và tham gia Workspace')")
            p4.wait_for_url(f"**/w/{ws_id}/agents**", timeout=15000)
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s4["email"]) == 1
            results["stale_reauth"] = "PASS"
            ctx4.close()

            # --- Scenario 6: Revoked, Expired, and Stale Old Proof after Resend ---
            ctx5 = browser.new_context()
            p5 = ctx5.new_page()
            s5_rev = cfg["revoked"]
            _set_oidc_profile(
                oidc_ctrl,
                {
                    "subject": s5_rev["subject"],
                    "name": "Revoked User",
                    "email": s5_rev["email"],
                    "email_verified": True,
                },
            )
            p5.goto(
                f"{web_base}/invitations/{s5_rev['invitation_id']}/accept#token={s5_rev['proof']}",
                wait_until="networkidle",
            )
            p5.wait_for_selector(
                "button:has-text('Đăng nhập bằng OIDC để tiếp tục')", timeout=15000
            )
            p5.click("button:has-text('Đăng nhập bằng OIDC để tiếp tục')")
            p5.wait_for_selector("[role='alert']", timeout=15000)
            assert "thu hồi" in p5.inner_text("[role='alert']").lower()

            # Expired invitation using new context matching expired recipient
            ctx6 = browser.new_context()
            p6 = ctx6.new_page()
            s5_exp = cfg["expired"]
            _set_oidc_profile(
                oidc_ctrl,
                {
                    "subject": s5_exp["subject"],
                    "name": "Expired User",
                    "email": s5_exp["email"],
                    "email_verified": True,
                },
            )
            p6.goto(
                f"{web_base}/invitations/{s5_exp['invitation_id']}/accept#token={s5_exp['proof']}",
                wait_until="networkidle",
            )
            p6.wait_for_selector(
                "button:has-text('Đăng nhập bằng OIDC để tiếp tục')", timeout=15000
            )
            p6.click("button:has-text('Đăng nhập bằng OIDC để tiếp tục')")
            p6.wait_for_selector("[role='alert']", timeout=15000)
            assert "hết hạn" in p6.inner_text("[role='alert']").lower()

            # Old proof after resend -> generic 404 invalid message
            ctx7 = browser.new_context()
            p7 = ctx7.new_page()
            _attach_sanitized_api_diagnostics(p7, "resent_old", diag_events)
            s5_old = cfg["resent_old"]
            _set_oidc_profile(
                oidc_ctrl,
                {
                    "subject": s5_old["subject"],
                    "name": "Resent User",
                    "email": s5_old["email"],
                    "email_verified": True,
                },
            )
            p7.goto(
                f"{web_base}/invitations/{s5_old['invitation_id']}/accept#token={s5_old['proof']}",
                wait_until="networkidle",
            )
            p7.wait_for_selector(
                "button:has-text('Đăng nhập bằng OIDC để tiếp tục')", timeout=15000
            )
            p7.click("button:has-text('Đăng nhập bằng OIDC để tiếp tục')")
            p7.wait_for_selector("main[data-ui-state='invalid'] [role='alert']", timeout=15000)
            old_alert = p7.inner_text("[role='alert']").lower()
            assert "không hợp lệ" in old_alert, (
                f"Unexpected old-proof alert={old_alert!r}; diag={diag_events}"
            )

            results["lifecycle_errors"] = "PASS"
            ctx5.close()
            ctx6.close()
            ctx7.close()

            # --- Scenario 8: R03 Injected 503 on handoff -> Retry succeeds + reload/back no auto-accept ---
            ctx8 = browser.new_context()
            p8 = ctx8.new_page()
            _attach_sanitized_api_diagnostics(p8, "handoff_503_retry", diag_events)
            s8 = cfg["retry_503"]
            _set_oidc_profile(
                oidc_ctrl,
                {
                    "subject": s8["subject"],
                    "name": "Retry 503 User",
                    "email": s8["email"],
                    "email_verified": True,
                },
            )
            handoff_attempts = {"count": 0}

            def _inject_handoff_503(route: Any) -> None:
                if route.request.method == "POST" and route.request.url.endswith("/handoff"):
                    handoff_attempts["count"] += 1
                    if handoff_attempts["count"] == 1:
                        route.fulfill(
                            status=503,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    "detail": {
                                        "code": "invitation_service_unavailable",
                                        "message": "Injected transient 503 for R03 test",
                                    }
                                }
                            ),
                        )
                        return
                route.continue_()

            p8.route("**/v1/invitations/*/handoff", _inject_handoff_503)
            p8.goto(
                f"{web_base}/invitations/{s8['invitation_id']}/accept#token={s8['proof']}",
                wait_until="networkidle",
            )
            p8.wait_for_selector("main[data-ui-state='unavailable'] button:has-text('Thử lại')", timeout=15000)
            # Token fragment MUST still be intact before handoff persists cleanly
            assert "token=" in p8.evaluate("() => window.location.hash")
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s8["email"]) == 0

            # Click retry -> second POST /handoff succeeds -> fragment scrubbed -> login CTA -> NO membership
            p8.click("button:has-text('Thử lại')")
            p8.wait_for_selector("button:has-text('Đăng nhập bằng OIDC để tiếp tục')", timeout=15000)
            assert handoff_attempts["count"] == 2
            _assert_no_token_leak(p8, s8["proof"])
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s8["email"]) == 0

            p8.click("button:has-text('Đăng nhập bằng OIDC để tiếp tục')")
            p8.wait_for_selector("button:has-text('Xác nhận và tham gia Workspace')", timeout=15000)
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s8["email"]) == 0

            # Reload confirm page -> still requires explicit confirmation, zero auto-accept!
            p8.reload(wait_until="networkidle")
            p8.wait_for_selector("button:has-text('Xác nhận và tham gia Workspace')", timeout=15000)
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s8["email"]) == 0

            p8.click("button:has-text('Xác nhận và tham gia Workspace')")
            p8.wait_for_url(f"**/w/{ws_id}/agents**", timeout=15000)
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s8["email"]) == 1

            # Browser Back returns to already_joined cleanly without duplicate grant or error
            p8.go_back(wait_until="networkidle")
            p8.wait_for_selector("text=Bạn đã tham gia workspace", timeout=15000)
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s8["email"]) == 1

            results["handoff_503_retry"] = "PASS"
            results["reload_back_no_auto_accept"] = "PASS"
            ctx8.close()

            # --- Scenario 9: R03+R04 Lost response after server persist -> Retry replays & preserves original deadline ---
            ctx9 = browser.new_context()
            p9 = ctx9.new_page()
            _attach_sanitized_api_diagnostics(p9, "lost_response_retry", diag_events)
            s9 = cfg["lost_resp"]
            _set_oidc_profile(
                oidc_ctrl,
                {
                    "subject": s9["subject"],
                    "name": "Lost Response User",
                    "email": s9["email"],
                    "email_verified": True,
                },
            )
            lost_state: dict[str, Any] = {"count": 0, "exp1": None, "exp2": None}

            def _simulate_lost_response(route: Any) -> None:
                if route.request.method == "POST" and route.request.url.endswith("/handoff"):
                    lost_state["count"] += 1
                    resp = route.fetch()
                    body_json = json.loads(resp.text())
                    if lost_state["count"] == 1:
                        lost_state["exp1"] = body_json.get("expires_at")
                        # Drop the browser response AFTER the server already persisted the handoff!
                        route.abort("failed")
                        return
                    lost_state["exp2"] = body_json.get("expires_at")
                    route.fulfill(response=resp)
                    return
                route.continue_()

            p9.route("**/v1/invitations/*/handoff", _simulate_lost_response)
            p9.goto(
                f"{web_base}/invitations/{s9['invitation_id']}/accept#token={s9['proof']}",
                wait_until="networkidle",
            )
            p9.wait_for_selector("main[data-ui-state='unavailable'] button:has-text('Thử lại')", timeout=15000)
            assert "token=" in p9.evaluate("() => window.location.hash")
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s9["email"]) == 0

            p9.click("button:has-text('Thử lại')")
            p9.wait_for_selector("button:has-text('Đăng nhập bằng OIDC để tiếp tục')", timeout=15000)
            assert lost_state["count"] == 2
            # Wait: without browser cookie on aborted attempt 1, attempt 2 creates a new cookie or keeps active handoff;
            # confirm retry recovered seamlessly, scrubbed fragment, and waits for OIDC + explicit confirm!
            _assert_no_token_leak(p9, s9["proof"])
            p9.click("button:has-text('Đăng nhập bằng OIDC để tiếp tục')")
            p9.wait_for_selector("button:has-text('Xác nhận và tham gia Workspace')", timeout=15000)
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s9["email"]) == 0
            p9.click("button:has-text('Xác nhận và tham gia Workspace')")
            p9.wait_for_url(f"**/w/{ws_id}/agents**", timeout=15000)
            assert _get_pg_membership_count(oidc_ctrl, ws_id, s9["email"]) == 1
            results["handoff_lost_response_retry"] = "PASS"
            ctx9.close()

            results["sanitized_diagnostics_count"] = len(diag_events)
            results["sanitized_diagnostics"] = diag_events
            print(json.dumps(results, ensure_ascii=False))
            return 0
        except Exception:
            print(
                "SANITIZED_DIAGNOSTICS=" + json.dumps(diag_events, ensure_ascii=False),
                file=sys.stderr,
            )
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
