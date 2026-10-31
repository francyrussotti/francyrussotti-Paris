"""Prova temporanea: quante aziende WTTJ hanno un portale di candidatura con
API pubblica (Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workable,
Personio, Teamtailor)? Solo API pubbliche dei portali, nessuna pagina WTTJ."""
import json, re, sys, collections
from concurrent.futures import ThreadPoolExecutor
import requests

H = {"User-Agent": "Mozilla/5.0 (compatible; paris-job-newsletter)"}
slugs = json.load(open("discovered_slugs.json"))["wttj"]
noti = json.load(open("discovered_slugs.json"))

def varianti(s):
    v = [s, s.replace("-", "")]
    for suf in ["-sas", "-sa", "-inc", "-ai", "-io", "-com", "-fr", "-group", "-france", "-1", "-2"]:
        if s.endswith(suf): v += [s[:-len(suf)], s[:-len(suf)].replace("-", "")]
    return list(dict.fromkeys(x for x in v if x))

def n_recruitee(x):
    r = requests.get(f"https://{x}.recruitee.com/api/offers/", headers=H, timeout=8)
    return len(r.json().get("offers", [])) if r.status_code == 200 else None
def n_workable(x):
    r = requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{x}", headers=H, timeout=8)
    return len(r.json().get("jobs", [])) if r.status_code == 200 else None
def n_personio(x):
    r = requests.get(f"https://{x}.jobs.personio.de/xml", headers=H, timeout=8)
    return r.text.count("<position>") if r.status_code == 200 and "<workzag-jobs" in r.text else None
def n_teamtailor(x):
    r = requests.get(f"https://{x}.teamtailor.com/jobs.rss", headers=H, timeout=8)
    return r.text.count("<item>") if r.status_code == 200 and "<rss" in r.text else None
def n_greenhouse(x):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{x}/jobs", headers=H, timeout=8)
    return len(r.json().get("jobs", [])) if r.status_code == 200 else None
def n_lever(x):
    r = requests.get(f"https://api.lever.co/v0/postings/{x}?mode=json", headers=H, timeout=8)
    return len(r.json()) if r.status_code == 200 and isinstance(r.json(), list) else None
def n_ashby(x):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{x}", headers=H, timeout=8)
    return len(r.json().get("jobs", [])) if r.status_code == 200 else None

PORTALI = {"recruitee": n_recruitee, "workable": n_workable, "personio": n_personio,
           "teamtailor": n_teamtailor, "greenhouse": n_greenhouse, "lever": n_lever, "ashby": n_ashby}

def prova(slug):
    out = {}
    for nome, f in PORTALI.items():
        for v in varianti(slug):
            try:
                n = f(v)
            except Exception:
                n = None
            if n:
                out[nome] = (v, n); break
    return slug, out

with ThreadPoolExecutor(16) as ex:
    ris = list(ex.map(prova, slugs))

cont, annunci, gia = collections.Counter(), collections.Counter(), 0
for slug, out in ris:
    for nome, (v, n) in out.items():
        nuovo = nome not in ("greenhouse", "lever", "ashby") or v not in noti.get(nome, [])
        cont[nome] += 1; annunci[nome] += n
        if nuovo: cont[nome + " (nuovi)"] += 1
    if out: gia += 1
print(f"aziende WTTJ: {len(slugs)}, con almeno un portale: {gia}")
for k in sorted(cont): print(f"  {k}: {cont[k]} aziende" + (f", {annunci[k]} annunci" if k in annunci else ""))
print("\nESEMPI")
for slug, out in ris:
    if out: print(" ", slug, json.dumps(out))
