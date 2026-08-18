#!/usr/bin/env python3
"""Read-only reconnaissance of the connected OpenWA WhatsApp Web page."""

from pathlib import Path

from playwright.sync_api import sync_playwright


CDP_URL = "http://127.0.0.1:34511"
OUT = Path("/tmp/openwa-recon.png")


with sync_playwright() as playwright:
    browser = playwright.chromium.connect_over_cdp(CDP_URL)
    contexts = browser.contexts
    pages = [page for context in contexts for page in context.pages]
    if not pages:
        raise SystemExit("No Chromium pages exposed by OpenWA")

    page = next((candidate for candidate in pages if "web.whatsapp.com" in candidate.url), pages[0])
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception as exc:
        print(f"networkidle timeout: {exc.__class__.__name__}")

    print(f"url={page.url}")
    print(f"title={page.title()}")
    print("body_text:")
    print(page.locator("body").inner_text(timeout=10_000)[:6000])
    page.screenshot(path=str(OUT), full_page=True)
    print(f"screenshot={OUT}")

    globals_snapshot = page.evaluate(
        """() => Object.keys(window).filter(key => /store|chat|wa|debug/i.test(key)).sort()"""
    )
    print(f"window_candidates={globals_snapshot[:200]}")

    store_snapshot = page.evaluate(
        """() => {
          const store = window.Store;
          if (!store) return {present: false};
          const chats = store.Chat && store.Chat.models ? store.Chat.models : [];
          return {
            present: true,
            chat_count: chats.length,
            groups: chats
              .filter(chat => chat.id && (chat.id.server === 'g.us' || String(chat.id._serialized || '').endsWith('@g.us')))
              .map(chat => ({
                id: chat.id._serialized,
                name: chat.name || chat.formattedTitle || chat.subject || '',
                unread: chat.unreadCount,
              }))
              .slice(0, 1000),
          };
        }"""
    )
    print(f"store_snapshot={store_snapshot}")

    browser.close()
