# ============================================================
#  📰 PARIS JOB NEWSLETTER — annunci in inglese, no francese richiesto
#
#  Fonti: Greenhouse, Lever (API JSON pubbliche) + Welcome to the Jungle
#  (HTML, con fallback Playwright per contenuto caricato via JS)
#
#  Filosofia:
#  - Nessuno scoring, nessuna sezione "aziende target": la mail è neutra,
#    mostra solo dati grezzi (titolo, azienda, location, link, data)
#    raggruppati per fonte. Decide l'utente.
#  - Dedup robusta: URL come chiave primaria, fingerprint (titolo+azienda+
#    location normalizzati, senza numeri) come rete di sicurezza contro
#    ripubblicazioni con URL diverso.
#  - Solo annunci NUOVI vengono mandati via email, mai ripresentati.
#
#  Scheduling: GitHub Actions (vedi .github/workflows/newsletter.yml)
# ============================================================

import requests
from bs4 import BeautifulSoup
from datetime import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json, time, re, os, hashlib, asyncio
import html as html_lib
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from langdetect import detect, LangDetectException
    LANGDETECT_OK = True
except ImportError:
    LANGDETECT_OK = False
    print("⚠️  langdetect non trovato — filtro lingua disattivato")

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False
    print("⚠️  Playwright non trovato — WTTJ userà solo il fallback HTML statico")

# ============================================================
# ✏️  CONFIGURAZIONE
# ============================================================

GMAIL_MITTENTE     = os.environ.get("GMAIL_MITTENTE", "")
GMAIL_PASSWORD     = os.environ.get("GMAIL_PASSWORD", "")
EMAIL_DESTINATARIO = os.environ.get("EMAIL_DESTINATARIO", "")

SEEN_FILE = "seen_jobs.json"

# Slug Greenhouse: si trova nell'URL della career page, es.
# https://job-boards.greenhouse.io/mistral -> "mistral"
GREENHOUSE_SLUGS_MANUALI = [
    # NB: alcune aziende (es. BSR) usano il prodotto Greenhouse "Job Boards" più
    # recente, dove lo slug nell'URL pubblico (job-boards.greenhouse.io/nome) NON
    # coincide sempre col token richiesto da questa API classica (boards-api...).
    # Verificato che risponde 200: se aggiungi altri slug a mano, testali con
    # curl https://boards-api.greenhouse.io/v1/boards/SLUG/jobs
    #
    # Seed presi da career-ops (github.com/santifer/career-ops), un progetto
    # open-source con lista aziende pre-verificate. Il filtro location per
    # Parigi resta comunque a livello di singolo annuncio: aggiungere aziende
    # globali non-francesi è sicuro, i loro annunci non-Parigi vengono
    # semplicemente scartati da passa_filtri(). Verificati dal vivo (200):
    "anthropic", "polyai", "parloa", "intercom", "humeai",
    "airtable", "vercel", "arizeai", "coreweave", "gleanwork",
    # Seconda ondata, verificata dal vivo (200):
    "stripe", "gitlab", "cloudflare", "datadog", "discord", "elastic",
    "grafanalabs", "cockroachlabs", "fastly", "coinbase", "reddit",
    "robinhood", "affirm", "lyft", "jetbrains", "dialpad", "speechmatics",
]

# Slug Lever: es. https://jobs.lever.co/payflows -> "payflows"
LEVER_SLUGS_MANUALI = [
    # "payflows",
]

# Slug Ashby: es. https://jobs.ashbyhq.com/mistral.ai -> "mistral.ai"
ASHBY_SLUGS_MANUALI = [
    "mistral.ai",  # verificato: Mistral AI usa Ashby, non Greenhouse/Lever
    # Seed da career-ops, verificati dal vivo (200):
    "elevenlabs", "deepgram", "vapi", "bland", "sierra", "decagon",
    "linear", "supabase", "posthog", "ramp", "modal", "railway",
    "browserbase", "n8n", "zapier", "attio", "cohere", "pinecone",
    "talkdesk",
]

# ID azienda SmartRecruiters: es. https://careers.smartrecruiters.com/Bosch -> "Bosch"
# NB: l'API pubblica è attiva solo per alcune aziende (dipende dal loro piano);
# quelle senza feed pubblico restituiscono semplicemente 0 risultati, senza errori.
SMARTRECRUITERS_IDS_MANUALI = [
    # "NomeAzienda",
]

def _carica_slug_scoperti() -> dict:
    """Se discover_companies.py è già stato eseguito, unisce automaticamente
    gli slug trovati a quelli manuali. Nessun intervento richiesto: basta
    far girare discover_companies.py periodicamente (vedi workflow dedicato)."""
    try:
        with open("discovered_slugs.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "greenhouse": data.get("greenhouse", []),
            "lever": data.get("lever", []),
            "ashby": data.get("ashby", []),
            "smartrecruiters": data.get("smartrecruiters", []),
            "wttj": data.get("wttj", []),
        }
    except FileNotFoundError:
        return {"greenhouse": [], "lever": [], "ashby": [], "smartrecruiters": [], "wttj": []}
    except Exception as e:
        print(f"⚠️  Errore lettura discovered_slugs.json: {e}")
        return {"greenhouse": [], "lever": [], "ashby": [], "smartrecruiters": [], "wttj": []}

_scoperti = _carica_slug_scoperti()
GREENHOUSE_SLUGS = sorted(set(GREENHOUSE_SLUGS_MANUALI) | set(_scoperti["greenhouse"]))
LEVER_SLUGS = sorted(set(LEVER_SLUGS_MANUALI) | set(_scoperti["lever"]))
ASHBY_SLUGS = sorted(set(ASHBY_SLUGS_MANUALI) | set(_scoperti["ashby"]))
SMARTRECRUITERS_IDS = sorted(set(SMARTRECRUITERS_IDS_MANUALI) | set(_scoperti["smartrecruiters"]))
WTTJ_SLUGS = sorted(set(_scoperti.get("wttj", [])))  # solo da auto-discovery, nessun seed manuale
print(f"📋 Aziende monitorate: {len(GREENHOUSE_SLUGS)} Greenhouse, {len(LEVER_SLUGS)} Lever, "
      f"{len(ASHBY_SLUGS)} Ashby, {len(SMARTRECRUITERS_IDS)} SmartRecruiters, {len(WTTJ_SLUGS)} WTTJ "
      f"({sum(len(v) for v in _scoperti.values())} da auto-discovery)")

