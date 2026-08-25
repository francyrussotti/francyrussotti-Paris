# ============================================================
#  🔎 AUTO-DISCOVERY — trova automaticamente aziende parigine
#  con board Greenhouse/Lever attivo, senza selezione manuale.
#
#  Fonti di partenza (elenchi pubblici, nessuna azienda scelta da te o da me):
#  - Station F: directory pubblica delle startup ospitate
#  - La French Tech: elenco ufficiale aziende certificate (pubblico)
#  - Y Combinator: directory pubblica, filtrata per location "Paris"/"France"
#
#  Output: aggiorna GREENHOUSE_SLUGS / LEVER_SLUGS in un file config
#  separato, che paris_job_newsletter.py importa.
# ============================================================

import requests
import re
import time
import json
import urllib.robotparser

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False
    print("⚠️  Playwright non trovato — StartupMap (SPA) verrà saltato")

HEADERS = {"User-Agent": "paris-job-newsletter-discovery/1.0 (uso personale)"}

# ------------------------------------------------------------
# 0) CONTROLLO ROBOTS.TXT — verifica live prima di ogni scraping HTML.
#    Se il sito vieta l'accesso al path che vogliamo leggere, la fonte
#    viene saltata automaticamente (nessuno scraping forzato).
# ------------------------------------------------------------

def path_permesso(base_url: str, path: str) -> bool:
    try:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{base_url.rstrip('/')}/robots.txt")
        rp.read()
        return rp.can_fetch(HEADERS["User-Agent"], f"{base_url.rstrip('/')}{path}")
    except Exception as e:
        # Se non riusciamo a leggere il robots.txt, per prudenza NON scraping:
        # meglio saltare la fonte che rischiare di violare regole non lette.
        print(f"⚠️  Impossibile leggere robots.txt di {base_url}: {e} — fonte saltata per prudenza")
        return False

# ------------------------------------------------------------
# 1) SORGENTI DI NOMI AZIENDE (pubbliche, non filtrate da te)
# ------------------------------------------------------------

def nomi_da_stationf() -> list[str]:
    """Station F pubblica un elenco di startup ospitate. L'endpoint esatto
    cambia nel tempo — qui uso la pagina pubblica 'companies' come sorgente
    HTML; se cambia struttura, va aggiornato il selettore."""
    if not path_permesso("https://stationf.co", "/companies"):
        return []
    nomi = []
    try:
        r = requests.get("https://stationf.co/companies", headers=HEADERS, timeout=15)
        r.raise_for_status()
        # Estrazione grezza di nomi da eventuale JSON embeddato nella pagina
        # (molte SPA moderne includono i dati in un tag <script id="__NEXT_DATA__">)
        nomi = re.findall(r'"name":"([^"]{2,60})"', r.text)
    except Exception as e:
        print(f"⚠️  Station F non raggiungibile: {e}")
    return list(dict.fromkeys(nomi))  # dedup mantenendo l'ordine


def nomi_da_startupmap() -> list[str]:
    """StartupMap (startupmap.one): 4.000+ startup europee con stato hiring
    live, filtrabile per città. Il sito è una SPA (contenuto via JS): senza
    Playwright non è leggibile con requests semplice. Prima di ogni lettura
    verifichiamo il robots.txt live — se non è verificabile o è vietato,
    la fonte viene saltata automaticamente, nessuna eccezione manuale.

    NB: il sito stesso si descrive come 'Curated, not scraped' — un segnale
    che tengono al controllo di come i dati vengono ridistribuiti. Se in
    futuro pubblicano un'API ufficiale, va preferita a questo scraping."""
    if not PLAYWRIGHT_OK:
        print("⚠️  StartupMap richiede Playwright (SPA) — fonte saltata, Playwright non disponibile")
        return []
    if not path_permesso("https://startupmap.one", "/"):
        return []

    nomi = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = browser.new_context(user_agent=HEADERS["User-Agent"], locale="en-US")
            page = ctx.new_page()
            # Filtro città Paris via query param, se supportato dal sito
            page.goto("https://startupmap.one/?city=Paris", wait_until="networkidle", timeout=45_000)
            page.wait_for_timeout(4000)
            html = page.content()
            browser.close()
        nomi = re.findall(r'"name":"([^"]{2,60})"', html)
    except Exception as e:
        print(f"⚠️  StartupMap non raggiungibile: {e}")
    return list(dict.fromkeys(nomi))


def nomi_da_ycombinator() -> list[str]:
    """YC pubblica un'API di ricerca sul suo sito (usata dal loro stesso
    frontend). Filtriamo per location contenente 'Paris' o 'France'."""
    nomi = []
    try:
        url = "https://www.ycombinator.com/companies/api/companies?query=&batch=&location=France"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            data = r.json()
            for c in data if isinstance(data, list) else data.get("companies", []):
                nome = c.get("name")
                if nome:
                    nomi.append(nome)
    except Exception as e:
        print(f"⚠️  YC directory non raggiungibile o formato cambiato: {e}")
    return nomi


