#!/usr/bin/env python3
"""One-off script: sign up for Privy and ZeroDev using AgentMail inboxes."""
from __future__ import annotations
import re, sys, time

import os
AGENTMAIL_API_KEY = os.environ["AGENTMAIL_API_KEY"]
PRIVY_EMAIL   = os.environ.get("PRIVY_EMAIL",   "plainpolicy298@agentmail.to")
ZERODEV_EMAIL = os.environ.get("ZERODEV_EMAIL", "cooperativecity548@agentmail.to")

from agentmail import AgentMail
from playwright.sync_api import sync_playwright, Page

am = AgentMail(api_key=AGENTMAIL_API_KEY)


def wait_for_email(inbox_id: str, timeout: int = 120) -> tuple[str, str]:
    """Poll until a new email arrives. Returns (subject, body)."""
    print(f"    waiting for email in {inbox_id} …", flush=True)
    deadline = time.time() + timeout
    seen: set[str] = set()
    # Seed seen with existing messages so we only catch new ones
    try:
        existing = am.inboxes.messages.list(inbox_id)
        seen = {m.message_id for m in (existing.messages or [])}
    except Exception:
        pass
    while time.time() < deadline:
        time.sleep(4)
        try:
            msgs = am.inboxes.messages.list(inbox_id)
            for m in (msgs.messages or []):
                if m.message_id not in seen:
                    full = am.inboxes.messages.get(inbox_id, m.message_id)
                    return full.subject or "", full.text or full.html or ""
        except Exception:
            pass
    raise TimeoutError(f"No email received in {inbox_id} after {timeout}s")


def first_link(text: str, fragment: str = "") -> str | None:
    for url in re.findall(r'https?://[^\s<>"\']+', text):
        url = url.rstrip(".,)")
        if not fragment or fragment in url:
            return url
    return None


def first_otp(text: str) -> str | None:
    m = re.search(r'\b(\d{6})\b', text)
    return m.group(1) if m else None


# ─────────────────────────── ZeroDev ─────────────────────────────────────────

