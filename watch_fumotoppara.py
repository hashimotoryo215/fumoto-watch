# -*- coding: utf-8 -*-
"""
ふもとっぱら 予約カレンダー監視（Messaging API 版）
- 複数日チェック: 環境変数 TARGET_DATE_LABELS（例 "11/1,11/2"）
- 行指定        : 環境変数 TARGET_ROWS（例 "キャンプ宿泊,キャンプ日帰り"）
- 常時通知      : 環境変数 ALWAYS_NOTIFY を "1"/"true"/"yes" にすると空き無しでも通知
- LINE送信      : 環境変数 LINE_CHANNEL_ACCESS_TOKEN（Messaging APIのチャネルアクセストークン）

GitHub Actions での実行を想定。
"""

import os
import sys
import re
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ========= 設定（環境変数） =========
PAGE_URL = "https://reserve.fumotoppara.net/reserved/reserved-calendar-list"

# 複数日: "11/1,11/2" など。未設定なら単一日の TARGET_DATE_LABEL を見る
_labels_env = os.getenv("TARGET_DATE_LABELS", "").strip()
if _labels_env:
    TARGET_DATE_LABELS = [s.strip() for s in _labels_env.split(",") if s.strip()]
else:
    TARGET_DATE_LABELS = [os.getenv("TARGET_DATE_LABEL", "11/1").strip()]

TARGET_ROWS = [s.strip() for s in os.getenv("TARGET_ROWS", "キャンプ宿泊,キャンプ日帰り").split(",") if s.strip()]
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "60000"))
ALWAYS_NOTIFY = os.getenv("ALWAYS_NOTIFY", "").lower() in ("1", "true", "yes")

# ========= 通知 =========
def line_broadcast(message: str):
    """LINE Messaging API の Broadcast で通知（友だち=自分だけなら実質自分宛）。"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("WARN: LINE_CHANNEL_ACCESS_TOKEN 未設定のため通知しません。")
        return
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {"messages": [{"type": "text", "text": message}]}
    res = requests.post("https://api.line.me/v2/bot/message/broadcast", headers=headers, json=data, timeout=30)
    try:
        res.raise_for_status()
    except Exception:
        print("LINE Broadcast 失敗:", res.text, file=sys.stderr)
        raise

# ========= 解析ユーティリティ =========
def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def _date_candidates(label: str):
    """
    "11/1" と "11/01"、"11/1(日)" のような表記ゆれに部分一致で耐える。
    """
    label = label.strip()
    if "/" in label:
        mon, day = label.split("/", 1)
        day_nz = day.lstrip("0") or "0"
        day_z2 = day if len(day) == 2 else day.zfill(2)
        return {f"{mon}/{day_nz}", f"{mon}/{day_z2}"}
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
    # テーブル描画待ち（いくつかの候補セレクタで待つ）
    for sel in ("table", "div[role='table']", "div:has-text('予約カレンダー')"):
        try:
            page.wait_for_selector(sel, timeout=TIMEOUT_MS)
            break
        except PlaywrightTimeoutError:
            continue

    # ヘッダー取得
    header_cells = page.query_selector_all("thead th") or page.query_selector_all("table tr:nth-child(1) th, table tr:nth-child(1) td")
    headers = [normalize_text(h.inner_text()) for h in header_cells]
    col_idx = pick_column_index(headers, date_label)
    if col_idx is None:
        raise RuntimeError(f"ヘッダーから日付 '{date_label}' の列が見つかりませんでした。")

    # 行取得（先頭セルに行名が含まれる行を探す）
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

    # 対象セル（列）取得
    data_cells = target_row.query_selector_all("td")
    if data_cells:
        # 行頭がthでデータがtdの場合に備えてオフセット調整
        j = col_idx - 1 if len(data_cells) + 1 == len(headers) else col_idx
        if 0 <= j < len(data_cells):
            return normalize_text(data_cells[j].inner_text())
        raise RuntimeError(f"列インデックス計算に失敗しました (j={j}, len={len(data_cells)}).")

    # フォールバック：行内のth/td混在配列で直接参照
    cells = target_row.query_selector_all("th,td")
    if 0 <= col_idx < len(cells):
        return normalize_text(cells[col_idx].inner_text())

    raise RuntimeError("対象セルを取得できませんでした。")

# ========= メイン =========
def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="ja-JP")
        page = context.new_page()
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(5000)  # JS描画の猶予

        results = {}  # {(row, date): symbol}
        for row in TARGET_ROWS:
            for d in TARGET_DATE_LABELS:
                try:
                    symbol = fetch_cell_symbol(page, row, d)
                    results[(row, d)] = symbol
                except Exception as e:
                    results[(row, d)] = f"ERROR: {e}"
        browser.close()

    # メッセージ生成
    alerts, errors = [], []
    for (row, d), symbol in results.items():
        if symbol in ("〇", "○", "△"):
            alerts.append(f"{d} の {row}: {symbol}")
        elif isinstance(symbol, str) and symbol.startswith("ERROR"):
            errors.append(f"{d} の {row}: {symbol}")

    if alerts or errors:
        lines = ["ふもとっぱら空き検知(Messaging API版)", "対象日: " + ", ".join(TARGET_DATE_LABELS)]
        if alerts:
            lines.append("【空きあり】")
            lines += [f"・{a}" for a in alerts]
        if errors:
            lines.append("【取得エラー】(参考)")
            lines += [f"・{e}" for e in errors]
        lines.append(f"確認: {PAGE_URL}")
        msg = "\n".join(lines)
        print(msg)
        try:
            line_broadcast(msg)
        except Exception as e:
            print(f"LINE通知失敗: {e}", file=sys.stderr)
    else:
        # 完全に空き無し
        print("空き無し: " + str(results))
        if ALWAYS_NOTIFY:
            msg = "\n".join([
                "ふもとっぱら空き検知(Messaging API版)",
                "対象日: " + ", ".join(TARGET_DATE_LABELS),
                "【空き無し】",
                f"確認: {PAGE_URL}",
            ])
            try:
                line_broadcast(msg)
            except Exception as e:
                print(f"LINE通知失敗: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
