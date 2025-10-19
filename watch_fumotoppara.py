import os
import sys
import re
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ▼複数日対応：TARGET_DATE_LABELS を優先。未設定なら TARGET_DATE_LABEL を単一日として使う
_target_labels_env = os.getenv("TARGET_DATE_LABELS")
if _target_labels_env and _target_labels_env.strip():
    TARGET_DATE_LABELS = [s.strip() for s in _target_labels_env.split(",") if s.strip()]
else:
    TARGET_DATE_LABELS = [os.getenv("TARGET_DATE_LABEL", "11/1").strip()]

TARGET_ROWS = [s.strip() for s in os.getenv("TARGET_ROWS", "キャンプ宿泊,キャンプ日帰り").split(",") if s.strip()]
PAGE_URL = "https://reserve.fumotoppara.net/reserved/reserved-calendar-list"
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")  # Messaging API のチャネルアクセストークン
TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "60000"))  # 60秒待機

def line_broadcast(message: str):
    """Messaging API の Broadcast で通知（あなたが唯一の友だちなら自分に届く）。"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("WARN: LINE_CHANNEL_ACCESS_TOKEN 未設定。通知は送信しません。")
        return
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {"messages": [{"type": "text", "text": message}]}
    res = requests.post("https://api.line.me/v2/bot/message/broadcast", headers=headers, json=data, timeout=30)
    try:
        res.raise_for_status()
    except Exception:
        print("LINE Broadcast 失敗:", res.text, file=sys.stderr)
        raise

def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def _date_candidates(label: str):
    """
    見出しの表記ゆれに耐えるための候補を返す。
    例: "11/01" と "11/1"、曜日表記入り "11/1(日)" などに対して部分一致で拾う。
    """
    label = label.strip()
    if "/" in label:
        mon, day = label.split("/", 1)
        day_nz = day.lstrip("0") or "0"
        day_z2 = day if len(day) == 2 else day.zfill(2)
        cand1 = f"{mon}/{day_nz}"
        cand2 = f"{mon}/{day_z2}"
        return {cand1, cand2}
    return {label}

def pick_column_index(headers, date_label):
    cands = _date_candidates(date_label)
    for i, h in enumerate(headers):
        hx = normalize_text(h)
        for c in cands:
            if c in hx:
                return i
    return None

def fetch_cell_symbol(page, row_label, date_label):
    # テーブル描画待ち（複数パターンに耐性）
    selectors = ["table", "div[role='table']", "div:has-text('予約カレンダー')"]
    ok = False
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=TIMEOUT_MS)
            ok = True
            break
        except PlaywrightTimeoutError:
            continue
    if not ok:
        raise RuntimeError("テーブルの描画を確認できませんでした。")

    # ヘッダ抽出
    header_cells = page.query_selector_all("thead th") or page.query_selector_all("table tr:nth-child(1) th, table tr:nth-child(1) td")
    headers = [normalize_text(h.inner_text()) for h in header_cells]
    col_idx = pick_column_index(headers, date_label)
    if col_idx is None:
        raise RuntimeError(f"ヘッダーから日付 '{date_label}' の列が見つかりませんでした。")

    # 行抽出
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

    # 対象セル抽出
    data_cells = target_row.query_selector_all("td")
    if data_cells:
        j = col_idx - 1 if len(data_cells) + 1 == len(headers) else col_idx
        if 0 <= j < len(data_cells):
            return normalize_text(data_cells[j].inner_text())
        raise RuntimeError(f"列インデックス計算に失敗しました (j={j}, len={len(data_cells)}).")

    all_cells = target_row.query_selector_all("th,td")
    if 0 <= col_idx < len(all_cells):
        return normalize_text(all_cells[col_idx].inner_text())

    raise RuntimeError("対象セルを取得できませんでした。")

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="ja-JP")
        page = context.new_page()
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(5000)  # JS描画の猶予

        # 結果: dict[(row, date)] = symbol
        results = {}
        for row in TARGET_ROWS:
            for d in TARGET_DATE_LABELS:
                try:
                    symbol = fetch_cell_symbol(page, row, d)
                    results[(row, d)] = symbol
                except Exception as e:
                    results[(row, d)] = f"ERROR: {e}"

        browser.close()

    # 通知文生成（空きありが1つでもあればBroadcast）
    alerts = []
    errors = []
    for (row, d), symbol in results.items():
        if symbol in ("〇", "○", "△"):
            alerts.append(f"{d} の {row}: {symbol}")
        elif isinstance(symbol, str) and symbol.startswith("ERROR"):
            errors.append(f"{d} の {row}: {symbol}")

    if alerts or errors:
        lines = ["ふもとっぱら空き検知(Messaging API版)"]
        lines.append("対象日: " + ", ".join(TARGET_DATE_LABELS))
        if alerts:
            lines.append("【空きあり】")
            for a in alerts:
                lines.append("・" + a)
        if errors:
            lines.append("【取得エラー】(参考)")
            for e in errors:
                lines.append("・" + e)
        lines.append(f"確認: {PAGE_URL}")
        msg = "\n".join(lines)
        print(msg)
        try:
            line_broadcast(msg)
        except Exception as e:
            print(f"LINE通知失敗: {e}", file=sys.stderr)
    else:
        # 完全に空き無し＆エラー無しの場合はログだけ
        print("空き無し: " + str(results))

if __name__ == "__main__":
    main()