def nomi_manuali_fallback() -> list[str]:
    """Fallback minimo se le fonti automatiche non rispondono: una manciata
    di nomi noti per bootstrap iniziale. Non è una lista 'target' — è solo
    un punto di partenza nel caso le fonti dinamiche falliscano."""
    return ["Mistral AI", "Payflows", "Alan", "Qonto", "Swile", "Pennylane", "Spendesk"]


def raccogli_nomi_aziende() -> list[str]:
    tutti = []
    tutti += nomi_da_stationf()
    time.sleep(1)
    tutti += nomi_da_startupmap()
    time.sleep(1)
    tutti += nomi_da_ycombinator()
    if not tutti:
        print("⚠️  Nessuna fonte dinamica ha risposto, uso fallback minimo")
        tutti = nomi_manuali_fallback()
    return list(dict.fromkeys(tutti))  # dedup

# ------------------------------------------------------------
# 2) GENERAZIONE VARIANTI SLUG DA UN NOME AZIENDA
# ------------------------------------------------------------

def genera_varianti_slug(nome: str) -> list[str]:
    base = nome.lower().strip()
    base = re.sub(r"[^\w\s-]", "", base)  # rimuove punteggiatura tipo '.', ','
    senza_spazi = re.sub(r"\s+", "", base)
    con_trattini = re.sub(r"\s+", "-", base)
    varianti = {senza_spazi, con_trattini}
    # varianti senza suffissi comuni tipo 'ai', 'inc', 'sas'
    for suffisso in [" ai", " inc", " sas", " sa", " labs"]:
        if base.endswith(suffisso):
            ridotto = base[: -len(suffisso)].strip()
            varianti.add(re.sub(r"\s+", "", ridotto))
            varianti.add(re.sub(r"\s+", "-", ridotto))
    return [v for v in varianti if v]

# ------------------------------------------------------------
# 3) VERIFICA SE UNO SLUG HA UN BOARD ATTIVO
# ------------------------------------------------------------

def verifica_greenhouse(slug: str) -> bool:
    try:
        r = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            headers=HEADERS, timeout=8
        )
        return r.status_code == 200 and len(r.json().get("jobs", [])) > 0
    except Exception:
        return False

def verifica_lever(slug: str) -> bool:
    try:
        r = requests.get(
            f"https://api.lever.co/v0/postings/{slug}?mode=json",
            headers=HEADERS, timeout=8
        )
        return r.status_code == 200 and len(r.json()) > 0
    except Exception:
        return False

def verifica_ashby(slug: str) -> bool:
    try:
        r = requests.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            headers=HEADERS, timeout=8
        )
        return r.status_code == 200 and len(r.json().get("jobs", [])) > 0
    except Exception:
        return False

def verifica_smartrecruiters(company_id: str) -> bool:
    try:
        r = requests.get(
            f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings",
            headers=HEADERS, timeout=8
        )
        return r.status_code == 200 and r.json().get("totalFound", 0) > 0
    except Exception:
        return False

# ------------------------------------------------------------
# 4) ORCHESTRAZIONE
# ------------------------------------------------------------

def scopri_slug_validi(nomi_aziende: list[str]) -> dict:
    trovati = {"greenhouse": [], "lever": [], "ashby": [], "smartrecruiters": []}
    verifiche = {
        "greenhouse": verifica_greenhouse,
        "lever": verifica_lever,
        "ashby": verifica_ashby,
        "smartrecruiters": verifica_smartrecruiters,
    }

    for nome in nomi_aziende:
        for fonte, funzione_verifica in verifiche.items():
            for slug in genera_varianti_slug(nome):
                if funzione_verifica(slug):
                    print(f"✅ {fonte.capitalize()} trovato: {nome} -> {slug}")
                    trovati[fonte].append(slug)
                    break  # una variante valida basta, passa alla prossima fonte
                time.sleep(0.3)

    return {k: sorted(set(v)) for k, v in trovati.items()}


def main():
    print("🔎 Raccolgo nomi aziende da fonti pubbliche...")
    nomi = raccogli_nomi_aziende()
    print(f"   → {len(nomi)} nomi raccolti\n")

    print("🔎 Verifico quali hanno board Greenhouse/Lever/Ashby/SmartRecruiters attivo...")
    slug_validi = scopri_slug_validi(nomi)

    with open("discovered_slugs.json", "w", encoding="utf-8") as f:
        json.dump(slug_validi, f, indent=2, ensure_ascii=False)

    totale = sum(len(v) for v in slug_validi.values())
    print(f"\n📊 Risultato: {totale} aziende trovate "
          f"({len(slug_validi['greenhouse'])} Greenhouse, {len(slug_validi['lever'])} Lever, "
          f"{len(slug_validi['ashby'])} Ashby, {len(slug_validi['smartrecruiters'])} SmartRecruiters) "
          f"→ salvati in discovered_slugs.json")
    print("   paris_job_newsletter.py li legge automaticamente, nessuna azione richiesta.")


if __name__ == "__main__":
    main()
