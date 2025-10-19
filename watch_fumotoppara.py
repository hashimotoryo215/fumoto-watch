import os
import sys
import re
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

TARGET_DATE_LABEL = os.getenv("TARGET_DATE_LABEL", "10/24")  # 例: "10/24"
TARGET_ROWS = os.getenv("TARGET_ROWS", "キャンプ宿泊,キャンプ日帰り").split(",")
PAGE_URL = "https://reserve.fumotoppara.net/reserved/reserved-calendar-list"
LINE_TOKEN = os.getenv("LINE_NOTIFY_TOKEN")  # 必須: LINE Notifyのトークン
TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "60000"))  # 60秒待機

def line_notify(message: str):
    if not LINE_TOKEN:
        print("WARN: LINE_NOTIFY_TOKEN が未設定のため通知しません。")
        return
    res = requests.post(
        "https://notify-api.line.me/api/notify",
        headers={"Authorization": f"Bearer {LINE_TOKEN}"},
        data={"message": message},
        timeout=30
    )
    res.raise_for_status()

def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def pick_column_index(headers, date_label):
    for i, h in enumerate(headers):
        if date_label in h:
            return i
    return None

def fetch_cell_symbol(page, row_label, date_label):
    selectors = ["table", "div[role='table']", "div:has-text('予約カレンダー')"]
    found = False
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=TIMEOUT_MS)
            found = True
            break
        except PlaywrightTimeoutError:
            continue
    if not found:
        raise RuntimeError("テーブルの描画を確認できませんでした。")

    header_cells = page.query_selector_all("thead th")
    if not header_cells:
        header_cells = page.query_selector_all("table tr:nth-child(1) th, table tr:nth-child(1) td")
    headers = [normalize_text(h.inner_text()) for h in header_cells]
    col_idx = pick_column_index(headers, date_label)
    if col_idx is None:
        raise RuntimeError(f"ヘッダーから日付 '{date_label}' の列が見つかりませんでした。")

    rows = page.query_selector_all("tbody tr") or page.query_selector_all("table tr")
    target_row = None
    for r in rows:
        first_cell = r.query_selector("th") or r.query_selector("td")
        if not first_cell:
            continue
        label = normalize_text(first_cell.inner_text())
        if row_label in label:
            target_row = r
            break
    if not target_row:
        raise RuntimeError(f"行 '{row_label}' が見つかりませんでした。")

    data_cells = target_row.query_selector_all("td")
    if data_cells:
        j = col_idx - 1 if len(data_cells) + 1 == len(headers) else col_idx
        if j < 0 or j >= len(data_cells):
            raise RuntimeError(f"列インデックス計算に失敗しました (j={j}, len={len(data_cells)}).")
        symbol = normalize_text(data_cells[j].inner_text())
        return symbol

    all_cells = target_row.query_selector_all("th,td")
    if col_idx < len(all_cells):
        return normalize_text(all_cells[col_idx].inner_text())

    raise RuntimeError("対象セルを取得できませんでした。")

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="ja-JP")
        page = context.new_page()
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=90000)
        try:
            page.wait_for_timeout(5000)  # JS描画の猶予
        except Exception:
            pass

        results = {}
        for row in TARGET_ROWS:
            try:
                symbol = fetch_cell_symbol(page, row, TARGET_DATE_LABEL)
                results[row] = symbol
            except Exception as e:
                results[row] = f"ERROR: {e}"

        browser.close()

    alerts = []
    for row, symbol in results.items():
        if symbol in ("〇", "○", "△"):
            alerts.append(f"【空きあり】{row} × {TARGET_DATE_LABEL}: {symbol}")
        elif symbol.startswith("ERROR"):
            alerts.append(f"【取得失敗】{row} × {TARGET_DATE_LABEL}: {symbol}")

    if alerts:
        msg = "\n".join(["ふもとっぱら空き検知"] + alerts + [f"確認: {PAGE_URL}"])
        print(msg)
        try:
            line_notify(msg)
        except Exception as e:
            print(f"LINE通知失敗: {e}", file=sys.stderr)
    else:
        print(f"空き無し: {results}")

if __name__ == "__main__":
    main()
