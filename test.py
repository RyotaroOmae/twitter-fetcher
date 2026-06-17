from playwright.sync_api import sync_playwright

handle="Paseman98"
url=f"https://x.com/{handle}"

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(locale="ja-JP")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    print("final url:", page.url)
    html = page.content()
    print("html len:", len(html))
    open("pw_debug.html","w",encoding="utf-8").write(html)
    b.close()

print("saved pw_debug.html")
