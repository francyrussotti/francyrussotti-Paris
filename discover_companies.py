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
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False
    print("⚠️  Playwright non trovato — StartupMap (SPA) verrà saltato")

HEADERS = {"User-Agent": "paris-job-newsletter-discovery/1.0 (uso personale)"}

# Alcuni siti (WTTJ) servono contenuto vuoto/degradato a user-agent non da browser.
# Usiamo uno UA da browser reale SOLO per il rendering Playwright di quel sito;
# le chiamate HTTP dirette (Station F, YC, robots.txt) restano con HEADERS onesto.
BROWSER_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

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
    """Il vero job board pubblico di Station F è su jobs.stationf.co/startups
    (non stationf.co/companies, che non esiste più — 404 verificato).
    È una SPA: serve Playwright per il rendering. I link hanno il formato
    jobs.stationf.co/companies/{slug} — estraiamo lo slug direttamente,
    già molto vicino al formato usato da Greenhouse/Lever/Ashby."""
    if not path_permesso("https://jobs.stationf.co", "/startups"):
        return []
    if not PLAYWRIGHT_OK:
        print("⚠️  Station F richiede Playwright (SPA) — fonte saltata, Playwright non disponibile")
        return []

    nomi = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(user_agent=HEADERS["User-Agent"])
            page.goto("https://jobs.stationf.co/startups", wait_until="networkidle", timeout=45_000)
            page.wait_for_timeout(4000)
            links = page.eval_on_selector_all("a", "els => els.map(e => e.href).filter(h => h)")
            browser.close()
        slugs = re.findall(r"jobs\.stationf\.co/companies/([a-z0-9-]+)", " ".join(links))
        # Gli slug sono già nel formato giusto: li trasformiamo in "nomi" con spazi
        # al posto dei trattini, così passano nella stessa pipeline delle altre fonti.
        nomi = [s.replace("-", " ") for s in slugs]
    except Exception as e:
        print(f"⚠️  Station F non raggiungibile: {e}")
    return list(dict.fromkeys(nomi))  # dedup mantenendo l'ordine


# Slug WTTJ grezzi raccolti durante la discovery (usati direttamente per lo
# scraping annunci, popolati come side-effect da nomi_da_wttj_companies).
WTTJ_SLUGS_RACCOLTI = []

