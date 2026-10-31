"""Prova temporanea 3: scraper Teamtailor e verifica della discovery sui siti veri."""
import sys
sys.path.insert(0, ".")
import paris_job_newsletter as pj
import discover_companies as dc

pj.TEAMTAILOR_SLUGS[:] = ["aqemia", "exotrail", "homa", "legalplace", "mypos", "pasqal", "payplug",
                          "playplay", "recommerce", "skello", "stoik", "stormgroup", "transdev"]
out = pj.scrape_teamtailor()
for o in out: print("  ", o["azienda"], "|", o["titolo"], "|", o["location"], "|", o["data"], "|", o["url"])

verifiche = {"greenhouse": dc.verifica_greenhouse, "lever": dc.verifica_lever, "ashby": dc.verifica_ashby,
             "smartrecruiters": dc.verifica_smartrecruiters, "teamtailor": dc.verifica_teamtailor}
for nome in ["payplug 1", "aqemia 1", "skello", "akur8 1", "scoreplay inc", "azienda inesistente xyz"]:
    print(nome, "→", dc._verifica_nome_su_tutte_le_fonti(nome, verifiche)[1])
print("con 'gia':", dc._verifica_nome_su_tutte_le_fonti("skello", verifiche, {"ashby": "skello-x"})[1])
