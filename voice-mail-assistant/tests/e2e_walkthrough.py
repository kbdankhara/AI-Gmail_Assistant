"""Browser run through every screen in demo mode. Start the server first:
    DEMO_MODE=1 DEV_LOGIN=1 flask --app wsgi run --port 5055
    python tests/e2e_walkthrough.py [screenshot_dir]
"""
import sys

from playwright.sync_api import sync_playwright

BASE = "http://localhost:5055"
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
SCRIPT = ["I'm good, thanks!", "Yes, go ahead.",
          "reply", "the numbers look right, go ahead and send them to finance", "yes send it",
          "mark it as read",
          "skip"]


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # Failed requests are expected twice (the refused save and the 404 page); script errors are not.
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)

        page.goto(BASE + "/")
        assert page.locator("h1").inner_text() == "Talk to your inbox."
        page.screenshot(path=f"{OUT}/01-landing.png", full_page=True)

        page.goto(BASE + "/login")
        page.click("#tab-create")
        assert page.locator("#auth-title").inner_text() == "Create your account"
        page.screenshot(path=f"{OUT}/02-login.png")
        page.click("#google-btn")
        page.wait_for_url("**/consent")

        assert page.locator("#accept-btn").is_disabled()
        for item in page.locator(".consent-item").all():
            item.locator(".box").click()
        page.screenshot(path=f"{OUT}/03-consent.png", full_page=True)
        page.click("#accept-btn")
        page.wait_for_url("**/profile?onboarding=1")

        # Saving with nothing filled in is refused with a clear message.
        page.click("#save-profile")
        page.wait_for_selector("#profile-error:not([hidden])")
        assert "Gmail" in page.locator("#profile-error").inner_text()

        page.fill(".gmail-addr-input", "kevin@gmail.com")
        page.fill(".gmail-pass-input", "abcd efgh ijkl mnop")
        page.click(".gmail-action")
        page.wait_for_function("document.querySelector('.gmail-action').textContent === 'Test Gmail login'")
        page.click(".gmail-action")
        page.wait_for_selector(".gmail-account .result-line.ok")
        page.click("#add-gmail-account")
        assert page.locator(".gmail-account").count() == 2
        page.locator(".gmail-addr-input").nth(1).fill("work@gmail.com")
        page.locator(".gmail-pass-input").nth(1).fill("qrstuvwxyzabcdef")
        page.click(".gmail-help-trigger >> nth=0")
        page.wait_for_selector("#gmail-help-backdrop.on")
        page.wait_for_timeout(300)
        page.screenshot(path=f"{OUT}/04-app-password-help.png")
        page.keyboard.press("Escape")
        page.fill("#ai-key", "sk-ant-demo-key")
        page.click("#ai-action")
        page.wait_for_function("document.getElementById('ai-action').textContent === 'Test AI connection'")
        page.click("#ai-action")
        page.wait_for_selector("#ai-result.ok")
        page.wait_for_timeout(300)
        page.screenshot(path=f"{OUT}/05-profile.png", full_page=True)
        page.click("#save-profile")   # also saves the second, still-unsaved Gmail row
        page.wait_for_url("**/assistant")

        assert page.locator(".setup-chip").first.inner_text().strip() == "Gmail connected (2)"
        page.screenshot(path=f"{OUT}/06-assistant-off.png", full_page=True)

        # Scripted conversation stands in for the microphone.
        page.add_init_script(f"window.__vmaScript = {SCRIPT!r};")
        page.reload()
        page.click("#power")
        page.wait_for_function("document.getElementById('pill-text').textContent.indexOf('watching') > -1", timeout=20000)
        log = page.locator("#log").inner_text()
        for expected in ("Hello Demo! How are you doing today?", "Glad to hear it!",
                         "You have 6 unread emails", "Email 1 of 6. In kevin@gmail.com. From Priya Shah",
                         "Here is the reply. Hi Priya,", "Your reply has been sent and the email is marked as read.",
                         "Done. It's marked as read.", "Okay, I've left it unread."):
            assert expected in log, expected
        assert page.locator("#stat-unread").inner_text() == "4"
        assert page.locator("#power").get_attribute("aria-pressed") == "true"
        page.screenshot(path=f"{OUT}/07-assistant-on.png", full_page=True)
        page.click("#power")
        assert "Turning off. Goodbye!" in page.locator("#log").inner_text()
        assert page.locator("#pill-text").inner_text() == "Assistant is off"

        page.click("#nav-avatar")
        page.wait_for_selector("#drawer.on")
        page.wait_for_timeout(350)
        page.screenshot(path=f"{OUT}/08-drawer.png")
        page.click("text=Payment")
        page.wait_for_url("**/payment")
        page.screenshot(path=f"{OUT}/09-payment.png", full_page=True)

        page.goto(BASE + "/profile")
        assert page.locator(".gmail-account").count() == 2
        assert page.locator("#delete-account").is_disabled()
        page.screenshot(path=f"{OUT}/10-profile-settings.png", full_page=True)
        page.goto(BASE + "/help")
        page.screenshot(path=f"{OUT}/11-help.png", full_page=True)
        page.goto(BASE + "/missing")
        page.screenshot(path=f"{OUT}/12-error.png")

        # Dark theme and phone width.
        dark = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark", storage_state=ctx.storage_state())
        dpage = dark.new_page()
        for name, url in (("assistant", "/assistant"), ("profile", "/profile"), ("payment", "/payment"), ("landing", "/")):
            dpage.goto(BASE + url)
            overflow = dpage.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            assert overflow <= 0, f"{name} scrolls sideways by {overflow}px on a phone"
            dpage.screenshot(path=f"{OUT}/13-dark-mobile-{name}.png", full_page=True)

        # Sign out returns to the sign-in page.
        page.goto(BASE + "/assistant")
        page.click("#nav-avatar")
        page.click("#drawer [data-signout]")
        page.wait_for_url("**/login")

        assert not errors, errors
        browser.close()
        print("walkthrough OK")


if __name__ == "__main__":
    main()
