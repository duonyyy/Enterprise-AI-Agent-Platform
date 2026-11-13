"""Playwright Chromium E2E driver for Step 03-D Workspace RBAC, Member Management, Switching & Revocation."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from typing import Any

from playwright.sync_api import BrowserContext, Page, sync_playwright


def _ctrl_post(ctrl_url: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{ctrl_url}{path}",
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _add_session_cookie(context: BrowserContext, web_base: str, cookie_name: str, cookie_val: str) -> None:
    context.add_cookies(
        [
            {
                "name": cookie_name,
                "value": cookie_val,
                "url": web_base,
                "httpOnly": True,
                "sameSite": "Lax",
            }
        ]
    )


def _assert_no_fake_placeholders(page: Page) -> None:
    body = page.locator("body").inner_text()
    for forbidden in ("DB-56", "Production Environment", "Vault Secret", "Mock Workspace"):
        assert forbidden not in body, f"Hardcoded placeholder '{forbidden}' found on settings page"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    cfg = json.loads(args.config)

    web_base: str = cfg["web_base"]
    ctrl_url: str = cfg["ctrl_url"]
    cookie_name: str = cfg["session_cookie_name"]
    ws_a: str = cfg["ws_a"]
    ws_b: str = cfg["ws_b"]

    diag_events: list[dict[str, Any]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            # -----------------------------------------------------------------
            # Scenario 1: Owner A on /w/<wsA>/settings (pagination, filter,
            # Cancel->Confirm role change, 409 conflict, last-owner UI, revoke,
            # safe Workspace Switcher & Multi-Tab isolation)
            # -----------------------------------------------------------------
            ctx_owner = browser.new_context()
            _add_session_cookie(ctx_owner, web_base, cookie_name, cfg["owner_a_session"])
            page = ctx_owner.new_page()

            def _record_api(resp: Any) -> None:
                url = str(resp.url)
                if "/v1/workspaces/" in url or "/v1/invitations/" in url:
                    diag_events.append(
                        {
                            "path": "/" + url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0],
                            "status": int(resp.status),
                        }
                    )

            page.on("response", _record_api)
            page.goto(f"{web_base}/w/{ws_a}/settings", wait_until="networkidle")
            page.wait_for_selector("text=Thành viên & Phân quyền Workspace", timeout=20000)

            _assert_no_fake_placeholders(page)
            page_text = page.locator("body").inner_text()
            assert ws_a in page_text or page.locator(f'input[value="{ws_a}"]').count() > 0
            assert "Workspace A" in page_text
            assert "Tenant A" in page_text

            # Cursor pagination: we seeded > 25 active members so "Tải thêm thành viên" must appear & work
            load_more_btn = page.get_by_role("button", name="Tải thêm thành viên")
            load_more_btn.wait_for(state="visible", timeout=15000)
            initial_rows = page.locator("tbody tr").count()
            assert initial_rows == 25, f"Expected first page size 25, got {initial_rows}"
            load_more_btn.click()
            page.wait_for_function(
                "() => document.querySelectorAll('tbody tr').length > 25",
                timeout=15000,
            )

            # Filter by "Đã thu hồi" then back to "Hoạt động"
            page.get_by_role("button", name="Đã thu hồi").click()
            page.wait_for_function(
                "() => document.body.innerText.includes('Đã thu hồi') && document.querySelectorAll('tbody tr').length === 1",
                timeout=10000,
            )
            page.get_by_role("button", name="Hoạt động").click()
            page.wait_for_function(
                "() => document.querySelectorAll('tbody tr').length === 25",
                timeout=10000,
            )
            # Load remaining members so Admin A & Op A & Extra Owner are all in table
            if page.get_by_role("button", name="Tải thêm thành viên").is_visible():
                page.get_by_role("button", name="Tải thêm thành viên").click()
                page.wait_for_function(
                    "() => document.querySelectorAll('tbody tr').length > 25",
                    timeout=15000,
                )

            # Cancel -> Confirm promotion of Admin A to Owner
            admin_select = page.locator(f'select[aria-label="Vai trò của {cfg["admin_a_name"]}"]')
            admin_select.wait_for(state="visible", timeout=10000)
            assert admin_select.input_value() == "admin"

            patch_count_before = sum(1 for e in diag_events if e["path"].endswith("/role"))
            admin_select.select_option("owner")
            page.wait_for_selector("text=Xác nhận thay đổi vai trò thành viên", timeout=10000)
            page.get_by_role("button", name="Hủy bỏ").click()
            page.wait_for_selector("text=Xác nhận thay đổi vai trò thành viên", state="detached", timeout=10000)
            assert admin_select.input_value() == "admin"
            patch_count_after_cancel = sum(1 for e in diag_events if e["path"].endswith("/role"))
            assert patch_count_after_cancel == patch_count_before, "Cancel triggered a PATCH request"

            # Confirm promotion to Owner
            admin_select.select_option("owner")
            page.wait_for_selector("text=Xác nhận thay đổi vai trò thành viên", timeout=10000)
            page.get_by_role("button", name="Xác nhận thực hiện").click()
            page.wait_for_selector("text=Xác nhận thay đổi vai trò thành viên", state="detached", timeout=15000)
            page.reload(wait_until="networkidle")
            if page.get_by_role("button", name="Tải thêm thành viên").is_visible():
                page.get_by_role("button", name="Tải thêm thành viên").click()
                page.wait_for_function(
                    "() => document.querySelectorAll('tbody tr').length > 25",
                    timeout=15000,
                )
            assert page.locator(f'select[aria-label="Vai trò của {cfg["admin_a_name"]}"]').input_value() == "owner"

            # Stale 409 conflict: mutate Op A on backend while Owner still sees "operator", then try viewer
            op_select = page.locator(f'select[aria-label="Vai trò của {cfg["op_a_name"]}"]')
            op_select.wait_for(state="visible", timeout=10000)
            assert op_select.input_value() == "operator"
            _ctrl_post(
                ctrl_url,
                "/mutate-member-role",
                {
                    "workspace_id": ws_a,
                    "membership_id": cfg["op_a_mid"],
                    "role": "developer",
                    "expected_role": "operator",
                },
            )
            op_select.select_option("viewer")
            page.wait_for_selector("text=Xác nhận thay đổi vai trò thành viên", timeout=10000)
            page.get_by_role("button", name="Xác nhận thực hiện").click()
            page.wait_for_selector("text=Xác nhận thay đổi vai trò thành viên", state="detached", timeout=15000)
            alert_text = page.locator('[role="alert"]').first.inner_text()
            assert alert_text, "Expected 409 conflict alert message"
            # Table auto-refetches to show authoritative "developer"
            page.wait_for_function(
                f"() => document.querySelector('select[aria-label=\"Vai trò của {cfg['op_a_name']}\"]')?.value === 'developer'",
                timeout=15000,
            )

            # Demote promoted Admin A back to viewer (now 1 Owner = Owner A, testing last-owner UI guard!)
            page.locator(f'select[aria-label="Vai trò của {cfg["admin_a_name"]}"]').select_option("viewer")
            page.wait_for_selector("text=Xác nhận thay đổi vai trò thành viên", timeout=10000)
            page.get_by_role("button", name="Xác nhận thực hiện").click()
            page.wait_for_selector("text=Xác nhận thay đổi vai trò thành viên", state="detached", timeout=15000)

            # Last active owner (Owner A) must have select & revoke disabled on UI
            owner_select = page.locator(f'select[aria-label="Vai trò của {cfg["owner_a_name"]}"]')
            assert owner_select.is_disabled(), "Last active owner role select should be disabled"
            owner_row = page.locator("tbody tr", has_text=cfg["owner_a_name"])
            assert owner_row.get_by_role("button", name="Thu hồi quyền").is_disabled()

            # Revoke Op A from UI -> confirm modal -> row moves to revoked / disappears from active filter
            op_row = page.locator("tbody tr", has_text=cfg["op_a_name"])
            op_row.get_by_role("button", name="Thu hồi quyền").click()
            page.wait_for_selector("text=Xác nhận thu hồi quyền thành viên", timeout=10000)
            page.get_by_role("button", name="Xác nhận thực hiện").click()
            page.wait_for_selector("text=Xác nhận thu hồi quyền thành viên", state="detached", timeout=15000)
            page.get_by_role("button", name="Đã thu hồi").click()
            page.wait_for_function(
                f"() => document.body.innerText.includes('{cfg['op_a_name']}')",
                timeout=15000,
            )

            # Switcher & Multi-Tab isolation: Tab 2 stays on ws_b while Tab 1 switches ws_a -> ws_b cleanly
            tab2 = ctx_owner.new_page()
            tab2.goto(f"{web_base}/w/{ws_b}/runs", wait_until="networkidle")
            tab2.wait_for_function(
                f"() => window.location.pathname === '/w/{ws_b}/runs'",
                timeout=15000,
            )

            # Switch in Tab 1 from /w/<wsA>/settings -> /w/<wsB>/settings cleanly
            page.locator('button[aria-label="Select workspace"]').click()
            page.locator('div[role="listbox"][aria-label="Available workspaces"]').wait_for(
                state="visible", timeout=10000
            )
            page.locator('[role="option"]', has_text="Workspace B").click()
            page.wait_for_function(
                f"() => window.location.pathname === '/w/{ws_b}/settings'",
                timeout=20000,
            )
            assert tab2.url.endswith(f"/w/{ws_b}/runs")
            tab2.reload(wait_until="networkidle")
            assert tab2.url.endswith(f"/w/{ws_b}/runs")
            ctx_owner.close()

            # -----------------------------------------------------------------
            # Scenario 2: Viewer (Admin A after demotion) sees <ForbiddenState>;
            # Admin B on Workspace A (we'll use Admin2 A!) cannot manage Owner
            # -----------------------------------------------------------------
            ctx_admin2 = browser.new_context()
            _add_session_cookie(ctx_admin2, web_base, cookie_name, cfg["admin2_a_session"])
            p_adm = ctx_admin2.new_page()
            p_adm.goto(f"{web_base}/w/{ws_a}/settings", wait_until="networkidle")
            p_adm.wait_for_selector("text=Thành viên & Phân quyền Workspace", timeout=20000)
            if p_adm.get_by_role("button", name="Tải thêm thành viên").is_visible():
                p_adm.get_by_role("button", name="Tải thêm thành viên").click()
                p_adm.wait_for_function(
                    "() => document.querySelectorAll('tbody tr').length > 25",
                    timeout=15000,
                )
            # Owner row renders read-only badge (zero select / zero revoke button) for Admin actor
            owner_row_adm = p_adm.locator("tbody tr", has_text=cfg["owner_a_name"])
            owner_row_adm.wait_for(state="visible", timeout=10000)
            assert p_adm.locator(f'select[aria-label="Vai trò của {cfg["owner_a_name"]}"]').count() == 0
            assert owner_row_adm.get_by_role("button", name="Thu hồi quyền").count() == 0
            assert "Chủ sở hữu (Owner)" in owner_row_adm.inner_text()
            # Developer dropdown for Admin does NOT contain owner/admin options
            dev_select_for_admin = p_adm.locator(f'select[aria-label="Vai trò của {cfg["dev_a_name"]}"]')
            dev_select_for_admin.wait_for(state="visible", timeout=10000)
            option_vals = dev_select_for_admin.locator("option").all_text_contents()
            assert not any("Chủ sở hữu" in o or "Quản trị" in o for o in option_vals), option_vals

            # Demote Admin2 -> viewer on backend and reload -> ForbiddenState!
            _ctrl_post(
                ctrl_url,
                "/mutate-member-role",
                {
                    "workspace_id": ws_a,
                    "membership_id": cfg["admin2_a_mid"],
                    "role": "viewer",
                    "expected_role": "admin",
                },
            )
            p_adm.reload(wait_until="networkidle")
            p_adm.wait_for_selector("text=Chỉ Owner hoặc Admin mới có quyền quản lý thành viên", timeout=15000)
            ctx_admin2.close()

            # -----------------------------------------------------------------
            # Scenario 3: Focus/Visibility Revocation Recheck + Cross-Workspace Survival
            # -----------------------------------------------------------------
            ctx_dev = browser.new_context()
            _add_session_cookie(ctx_dev, web_base, cookie_name, cfg["dev_a_session"])
            p_dev = ctx_dev.new_page()
            p_dev.goto(f"{web_base}/w/{ws_a}/runs", wait_until="networkidle")
            p_dev.wait_for_function(
                f"() => window.location.pathname === '/w/{ws_a}/runs'",
                timeout=15000,
            )
            # Revoke dev_a in WS A while dev_a remains active in WS B
            _ctrl_post(
                ctrl_url,
                "/revoke-member",
                {
                    "workspace_id": ws_a,
                    "membership_id": cfg["dev_a_mid"],
                    "expected_role": "developer",
                },
            )
            p_dev.evaluate("() => window.dispatchEvent(new Event('focus'))")
            p_dev.wait_for_function(
                f"() => window.location.pathname.startsWith('/w/{ws_b}/')",
                timeout=20000,
            )
            ctx_dev.close()

            # -----------------------------------------------------------------
            # Scenario 4: Promoted Owner Invitation Replay displays Owner role
            # -----------------------------------------------------------------
            ctx_prom = browser.new_context()
            _add_session_cookie(ctx_prom, web_base, cookie_name, cfg["promoted_owner_session"])
            p_prom = ctx_prom.new_page()
            inv_id = cfg["promoted_invitation_id"]
            raw_tok = cfg["promoted_invitation_token"]
            p_prom.goto(f"{web_base}/invitations/{inv_id}/accept#token={raw_tok}", wait_until="networkidle")
            p_prom.wait_for_function(
                "() => document.body.innerText.includes('Chủ sở hữu (Owner)')",
                timeout=20000,
            )
            ctx_prom.close()

            print(json.dumps({"status": "ok", "events": diag_events}))
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())