HEADERS = {
    "User-Agent": "paris-job-newsletter/1.0 (uso personale, contatto: imposta la tua email qui)",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# WTTJ serve contenuto vuoto/degradato a user-agent non da browser (verificato
# dal vivo). Usiamo uno UA da browser reale SOLO per il rendering Playwright
# di quel sito; le API JSON (Greenhouse/Lever/Ashby/SmartRecruiters) restano
# con HEADERS onesto, non hanno questo problema.
BROWSER_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Frasi che indicano un requisito HARD di francese -> annuncio scartato.
# Sono regex, controllate frase per frase (vedi requires_french_hard).
_FR = r"french|fran[cç]ais"
# "French" usato come aggettivo di altro (mercato, aziende, legge...) non
# riguarda la lingua: "strong presence in the French market" non va scartato.
_FR_NON_LINGUA = (
    r"market|markets|company|companies|startup|startups|tech|ecosystem|clients?|customers?|"
    r"government|law|legal|regulat\w*|context|entit\w*|subsidiar\w*|offices?|teams?|business|"
    r"retail|banks?|economy|territory|public|state|administration|labou?r|tax|accounting|gaap|"
    r"payroll|employment|social|healthcare|insurance|partners?|brands?|users?|sme|smes|scale-?ups?|"
    r"(?:and|&|/)\s+(?:european|international|uk|us|german|spanish|italian)\s+\w+"
)
_FRL = rf"\b(?:{_FR})\b(?!\s+(?:{_FR_NON_LINGUA})\b)"  # "French" inteso come lingua
_EN = r"\b(?:english|anglais)\b"
FRENCH_REQUIRED_PATTERNS = [
    rf"\b(fluent|fluency|fluently|native|bilingual|proficient|proficiency|excellent|perfect|"
    rf"full professional|advanced|mother tongue)\b[^.;\n]{{0,30}}{_FRL}",
    rf"\b(strong|professional|business)\s+(?:level\s+(?:of\s+)?|working\s+)?(?:knowledge\s+of\s+)?{_FRL}",
    rf"{_FRL}[^.;\n]{{0,30}}\b(required|mandatory|essential|must|needed|compulsory|"
    rf"fluency|proficiency|native|speaker|speaking|skills)\b",
    rf"\b(speak|write|communicate)\b[^.;\n]{{0,25}}{_FRL}",
    rf"\b(written|spoken|oral)\b[^.;\n]{{0,25}}{_FRL}",
    rf"\b(c1|c2|b2)\b[^.;\n]{{0,25}}{_FRL}",
    rf"{_FRL}[^.;\n]{{0,15}}\b(c1|c2|b2)\b",
    rf"{_EN}\s*(?:and|&|/|et)\s*{_FRL}",
    rf"{_FRL}\s*(?:and|&|/|et)\s*{_EN}",
    r"fran[cç]ais\s+(courant|requis|indispensable|obligatoire|exig[ée]|natif)",
    r"ma[iî]trise\s+(du|de la langue)\s+fran[cç]ais",
]
_FRENCH_REQUIRED_RE = [re.compile(p, re.IGNORECASE) for p in FRENCH_REQUIRED_PATTERNS]

# Se la stessa frase contiene uno di questi, il francese è solo gradito -> NON scartare
FRENCH_OPTIONAL_MARKERS = [
    "a plus", "an advantage", "advantageous", "a bonus", "bonus", "nice to have",
    "nice-to-have", "appreciated", "preferred", "is helpful", "would be helpful",
    "not required", "not mandatory", "not necessary", "no french", "not needed",
    "optional", "welcome", "notions of", "basic french", "is an asset", "asset",
    "un plus", "apprécié", "souhaité", "would help", "ideally", "desirable",
]

# ============================================================
# 🔧  UTILITIES HTTP
# ============================================================

def http_get(url, **kw):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, **kw)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"    ⚠️  GET {url[:70]}... → {e}")
        return None

# ============================================================
# 🌍  FILTRI: lingua, location, francese
# ============================================================

TESTO_CORTO = 200  # caratteri: sotto questa soglia c'è in pratica solo il titolo

def is_english(text: str) -> bool:
    """Rileva se il testo è in inglese. Se langdetect non è disponibile,
    non blocca nulla (fail-open) per non perdere annunci per un problema tecnico.

    Su testi corti (es. solo il titolo, senza descrizione — capita per WTTJ e
    in parte SmartRecruiters) langdetect è inaffidabile e può classificare
    erroneamente titoli francesi come inglese. Aggiungiamo un controllo
    euristico su segnali francesi molto specifici come rete di sicurezza
    aggiuntiva quando il testo è corto."""
    # Euristica solo sui testi corti: su una descrizione intera in inglese
    # basta un "résumé" o "Banque de France" per farla sembrare francese.
    if len((text or "").strip()) < TESTO_CORTO and ha_segnali_francesi_forti(text):
        return False
    if not LANGDETECT_OK:
        return True
    try:
        return detect(text[:1500]) == "en"
    except LangDetectException:
        return False

# Caratteri accentati che compaiono quasi solo in francese (non in inglese) e
# parole funzionali francesi molto comuni, usate come segnale forte anche su
# testi brevi dove langdetect da solo è inaffidabile (es. titoli senza descrizione).
_ACCENTI_FRANCESI = set("éèêëàâäùûüçîï")
_PAROLE_FUNZIONALI_FRANCESI = {
    " de ", " des ", " du ", " le ", " la ", " les ", " et ", " pour ", " avec ",
    " chargé", "chargée", "responsable de", "assistant(e)", "alternance",
    "stagiaire", "confirmé", "confirmée", "h/f", "f/h",
}

def ha_segnali_francesi_forti(text: str) -> bool:
    testo = f" {(text or '').lower()} "
    if any(c in testo for c in _ACCENTI_FRANCESI):
        return True
    return any(parola in testo for parola in _PAROLE_FUNZIONALI_FRANCESI)

def is_paris_location(location: str) -> bool:
    loc = (location or "").lower()
    keyword_parigi = ["paris", "france", "île-de-france", "ile-de-france", "idf",
                       "la défense", "la defense", "la-defense", "neuilly-sur-seine",
                       "boulogne-billancourt", "issy-les-moulineaux", "levallois-perret",
                       "saint-denis", "saint-ouen", "montreuil", "puteaux", "courbevoie",
                       "nanterre", "clichy", "ivry-sur-seine", "vincennes"]
    return any(kw in loc for kw in keyword_parigi) or loc.strip() == "fr"

def pulisci_html(testo: str) -> str:
    """Toglie i tag HTML dalle descrizioni (Greenhouse le manda come HTML
    con entità escapate, es. '&lt;p&gt;'). Il testo pulito serve sia al
    rilevamento lingua sia al filtro francese."""
    if not testo or "<" not in testo and "&" not in testo:
        return testo or ""
    testo = html_lib.unescape(testo)
    # A capo solo dopo i blocchi (paragrafi, elenchi...), non dopo <strong>/<a>:
    # "Fluent <strong>French</strong> required" deve restare una frase sola.
    testo = re.sub(r"(?i)(</(p|li|div|h[1-6]|ul|ol|tr)>|<br\s*/?>)", r"\1\n", testo)
    return BeautifulSoup(testo, "html.parser").get_text()

def _frasi(testo: str) -> list[str]:
    return [f for f in re.split(r"(?<=[.!?;])\s+|\n+|•", testo) if f.strip()]

def requires_french_hard(text: str) -> bool:
    """True solo se il francese è un requisito esplicito e vincolante.
    Il controllo è per singola frase: 'Fluent French is a plus' NON fa
    scartare l'annuncio, 'Fluent French required' sì."""
    if not re.search(_FR, text or "", re.IGNORECASE):
        return False
    for frase in _frasi(text):
        if not any(rx.search(frase) for rx in _FRENCH_REQUIRED_RE):
            continue
        f = frase.lower()
        if any(m in f for m in FRENCH_OPTIONAL_MARKERS):
            continue
        return True
    return False

# Un titolo che nomina il francese ("Account Executive - French Market",
# "Francophone Sales") in pratica richiede di parlarlo.
_FRANCESE_NEL_TITOLO = re.compile(r"\b(french|francophone|fran[cç]ais)\b", re.IGNORECASE)

