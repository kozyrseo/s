"""
KOZYR — диагностика доступа к Google Search Console.
Показывает:
  1. email service account (чтобы сверить с тем, что добавлен в GSC)
  2. СПИСОК всех property, к которым у service account есть доступ (sites.list)
  3. текущее значение GSC_SITE_URL и совпадает ли оно с доступными property
  4. пробный запрос данных по совпавшему property (проверка, что данные идут)

Только чтение. Ничего не меняет.
Запуск: python check_gsc.py
"""
import os
import json
import sys

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
except ImportError:
    print("❌ Нет библиотек. В workflow добавь: pip install google-api-python-client")
    sys.exit(1)

GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

def main():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        print("❌ GOOGLE_SERVICE_ACCOUNT_JSON не задан")
        sys.exit(1)

    try:
        info = json.loads(raw)
    except Exception as e:
        print(f"❌ JSON креды не парсятся: {e}")
        sys.exit(1)

    # 1. Кто мы
    sa_email = info.get("client_email", "?")
    project = info.get("project_id", "?")
    print("=" * 60)
    print("1. SERVICE ACCOUNT")
    print("=" * 60)
    print(f"   email:   {sa_email}")
    print(f"   project: {project}")
    print()
    print("   ⚠️  Этот email ДОЛЖЕН быть в Search Console →")
    print("      Settings → Users and permissions (роль Full)")
    print()

    # 2. Строим клиент
    try:
        creds = Credentials.from_service_account_info(info, scopes=GSC_SCOPES)
        service = build("searchconsole", "v1", credentials=creds)
    except Exception as e:
        print(f"❌ Не удалось создать клиент GSC: {e}")
        sys.exit(1)

    # 3. Список доступных property
    print("=" * 60)
    print("2. PROPERTY, ДОСТУПНЫЕ SERVICE ACCOUNT (sites.list)")
    print("=" * 60)
    try:
        resp = service.sites().list().execute()
        sites = resp.get("siteEntry", [])
    except Exception as e:
        print(f"❌ sites.list() упал: {e}")
        print("   Вероятно, service account вообще не добавлен ни в одно property,")
        print("   или Search Console API выключен.")
        sys.exit(1)

    if not sites:
        print("   ⚠️  ПУСТО! Service account не имеет доступа НИ К ОДНОМУ property.")
        print("   → Зайди в Search Console нужным property → Settings →")
        print("     Users and permissions → Add user → вставь email выше → Full.")
        sys.exit(0)

    print(f"   Найдено property: {len(sites)}\n")
    available_urls = []
    for s in sites:
        url = s.get("siteUrl", "?")
        level = s.get("permissionLevel", "?")
        available_urls.append(url)
        print(f"   • {url}")
        print(f"     доступ: {level}")
    print()

    # 4. Сверка с секретом
    print("=" * 60)
    print("3. СВЕРКА С GSC_SITE_URL")
    print("=" * 60)
    secret_url = os.environ.get("GSC_SITE_URL", "").strip()
    print(f"   GSC_SITE_URL сейчас = '{secret_url}'")
    print()
    if not secret_url:
        print("   ⚠️  Секрет пустой!")
    elif secret_url in available_urls:
        print(f"   ✅ СОВПАДЕНИЕ! '{secret_url}' есть в списке доступных.")
        print("      Значит формат верный — данные должны идти.")
    else:
        print(f"   ❌ '{secret_url}' НЕ найден среди доступных property!")
        print()
        print("   👉 ВОТ ПРАВИЛЬНОЕ значение для секрета — скопируй одно из:")
        for url in available_urls:
            print(f"        {url}")
        print()
        print("   Впиши его РОВНО как показано в GitHub → Secrets → GSC_SITE_URL")

    # 5. Пробный запрос по совпавшему (или первому) property
    print()
    print("=" * 60)
    print("4. ПРОБНЫЙ ЗАПРОС ДАННЫХ")
    print("=" * 60)
    test_url = secret_url if secret_url in available_urls else (available_urls[0] if available_urls else None)
    if not test_url:
        print("   Нечего тестировать.")
        return
    print(f"   Запрашиваю данные по: {test_url}")
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=60)
    try:
        data = service.searchanalytics().query(
            siteUrl=test_url,
            body={
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "dimensions": ["query"],
                "rowLimit": 5,
            },
        ).execute()
        rows = data.get("rows", [])
        if rows:
            total_clicks = sum(r.get("clicks", 0) for r in rows)
            total_impr = sum(r.get("impressions", 0) for r in rows)
            print(f"   ✅ ДАННЫЕ ИДУТ! Топ-5 запросов (показы {total_impr}, клики {total_clicks}):")
            for r in rows:
                q = r.get("keys", ["?"])[0]
                print(f"      • {q} — {r.get('impressions',0)} показов, {r.get('clicks',0)} кликов")
        else:
            print("   ⚠️  Запрос прошёл, но данных нет (0 строк).")
            print("      Возможно, для этого property ещё нет статистики за период.")
    except Exception as e:
        print(f"   ❌ Запрос упал: {e}")

if __name__ == "__main__":
    main()
