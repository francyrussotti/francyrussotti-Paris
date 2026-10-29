import requests, re, json
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
BASE = "https://careers.ledger.com"
r = requests.get(f"{BASE}/job-search-results/", headers=UA, timeout=30)
t = r.text
print("pagina:", r.status_code, r.url, len(t))
print("indizi piattaforma:", [k for k in ("phApp", "phenom", "refineSearch", "greenhouse", "lever.co", "ashbyhq", "workday", "smartrecruiters", "teamtailor", "welcomekit") if k.lower() in t.lower()])
for k in ("locale", "country", "pageId", "siteType", "lang", "refNum"):
    print(k, re.findall(rf'"{k}"\s*:\s*"([^"]{{0,40}})"', t)[:3])
for slug in ("ledger", "ledgerhq"):
    for nome, u in (("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"), ("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json"), ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}")):
        try:
            x = requests.get(u, timeout=15); print(nome, slug, x.status_code, len(x.text))
        except Exception as e: print(nome, slug, "ERR", e)
loc = (re.findall(r'"locale"\s*:\s*"([^"]+)"', t) or ["en_global"])[0]
country = (re.findall(r'"country"\s*:\s*"([^"]+)"', t) or ["global"])[0]
pageid = (re.findall(r'"pageId"\s*:\s*"([^"]+)"', t) or ["page1"])[0]
body = {"lang": loc, "deviceType": "desktop", "country": country, "pageName": "search-results", "ddoKey": "refineSearch",
        "sortBy": "", "subsearch": "", "from": 0, "jobs": True, "counts": True, "all_fields": ["category", "country", "state", "city", "type"],
        "size": 1000, "clearAll": False, "jdsource": "facets", "isSliderEnable": False, "pageId": pageid, "siteType": "external",
        "keywords": "", "global": True, "selected_fields": {}, "locationData": {}}
w = requests.post(f"{BASE}/widgets", json=body, headers={**UA, "Content-Type": "application/json", "Accept": "application/json"}, timeout=30)
print("widgets:", w.status_code, w.headers.get("content-type"), len(w.text))
try:
    d = w.json()["refineSearch"]; jobs = d["data"]["jobs"]
    print("totalHits", d.get("totalHits"), "jobs", len(jobs))
    print("chiavi:", sorted(jobs[0].keys()))
    print(json.dumps({k: jobs[0].get(k) for k in ("title", "city", "country", "cityState", "location", "multi_location", "applyUrl", "url", "postedDate", "dateCreated", "jobId", "descriptionTeaser")}, ensure_ascii=False)[:1200])
    par = [j for j in jobs if "paris" in json.dumps(j).lower()]
    print("con Paris:", len(par)); [print("  ", j.get("title"), "|", j.get("cityState") or j.get("city"), "|", j.get("multi_location")) for j in par[:12]]
except Exception as e:
    print("ERR json", e, w.text[:500])
