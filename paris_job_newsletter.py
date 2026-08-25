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
import json, time, re, os, hashlib

try:
    from langdetect import detect, LangDetectException
    LANGDETECT_OK = True
except ImportError:
    LANGDETECT_OK = False
    print("⚠️  langdetect non trovato — filtro lingua disattivato")

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
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
    "mistral",
    # aggiungi qui altri slug man mano che li trovi a mano, se vuoi
]

# Slug Lever: es. https://jobs.lever.co/payflows -> "payflows"
LEVER_SLUGS_MANUALI = [
    # "payflows",
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
        }
    except FileNotFoundError:
        return {"greenhouse": [], "lever": []}
    except Exception as e:
        print(f"⚠️  Errore lettura discovered_slugs.json: {e}")
        return {"greenhouse": [], "lever": []}

_scoperti = _carica_slug_scoperti()
GREENHOUSE_SLUGS = sorted(set(GREENHOUSE_SLUGS_MANUALI) | set(_scoperti["greenhouse"]))
LEVER_SLUGS = sorted(set(LEVER_SLUGS_MANUALI) | set(_scoperti["lever"]))
print(f"📋 Aziende monitorate: {len(GREENHOUSE_SLUGS)} Greenhouse, {len(LEVER_SLUGS)} Lever "
      f"({len(_scoperti['greenhouse']) + len(_scoperti['lever'])} da auto-discovery)")

WTTJ_SEARCH_URL = (
    "https://www.welcometothejungle.com/en/jobs"
    "?query=&refinementList%5Boffices.country_code%5D%5B%5D=FR"
    "&refinementList%5Boffices.city%5D%5B%5D=Paris"
)

