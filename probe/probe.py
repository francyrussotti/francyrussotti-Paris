import requests, paris_job_newsletter as m
d = requests.get("https://api.ashbyhq.com/posting-api/job-board/ledger", timeout=20).json()["jobs"]
par = [j for j in d if m.is_paris_location(", ".join([j.get("location","")] + [(x or {}).get("location","") for x in (j.get("secondaryLocations") or [])]))]
print(f"LEDGER: {len(d)} annunci totali, {len(par)} a Parigi/Francia")
m.ASHBY_SLUGS = ["ledger"]
r = m.scrape_ashby()
print(f"LEDGER compatibili: {len(r)}")
for o in r: print("  OK", o["titolo"], "|", o["location"], "|", o["url"])
esclusi = {j["title"] for j in par} - {o["titolo"] for o in r}
print("ESCLUSI dai filtri:", sorted(esclusi)[:40])