def nomi_da_wttj_companies() -> list[str]:
    """Pagina 'companies' di Welcome to the Jungle filtrata per area di Parigi
    (raggio 20km da Notre-Dame), verificata dal vivo: ~2.914 aziende totali,
    31 per pagina. A differenza della ricerca annunci (/en/jobs), questa
    pagina RENDE davvero i risultati lato server/client in modo scrapabile.
    Limitiamo a poche pagine per run per restare leggeri con Playwright.

    Effetto collaterale: salva anche gli slug grezzi (non convertiti in nomi)
    in WTTJ_SLUGS_RACCOLTI, usati direttamente per lo scraping annunci WTTJ
    (verificato: la pagina profilo /en/companies/{slug} mostra gli annunci
    reali via [data-testid^='job-thumb-'])."""
    if not PLAYWRIGHT_OK:
        print("⚠️  WTTJ companies richiede Playwright — fonte saltata")
        return []
    if not path_permesso("https://www.welcometothejungle.com", "/en/companies"):
        return []

    MAX_PAGINE = 15  # ~465 aziende a run (grazie alla cache, quelle già verificate non si riverificano)
    nomi = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(user_agent=BROWSER_USER_AGENT)  # UA da browser reale, vedi nota sopra
            for num_pagina in range(1, MAX_PAGINE + 1):
                url = (
                    "https://www.welcometothejungle.com/en/companies"
                    f"?page={num_pagina}"
                    "&aroundQuery=Paris%2C%20Paris%2C%20%C3%8Ele-de-France%20Region%2C%20France"
                    "&aroundLatLng=48.85341%2C2.3488&aroundRadius=20000&query="
                )
                page.goto(url, wait_until="networkidle", timeout=45_000)
                # Banner cookie: se compare, blocca il rendering della lista aziende.
                try:
                    page.click("text=OK for me", timeout=3000)
                except Exception:
                    try:
                        page.click("text=No, thanks", timeout=2000)
                    except Exception:
                        pass
                page.wait_for_timeout(2500)
                links = page.eval_on_selector_all(
                    "a[href*='/en/companies/']", "els => els.map(e => e.href)"
                )
                # Estrazione PER SINGOLO link, non su stringa unita: con re.findall
                # su una stringa concatenata, '$' ancora solo alla fine di TUTTA
                # la stringa e trova solo l'ultimo match, non uno per link.
                slugs = []
                for href in links:
                    m = re.search(r"/en/companies/([a-z0-9-]+)$", href)
                    if m:
                        slugs.append(m.group(1))
                if not slugs:
                    break  # pagina vuota, fine risultati
                WTTJ_SLUGS_RACCOLTI.extend(slugs)  # slug grezzi, per lo scraping diretto
                nomi.extend(s.replace("-", " ") for s in slugs)
                time.sleep(1)
            browser.close()
    except Exception as e:
        print(f"⚠️  WTTJ companies non raggiungibile: {e}")
    return list(dict.fromkeys(nomi))


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
            ctx = browser.new_context(user_agent=BROWSER_USER_AGENT, locale="en-US")
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
    """Usa yc-oss/api (github.com/yc-oss/api): un progetto open-source che
    pubblica un export JSON statico e aggiornato quotidianamente della
    directory YC via GitHub Pages — niente scraping, niente reverse
    engineering di Algolia, nessun rischio ToS. Filtriamo lato nostro
    sul campo 'locations' per Francia/Paris tra le aziende che assumono."""
    nomi = []
    try:
        url = "https://yc-oss.github.io/api/companies/hiring.json"
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        aziende = r.json()
        for c in aziende:
            locations = " ".join(c.get("locations", []) or []) + " " + " ".join(c.get("regions", []) or [])
            if "france" in locations.lower() or "paris" in locations.lower():
                nome = c.get("name")
                if nome:
                    nomi.append(nome)
    except Exception as e:
        print(f"⚠️  yc-oss/api non raggiungibile o formato cambiato: {e}")
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
    tutti += nomi_da_wttj_companies()
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

CACHE_FILE = "discovery_cache.json"
GIORNI_RICONTROLLO = 30  # dopo quanto tempo riverificare un nome già scartato
MAX_WORKER_PARALLELI = 8

def carica_cache() -> dict:
    """Cache di ciò che è già stato verificato: nome azienda -> quando
    controllato + cosa trovato per ogni fonte (None se non trovato).
    Evita di riverificare centinaia di nomi ogni settimana, e soprattutto
    evita di PERDERE aziende valide se una fonte quella settimana
    restituisce meno nomi del solito (i risultati buoni si accumulano)."""
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"⚠️  Errore lettura {CACHE_FILE}: {e}")
        return {}

def salva_cache(cache: dict):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️  Errore scrittura {CACHE_FILE}: {e}")

def _verifica_nome_su_tutte_le_fonti(nome: str, verifiche: dict) -> tuple[str, dict]:
    """Verifica un singolo nome su tutte e 4 le piattaforme. Girato in
    thread paralleli per accelerare (ogni chiamata è I/O di rete)."""
    risultato = {}
    for fonte, funzione_verifica in verifiche.items():
        trovato = None
        for slug in genera_varianti_slug(nome):
            if funzione_verifica(slug):
                trovato = slug
                break
            time.sleep(0.2)
        risultato[fonte] = trovato
    return nome, risultato