def signup_zerodev(p) -> str | None:
    print("\n── ZeroDev signup")
    browser = p.chromium.launch(headless=False, slow_mo=150)
    page: Page = browser.new_page()
    result = None
    try:
        page.goto("https://dashboard.zerodev.app/login", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # Enter email and submit
        page.locator('input[type="email"]').fill(ZERODEV_EMAIL)
        page.locator('button:has-text("Continue with Email")').click()
        page.wait_for_timeout(2000)
        page.screenshot(path="/tmp/zd_after_submit.png")
        print("    email submitted, waiting for verification email …")

        subject, body = wait_for_email(ZERODEV_EMAIL, timeout=120)
        print(f"    got email: {subject!r}")

        link = first_link(body, "zerodev") or first_link(body, "magic") or first_link(body)
        otp  = first_otp(body)

        if link:
            print(f"    following link: {link[:80]}")
            page.goto(link, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
        elif otp:
            print(f"    entering OTP: {otp}")
            page.locator('input').first.fill(otp)
            page.keyboard.press("Enter")
            page.wait_for_timeout(3000)

        page.screenshot(path="/tmp/zd_after_auth.png")
        print(f"    current URL: {page.url}")

        # If not on dashboard yet, wait a bit
        if "dashboard.zerodev.app" not in page.url or "login" in page.url:
            page.wait_for_url("**/dashboard.zerodev.app/**", timeout=15000)

        page.wait_for_timeout(3000)
        page.screenshot(path="/tmp/zd_dashboard.png")
        print(f"    on dashboard: {page.url}")

        # Create a new project
        for txt in ["New Project", "New project", "Create Project", "Create project", "+"]:
            btn = page.locator(f"button:has-text('{txt}')").first
            if btn.is_visible(timeout=2000):
                btn.click()
                page.wait_for_timeout(2000)
                break

        # Fill project name
        name_inp = page.locator('input[placeholder*="name" i], input[placeholder*="project" i]').first
        if name_inp.is_visible(timeout=3000):
            name_inp.fill("docs-eval")
            # Submit the form
            for txt in ["Create", "Submit", "Save"]:
                btn = page.locator(f"button:has-text('{txt}')").first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    break
            else:
                page.keyboard.press("Enter")
            page.wait_for_timeout(3000)

        page.screenshot(path="/tmp/zd_project.png")
        body_text = page.inner_text("body")

        # ZeroDev project IDs are UUIDs
        uuid_m = re.search(
            r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
            body_text
        )
        if uuid_m:
            result = uuid_m.group(0)
            print(f"    Project ID: {result}")
        else:
            print("    could not extract Project ID from page text")
            print("    screenshot saved to /tmp/zd_project.png")

    except Exception as e:
        print(f"    ERROR: {e}")
        try: page.screenshot(path="/tmp/zd_error.png")
        except: pass
    finally:
        browser.close()
    return result


# ─────────────────────────── Privy ───────────────────────────────────────────

def signup_privy(p) -> str | None:
    print("\n── Privy signup")
    browser = p.chromium.launch(headless=False, slow_mo=150)
    page: Page = browser.new_page()
    result = None
    try:
        page.goto("https://dashboard.privy.io", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        # Dismiss cookie banner if present
        agree = page.locator("button:has-text('AGREE & PROCEED'), button:has-text('Accept')").first
        if agree.is_visible(timeout=2000):
            agree.click()
            page.wait_for_timeout(1000)

        # Click "Get started"
        page.locator("button:has-text('Get started')").first.click()
        page.wait_for_timeout(3000)
        page.screenshot(path="/tmp/privy_getstarted.png")
        print(f"    after get-started: {page.url}")

        # Find email input
        email_inp = page.locator('input[type="email"], input[placeholder*="email" i]').first
        email_inp.wait_for(timeout=10000)
        email_inp.fill(PRIVY_EMAIL)

        # Submit
        for txt in ["Continue", "Sign up", "Log in", "Send magic link", "Next", "Submit"]:
            btn = page.locator(f"button:has-text('{txt}')").first
            if btn.is_visible(timeout=1000):
                btn.click()
                break
        page.wait_for_timeout(2000)
        page.screenshot(path="/tmp/privy_after_email.png")
        print("    email submitted, waiting for verification email …")

        subject, body = wait_for_email(PRIVY_EMAIL, timeout=120)
        print(f"    got email: {subject!r}")

        link = first_link(body, "dashboard.privy.io") or first_link(body, "privy")
        otp  = first_otp(body)

        if link:
            print(f"    following link: {link[:80]}")
            page.goto(link, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
        elif otp:
            print(f"    entering OTP: {otp}")
            otp_inp = page.locator('input[maxlength="6"], input[placeholder*="code" i], input[placeholder*="OTP" i]').first
            otp_inp.fill(otp)
            page.keyboard.press("Enter")
            page.wait_for_timeout(4000)

        page.screenshot(path="/tmp/privy_after_auth.png")
        print(f"    current URL: {page.url}")

        # Wait to land on the dashboard
        page.wait_for_url("**dashboard.privy.io/**", timeout=20000)
        page.wait_for_timeout(4000)
        page.screenshot(path="/tmp/privy_after_auth2.png")
        print(f"    landed on: {page.url}")

        # Handle /welcome onboarding if present
        max_onboarding_steps = 8
        for step in range(max_onboarding_steps):
            if "welcome" not in page.url and "onboard" not in page.url:
                break
            print(f"    onboarding step {step+1}: {page.url}")
            page.screenshot(path=f"/tmp/privy_onboard_{step}.png")

            # Fill "Add name" / project name input if present
            name_inp = page.locator('input[placeholder="Add name"], input[placeholder*="name" i]').first
            if name_inp.is_visible(timeout=1000):
                name_inp.fill("docs-eval")
                page.wait_for_timeout(500)

            # Handle "Don't have a website" checkbox via JS (aria-hidden)
            cb = page.locator('input[type="checkbox"]').first
            try:
                is_checked = page.evaluate('document.querySelector(\'input[type="checkbox"]\').checked')
                if not is_checked:
                    page.evaluate('document.querySelector(\'input[type="checkbox"]\').click()')
                    page.wait_for_timeout(500)
            except Exception:
                # Try clicking the label text instead
                try:
                    page.locator('text="Don\'t have a website"').click()
                    page.wait_for_timeout(500)
                except Exception:
                    pass

            # Fill website URL input if visible (alternative to checkbox)
            url_inp = page.locator('input[placeholder*="website" i], input[placeholder*="url" i], input[type="url"]').first
            if url_inp.is_visible(timeout=500):
                url_inp.fill("https://docs-eval.example.com")
                page.wait_for_timeout(300)

            # Click Continue / Next / Submit / Get started
            clicked = False
            for btn_txt in ["Continue", "Next", "Get started", "Submit", "Finish"]:
                btn = page.locator(f"button:has-text('{btn_txt}')").first
                if btn.is_visible(timeout=800):
                    try:
                        btn.click()
                        clicked = True
                        break
                    except Exception:
                        pass
            if not clicked:
                # Try the first enabled button
                try:
                    page.locator("button:not([disabled])").first.click()
                except Exception:
                    pass

            page.wait_for_timeout(3000)

        page.screenshot(path="/tmp/privy_dashboard.png")
        print(f"    on dashboard: {page.url}")

        # Try to find the App ID in the URL or on the page
        # URL pattern: /apps/<app_id>/...
        url_m = re.search(r'/apps/([^/]+)', page.url)
        if url_m:
            result = url_m.group(1)
            print(f"    App ID (from URL): {result}")
        else:
            body_text = page.inner_text("body")
            # Privy App IDs look like "cl..." (CUID)
            cuid_m = re.search(r'\b(cl[a-z0-9]{20,})\b', body_text)
            if cuid_m:
                result = cuid_m.group(1)
                print(f"    App ID (from page): {result}")
            else:
                print("    could not extract App ID — screenshot at /tmp/privy_dashboard.png")

    except Exception as e:
        print(f"    ERROR: {e}")
        try: page.screenshot(path="/tmp/privy_error.png")
        except: pass
    finally:
        browser.close()
    return result


# ─────────────────────────── Main ────────────────────────────────────────────

if __name__ == "__main__":
    with sync_playwright() as p:
        zerodev_id = signup_zerodev(p)
        privy_id   = signup_privy(p)

    print("\n" + "═" * 55)
    print("RESULTS — add these to your .env")
    print("═" * 55)
    if privy_id:
        print(f"PRIVY_APP_ID={privy_id}")
    else:
        print("PRIVY_APP_ID=<not extracted — check /tmp/privy_dashboard.png>")

    if zerodev_id:
        print(f"ZERODEV_PROJECT_ID={zerodev_id}")
        print(f"BUNDLER_URL=https://rpc.zerodev.app/api/v2/bundler/{zerodev_id}")
        print(f"PAYMASTER_URL=https://rpc.zerodev.app/api/v2/paymaster/{zerodev_id}")
    else:
        print("ZERODEV_PROJECT_ID=<not extracted — check /tmp/zd_project.png>")

    print(f"AGENTMAIL_API_KEY={AGENTMAIL_API_KEY}")
    print(f"ANTHROPIC_API_KEY=<your key>")
