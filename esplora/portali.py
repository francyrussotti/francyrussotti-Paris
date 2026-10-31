"""Prova temporanea 2: struttura del feed pubblico Teamtailor (jobs.rss)."""
import re, requests
H = {"User-Agent": "Mozilla/5.0 (compatible; paris-job-newsletter)"}
for slug in ["skello", "pasqal", "homa"]:
    r = requests.get(f"https://{slug}.teamtailor.com/jobs.rss", headers=H, timeout=15)
    print(f"\n===== {slug}: {r.status_code} {r.headers.get('content-type')} {len(r.text)}")
    print(r.text[:600])
    items = re.findall(r"<item>(.*?)</item>", r.text, re.S)
    print("items:", len(items))
    if items:
        print("--- primo item (senza descrizione):")
        print(re.sub(r"<description>.*?</description>", "<description>…</description>", items[0], flags=re.S)[:2500])