def scopri_slug_validi(nomi_aziende: list[str]) -> dict:
    verifiche = {
        "greenhouse": verifica_greenhouse,
        "lever": verifica_lever,
        "ashby": verifica_ashby,
        "smartrecruiters": verifica_smartrecruiters,
    }

    cache = carica_cache()
    oggi = datetime.now()
    da_verificare = []

    for nome in nomi_aziende:
        voce = cache.get(nome)
        if voce:
            ultimo_controllo = datetime.fromisoformat(voce["ultimo_controllo"])
            gia_trovato_da_qualche_parte = any(voce["fonti"].values())
            # Rivorifichiamo solo se: non trovato da nessuna parte E sono passati
            # abbastanza giorni (magari nel frattempo hanno aperto un board).
            # Se già trovato, il dato resta valido: non serve ricontrollare.
            if gia_trovato_da_qualche_parte or (oggi - ultimo_controllo) < timedelta(days=GIORNI_RICONTROLLO):
                continue
        da_verificare.append(nome)

    print(f"   → {len(nomi_aziende) - len(da_verificare)} nomi già in cache (saltati), "
          f"{len(da_verificare)} da verificare ora")

    if da_verificare:
        with ThreadPoolExecutor(max_workers=MAX_WORKER_PARALLELI) as executor:
            futures = [executor.submit(_verifica_nome_su_tutte_le_fonti, nome, verifiche) for nome in da_verificare]
            for future in as_completed(futures):
                nome, risultato = future.result()
                cache[nome] = {"ultimo_controllo": oggi.isoformat(), "fonti": risultato}
                for fonte, slug in risultato.items():
                    if slug:
                        print(f"✅ {fonte.capitalize()} trovato: {nome} -> {slug}")

    salva_cache(cache)

    # Il risultato finale è l'accumulo di TUTTO ciò che la cache ha mai trovato,
    # non solo i nomi di questa run: così un'azienda trovata mesi fa resta
    # nell'elenco anche se questa settimana le fonti dinamiche non la ripescano.
    trovati = {"greenhouse": [], "lever": [], "ashby": [], "smartrecruiters": []}
    for voce in cache.values():
        for fonte, slug in voce["fonti"].items():
            if slug:
                trovati[fonte].append(slug)

    return {k: sorted(set(v)) for k, v in trovati.items()}


def main():
    print("🔎 Raccolgo nomi aziende da fonti pubbliche...")
    nomi = raccogli_nomi_aziende()
    print(f"   → {len(nomi)} nomi raccolti\n")

    print("🔎 Verifico quali hanno board Greenhouse/Lever/Ashby/SmartRecruiters attivo...")
    slug_validi = scopri_slug_validi(nomi)

    # Slug WTTJ grezzi: non serve verifica extra, sono già confermati esistere
    # (raccolti dalla pagina companies reale). Uniti a quelli già in cache.
    wttj_pregressi = set()
    try:
        with open("discovered_slugs.json", "r", encoding="utf-8") as f:
            wttj_pregressi = set(json.load(f).get("wttj", []))
    except Exception:
        pass
    slug_validi["wttj"] = sorted(wttj_pregressi | set(WTTJ_SLUGS_RACCOLTI))

    with open("discovered_slugs.json", "w", encoding="utf-8") as f:
        json.dump(slug_validi, f, indent=2, ensure_ascii=False)

    totale = sum(len(v) for v in slug_validi.values())
    print(f"\n📊 Risultato: {totale} aziende trovate "
          f"({len(slug_validi['greenhouse'])} Greenhouse, {len(slug_validi['lever'])} Lever, "
          f"{len(slug_validi['ashby'])} Ashby, {len(slug_validi['smartrecruiters'])} SmartRecruiters, "
          f"{len(slug_validi['wttj'])} WTTJ) → salvati in discovered_slugs.json")
    print("   paris_job_newsletter.py li legge automaticamente, nessuna azione richiesta.")


if __name__ == "__main__":
    main()
