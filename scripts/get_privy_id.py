#!/usr/bin/env python3
"""Get Privy App ID — handles existing account login."""
from __future__ import annotations
import re, sys, time

from agentmail import AgentMail
from playwright.sync_api import sync_playwright, Page
try:
    from playwright_stealth import Stealth
    USE_STEALTH = True
except Exception:
    USE_STEALTH = False

import os
AGENTMAIL_API_KEY = os.environ["AGENTMAIL_API_KEY"]
PRIVY_EMAIL = os.environ.get("PRIVY_EMAIL", "plainpolicy298@agentmail.to")

am = AgentMail(api_key=AGENTMAIL_API_KEY)


def wait_for_new_email(inbox_id: str, seen: set, timeout: int = 180) -> tuple[str, str]:
    print(f"    polling {inbox_id} …", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        try:
            msgs = am.inboxes.messages.list(inbox_id)
            for m in (msgs.messages or []):
                if m.message_id not in seen:
                    full = am.inboxes.messages.get(inbox_id, m.message_id)
                    return full.subject or "", full.text or full.html or ""
        except Exception as e:
            print(f"      poll error: {e}", flush=True)
    raise TimeoutError(f"No email in {inbox_id} after {timeout}s")


def first_otp(text: str) -> str | None:
    m = re.search(r'\b(\d{6})\b', text)
    return m.group(1) if m else None


def extract_app_id(url: str) -> str | None:
    m = re.search(r'/apps/([^/?#]+)', url)
    return m.group(1) if m else None


def find_app_id_on_page(page: Page) -> str | None:
    # Check URL first
    app_id = extract_app_id(page.url)
    if app_id:
        return app_id
    # Check page body for CUID
    body = page.inner_text("body")
    m = re.search(r'\b(cl[a-z0-9]{20,})\b', body)
    return m.group(1) if m else None


def navigate_to_app(page: Page) -> str | None:
    """From org overview, find or create an app and return its ID."""
    print(f"    navigate_to_app from: {page.url}", flush=True)
    page.screenshot(path="/tmp/privy_org.png")
    body = page.inner_text("body")

    # Look for a CUID in the body already
    m = re.search(r'\b(cl[a-z0-9]{20,})\b', body)
    if m:
        return m.group(1)

    # Try clicking on any app link or card
    for sel in [
        'a[href*="/apps/"]',
        'button:has-text("Open")',
        'button:has-text("View")',
        '[data-testid*="app"]',
        'div[class*="app"] a',
    ]:
        els = page.locator(sel).all()
        for el in els:
            if el.is_visible():
                try:
                    el.click()
                    page.wait_for_timeout(2000)
                    app_id = extract_app_id(page.url)
                    if app_id:
                        return app_id
                    page.go_back()
                    page.wait_for_timeout(1000)
                except Exception:
                    pass

    # Try creating a new app
    print("    trying to create a new app …", flush=True)
    for txt in ["New app", "Create app", "Add app", "New App", "Create App"]:
        btn = page.locator(f"button:has-text('{txt}'), a:has-text('{txt}')").first
        if btn.is_visible(timeout=800):
            btn.click()
            page.wait_for_timeout(2000)
            break

    # Fill app name if prompted
    name_inp = page.locator('input[placeholder*="name" i], input[placeholder*="app" i]').first
    if name_inp.is_visible(timeout=2000):
        name_inp.fill("docs-eval")
        page.wait_for_timeout(300)
        for txt in ["Create", "Submit", "Save", "Continue"]:
            btn = page.locator(f"button:has-text('{txt}')").first
            if btn.is_visible(timeout=800):
                btn.click()
                break
        page.wait_for_timeout(3000)

    app_id = extract_app_id(page.url)
    if not app_id:
        body = page.inner_text("body")
        m = re.search(r'\b(cl[a-z0-9]{20,})\b', body)
        app_id = m.group(1) if m else None
    return app_id


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=100)
    page = browser.new_page()
    if USE_STEALTH:
        Stealth(navigator_webdriver=True).apply_stealth_sync(page)
        print("stealth enabled")

    # Snapshot existing emails
    try:
        existing = am.inboxes.messages.list(PRIVY_EMAIL)
        seen = {m.message_id for m in (existing.messages or [])}
        print(f"    {len(seen)} existing messages")
    except Exception:
        seen = set()

    page.goto("https://dashboard.privy.io", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    print(f"start: {page.url}", flush=True)

    app_id = extract_app_id(page.url)
    if not app_id:
        # Click Get started / Sign in
        for txt in ["Get started", "Sign in", "Log in", "Continue"]:
            btn = page.locator(f"button:has-text('{txt}')").first
            if btn.is_visible(timeout=1500):
                btn.click()
                page.wait_for_timeout(2000)
                break

        # Fill email
        email_inp = page.locator('input[type="email"], input[placeholder*="email" i]').first
        try:
            email_inp.wait_for(timeout=8000)
            email_inp.fill(PRIVY_EMAIL)
        except Exception as e:
            print(f"no email input: {e}")

        for txt in ["Continue", "Send magic link", "Sign in", "Next", "Submit"]:
            btn = page.locator(f"button:has-text('{txt}')").first
            if btn.is_visible(timeout=1000):
                btn.click()
                break
        page.wait_for_timeout(2000)
        print("email submitted", flush=True)

        # Wait for OTP
        try:
            subject, body = wait_for_new_email(PRIVY_EMAIL, seen, timeout=180)
            print(f"got email: {subject!r}")
            otp = first_otp(body)
            print(f"OTP: {otp}")
        except TimeoutError:
            print("no email — enter OTP manually if browser is open")
            otp = None

        if otp:
            page.wait_for_timeout(1000)
            otp_inputs = [i for i in page.locator('input[maxlength="1"]').all() if i.is_visible()]
            print(f"found {len(otp_inputs)} OTP inputs")
            if len(otp_inputs) >= 6:
                for idx, digit in enumerate(otp[:6]):
                    otp_inputs[idx].click()
                    otp_inputs[idx].fill(digit)
                    page.wait_for_timeout(80)
            else:
                page.keyboard.type(otp)
            page.wait_for_timeout(3000)

        # Accept T&C
        for sel in ['button:has-text("Accept")', 'button:has-text("Agree")',
                    'button:has-text("I agree")', 'button:has-text("Accept and continue")']:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1000):
                btn.click()
                page.wait_for_timeout(1500)
                break

        print(f"after auth: {page.url}", flush=True)

    # Handle onboarding
    for step in range(8):
        if "welcome" not in page.url and "onboard" not in page.url:
            break
        print(f"    onboarding step {step+1}: {page.url}", flush=True)
        for sel in ['input[placeholder="Add name"]', 'input[placeholder*="name" i]']:
            inp = page.locator(sel).first
            if inp.is_visible(timeout=500):
                inp.fill("docs-eval")
                break
        try:
            checked = page.evaluate('!!document.querySelector(\'input[type="checkbox"]\')?.checked')
            if not checked:
                page.evaluate('document.querySelector(\'input[type="checkbox"]\')?.click()')
                page.wait_for_timeout(300)
        except Exception:
            pass
        for txt in ["Continue", "Next", "Get started", "Finish"]:
            btn = page.locator(f"button:has-text('{txt}')").first
            if btn.is_visible(timeout=600):
                try:
                    btn.click()
                    page.wait_for_timeout(2500)
                    break
                except Exception:
                    pass

    # Wait for stable dashboard URL
    try:
        page.wait_for_url("**dashboard.privy.io/**", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(2000)
    print(f"dashboard: {page.url}", flush=True)

    app_id = find_app_id_on_page(page)

    # If on org overview, navigate into an app
    if not app_id and "organization-overview" in page.url:
        app_id = navigate_to_app(page)

    # Try navigating to /apps/ directly
    if not app_id:
        page.goto("https://dashboard.privy.io/apps", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        print(f"apps page: {page.url}", flush=True)
        app_id = find_app_id_on_page(page)
        if not app_id:
            # Click first app link
            links = [a for a in page.locator('a[href*="/apps/"]').all() if a.is_visible()]
            if links:
                links[0].click()
                page.wait_for_timeout(2000)
                app_id = extract_app_id(page.url)

    page.screenshot(path="/tmp/privy_final.png")
    print(f"final URL: {page.url}", flush=True)

    if app_id:
        print(f"\n{'='*50}")
        print(f"PRIVY_APP_ID={app_id}")
        print(f"{'='*50}")
    else:
        print("Could not extract App ID automatically.")
        print(f"Current URL: {page.url}")
        print("Check /tmp/privy_final.png")

    # Keep browser open briefly so user can see
    page.wait_for_timeout(5000)
    browser.close()
