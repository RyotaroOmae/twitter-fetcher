from playwright.sync_api import sync_playwright

USER_DATA_DIR = r"/mnt/c/Users/omary/AppData/Local/Google/Chrome/User Data"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=USER_DATA_DIR,
        headless=False,
        locale="ja-JP",
        viewport={"width": 1280, "height": 900},
    )
    page = ctx.new_page()
    page.goto("https://x.com/login", wait_until="domcontentloaded", timeout=60000)
    print("開いたブラウザでXにログインしてください。ログイン後、ブラウザを閉じれば完了です。")
    page.wait_for_timeout(600000)
    ctx.close()

