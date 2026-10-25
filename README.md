# Paris Job Newsletter

Scraper per annunci di lavoro a Parigi in inglese (senza francese richiesto),
da fonti pubbliche: Greenhouse, Lever, Ashby, SmartRecruiters, Welcome to the Jungle
e i job board pubblici dei fondi VC (portafogli di Accel, Balderton, Alven, XAnge, Sequoia…).

## Setup

1. **Aziende da monitorare — automatico**: non serve curare una lista a mano.
   `discover_companies.py` parte da fonti pubbliche (Station F, YC directory
   filtrata Francia) e trova da solo quali aziende hanno un board Greenhouse/Lever
   attivo, salvandole in `discovered_slugs.json`. Gira in automatico ogni lunedì
   (workflow `discovery.yml`) e `paris_job_newsletter.py` lo legge da solo.
   Se vuoi comunque aggiungere qualcosa a mano, usa `GREENHOUSE_SLUGS_MANUALI` /
   `LEVER_SLUGS_MANUALI` in `paris_job_newsletter.py` — si sommano a quelle scoperte.

2. **Gmail App Password**: su myaccount.google.com → Sicurezza → Password per le app,
   genera una password dedicata (NON la tua password normale).

3. **GitHub Secrets** (Settings → Secrets and variables → Actions):
   - `GMAIL_MITTENTE` — il tuo indirizzo Gmail
   - `GMAIL_PASSWORD` — la App Password generata al punto 2
   - `EMAIL_DESTINATARIO` — dove ricevere la newsletter (anche più indirizzi separati da virgola)

4. **Test locale** (opzionale, prima di affidarti al cloud):
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   export GMAIL_MITTENTE="..."
   export GMAIL_PASSWORD="..."
   export EMAIL_DESTINATARIO="..."
   python paris_job_newsletter.py
   ```

5. **Push su GitHub**: il workflow in `.github/workflows/newsletter.yml` gira automaticamente
   ogni giorno alle 07:00 UTC. Puoi anche avviarlo a mano da tab "Actions" → "Run workflow".

## Filosofia del progetto

- **Nessuno scoring, nessuna priorità per azienda**: la mail mostra solo dati grezzi
  (titolo, location, link, data), raggruppati per fonte. Nessun bias editoriale.
- **Dedup robusta**: fingerprint su titolo+azienda+location normalizzati (non solo URL),
  così le ripubblicazioni con link diverso non si ripresentano. L'azienda è identificata
  dal suo slug, quindi due aziende con lo stesso titolo a Parigi restano annunci distinti.
- **Solo fonti legali**: Greenhouse e Lever espongono API JSON pubbliche pensate per
  essere consumate esternamente. WTTJ è scraping HTML leggero con pause tra le richieste.
  Niente LinkedIn/Indeed/Glassdoor (ToS restrittivi, alto rischio ban).

## Manutenzione

- Se uno scraper trova 0 risultati per più run consecutive, controlla il log
  (`health_check` stampa un warning) — probabile cambio di struttura del sito.
- WTTJ è la parte più fragile: il 25/10/2026 ha cambiato sito. Ora leggiamo la pagina
  `/en/companies/{slug}/jobs` di ogni azienda (link `/jobs/{titolo}_{città}`). Se la riga
  `[diagnostica]` del log mostra 0 annunci letti, la struttura è cambiata di nuovo.
- **Fondi VC**: i board sono in `VC_GETRO` e `VC_CONSIDER` in `paris_job_newsletter.py`.
  Per aggiungere un fondo che usa Getro serve l'id del board (nella pagina, nel JSON
  `__NEXT_DATA__` → `"network":{"id":...}`); per Consider basta il dominio.
  Al primo run di una fonte nuova arrivano solo gli annunci degli ultimi 7 giorni.
- **Filtri sul titolo**: stage/internship e ruoli troppo senior sono esclusi
  (`ESCLUDI_TITOLI` in `paris_job_newsletter.py`, modificabile).
- **Controllo descrizioni**: per WTTJ e Portfolio VC (che danno solo il titolo) la
  pagina di ogni annuncio in uscita viene letta per scartare chi chiede il francese.
- **Avvisi**: se una fonte trova 0 annunci o crolla rispetto alla media, la mail lo
  segnala in un riquadro arancione. I board che rispondono 404 per 5 run di fila
  vengono ricontrollati solo il lunedì (`slug_inattivi.json`).
- **Auto-discovery dai fondi VC**: `discover_companies.py` legge anche gli annunci dei
  board VC e ricava dal link la piattaforma dell'azienda (es. `jobs.ashbyhq.com/gorgias`).
  Per le aziende appena aggiunte, al primo run arrivano solo gli annunci degli ultimi
  7 giorni (`aziende_note.json`); lo stesso link non viene mai mandato due volte.
- WTTJ apre `WTTJ_PAGINE_PARALLELE` pagine insieme (default 4). Se nel log compaiono
  molti timeout o il circuit breaker scatta spesso, abbassa il numero.
- **Nomi azienda**: se un nome appare scritto male nella mail, aggiungilo a
  `NOMI_AZIENDE` in `paris_job_newsletter.py` (slug → nome giusto).
- **Filtro francese**: le frasi che fanno scartare un annuncio sono in
  `FRENCH_REQUIRED_PATTERNS`; quelle che lo salvano ("is a plus", "nice to have"…)
  in `FRENCH_OPTIONAL_MARKERS`. Il controllo è frase per frase.
