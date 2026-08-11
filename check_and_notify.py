"""
Проверяет все машины в базе Supabase и шлёт уведомление в Telegram,
если до конца злиття осталось от 6 до 10 минут (окно с запасом,
чтобы не пропустить момент даже при небольших задержках проверки).
Запускается по расписанию через GitHub Actions (см. .github/workflows/check.yml).
"""

import os
import sys
import math
import time
import urllib.request
import urllib.error
import json

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

WARNING_WINDOW_MAX_SECONDS = 10 * 60  # верхняя граница окна предупреждения
WARNING_WINDOW_MIN_SECONDS = 6 * 60   # нижняя граница окна предупреждения
RESET_THRESHOLD_SECONDS = WARNING_WINDOW_MAX_SECONDS  # выше этого — сбрасываем флаг


def http_request(url, method="GET", headers=None, body=None):
    headers = headers or {}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode("utf-8", errors="ignore")


def fetch_machines():
    url = f"{SUPABASE_URL}/rest/v1/machines?select=id,data"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
    }
    status, data = http_request(url, headers=headers)
    if status != 200:
        print(f"Ошибка загрузки машин: {status} {data}", file=sys.stderr)
        return []
    return data


def patch_machine_data(row_id, new_data):
    url = f"{SUPABASE_URL}/rest/v1/machines?id=eq.{row_id}"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    status, _ = http_request(url, method="PATCH", headers=headers, body={"data": new_data})
    if status not in (200, 204):
        print(f"Ошибка обновления машины {row_id}: {status}", file=sys.stderr)


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    body = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    headers = {"Content-Type": "application/json"}
    status, resp = http_request(url, method="POST", headers=headers, body=body)
    if status != 200:
        print(f"Ошибка отправки в Telegram: {status} {resp}", file=sys.stderr)


def compute_remaining_seconds(m):
    """Та же формула, что и в приложении (index.html)."""
    pallet_num = m.get("palletNum", 1)
    per_pallet = m.get("perPallet", 1)
    on_current = m.get("onCurrent", 0)
    total = m.get("total", 0)
    cycle = m.get("cycle", 1)
    entered_at_ms = m.get("enteredAt", 0)

    full_pallets = max(pallet_num - 1, 0)
    done_at_entry = (full_pallets * per_pallet) + on_current

    remaining_seconds_at_entry = max(total - done_at_entry, 0) * cycle
    finish_at_ms = entered_at_ms + remaining_seconds_at_entry * 1000

    now_ms = time.time() * 1000
    remaining_seconds = max((finish_at_ms - now_ms) / 1000, 0)
    return remaining_seconds


def main():
    rows = fetch_machines()
    for row in rows:
        row_id = row["id"]
        data = row.get("data") or {}
        machine_name = data.get("machine", f"#{row_id}")

        remaining_seconds = compute_remaining_seconds(data)
        already_notified = data.get("notified5min", False)

        in_warning_window = WARNING_WINDOW_MIN_SECONDS <= remaining_seconds <= WARNING_WINDOW_MAX_SECONDS

        if in_warning_window:
            if not already_notified:
                minutes = math.ceil(remaining_seconds / 60)
                send_telegram_message(
                    f"⚠️ Машина {machine_name}: злиття закінчується через ~{minutes} хв. "
                    f"Час поміняти етикетку!"
                )
                data["notified5min"] = True
                patch_machine_data(row_id, data)
        elif remaining_seconds > RESET_THRESHOLD_SECONDS or remaining_seconds <= 0:
            # Если запас снова стал большим (например данные обновили) или злиття уже
            # закончилось — сбрасываем флаг, чтобы уведомление могло сработать заново
            # для нового цикла.
            if already_notified:
                data["notified5min"] = False
                patch_machine_data(row_id, data)


if __name__ == "__main__":
    main()