def passa_filtri(titolo: str, location: str, descrizione: str) -> bool:
    if not is_paris_location(location):
        return False
    if _FRANCESE_NEL_TITOLO.search(titolo or ""):
        return False
    descrizione = pulisci_html(descrizione)
    testo_completo = f"{titolo}\n{descrizione}"
    if not is_english(testo_completo):
        return False
    if requires_french_hard(testo_completo):
        return False
    return True

# ============================================================
# 👁️  DEDUP ROBUSTA (URL + fingerprint normalizzato)
# ============================================================

def normalizza_testo(testo: str) -> str:
    """minuscolo, senza punteggiatura, senza numeri (ID annuncio che
    cambiano tra ripubblicazioni), spazi normalizzati."""
    testo = (testo or "").lower()
    testo = re.sub(r"[^\w\s]", "", testo)
    testo = re.sub(r"\d+", "", testo)
    testo = re.sub(r"\s+", " ", testo).strip()
    return testo

def job_id(azienda: str, offerta: dict) -> str:
    """ID stabile: fingerprint (titolo+azienda+location normalizzati) come
    chiave primaria — così un annuncio ripubblicato con URL diverso viene
    comunque riconosciuto. L'URL da solo non basta (vedi ATS che aggiungono
    parametri random ai link)."""
    base = (
        f"{normalizza_testo(offerta.get('titolo',''))}|"
        f"{normalizza_testo(azienda)}|"
        f"{normalizza_testo(offerta.get('location',''))}"
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()

# Versione del formato degli ID in seen_jobs.json.
# v1 (vecchio): l'ID usava il nome della FONTE ("Greenhouse") al posto
#   dell'azienda → "Software Engineer, Paris" di Stripe e di Datadog avevano
#   lo stesso ID e il secondo non arrivava mai nella mail.
# v2: l'ID usa il nome dell'azienda.
SEEN_VERSIONE = 2

# La migrazione v1 → v2 si fa fonte per fonte: una fonte è "migrata" solo
# dopo un run in cui ha restituito annunci. Se una fonte salta un giorno
# (es. sito irraggiungibile), i suoi vecchi annunci restano riconosciuti
# col vecchio ID e non tornano come nuovi quando la fonte riprende.
# Fonti che esistevano già col vecchio formato v1 (per loro serve la migrazione).
FONTI_STORICHE = ["Greenhouse", "Lever", "Ashby", "SmartRecruiters", "Welcome to the Jungle"]
TUTTE_LE_FONTI = FONTI_STORICHE + ["Portfolio VC"]

# Quando si aggiunge una fonte NUOVA, al primo run mostriamo solo gli annunci
# pubblicati negli ultimi N giorni: gli altri vengono segnati come visti.
# Senza questo, la prima mail conterrebbe centinaia di annunci tutti insieme.
GIORNI_PRIMO_RUN_FONTE_NUOVA = 7

def carica_seen() -> tuple[set, set]:
    """Restituisce (ID già visti, fonti già migrate al formato v2)."""
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            ids = set(data.get("ids", []))
            versione = data.get("versione", 1)
            if versione < 2:
                fonti_migrate = set()
            elif "fonti_migrate" in data:
                fonti_migrate = set(data["fonti_migrate"])
            else:
                # Primo run v2 (25/10/2026): WTTJ aveva restituito 0 annunci,
                # quindi solo le altre fonti sono state migrate.
                fonti_migrate = set(FONTI_STORICHE) - {"Welcome to the Jungle"}
            print(f"📂 Caricati {len(ids)} ID già visti (formato v{versione})")
            return ids, fonti_migrate
        except Exception as e:
            print(f"⚠️  Errore lettura {SEEN_FILE}: {e}")
    else:
        print(f"📂 {SEEN_FILE} non trovato — prima esecuzione")
    return set(), set(TUTTE_LE_FONTI)

def salva_seen(seen: set, fonti_migrate: set):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump({"versione": SEEN_VERSIONE, "fonti_migrate": sorted(fonti_migrate),
                       "ids": sorted(seen), "aggiornato": datetime.now().isoformat()}, f, indent=2)
        print(f"💾 Salvati {len(seen)} ID")
    except Exception as e:
        print(f"⚠️  Errore scrittura {SEEN_FILE}: {e}")

def _troppo_vecchio(offerta: dict, giorni: int) -> bool:
    try:
        data = datetime.strptime(offerta.get("data", ""), "%Y-%m-%d")
    except ValueError:
        return True  # senza data non sappiamo se è recente: al primo run lo saltiamo
    return (datetime.now() - data).days > giorni

def filtra_nuove(nome_fonte: str, offerte: list, seen: set,
                 migrazione_da_v1: bool = False,
                 solo_ultimi_giorni: int | None = None) -> tuple[list, set]:
    """Separa gli annunci nuovi da quelli già visti.

    migrazione_da_v1: un annuncio il cui vecchio ID v1 è già in seen viene
      considerato già visto (niente mail con annunci già ricevuti).
    solo_ultimi_giorni: primo run di una fonte nuova, mostra solo gli
      annunci recenti e segna gli altri come visti."""
    nuove, nuovi_ids = [], set()
    for o in offerte:
        jid = job_id(o.get("azienda_id") or o.get("azienda", ""), o)
        if jid in seen or jid in nuovi_ids:
            continue
        nuovi_ids.add(jid)
        if migrazione_da_v1 and job_id(nome_fonte, o) in seen:
            continue  # già ricevuto col vecchio formato: registra solo il nuovo ID
        if solo_ultimi_giorni is not None and _troppo_vecchio(o, solo_ultimi_giorni):
            continue
        nuove.append(o)
    seen_aggiornato = seen | nuovi_ids
    print(f"   → {len(offerte)} trovate · {len(nuove)} nuove · {len(offerte)-len(nuove)} già viste")
    return nuove, seen_aggiornato

# ============================================================
# 🏷️  UTILITY — nome azienda leggibile da uno slug
# ============================================================

# Nomi scritti bene per gli slug che la regola automatica sbaglierebbe.
# Aggiungine pure: chiave = slug esatto, valore = nome da mostrare nella mail.
NOMI_AZIENDE = {
    "mistral.ai": "Mistral AI", "polyai": "PolyAI", "humeai": "Hume AI",
    "arizeai": "Arize AI", "gleanwork": "Glean", "grafanalabs": "Grafana Labs",
    "cockroachlabs": "Cockroach Labs", "coreweave": "CoreWeave", "gitlab": "GitLab",
    "jetbrains": "JetBrains", "elevenlabs": "ElevenLabs", "posthog": "PostHog",
    "n8n": "n8n", "openai": "OpenAI", "hubspot": "HubSpot", "linkedin": "LinkedIn",
    "github": "GitHub", "paypal": "PayPal", "doctolib": "Doctolib",
    "backmarket": "Back Market", "blablacar": "BlaBlaCar", "alan": "Alan",
}

_SIGLE = {"ai": "AI", "io": "IO", "hr": "HR", "ia": "IA", "sas": "SAS", "it": "IT"}

def slug_a_nome_leggibile(slug: str) -> str:
    """Trasforma uno slug tecnico in un nome leggibile:
    'mistral.ai' → 'Mistral AI', 'payplug-1' → 'Payplug',
    'welcome-to-the-jungle' → 'Welcome To The Jungle'."""
    if slug in NOMI_AZIENDE:
        return NOMI_AZIENDE[slug]
    pulito = re.sub(r"[-_]\d+$", "", slug)                  # 'payplug-1' → 'payplug'
    pulito = re.sub(r"\.(com|co|io|fr|eu|app|so)$", "", pulito)  # domini senza significato
    parole = re.split(r"[.\-_\s]+", pulito)
    return " ".join(_SIGLE.get(p.lower(), p.capitalize()) for p in parole if p)

def _con_azienda(offerta: dict, slug: str, nome: str) -> dict:
    """Aggiunge nome da mostrare e chiave stabile per la dedup. La chiave è
    lo slug, così se cambia il nome mostrato l'annuncio non ricompare."""
    offerta["azienda"] = nome
    offerta["azienda_id"] = slug
    return offerta

# ============================================================
# 🏢  SCRAPER 1 — Greenhouse (API JSON pubblica)
# ============================================================

MAX_WORKER_PARALLELI = 8

def _esegui_in_parallelo(elementi: list, funzione) -> list:
    """Esegue `funzione(elemento)` per ogni elemento in thread paralleli
    (ogni chiamata è I/O di rete, non CPU-bound: i thread bastano, niente
    bisogno di multiprocessing). Appiattisce le liste di risultati."""
    risultati = []
    with ThreadPoolExecutor(max_workers=MAX_WORKER_PARALLELI) as executor:
        futures = [executor.submit(funzione, el) for el in elementi]
        for future in as_completed(futures):
            risultati.extend(future.result() or [])
    return risultati

def scrape_greenhouse():
    print("🔍 Greenhouse...")

    def per_azienda(slug):
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        r = http_get(url)
        if not r:
            return []
        offerte = []
        for job in r.json().get("jobs", []):
            azienda = job.get("company_name") or slug_a_nome_leggibile(slug)
            titolo = job.get("title", "")
            location = job.get("location", {}).get("name", "")
            descrizione = job.get("content", "")
            if not passa_filtri(titolo, location, descrizione):
                continue
            offerte.append(_con_azienda({
                "titolo": titolo,
                "azienda": azienda,
                "location": location,
                "url": job.get("absolute_url", ""),
                "data": job.get("updated_at", "")[:10],
            }, slug, azienda))
        return offerte

    risultati = _esegui_in_parallelo(GREENHOUSE_SLUGS, per_azienda)
    print(f"   → {len(risultati)} offerte compatibili trovate")
    return risultati

# ============================================================
# 🏢  SCRAPER 2 — Lever (API JSON pubblica)
# ============================================================

def scrape_lever():
    print("🔍 Lever...")

    def per_azienda(slug):
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        r = http_get(url)
        if not r:
            return []
        azienda = slug_a_nome_leggibile(slug)
        offerte = []
        for job in r.json():
            titolo = job.get("text", "")
            location = (job.get("categories", {}) or {}).get("location", "")
            descrizione = job.get("descriptionPlain", "") or job.get("description", "")
            if not passa_filtri(titolo, location, descrizione):
                continue
            ts = job.get("createdAt")
            data = ""
            if ts:
                try:
                    data = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
                except Exception:
                    pass
            offerte.append(_con_azienda({
                "titolo": titolo,
                "azienda": azienda,
                "location": location,
                "url": job.get("hostedUrl", ""),
                "data": data,
            }, slug, azienda))
        return offerte

    risultati = _esegui_in_parallelo(LEVER_SLUGS, per_azienda)
    print(f"   → {len(risultati)} offerte compatibili trovate")
    return risultati

# ============================================================
# 🏢  SCRAPER — Ashby (API JSON pubblica)
# ============================================================

def scrape_ashby():
    print("🔍 Ashby...")

    def per_azienda(slug):
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
        r = http_get(url)
        if not r:
            return []
        azienda = slug_a_nome_leggibile(slug)
        offerte = []
        for job in r.json().get("jobs", []):
            titolo = job.get("title", "")
            location = job.get("location", "") or job.get("address", {}).get("postalAddress", {}).get("addressLocality", "")
            descrizione = job.get("descriptionPlain", "") or job.get("descriptionHtml", "")
            if not passa_filtri(titolo, location, descrizione):
                continue
            offerte.append(_con_azienda({
                "titolo": titolo,
                "azienda": azienda,
                "location": location,
                "url": job.get("jobUrl", "") or job.get("applyUrl", ""),
                "data": (job.get("publishedAt", "") or "")[:10],
            }, slug, azienda))
        return offerte

    risultati = _esegui_in_parallelo(ASHBY_SLUGS, per_azienda)
    print(f"   → {len(risultati)} offerte compatibili trovate")
    return risultati

# ============================================================
# 🏢  SCRAPER — SmartRecruiters (API JSON pubblica, tier-dipendente)
# ============================================================

def scrape_smartrecruiters():
    print("🔍 SmartRecruiters...")

    def per_azienda(company_id):
        url = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings?country=fr&city=Paris"
        r = http_get(url)
        if not r:
            return []  # normale: non tutte le aziende hanno il feed pubblico attivo
        offerte = []
        for job in r.json().get("content", []):
            titolo = job.get("name", "")
            location = job.get("location", {}).get("city", "")
            azienda = job.get("company", {}).get("name") or slug_a_nome_leggibile(company_id)

            # SmartRecruiters dichiara esplicitamente la lingua dell'annuncio nel
            # dettaglio (campo "language") — molto più affidabile di langdetect
            # su una descrizione vuota, che causava falsi positivi in francese.
            job_id = job.get("id", "")
            descrizione = ""
            lingua_dichiarata = None
            dettaglio = http_get(f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings/{job_id}")
            if dettaglio:
                d = dettaglio.json()
                lingua_dichiarata = (d.get("language") or {}).get("code")
                sezioni = (d.get("jobAd") or {}).get("sections", {})
                descrizione = " ".join(s.get("text", "") for s in sezioni.values())

            if lingua_dichiarata and lingua_dichiarata != "en":
                continue  # lingua esplicitamente dichiarata non-inglese: scarta senza ambiguità
            if not passa_filtri(titolo, location, descrizione):
                continue
            offerte.append(_con_azienda({
                "titolo": titolo,
                "azienda": azienda,
                "location": location,
                "url": f"https://jobs.smartrecruiters.com/{company_id}/{job.get('id','')}",
                "data": (job.get("releasedDate", "") or "")[:10],
            }, company_id, azienda))
        return offerte

    risultati = _esegui_in_parallelo(SMARTRECRUITERS_IDS, per_azienda)
    print(f"   → {len(risultati)} offerte compatibili trovate")
    return risultati

# ============================================================
# 🏢  SCRAPER 3 — Welcome to the Jungle (HTML + fallback Playwright)
# ============================================================

WTTJ_BASE_URL = "https://www.welcometothejungle.com"
WTTJ_PAGINE_PARALLELE = 4   # pagine aperte insieme: abbastanza per andare ~4x più veloci,
                            # poche per non sembrare un attacco al sito
WTTJ_SOGLIA_FALLIMENTI = 15 # fallimenti di fila oltre cui ci fermiamo (probabile blocco)

WTTJ_MAX_PAGINE_AZIENDA = 5  # la lista annunci di un'azienda mostra 30 annunci per pagina
_JS_LINK_ANNUNCI = "els => els.map(e => [e.href, e.innerText])"

# Struttura verificata dal vivo il 25/10/2026 (WTTJ ha cambiato sito quel giorno:
# le pagine azienda ora sono /en/companies-v1/{slug} e il vecchio blocco
# [data-testid^='job-thumb-'] non esiste più). La pagina /en/companies/{slug}/jobs
# elenca gli annunci come link /companies/{slug}/jobs/{titolo}_{città}[_RIF]:
# il testo del link è il titolo, la città è nell'indirizzo.
# La pagina va caricata come nella versione verificata: browser.new_page con UA
# da browser, nessun blocco di immagini/font, attesa di 2s dopo il caricamento.

def _citta_da_url_wttj(url: str) -> str:
    """'.../jobs/business-developer-sdr_paris_PAYFI_kAM5w6l' -> 'Paris'."""
    ultimo = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    parti = ultimo.split("_")
    if len(parti) > 1 and parti[1] and parti[1] == parti[1].lower():
        return "-".join(p.capitalize() for p in parti[1].split("-"))
    return ""

async def _wttj_async(slugs: list) -> tuple[list, int]:
    risultati = []
    stato = {"fallimenti_consecutivi": 0, "stop": False, "saltate": 0,
             "pagine_ok": 0, "card_totali": 0, "esempi_vuoti": []}
    coda: asyncio.Queue = asyncio.Queue()
    for s in slugs:
        coda.put_nowait(s)

    async def leggi_pagina(page, url) -> list:
        await page.goto(url, wait_until="networkidle", timeout=15_000)
        await page.wait_for_timeout(2000)
        link = await page.eval_on_selector_all("a[href*='/jobs/']", _JS_LINK_ANNUNCI)
        return [(h, t) for h, t in link if re.search(r"/companies/[^/]+/jobs/[^/?#]+", h or "")]

    async def worker(browser):
        page = await browser.new_page(user_agent=BROWSER_USER_AGENT)
        banner_gestito = False
        while True:
            try:
                slug = coda.get_nowait()
            except asyncio.QueueEmpty:
                break
            if stato["stop"]:
                stato["saltate"] += 1
                continue
            annunci = {}
            try:
                for n in range(1, WTTJ_MAX_PAGINE_AZIENDA + 1):
                    url = f"{WTTJ_BASE_URL}/en/companies/{slug}/jobs" + (f"?page={n}" if n > 1 else "")
                    link = await leggi_pagina(page, url)
                    if not banner_gestito:
                        # Il banner cookie compare solo alla prima visita di ogni pagina.
                        for testo in ("OK for me", "No, thanks"):
                            try:
                                await page.click(f"text={testo}", timeout=2500)
                                break
                            except Exception:
                                pass
                        banner_gestito = True
                    nuovi = {h: t for h, t in link if h not in annunci}
                    annunci.update(nuovi)
                    if len(link) < 30 or not nuovi:
                        break
                stato["fallimenti_consecutivi"] = 0
                stato["pagine_ok"] += 1
                stato["card_totali"] += len(annunci)
                if not annunci and len(stato["esempi_vuoti"]) < 3:
                    stato["esempi_vuoti"].append(f"{slug} (titolo pagina: {await page.title()!r})")
            except Exception as e:
                print(f"    ⚠️  {slug}: {str(e).splitlines()[0]}")
                stato["fallimenti_consecutivi"] += 1
                if stato["fallimenti_consecutivi"] >= WTTJ_SOGLIA_FALLIMENTI and not stato["stop"]:
                    stato["stop"] = True
                    print(f"    ⚠️  {WTTJ_SOGLIA_FALLIMENTI} fallimenti consecutivi — probabile blocco/"
                          f"rate-limit del sito. Mi fermo qui, le aziende rimaste verranno "
                          f"riprese al prossimo run.")
                continue

            azienda = slug_a_nome_leggibile(slug)
            for href, testo in annunci.items():
                titolo = (testo or "").strip().split("\n")[0].strip()
                location = _citta_da_url_wttj(href)
                if not titolo or not passa_filtri(titolo, location, ""):
                    continue
                risultati.append(_con_azienda({
                    "titolo": titolo,
                    "azienda": azienda,
                    "location": location,
                    "url": href.split("?")[0],
                    "data": "",
                }, slug, azienda))
            await asyncio.sleep(0.5)
        await page.close()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        await asyncio.gather(*(worker(browser) for _ in range(WTTJ_PAGINE_PARALLELE)))
        await browser.close()
    # Diagnostica: distingue "pagine senza annunci" (selettore/caricamento rotto)
    # da "annunci trovati ma scartati dai filtri".
    print(f"   [diagnostica] {stato['pagine_ok']} aziende caricate, {stato['card_totali']} annunci "
          f"letti prima dei filtri")
    if stato["pagine_ok"] and not stato["card_totali"]:
        print(f"   ⚠️  Nessun annuncio letto da nessuna pagina: probabile cambio di struttura "
              f"del sito o pagina non caricata del tutto. Esempi: {stato['esempi_vuoti']}")
    return risultati, stato["saltate"]

def scrape_wttj():
    """Scraping WTTJ per singola azienda (pagina /jobs dell'azienda), usando
    gli slug raccolti dall'auto-discovery. Struttura: vedi _wttj_async.

    Le pagine vengono aperte WTTJ_PAGINE_PARALLELE alla volta (prima era una
    alla volta: ~70 minuti per 521 aziende). Se il sito inizia a rifiutare
    troppe richieste di fila ci fermiamo: aspettare non aiuterebbe."""
    print("🔍 Welcome to the Jungle...")
    if not PLAYWRIGHT_OK:
        print("   → Playwright non disponibile, fonte saltata")
        return []
    if not WTTJ_SLUGS:
        print("   → nessuno slug WTTJ disponibile (serve prima far girare l'auto-discovery)")
        return []

    inizio = time.time()
    try:
        risultati, saltate = asyncio.run(_wttj_async(WTTJ_SLUGS))
    except Exception as e:
        print(f"   ⚠️  Errore generale WTTJ: {e}")
        return []
    if saltate:
        print(f"   [nota] {saltate} aziende non controllate in questo run "
              f"per il circuit breaker — verranno riprese al prossimo run.")
    print(f"   → {len(risultati)} offerte compatibili trovate "
          f"({len(WTTJ_SLUGS)} aziende in {(time.time()-inizio)/60:.1f} min)")
    return risultati


# ============================================================
# 🏢  SCRAPER — Portfolio dei fondi VC (Getro + Consider)
# ============================================================
# Molti fondi pubblicano un job board con gli annunci di tutte le aziende in
# portafoglio. Verificati dal vivo il 25/10/2026 (vedi README per aggiungerne).

# Getro: dominio del board -> (nome fondo, id collection). L'id si trova nella
# pagina del board, nel JSON __NEXT_DATA__ ("network":{"id":...}).
VC_GETRO = {
    "jobs.accel.com": ("Accel", 8672),
    "careers.atomico.com": ("Atomico", 36986),
    "jobs.pointnine.com": ("Point Nine", 1680),
    "jobs.headline.com": ("Headline", 3293),
    "jobs.earlybird.com": ("Earlybird", 617),
    "jobs.generalcatalyst.com": ("General Catalyst", 222),
    "jobs.hvcapital.com": ("HV Capital", 234),
    "jobs.dawncapital.com": ("Dawn Capital", 3063),
    "jobs.insightpartners.com": ("Insight Partners", 246),
}
# Consider: dominio del board -> nome fondo
VC_CONSIDER = {
    "jobs.alven.co": "Alven",
    "jobs.xange.vc": "XAnge",
    "jobs.newfundcap.com": "Newfund",
    "careers.balderton.com": "Balderton",
    "jobs.sequoiacap.com": "Sequoia",
    "jobs.notion.vc": "Notion Capital",
}
VC_MAX_PAGINE = 150  # tetto di sicurezza per board (Sequoia ha ~11.000 annunci, 100 per pagina)

_JSON_HEADERS = {**HEADERS, "User-Agent": BROWSER_USER_AGENT,
                 "Accept": "application/json", "Content-Type": "application/json"}

def _data_da_timestamp(valore) -> str:
    """Getro/Consider danno la data come secondi, millisecondi o stringa ISO."""
    if valore in (None, ""):
        return ""
    try:
        n = float(valore)
        if n > 1e12:
            n /= 1000
        return datetime.fromtimestamp(n).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return str(valore)[:10]

# Annunci che puntano a board che leggiamo già direttamente: li saltiamo,
# altrimenti lo stesso annuncio arriverebbe due volte.
def _gia_coperto(url: str) -> bool:
    u = (url or "").lower()
    regole = [
        (r"greenhouse\.io/(?:embed/job_app\?for=)?([\w.-]+)", GREENHOUSE_SLUGS),
        (r"jobs\.lever\.co/([\w.-]+)", LEVER_SLUGS),
        (r"jobs\.ashbyhq\.com/([\w.%-]+)", ASHBY_SLUGS),
        (r"smartrecruiters\.com/([\w.-]+)", SMARTRECRUITERS_IDS),
        (r"welcometothejungle\.com/\w+/companies/([\w-]+)", WTTJ_SLUGS),
    ]
    for pattern, slugs in regole:
        m = re.search(pattern, u)
        if m and m.group(1).lower() in {s.lower() for s in slugs}:
            return True
    return False

def _offerta_vc(titolo, azienda, location, url, data, fondo):
    slug = re.sub(r"[^a-z0-9]+", "-", (azienda or "").lower()).strip("-") or "sconosciuta"
    o = _con_azienda({"titolo": titolo, "location": location, "url": url, "data": data}, slug, azienda or "N/D")
    o["via"] = fondo
    return o

def _getro_board(dominio: str, fondo: str, collection_id: int) -> list:
    offerte = []
    url_api = f"https://api.getro.com/api/v2/collections/{collection_id}/search/jobs"
    headers = {**_JSON_HEADERS, "Origin": f"https://{dominio}", "Referer": f"https://{dominio}/jobs"}
    totale = None
    for pagina in range(VC_MAX_PAGINE):
        body = {"hitsPerPage": 20, "page": pagina, "query": "",
                "filters": {"page": pagina, "searchable_locations": ["Paris, France"]}}
        try:
            r = requests.post(url_api, headers=headers, json=body, timeout=20)
            r.raise_for_status()
            dati = r.json()
        except Exception as e:
            print(f"    ⚠️  {fondo} (Getro) pagina {pagina}: {e}")
            break
        risultati = dati.get("results", dati)
        jobs = risultati.get("jobs") or []
        totale = risultati.get("count", totale)
        for job in jobs:
            titolo = job.get("title", "")
            location = ", ".join(job.get("locations") or [])
            url = job.get("url", "")
            if _gia_coperto(url) or not passa_filtri(titolo, location, ""):
                continue
            offerte.append(_offerta_vc(titolo, (job.get("organization") or {}).get("name", ""),
                                       location, url, _data_da_timestamp(job.get("createdAt")), fondo))
        if not jobs or (totale is not None and (pagina + 1) * 20 >= totale):
            break
        time.sleep(0.3)
    print(f"    {fondo}: {totale if totale is not None else '?'} annunci a Parigi, {len(offerte)} compatibili")
    return offerte

def _con_riprova(funzione, tentativi=2):
    """Consider a volte risponde lentamente: un secondo tentativo con più pazienza."""
    for i in range(tentativi):
        try:
            return funzione(30 if i == 0 else 60)
        except requests.RequestException:
            if i == tentativi - 1:
                raise
            time.sleep(3)

def _consider_board(dominio: str, fondo: str) -> list:
    """Consider non filtra bene per città: scarichiamo tutti gli annunci del
    board (pochi MB) e teniamo quelli di Parigi con is_paris_location."""
    sessione = requests.Session()
    try:
        pagina_html = _con_riprova(lambda t: sessione.get(
            f"https://{dominio}/jobs", headers={"User-Agent": BROWSER_USER_AGENT}, timeout=t)).text
        token = re.search(r'csrfToken":"([^"]+)"', pagina_html).group(1)
        board = re.search(r'"board":\{"id":"([^"]+)","isParent":(true|false)', pagina_html)
    except Exception as e:
        print(f"    ⚠️  {fondo} (Consider): pagina non leggibile ({e})")
        return []
    headers = {**_JSON_HEADERS, "x-csrf-token": token, "Origin": f"https://{dominio}", "Referer": f"https://{dominio}/jobs"}
    meta = {"size": 100}
    offerte, letti, totale, url_visti = [], 0, None, set()
    for _ in range(VC_MAX_PAGINE):
        body = {"meta": meta, "board": {"id": board.group(1), "isParent": board.group(2) == "true"},
                "query": {}, "grouped": False}
        try:
            r = _con_riprova(lambda t: sessione.post(
                f"https://{dominio}/api-boards/search-jobs", headers=headers, json=body, timeout=t))
            r.raise_for_status()
            dati = r.json()
        except Exception as e:
            print(f"    ⚠️  {fondo} (Consider): {e}")
            break
        jobs = dati.get("jobs") or []
        totale = dati.get("total", totale)
        nuovi_in_pagina = 0
        for job in jobs:
            url = job.get("url") or job.get("applyUrl") or ""
            if url in url_visti:
                continue
            url_visti.add(url)
            nuovi_in_pagina += 1
            letti += 1
            titolo = job.get("title", "")
            location = ", ".join(job.get("locations") or [])
            if _gia_coperto(url) or not passa_filtri(titolo, location, ""):
                continue
            offerte.append(_offerta_vc(titolo, job.get("companyName", ""), location, url,
                                       _data_da_timestamp(job.get("timeStamp")), fondo))
        sequenza = (dati.get("meta") or {}).get("sequence")
        if not jobs or not nuovi_in_pagina or not sequenza or (totale is not None and letti >= totale):
            break
        meta = {"size": 100, "sequence": sequenza}
        time.sleep(0.3)
    print(f"    {fondo}: {letti}/{totale if totale is not None else '?'} annunci letti, {len(offerte)} compatibili a Parigi")
    return offerte

def scrape_portfolio_vc():
    print("🔍 Portfolio VC (Getro + Consider)...")
    compiti = [lambda d=d, f=f, i=i: _getro_board(d, f, i) for d, (f, i) in VC_GETRO.items()]
    compiti += [lambda d=d, f=f: _consider_board(d, f) for d, f in VC_CONSIDER.items()]
    tutte = _esegui_in_parallelo(compiti, lambda compito: compito())
    # Un'azienda finanziata da più fondi compare in più board: teniamo un solo
    # annuncio per link, unendo i nomi dei fondi.
    per_url = {}
    for o in tutte:
        chiave = re.sub(r"[?#].*$", "", o["url"]) or id(o)
        if chiave in per_url:
            fondi = per_url[chiave]["via"].split(", ")
            if o["via"] not in fondi:
                per_url[chiave]["via"] = ", ".join(fondi + [o["via"]])
        else:
            per_url[chiave] = o
    risultati = list(per_url.values())
    print(f"   → {len(risultati)} offerte compatibili trovate ({len(tutte) - len(risultati)} doppioni tra fondi)")
    return risultati

# ============================================================
# 📧  GENERAZIONE EMAIL — neutra, nessuno scoring, per fonte
# Keyword dei job title emersi dal percorso di orientamento professionale
# ([[orientamento-professionale]]): usate SOLO per mostrare un badge informativo
# nella mail, mai per filtrare, ordinare o dare priorità — resta una lista
# neutra, la valutazione dell'azienda resta libera come richiesto.
JOB_TITLE_KEYWORDS = [
    "customer success", "growth", "business development", "partnerships",
    "operations associate", "no-code", "nocode", "product operations",
    "sales engineer", "solutions associate", "solutions engineer",
]

def matcha_job_title_target(titolo: str) -> bool:
    t = (titolo or "").lower()
    return any(kw in t for kw in JOB_TITLE_KEYWORDS)

# ============================================================

FONTE_COLORS = {
    "Portfolio VC": ("#0f766e", "#ffffff"),
    "Greenhouse": ("#0f9d58", "#ffffff"),
    "Lever":      ("#5b6bf5", "#ffffff"),
    "Ashby":      ("#7c3aed", "#ffffff"),
    "SmartRecruiters": ("#f26522", "#ffffff"),
    "Welcome to the Jungle": ("#fdc22b", "#1a1a1a"),
}

def ordina_neutro(offerte: list) -> list:
    """Ordinamento oggettivo: data più recente prima, poi azienda in ordine
    alfabetico. Nessun giudizio di rilevanza o priorità."""
    return sorted(offerte, key=lambda o: (o.get("data", ""), o.get("titolo", "")), reverse=True)

_MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
         "agosto", "settembre", "ottobre", "novembre", "dicembre"]

def _data_italiana(d: datetime) -> str:
    return f"{d.day} {_MESI[d.month - 1]} {d.year}"

def _e(testo) -> str:
    """Escape HTML: un titolo con '&' o '<' non deve rompere l'email."""
    return html_lib.escape(str(testo or ""), quote=True)

def _url_sicuro(url: str) -> str:
    return url if (url or "").startswith(("http://", "https://")) else "#"

def conta_ruoli_target(tutte_nuove: dict) -> int:
    return sum(matcha_job_title_target(o.get("titolo", ""))
               for offerte in tutte_nuove.values() for o in offerte)

def genera_html(tutte_nuove: dict, totale_trovate: int):
    oggi_str = _data_italiana(datetime.now())
    totale = sum(len(v) for v in tutte_nuove.values())
    n_target = conta_ruoli_target(tutte_nuove)
    sezioni = ""

    for fonte, offerte in tutte_nuove.items():
        if not offerte:
            continue
        bg, fg = FONTE_COLORS.get(fonte, ("#1a1a2e", "#ffffff"))
        righe = ""
        for o in ordina_neutro(offerte):
            azienda_riga = (f'<div style="font-size:13px;font-weight:600;color:#444;margin-bottom:2px;">'
                            f'🏢 {_e(o.get("azienda") or "N/D")}'
                            + (f' <span style="font-weight:400;color:#888;">· via {_e(o["via"])}</span>' if o.get("via") else "")
                            + '</div>')
            loc = f'<span style="color:#888;">📍 {_e(o["location"])}</span>&ensp;' if o.get("location") else ""
            data = f'<span style="color:#aaa;">📅 {_e(o["data"])}</span>' if o.get("data") else ""
            # Badge puramente informativo, nessun impatto su ordinamento o filtro.
            badge = ('<span style="background:#e0e7ff;color:#3730a3;font-size:10px;'
                     'font-weight:700;padding:2px 6px;border-radius:4px;margin-left:6px;'
                     'white-space:nowrap;">🎯 ruolo target</span>') if matcha_job_title_target(o.get("titolo", "")) else ""
            righe += f"""
            <tr>
              <td style="padding:11px 16px;border-bottom:1px solid #f0f0f0;">
                {azienda_riga}
                <a href="{_e(_url_sicuro(o.get('url', '')))}" style="color:#1a1a2e;font-weight:600;text-decoration:none;
                   font-size:14px;line-height:1.4;">{_e(o['titolo'])}</a>{badge}
                <div style="margin-top:3px;font-size:12px;">{loc}{data}</div>
              </td>
            </tr>"""
        sezioni += f"""
        <div style="margin-bottom:24px;border-radius:8px;overflow:hidden;
                    box-shadow:0 2px 8px rgba(0,0,0,.08);">
          <table width="100%" cellpadding="0" cellspacing="0" style="background:{bg};">
            <tr>
              <td style="padding:11px 16px;color:{fg};font-size:16px;font-weight:700;">{_e(fonte)}</td>
              <td style="padding:11px 16px;color:{fg};font-size:12px;text-align:right;">
                {len(offerte)} nuov{'o' if len(offerte)==1 else 'i'}</td>
            </tr>
          </table>
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;">{righe}</table>
        </div>"""

    if not sezioni:
        body = (
            '<div style="text-align:center;padding:40px 0;color:#aaa;">'
            '<p style="font-size:40px;">✅</p>'
            '<p style="font-size:16px;font-weight:600;">Nessun annuncio nuovo oggi.</p>'
            f'<p style="font-size:13px;">{totale_trovate} annunci totali analizzati, nessuna novità.</p></div>'
        )
    else:
        body = sezioni

    riga_target = f" · di cui <strong>{n_target}</strong> 🎯 ruoli target" if n_target else ""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="max-width:640px;margin:28px auto;padding:0 12px;">
    <div style="background:#0d1b2a;background:linear-gradient(135deg,#0d1b2a,#1b3a5c);
                border-radius:12px 12px 0 0;padding:26px 24px 20px;text-align:center;">
      <div style="font-size:34px;margin-bottom:6px;">🗼</div>
      <h1 style="color:#fff;margin:0;font-size:21px;letter-spacing:1px;">PARIS JOB NEWSLETTER</h1>
      <p style="color:#7eb8e0;margin:6px 0 0;font-size:13px;">{oggi_str}</p>
    </div>
    <div style="background:#dbeafe;padding:11px 20px;
                border-left:4px solid #2563eb;border-right:4px solid #2563eb;
                text-align:center;font-size:14px;color:#1e3a5f;">
      🆕 <strong>{totale} nuov{'o annuncio' if totale==1 else 'i annunci'}</strong>{riga_target}
      <div style="font-size:12px;color:#4b6a8f;margin-top:2px;">{totale_trovate} annunci compatibili analizzati</div>
    </div>
    <div style="background:#f8f9fa;padding:20px 14px;">{body}</div>
    <div style="background:#0d1b2a;border-radius:0 0 12px 12px;padding:13px;
                text-align:center;font-size:11px;color:#4a6fa5;">
      Lista neutra, nessun ordinamento per rilevanza · {oggi_str}
    </div>
  </div>
</body></html>""", totale

def genera_testo(tutte_nuove: dict, totale_trovate: int) -> str:
    """Versione solo testo della mail: la leggono i client che non mostrano
    l'HTML, e averla migliora la consegna (meno probabilità di finire in spam)."""
    righe = [f"PARIS JOB NEWSLETTER — {_data_italiana(datetime.now())}", ""]
    totale = sum(len(v) for v in tutte_nuove.values())
    righe.append(f"{totale} nuovi annunci su {totale_trovate} analizzati")
    for fonte, offerte in tutte_nuove.items():
        if not offerte:
            continue
        righe += ["", f"== {fonte} ({len(offerte)}) =="]
        for o in ordina_neutro(offerte):
            target = " [ruolo target]" if matcha_job_title_target(o.get("titolo", "")) else ""
            dettagli = " · ".join(x for x in (o.get("location"), o.get("data")) if x)
            via = f" (via {o['via']})" if o.get("via") else ""
            righe += [f"- {o.get('azienda', 'N/D')}{via} — {o['titolo']}{target}",
                      f"  {dettagli}" if dettagli else "", f"  {o.get('url', '')}"]
    return "\n".join(r for r in righe if r is not None)

# ============================================================
# 📤  INVIO EMAIL
# ============================================================

def oggetto_email(totale: int, n_target: int) -> str:
    data = f"{datetime.now():%d/%m}"
    if totale == 0:
        return f"🗼 Paris Jobs {data} — nessun annuncio nuovo"
    testo = f"🗼 Paris Jobs {data} — {totale} nuov{'o' if totale == 1 else 'i'}"
    if n_target:
        testo += f" ({n_target} 🎯 ruol{'o' if n_target == 1 else 'i'} target)"
    return testo

def invia_email(html_body, testo_body, oggetto):
    destinatari = [d.strip() for d in EMAIL_DESTINATARIO.split(",") if d.strip()]
    if not destinatari or not GMAIL_MITTENTE or not GMAIL_PASSWORD:
        print("⚠️  Credenziali email mancanti, salto invio (controlla i Secrets)")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = oggetto
    msg["From"] = GMAIL_MITTENTE
    msg["To"] = ", ".join(destinatari)
    msg.attach(MIMEText(testo_body, "plain", "utf-8"))  # prima il testo, poi l'HTML (preferito)
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_MITTENTE, GMAIL_PASSWORD)
            s.sendmail(GMAIL_MITTENTE, destinatari, msg.as_string())
        print(f"✅ Email inviata a {len(destinatari)} destinatari")
    except smtplib.SMTPAuthenticationError:
        print("❌ Errore autenticazione → rigenera App Password su myaccount.google.com")
    except Exception as e:
        print(f"❌ Errore invio: {e}")