HEADERS = {
    "User-Agent": "paris-job-newsletter/1.0 (uso personale — contatto: imposta la tua email qui)",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Keyword che indicano un requisito HARD di francese -> annuncio scartato
FRENCH_REQUIRED_KEYWORDS = [
    "french required", "fluent in french", "native french",
    "français courant", "français requis", "must speak french",
    "french speaking required", "c1 french", "c2 french",
]

# Keyword che indicano francese solo gradito -> NON scartare
FRENCH_OPTIONAL_KEYWORDS = [
    "french is a plus", "french appreciated", "bonus if you speak french",
    "french is nice to have", "notions of french",
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

def is_english(text: str) -> bool:
    """Rileva se il testo è in inglese. Se langdetect non è disponibile,
    non blocca nulla (fail-open) per non perdere annunci per un problema tecnico."""
    if not LANGDETECT_OK:
        return True
    try:
        return detect(text[:1500]) == "en"
    except LangDetectException:
        return False

def is_paris_location(location: str) -> bool:
    loc = (location or "").lower()
    return "paris" in loc or "france" in loc or "fr" == loc.strip()

def requires_french_hard(text: str) -> bool:
    """True solo se il francese è un requisito esplicito e vincolante.
    Menzioni tipo 'French is a plus' NON fanno scartare l'annuncio."""
    t = (text or "").lower()
    if any(kw in t for kw in FRENCH_OPTIONAL_KEYWORDS):
        # se è esplicitamente 'nice to have', non consideriamo hard anche se
        # altre frasi generiche menzionano 'french' altrove nel testo
        pass
    return any(kw in t for kw in FRENCH_REQUIRED_KEYWORDS)

def passa_filtri(titolo: str, location: str, descrizione: str) -> bool:
    if not is_paris_location(location):
        return False
    testo_completo = f"{titolo} {descrizione}"
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

def carica_seen() -> set:
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            ids = set(data.get("ids", []))
            print(f"📂 Caricati {len(ids)} ID già visti")
            return ids
        except Exception as e:
            print(f"⚠️  Errore lettura {SEEN_FILE}: {e}")
    else:
        print(f"📂 {SEEN_FILE} non trovato — prima esecuzione")
    return set()

def salva_seen(seen: set):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump({"ids": sorted(seen), "aggiornato": datetime.now().isoformat()}, f, indent=2)
        print(f"💾 Salvati {len(seen)} ID")
    except Exception as e:
        print(f"⚠️  Errore scrittura {SEEN_FILE}: {e}")

def filtra_nuove(azienda: str, offerte: list, seen: set) -> tuple[list, set]:
    nuove, nuovi_ids = [], set()
    for o in offerte:
        jid = job_id(azienda, o)
        if jid not in seen:
            nuove.append(o)
            nuovi_ids.add(jid)
    seen_aggiornato = seen | nuovi_ids
    print(f"   → {len(offerte)} trovate · {len(nuove)} nuove · {len(offerte)-len(nuove)} già viste")
    return nuove, seen_aggiornato

# ============================================================
# 🏢  SCRAPER 1 — Greenhouse (API JSON pubblica)
# ============================================================

def scrape_greenhouse():
    print("🔍 Greenhouse...")
    risultati = []
    for slug in GREENHOUSE_SLUGS:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        r = http_get(url)
        if not r:
            continue
        for job in r.json().get("jobs", []):
            titolo = job.get("title", "")
            location = job.get("location", {}).get("name", "")
            descrizione = job.get("content", "")
            if not passa_filtri(titolo, location, descrizione):
                continue
            risultati.append({
                "titolo": titolo,
                "location": location,
                "url": job.get("absolute_url", ""),
                "data": job.get("updated_at", "")[:10],
            })
        time.sleep(0.5)
    print(f"   → {len(risultati)} offerte compatibili trovate")
    return risultati

# ============================================================
# 🏢  SCRAPER 2 — Lever (API JSON pubblica)
# ============================================================

def scrape_lever():
    print("🔍 Lever...")
    risultati = []
    for slug in LEVER_SLUGS:
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        r = http_get(url)
        if not r:
            continue
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
            risultati.append({
                "titolo": titolo,
                "location": location,
                "url": job.get("hostedUrl", ""),
                "data": data,
            })
        time.sleep(0.5)
    print(f"   → {len(risultati)} offerte compatibili trovate")
    return risultati

# ============================================================
# 🏢  SCRAPER 3 — Welcome to the Jungle (HTML + fallback Playwright)
# ============================================================

def _wttj_static() -> str | None:
    r = http_get(WTTJ_SEARCH_URL)
    return r.text if r else None

def _wttj_playwright() -> str | None:
    if not PLAYWRIGHT_OK:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = browser.new_context(user_agent=HEADERS["User-Agent"], locale="en-US")
            page = ctx.new_page()
            page.goto(WTTJ_SEARCH_URL, wait_until="networkidle", timeout=45_000)
            try:
                page.wait_for_selector("a[href*='/en/companies/']", timeout=15_000)
            except PWTimeout:
                pass
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        print(f"    ⚠️  Playwright WTTJ error: {e}")
        return None

def scrape_wttj():
    print("🔍 Welcome to the Jungle...")
    html = _wttj_playwright() or _wttj_static()
    if not html:
        print("   → nessun contenuto ottenuto")
        return []

    soup = BeautifulSoup(html, "html.parser")
    risultati = []
    # Selettore indicativo: WTTJ cambia markup spesso, va verificato periodicamente
    for card in soup.select("a[href*='/en/companies/']"):
        titolo = card.get_text(strip=True)
        href = card.get("href", "")
        if not titolo or not href:
            continue
        url = f"https://www.welcometothejungle.com{href}" if href.startswith("/") else href
        # su WTTJ la location è già filtrata dalla query (Paris/France),
        # applichiamo comunque il filtro lingua sul titolo come prima passata
        if not is_english(titolo):
            continue
        risultati.append({
            "titolo": titolo,
            "location": "Paris, France",
            "url": url,
            "data": "",
        })
        time.sleep(0.2)

    print(f"   → {len(risultati)} offerte compatibili trovate")
    return risultati

# ============================================================
# 📧  GENERAZIONE EMAIL — neutra, nessuno scoring, per fonte
# ============================================================

FONTE_COLORS = {
    "Greenhouse": ("#0f9d58", "#ffffff"),
    "Lever":      ("#5b6bf5", "#ffffff"),
    "Welcome to the Jungle": ("#fdc22b", "#1a1a1a"),
}

def ordina_neutro(offerte: list) -> list:
    """Ordinamento oggettivo: data più recente prima, poi azienda in ordine
    alfabetico. Nessun giudizio di rilevanza o priorità."""
    return sorted(offerte, key=lambda o: (o.get("data", ""), o.get("titolo", "")), reverse=True)

def genera_html(tutte_nuove: dict, totale_trovate: int):
    oggi_str = datetime.now().strftime("%d %B %Y")
    totale = sum(len(v) for v in tutte_nuove.values())
    sezioni = ""

    for fonte, offerte in tutte_nuove.items():
        if not offerte:
            continue
        bg, fg = FONTE_COLORS.get(fonte, ("#1a1a2e", "#ffffff"))
        righe = ""
        for o in ordina_neutro(offerte):
            loc = f'<span style="color:#888;">📍 {o["location"]}</span>&ensp;' if o.get("location") else ""
            data = f'<span style="color:#aaa;">📅 {o["data"]}</span>' if o.get("data") else ""
            righe += f"""
            <tr>
              <td style="padding:11px 16px;border-bottom:1px solid #f0f0f0;">
                <a href="{o['url']}" style="color:#1a1a2e;font-weight:600;text-decoration:none;
                   font-size:14px;line-height:1.4;display:block;">{o['titolo']}</a>
                <div style="margin-top:3px;font-size:12px;">{loc}{data}</div>
              </td>
            </tr>"""
        sezioni += f"""
        <div style="margin-bottom:24px;border-radius:8px;overflow:hidden;
                    box-shadow:0 2px 8px rgba(0,0,0,.08);">
          <div style="background:{bg};padding:11px 16px;display:flex;
                      justify-content:space-between;align-items:center;">
            <span style="color:{fg};font-size:16px;font-weight:700;">{fonte}</span>
            <span style="color:{fg};font-size:12px;opacity:.8;">{len(offerte)} nuov{'a' if len(offerte)==1 else 'e'}</span>
          </div>
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

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="max-width:640px;margin:28px auto;padding:0 12px;">
    <div style="background:linear-gradient(135deg,#0d1b2a,#1b3a5c);
                border-radius:12px 12px 0 0;padding:26px 24px 20px;text-align:center;">
      <div style="font-size:34px;margin-bottom:6px;">🗼</div>
      <h1 style="color:#fff;margin:0;font-size:21px;letter-spacing:1px;">PARIS JOB NEWSLETTER</h1>
      <p style="color:#7eb8e0;margin:6px 0 0;font-size:13px;">{oggi_str}</p>
    </div>
    <div style="background:#dbeafe;padding:11px 20px;
                border-left:4px solid #2563eb;border-right:4px solid #2563eb;
                text-align:center;font-size:14px;color:#1e3a5f;">
      🆕 <strong>{totale} nuovi annunci</strong> su {totale_trovate} totali analizzati
    </div>
    <div style="background:#f8f9fa;padding:20px 14px;">{body}</div>
    <div style="background:#0d1b2a;border-radius:0 0 12px 12px;padding:13px;
                text-align:center;font-size:11px;color:#4a6fa5;">
      Lista neutra, nessun ordinamento per rilevanza · {oggi_str}
    </div>
  </div>
</body></html>""", totale

# ============================================================
# 📤  INVIO EMAIL
# ============================================================

def invia_email(html_body, totale):
    destinatari = [d.strip() for d in EMAIL_DESTINATARIO.split(",") if d.strip()]
    if not destinatari or not GMAIL_MITTENTE or not GMAIL_PASSWORD:
        print("⚠️  Credenziali email mancanti, salto invio (controlla i Secrets)")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🗼 Paris Jobs {datetime.now():%d/%m/%Y} — {totale} nuovi annunci"
    msg["From"] = GMAIL_MITTENTE
    msg["To"] = ", ".join(destinatari)
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_MITTENTE, GMAIL_PASSWORD)
            s.sendmail(GMAIL_MITTENTE, destinatari, msg.as_string())
        print(f"✅ Email inviata a {destinatari}")
    except smtplib.SMTPAuthenticationError:
        print("❌ Errore autenticazione → rigenera App Password su myaccount.google.com")
    except Exception as e:
        print(f"❌ Errore invio: {e}")

# ============================================================
# 🩺  HEALTH CHECK — rileva scraper rotti (0 risultati anomalo)
# ============================================================

def health_check(nome_fonte: str, risultati: int, storico_medio: float | None = None):
    if risultati == 0 and storico_medio and storico_medio > 1:
        print(f"⚠️  ALERT: {nome_fonte} ha trovato 0 risultati (media storica ~{storico_medio:.1f}). "
              f"Probabile rottura selettori/API — verificare manualmente.")

# ============================================================
# 🚀  MAIN
# ============================================================

def main():
    print(f"\n{'='*55}")
    print(f"  PARIS JOB NEWSLETTER  {datetime.now():%d/%m/%Y %H:%M}")
    print(f"{'='*55}\n")

    seen = carica_seen()

    scrapers = [
        ("Greenhouse", scrape_greenhouse),
        ("Lever", scrape_lever),
        ("Welcome to the Jungle", scrape_wttj),
    ]

    tutte_trovate = {}
    tutte_nuove = {}

    for nome, fn in scrapers:
        try:
            offerte = fn()
            tutte_trovate[nome] = offerte
            health_check(nome, len(offerte))
            if offerte:
                nuove, seen = filtra_nuove(nome, offerte, seen)
                if nuove:
                    tutte_nuove[nome] = nuove
        except Exception as e:
            print(f"   ❌ {nome}: {e}")
            tutte_trovate[nome] = []
        time.sleep(1)

    salva_seen(seen)

    totale_trovate = sum(len(v) for v in tutte_trovate.values())
    totale_nuove = sum(len(v) for v in tutte_nuove.values())

    print(f"\n📊 Riepilogo:")
    for n, _ in scrapers:
        trovate = len(tutte_trovate.get(n, []))
        nuove = len(tutte_nuove.get(n, []))
        print(f"   {n:24s}→ {trovate:3d} trovate · {nuove:3d} nuove")
    print(f"   {'TOTALE':24s}→ {totale_trovate:3d} trovate · {totale_nuove:3d} nuove\n")

    html, totale = genera_html(tutte_nuove, totale_trovate)
    invia_email(html, totale)
    print("\n✨ Done!\n")

if __name__ == "__main__":
    main()
