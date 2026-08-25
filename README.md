# Paris Job Newsletter

Scraper per annunci di lavoro a Parigi in inglese (senza francese richiesto),
da fonti pubbliche legali: Greenhouse, Lever, Welcome to the Jungle.

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
  così le ripubblicazioni con link diverso non si ripresentano.
- **Solo fonti legali**: Greenhouse e Lever espongono API JSON pubbliche pensate per
  essere consumate esternamente. WTTJ è scraping HTML leggero con pause tra le richieste.
  Niente LinkedIn/Indeed/Glassdoor (ToS restrittivi, alto rischio ban).

## Manutenzione

- Se uno scraper trova 0 risultati per più run consecutive, controlla il log
  (`health_check` stampa un warning) — probabile cambio di struttura del sito.
- Il selettore CSS di WTTJ (`a[href*='/en/companies/']`) va verificato periodicamente:
  è la parte più fragile del progetto.