# ============================================================
# 🩺  HEALTH CHECK — rileva scraper rotti (0 risultati anomalo)
# ============================================================

STATS_FILE = "stats_storico.json"
GIORNI_STORICO_MEDIA = 14  # quanti giorni indietro guardare per calcolare la "media normale"

def carica_storico() -> list:
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"⚠️  Errore lettura {STATS_FILE}: {e}")
        return []

def salva_storico(storico: list):
    # Teniamo solo gli ultimi 90 giorni per non far crescere il file all'infinito
    storico = storico[-90:]
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(storico, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️  Errore scrittura {STATS_FILE}: {e}")

def media_storica_per_fonte(storico: list, nome_fonte: str) -> float | None:
    """Media dei risultati trovati per questa fonte negli ultimi N giorni
    (esclude il giorno corrente, che non è ancora nello storico)."""
    recenti = storico[-GIORNI_STORICO_MEDIA:]
    valori = [g["fonti"].get(nome_fonte, 0) for g in recenti if nome_fonte in g.get("fonti", {})]
    if not valori:
        return None
    return sum(valori) / len(valori)

def health_check(nome_fonte: str, risultati: int, storico_medio: float | None = None):
    if risultati == 0 and storico_medio and storico_medio > 1:
        print(f"⚠️  ALERT: {nome_fonte} ha trovato 0 risultati (media storica ~{storico_medio:.1f} "
              f"negli ultimi {GIORNI_STORICO_MEDIA} giorni). Probabile rottura selettori/API "
              f"— verificare manualmente.")
    elif storico_medio and storico_medio > 3 and risultati < storico_medio * 0.3:
        # Calo forte (sotto il 30% della media) ma non a zero: segnale più debole,
        # comunque utile da vedere nel log invece di passare inosservato.
        print(f"⚠️  Nota: {nome_fonte} ha trovato solo {risultati} risultati, "
              f"ben sotto la media storica (~{storico_medio:.1f}). Da tenere d'occhio.")

# ============================================================
# 🚀  MAIN
# ============================================================

def main():
    print(f"\n{'='*55}")
    print(f"  PARIS JOB NEWSLETTER  {datetime.now():%d/%m/%Y %H:%M}")
    print(f"{'='*55}\n")

    seen, fonti_migrate = carica_seen()
    da_migrare = [f for f in FONTI_STORICHE if f not in fonti_migrate]
    if da_migrare:
        print(f"🔄 Aggiorno il formato di seen_jobs.json per: {', '.join(da_migrare)} "
              f"(senza rimandare annunci già ricevuti)")
    storico = carica_storico()

    scrapers = [
        ("Greenhouse", scrape_greenhouse),
        ("Lever", scrape_lever),
        ("Ashby", scrape_ashby),
        ("SmartRecruiters", scrape_smartrecruiters),
        ("Welcome to the Jungle", scrape_wttj),
        ("Portfolio VC", scrape_portfolio_vc),
    ]

    tutte_trovate = {}
    tutte_nuove = {}

    for nome, fn in scrapers:
        try:
            offerte = fn()
            tutte_trovate[nome] = offerte
            media = media_storica_per_fonte(storico, nome)
            health_check(nome, len(offerte), media)
            if offerte:
                prima_volta = nome not in fonti_migrate
                if prima_volta and nome not in FONTI_STORICHE:
                    print(f"   🆕 Primo run di {nome}: mostro solo gli annunci degli ultimi "
                          f"{GIORNI_PRIMO_RUN_FONTE_NUOVA} giorni")
                nuove, seen = filtra_nuove(
                    nome, offerte, seen,
                    migrazione_da_v1=prima_volta and nome in FONTI_STORICHE,
                    solo_ultimi_giorni=GIORNI_PRIMO_RUN_FONTE_NUOVA if prima_volta and nome not in FONTI_STORICHE else None)
                fonti_migrate.add(nome)
                if nuove:
                    tutte_nuove[nome] = nuove
        except Exception as e:
            print(f"   ❌ {nome}: {e}")
            tutte_trovate[nome] = []
        time.sleep(1)

    salva_seen(seen, fonti_migrate)

    # Aggiungiamo la riga di oggi allo storico, per i confronti dei prossimi giorni
    storico.append({
        "data": datetime.now().strftime("%Y-%m-%d"),
        "fonti": {nome: len(tutte_trovate.get(nome, [])) for nome, _ in scrapers},
    })
    salva_storico(storico)

    totale_trovate = sum(len(v) for v in tutte_trovate.values())
    totale_nuove = sum(len(v) for v in tutte_nuove.values())

    print(f"\n📊 Riepilogo:")
    for n, _ in scrapers:
        trovate = len(tutte_trovate.get(n, []))
        nuove = len(tutte_nuove.get(n, []))
        print(f"   {n:24s}→ {trovate:3d} trovate · {nuove:3d} nuove")
    print(f"   {'TOTALE':24s}→ {totale_trovate:3d} trovate · {totale_nuove:3d} nuove\n")

    html, totale = genera_html(tutte_nuove, totale_trovate)
    testo = genera_testo(tutte_nuove, totale_trovate)
    invia_email(html, testo, oggetto_email(totale, conta_ruoli_target(tutte_nuove)))
    print("\n✨ Done!\n")

if __name__ == "__main__":
    main()
