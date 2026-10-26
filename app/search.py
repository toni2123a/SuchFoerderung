"""Regelbasierte, nachvollziehbare Suche ohne externe KI.

Grundsätze:
* Harte Ausschlüsse nur, wenn strukturierte Quelldaten sie eindeutig tragen.
* Unbekannte Angaben werden nie als erfüllt behandelt, sondern als "prüfen" ausgewiesen.
* Relevanz = gewichtete Treffer der Suchbegriffe je Feld (+ Themenbereich), keine Prozentwerte.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import config, db, regions
from .textutil import fold, sentences, short

# --- Themen und Synonyme ----------------------------------------------------

TOPICS = {
    "digitalisierung": {"label": "Digitalisierung", "areas": ["digitalisierung"],
                        "terms": ["digital", "software", "informationstechn", "e-commerce", "cyber"]},
    "energie": {"label": "Energie", "areas": ["energieeffizienz_erneuerbare_energien"],
                "terms": ["energie", "photovoltaik", "solar", "wärmepumpe", "erneuerbar"]},
    "gebaeude": {"label": "Gebäude", "areas": ["wohnungsbau_modernisierung", "staedtebau_stadterneuerung"],
                 "terms": ["gebäude", "sanierung", "modernisierung", "wohnraum", "dämmung"]},
    "gruendung": {"label": "Gründung", "areas": ["existenzgruendung_festigung"],
                  "terms": ["gründung", "existenzgründ", "start-up", "gründer"]},
    "weiterbildung": {"label": "Weiterbildung", "areas": ["aus_weiterbildung"],
                      "terms": ["weiterbildung", "qualifizierung", "fortbildung", "schulung"]},
    "mobilitaet": {"label": "Mobilität", "areas": ["mobilitaet"],
                   "terms": ["mobilität", "elektromobil", "ladeinfrastruktur", "radverkehr", "fahrzeug"]},
    "forschung": {"label": "Forschung & Innovation",
                  "areas": ["forschung_innovation_themenspezifisch", "forschung_innovation_themenoffen"],
                  "terms": ["forschung", "innovation", "entwicklung"]},
    "umwelt": {"label": "Umwelt & Naturschutz", "areas": ["umwelt_naturschutz"],
               "terms": ["umwelt", "naturschutz", "klimaschutz", "biodiversität"]},
    "landwirtschaft": {"label": "Landwirtschaft", "areas": ["landwirtschaft_laendliche_entwicklung"],
                       "terms": ["landwirtschaft", "ländlich", "agrar"]},
    "kultur": {"label": "Kultur, Medien & Sport", "areas": ["kultur_medien_sport"],
               "terms": ["kultur", "kunst", "sport", "medien"]},
    "soziales": {"label": "Gesundheit & Soziales", "areas": ["gesundheit_soziales"],
                 "terms": ["sozial", "gesundheit", "pflege", "inklusion"]},
    "finanzierung": {"label": "Unternehmensfinanzierung", "areas": ["unternehmensfinanzierung"],
                     "terms": ["investition", "finanzierung", "betriebsmittel", "wachstum"]},
}

# Kleine, bewusst überschaubare Synonymliste. Schlüssel werden auch in zusammengesetzten
# Wörtern erkannt (z. B. "Photovoltaikanlage" enthält "photovoltaik").
SYNONYMS = {
    "photovoltaik": ["photovoltaik", "solar"],
    "solar": ["solar", "photovoltaik"],
    "pv": ["photovoltaik", "solar"],
    "waermepumpe": ["wärmepumpe", "heizung"],
    "heizung": ["heizung", "wärmepumpe", "heizungstausch"],
    "daemmung": ["dämmung", "wärmedämmung", "energetische sanierung"],
    "sanier": ["sanierung", "modernisierung"],
    "elektroauto": ["elektromobil", "elektrofahrzeug", "ladeinfrastruktur"],
    "e-auto": ["elektromobil", "elektrofahrzeug"],
    "ladesaeule": ["ladeinfrastruktur", "ladestation", "ladepunkt"],
    "wallbox": ["ladeinfrastruktur", "ladestation"],
    "lastenrad": ["lastenrad", "lastenfahrrad"],
    "software": ["software", "digital"],
    "app": ["digital", "software"],
    "website": ["digital", "online"],
    "onlineshop": ["e-commerce", "online"],
    "ki": ["künstliche intelligenz"],
    "kuenstliche intelligenz": ["künstliche intelligenz"],
    "it": ["informationstechn", "digital"],
    "startup": ["start-up", "gründung", "existenzgründ"],
    "start-up": ["start-up", "gründung", "existenzgründ"],
    "gruend": ["gründung", "existenzgründ"],
    "selbststaendig": ["selbstständig", "existenzgründ", "gründung"],
    "weiterbild": ["weiterbildung", "qualifizierung", "fortbildung"],
    "ausbildung": ["ausbildung", "auszubildende"],
    "barrierefrei": ["barrierefrei", "barrierearm"],
    "ehrenamt": ["ehrenamt", "bürgerschaftlich"],
    "verein": ["verein", "ehrenamt"],
    "denkmal": ["denkmal"],
    "export": ["export", "außenwirtschaft", "internationalisierung"],
    "messe": ["messe", "ausstellung"],
    "wohnung": ["wohnraum", "wohnungsbau", "wohneigentum"],
    "eigenheim": ["wohneigentum", "eigenheim", "wohnraum"],
    "hausbau": ["wohneigentum", "neubau"],
}

STOPWORDS = set("""
aber alle allem allen aller alles als also am an ander andere anderem anderen anderer anderes
auch auf aus bei bin bis bist da damit dann der den des dem die das dass daß du dein deine
durch ein eine einem einen einer eines er es etwa euer für gegen hat habe haben hatte ich ihr
ihre im in ist ja jede jedem jeden jeder jedes kann kein keine machen man mein meine mit möchte
möchten moechte nach nicht noch nur ob oder ohne sehr sein seine sich sie sind so soll sollen
über um und uns unser unsere unter viel vom von vor wann war was weil wenn werden wie wir will
wir wird wo wollen zu zum zur zwischen neue neuen neuer neues planen plane geplant gerne bitte
umsetzen umsetzung möglich etwas einige mehr viele unseren unserer unserem meinen meinem meiner
möchten möchtest willst wollt würden würde gern brauchen brauche benötigen benötige suchen suche
bieten biete anbieten bietet machen mache haben dafür hierfür dabei sowie
""".split())


norm = fold


def _alt(text: str) -> str:
    """Umlaut-Umschreibung (ae/oe/ue) für Synonym-Schlüssel."""
    return text.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")


def _stem(word: str) -> str:
    for suffix in ("ungen", "ung", "en", "er", "es", "e", "n", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 5:
            return word[: -len(suffix)]
    return word


@dataclass
class Term:
    text: str          # Anzeigeform (wie eingegeben bzw. Synonym)
    key: str           # normalisierte Suchform (ggf. Wortstamm)
    weight: float      # 1.0 = Nutzerbegriff, 0.6 = Synonym/Thema
    group: str         # Ursprungswort; Synonyme teilen die Gruppe ihres Wortes
    idf: float = 1.0   # Seltenheit in der Datenbasis (häufige Wörter zählen weniger)
    full: str = ""     # vollständige Wortform (ohne Stammbildung)
    content: bool = True  # großgeschriebenes Wort (Substantiv) bzw. Synonym eines solchen
    synonym: bool = False
    word_part: bool = False  # Synonym ist Bestandteil des eingegebenen Wortes ("photovoltaik" in "Photovoltaikanlage")
    _rx: re.Pattern | None = None

    def pattern(self) -> re.Pattern:
        """Wortstamm nur am Wortanfang; mitten im Wort (Kompositum) nur die volle Wortform.
        So passt "Reinigung" auf "Glasreinigung", der Stamm "reinig" aber nicht auf "Breinig".
        Kurze Wörter (< 6 Zeichen) nur als ganzes Wort mit üblichen Endungen ("Dach" ≠ "Dachfonds")."""
        if self._rx is None:
            full = self.full or self.key
            if len(self.key) < 6:
                self._rx = re.compile(r"\b" + re.escape(self.key) + r"(e|en|er|es|n|s)?\b")
                return self._rx
            alts = [r"\b" + re.escape(self.key)]
            if len(full) >= 5:
                alts.append(re.escape(full))
            self._rx = re.compile("|".join(alts))
        return self._rx

    def found(self, text: str) -> bool:
        return bool(text) and self.key in text and bool(self.pattern().search(text))


def build_terms(*texts: str, extra: list[str] | None = None) -> list[Term]:
    terms: dict[str, Term] = {}

    def add(display: str, key: str, weight: float, group: str, content: bool = True, word_part: bool = False):
        key = norm(key).strip()
        if len(key) < 3:
            return
        cur = terms.get(key)
        if cur is None or cur.weight < weight:
            terms[key] = Term(display, key, weight, group, full=norm(display).strip(), content=content,
                              synonym=weight < 1, word_part=word_part)

    for text in texts:
        if not text:
            continue
        words = re.findall(r"[\wÄÖÜäöüß][\wÄÖÜäöüß\-]*", text)
        for raw in words:
            low = raw.casefold().strip("-")
            alt = _alt(low)
            group = norm(low)
            # Im Deutschen tragen großgeschriebene Wörter (Substantive) den Inhalt
            content = raw[:1].isupper()
            for skey, values in SYNONYMS.items():
                if alt == skey or (len(skey) >= 5 and skey in alt):
                    for v in values:
                        add(v, v, 0.6, group, content, word_part=norm(v) in norm(low) and norm(v) != norm(low))
            if low in STOPWORDS or len(low) < 4 or low.isdigit():
                continue
            add(raw.strip("-"), _stem(low), 1.0, group, content)
    if not any(t.content for t in terms.values()):
        # durchgehend kleingeschrieben: alle Wörter gleich behandeln
        for t in terms.values():
            t.content = True
    else:
        # kleingeschriebene Wörter (Verben, Adjektive) zählen halb
        for t in terms.values():
            if not t.content:
                t.weight *= 0.5
    for e in extra or []:
        add(e, e, 0.6, "thema")
    result = list(terms.values())[:40]
    # Komposita mit dem Grundwort zusammenfassen: "Glasreinigung" gehört zur Gruppe "Reinigung",
    # "Vereinsheim" zur Gruppe des Begriffs "Verein".
    for t in result:
        if t.group == "thema":
            continue
        for base in result:
            if (base is not t and base.group != "thema" and base.group != t.group
                    and base.key in t.key and len(base.key) >= 5):
                old = t.group
                for x in result:
                    if x.group == old:
                        x.group = base.group
    return result


def fts_query(terms: list[Term]) -> str | None:
    parts = ['"' + t.key.replace('"', "") + '"' for t in terms if len(t.key) >= 3]
    return " OR ".join(parts) if parts else None


def apply_idf(conn, table: str, terms: list[Term]) -> None:
    """Gewichtet Begriffe nach Seltenheit (inverse Dokumenthäufigkeit) im jeweiligen Index."""
    import math
    total = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] or 1
    for t in terms:
        df = conn.execute(f"SELECT count(*) FROM {table} WHERE {table} MATCH ?",
                          ('"' + t.key.replace('"', "") + '"',)).fetchone()[0]
        # geglättet: häufige Begriffe zählen weniger, aber nie gar nicht
        t.idf = 0.0 if df == 0 else math.log(1 + total / df)


def coverage(terms: list[Term], hits: list[dict]) -> float:
    """Anteil der (nach Seltenheit gewichteten) eigenen Suchwörter, die im Treffer vorkommen.

    * Eine Wortgruppe zählt mit der Seltenheit ihres häufigsten Begriffs (max. 3,0).
    * Gruppen aus kleingeschriebenen Wörtern (Verben, Adjektive) zählen halb.
    * Treffer nur in nachrangigen Feldern (langer Richtlinientext) zählen halb,
      Treffer nur über echte Synonyme zu 60 %."""
    groups: dict[str, float] = {}
    content_groups = {t.group for t in terms if t.content}
    for t in terms:
        if t.group != "thema" and t.idf > 0:
            groups[t.group] = min(groups.get(t.group, t.idf), t.idf, 3.0)
    for g in groups:
        if g not in content_groups:
            groups[g] *= 0.5
    if not groups:
        return 0.0
    hit_factor: dict[str, float] = {}
    for h in hits:
        f = (1.0 if h["best_weight"] >= 2 else 0.5) * (0.6 if h["synonym"] and not h["word_part"] else 1.0)
        hit_factor[h["group"]] = max(hit_factor.get(h["group"], 0.0), f)
    return sum(v * hit_factor.get(g, 0.0) for g, v in groups.items()) / sum(groups.values())


MIN_COVERAGE = 0.25


def score_fields(terms: list[Term], fields: list[tuple[str, str, float]]) -> tuple[float, list[dict]]:
    """fields: (Feldname, normalisierter Text, Gewicht). Gibt Score und Trefferliste zurück."""
    score = 0.0
    hits = []
    for t in terms:
        found_in = []
        best = 0.0
        for name, text, weight in fields:
            if t.found(text):
                found_in.append(name)
                best = max(best, weight)
        if found_in:
            score += (best + 0.3 * (len(found_in) - 1)) * t.weight * t.idf
            hits.append({"term": t.text, "fields": found_in, "synonym": t.synonym, "word_part": t.word_part,
                         "group": t.group, "best_weight": best})
    return score, hits


def _explain_hits(hits: list[dict], limit: int = 6) -> list[str]:
    out = []
    for h in sorted(hits, key=lambda h: (h["synonym"], -len(h["fields"])))[:limit]:
        prefix = "Verwandter Begriff" if h["synonym"] else "Suchbegriff"
        out.append(f"{prefix} „{h['term']}“ gefunden in: {', '.join(h['fields'])}")
    return out


# --- Förderprogramme --------------------------------------------------------

APPLICANTS = {
    "privatperson": {"label": "Privatperson", "slugs": ["privatperson"]},
    "unternehmen": {"label": "Unternehmen/Selbstständige", "slugs": ["unternehmen", "existenzgruenderin"]},
    "verein": {"label": "Verein", "slugs": ["verband_vereinigung"]},
    "kommune": {"label": "Kommune", "slugs": ["kommune", "oeffentliche_einrichtung"]},
    "forschung": {"label": "Forschungseinrichtung", "slugs": ["forschungseinrichtung", "hochschule"]},
}
ELIGIBLE_LABELS = {
    "unternehmen": "Unternehmen", "existenzgruenderin": "Existenzgründer/-innen",
    "verband_vereinigung": "Verbände/Vereinigungen", "oeffentliche_einrichtung": "Öffentliche Einrichtungen",
    "kommune": "Kommunen", "forschungseinrichtung": "Forschungseinrichtungen", "hochschule": "Hochschulen",
    "privatperson": "Privatpersonen", "bildungseinrichtung": "Bildungseinrichtungen",
}
FUNDING_TYPES = {
    "zuschuss": "Zuschuss", "darlehen": "Darlehen", "buergschaft": "Bürgschaft",
    "beteiligung": "Beteiligung", "garantie": "Garantie", "sonstige": "Sonstige",
}
FUNDER_LEVELS = {"bund": "Bund", "land": "Land", "eu": "EU"}
AREA_LABELS = {
    "infrastruktur": "Infrastruktur", "gesundheit_soziales": "Gesundheit & Soziales",
    "unternehmensfinanzierung": "Unternehmensfinanzierung",
    "forschung_innovation_themenspezifisch": "Forschung & Innovation (themenspezifisch)",
    "aus_weiterbildung": "Aus- & Weiterbildung", "umwelt_naturschutz": "Umwelt- & Naturschutz",
    "landwirtschaft_laendliche_entwicklung": "Landwirtschaft & Ländliche Entwicklung",
    "existenzgruendung_festigung": "Existenzgründung & -festigung",
    "energieeffizienz_erneuerbare_energien": "Energieeffizienz & Erneuerbare Energien",
    "kultur_medien_sport": "Kultur, Medien & Sport",
    "forschung_innovation_themenoffen": "Forschung & Innovation (themenoffen)",
    "mobilitaet": "Mobilität", "digitalisierung": "Digitalisierung",
    "wohnungsbau_modernisierung": "Wohnungsbau & Modernisierung",
    "regionalfoerderung": "Regionalförderung", "smart_cities_regionen": "Smart Cities & Regionen",
    "arbeit": "Arbeit", "beratung": "Beratung", "staedtebau_stadterneuerung": "Städtebau & Stadterneuerung",
    "frauenfoerderung": "Frauenförderung", "aussenwirtschaft": "Außenwirtschaft",
    "messen_ausstellungen": "Messen & Ausstellungen", "corona": "Corona-Hilfe",
}
SIZE_LABELS = {
    "kleinstunternehmen": "Kleinstunternehmen", "kleines_unternehmen": "kleine Unternehmen",
    "mittleres_unternehmen": "mittlere Unternehmen", "grosses_unternehmen": "große Unternehmen",
}

_AMOUNT_RE = re.compile(r"(\d[\d.,]*\s*(Euro|EUR|€|Prozent|%)|Höchstbetrag|Zuschuss von|bis zu \d)", re.I)
_START_RE = re.compile(r"(begonnen|Vorhabenbeginn|Vorhabensbeginn|Maßnahmenbeginn|Maßnahmebeginn|vorzeitig\w* (Maßnahme|Vorhaben)\w*beginn)", re.I)


def size_class(employees: int | None) -> str | None:
    """Größenklasse allein nach Mitarbeiterzahl (EU-Schwellen 10/50/250)."""
    if employees is None:
        return None
    if employees < 10:
        return "kleinstunternehmen"
    if employees < 50:
        return "kleines_unternehmen"
    if employees < 250:
        return "mittleres_unternehmen"
    return "grosses_unternehmen"


@dataclass
class FoerderQuery:
    applicant: str
    land: str
    description: str
    topic: str | None = None
    budget: float | None = None
    employees: int | None = None
    started: str = "unbekannt"     # ja | nein | unbekannt
    funding_type: str | None = None
    keyword: str | None = None


def _labels(slugs, mapping):
    return [mapping.get(s, s) for s in slugs]


def amount_excerpts(*texts: str, limit: int = 2) -> list[str]:
    """Originalsätze mit Beträgen/Prozentangaben – unverändert übernommen."""
    out = []
    for text in texts:
        for s in sentences(text or ""):
            if _AMOUNT_RE.search(s) and s not in out:
                out.append(short(s, 300))
                if len(out) >= limit:
                    return out
    return out


def start_excerpt(*texts: str) -> str | None:
    for text in texts:
        for s in sentences(text or ""):
            if _START_RE.search(s):
                return short(s, 300)
    return None


def search_programs(conn, q: FoerderQuery, limit: int = 100) -> dict:
    if q.applicant not in APPLICANTS:
        raise ValueError("Unbekannter Antragstellertyp")
    if q.land not in regions.BUNDESLAENDER:
        raise ValueError("Unbekanntes Bundesland")
    topic = TOPICS.get(q.topic or "")
    terms = build_terms(q.description, q.keyword or "")
    topic_terms = build_terms(extra=topic["terms"]) if topic else []
    keyword_terms = build_terms(q.keyword) if q.keyword else []

    all_terms = terms + [t for t in topic_terms if t.key not in {x.key for x in terms}]
    apply_idf(conn, "programs_fts", all_terms)
    rows = []
    query = fts_query(all_terms)
    candidate_ids = set()
    if query:
        candidate_ids = {r["id"] for r in conn.execute(
            "SELECT id FROM programs_fts WHERE programs_fts MATCH ?", (query,))}
    if topic:
        for r in conn.execute("SELECT id, areas FROM programs WHERE active=1"):
            if set(db.jload(r["areas"])) & set(topic["areas"]):
                candidate_ids.add(r["id"])
    if candidate_ids:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS cand(id TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM cand")
        conn.executemany("INSERT OR IGNORE INTO cand(id) VALUES (?)", [(i,) for i in candidate_ids])
        rows = conn.execute(
            "SELECT p.* FROM programs p JOIN cand c ON c.id = p.id WHERE p.active=1").fetchall()

    excluded = {"region": 0, "applicant": 0, "company_size": 0, "funding_type": 0, "keyword": 0}
    wanted = set(APPLICANTS[q.applicant]["slugs"])
    user_size = size_class(q.employees) if q.applicant == "unternehmen" else None
    results = []
    for r in rows:
        why, check = [], []
        region_factor = 1.0
        gebiete = db.jload(r["regions"])
        known = [g for g in gebiete if g in regions.FDB_BUNDESWEIT or g in regions.FDB_GEBIET]
        lands = {regions.FDB_GEBIET[g] for g in gebiete if g in regions.FDB_GEBIET}
        bundesweit = any(g in regions.FDB_BUNDESWEIT for g in gebiete)
        if bundesweit:
            why.append("Fördergebiet: bundesweit")
        elif q.land in lands:
            why.append(f"Fördergebiet: {regions.BUNDESLAENDER[q.land]}")
        elif known and not (set(gebiete) - set(known)):
            excluded["region"] += 1
            continue
        else:
            check.append("Fördergebiet in der Datenbank nicht eindeutig angegeben – Gültigkeit für "
                         f"{regions.BUNDESLAENDER[q.land]} prüfen.")
            region_factor = 0.8
        if lands and not bundesweit:
            check.append("Ob Sitz, Betriebsstätte oder Vorhabensort im Fördergebiet liegen muss, regelt die Richtlinie.")

        eligible = db.jload(r["eligible"])
        if eligible:
            match = wanted & set(eligible)
            if not match:
                excluded["applicant"] += 1
                continue
            why.append("Förderberechtigt laut Datenbank: " + ", ".join(_labels(sorted(match), ELIGIBLE_LABELS)))
            if q.applicant == "kommune" and match == {"oeffentliche_einrichtung"}:
                check.append("Programm nennt „Öffentliche Einrichtungen“, nicht ausdrücklich Kommunen – prüfen.")
            if q.applicant == "unternehmen" and match == {"existenzgruenderin"}:
                check.append("Programm richtet sich laut Datenbank an Existenzgründer/-innen – prüfen, ob das auf dich zutrifft.")
        else:
            check.append("Förderberechtigte sind in der Datenbank nicht angegeben.")

        sizes = db.jload(r["company_sizes"])
        if q.applicant == "unternehmen" and sizes:
            if user_size == "grosses_unternehmen" and "grosses_unternehmen" not in sizes:
                # Ab 250 Beschäftigten ist ein Unternehmen nach EU-Definition kein KMU.
                excluded["company_size"] += 1
                continue
            if user_size and user_size in sizes:
                why.append(f"Unternehmensgröße laut Datenbank zugelassen: {SIZE_LABELS[user_size]} "
                           "(nur nach Mitarbeiterzahl bewertet)")
                if user_size != "grosses_unternehmen":
                    check.append("KMU-Status hängt auch von Umsatz, Bilanzsumme und verbundenen Unternehmen ab.")
            elif user_size:
                check.append("Zugelassene Größen laut Datenbank: " + ", ".join(_labels(sizes, SIZE_LABELS))
                             + " – deine Einordnung prüfen.")
            elif set(sizes) != set(SIZE_LABELS):
                check.append("Nur für " + ", ".join(_labels(sizes, SIZE_LABELS)) + " – Unternehmensgröße prüfen.")

        ftypes = db.jload(r["funding_types"])
        if q.funding_type and q.funding_type not in ftypes:
            excluded["funding_type"] += 1
            continue

        title_n, teaser_n, kw_n = norm(r["title"]), norm(r["teaser"]), norm(r["keywords"])
        summary_n, body_n = norm(r["summary"]), norm((r["eligibility"] or "") + " " + (r["legal_text"] or ""))
        fields = [("Titel", title_n, 6.0), ("Stichwörtern", kw_n, 4.0), ("Kurztext", teaser_n, 3.0),
                  ("Zusammenfassung", summary_n, 2.0), ("Richtlinientext", body_n, 1.0)]
        if keyword_terms:
            if not score_fields(keyword_terms, fields)[1]:
                excluded["keyword"] += 1
                continue
        score, hits = score_fields(terms, fields)
        areas = db.jload(r["areas"])
        topic_match = bool(topic and set(areas) & set(topic["areas"]))
        if topic_match:
            score += 5
            why.append("Förderbereich passt zum Thema „" + topic["label"] + "“: "
                       + ", ".join(_labels(sorted(set(areas) & set(topic["areas"])), AREA_LABELS)))
        if coverage(terms, hits) < MIN_COVERAGE:
            if not topic_match:
                continue
            why.append("Hinweis: Vor allem der Themenbereich passt, deine Beschreibung kommt im Text kaum vor.")
            score *= 0.5
        score *= region_factor
        why = _explain_hits(hits) + why

        if q.started == "ja":
            ex = start_excerpt(r["eligibility"], r["summary"], r["legal_text"])
            check.append("Dein Vorhaben hat bereits begonnen – prüfen, ob ein Beginn vor Antragstellung zulässig ist."
                         + (f" Auszug aus der Quelle: „{ex}“" if ex else ""))
        elif q.started == "unbekannt":
            check.append("Klären, ob das Vorhaben vor der Antragstellung beginnen darf.")
        if q.budget:
            check.append(f"Dein geplantes Budget ({q.budget:,.0f} €)".replace(",", ".")
                         + " mit Förderhöhe sowie Mindest- und Höchstbeträgen der Richtlinie abgleichen.")
        check.append("Alle Voraussetzungen in der Richtlinie bzw. beim Fördergeber prüfen.")

        results.append({
            "id": r["id"],
            "score": round(score, 2),
            "title": r["title"],
            "description": short(r["teaser"] or r["summary"], 400) or "Nicht angegeben",
            "funder": db.jload(r["organisations"]) or [],
            "funder_level": _labels(db.jload(r["funder_levels"]), FUNDER_LEVELS),
            "regions": [regions.fdb_region_label(g) for g in gebiete] or ["Nicht angegeben"],
            "funding_types": _labels(ftypes, FUNDING_TYPES) or ["Nicht angegeben"],
            "amount_excerpts": amount_excerpts(r["summary"], r["teaser"]),
            "deadline": None,
            "status": "In der Förderdatenbank gelistet",
            "why": why,
            "check": check,
            "source_url": r["source_url"],
        })
    results.sort(key=lambda x: (-x["score"], x["title"]))
    return {"total": len(results), "results": results[:limit], "excluded": excluded,
            "terms": [t.text for t in all_terms]}


def program_detail(conn, program_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
    if not r:
        return None
    return {
        "id": r["id"],
        "active": bool(r["active"]),
        "title": r["title"],
        "teaser": r["teaser"],
        "summary": r["summary"],
        "eligibility": r["eligibility"],
        "legal_text": r["legal_text"],
        "procedure_text": r["procedure_text"],
        "keywords": r["keywords"],
        "funding_types": _labels(db.jload(r["funding_types"]), FUNDING_TYPES),
        "regions": [regions.fdb_region_label(g) for g in db.jload(r["regions"])],
        "funder_level": _labels(db.jload(r["funder_levels"]), FUNDER_LEVELS),
        "eligible": _labels(db.jload(r["eligible"]), ELIGIBLE_LABELS),
        "areas": _labels(db.jload(r["areas"]), AREA_LABELS),
        "company_sizes": _labels(db.jload(r["company_sizes"]), SIZE_LABELS),
        "funder": db.jload(r["organisations"]),
        "contacts": db.jload(r["contacts"]),
        "links": db.jload(r["links"]),
        "source_url": r["source_url"],
        "source_id": r["id"],
        "last_seen": r["last_seen"],
    }


# --- Öffentliche Aufträge ---------------------------------------------------

STATUS_LABELS = {
    "offen": "Angebots-/Teilnahmefrist läuft",
    "frist_unbekannt": "Frist nicht angegeben",
    "frist_abgelaufen": "Frist abgelaufen",
    "vorinformation": "Vorinformation (noch keine Ausschreibung)",
    "vergeben": "Ergebnis veröffentlicht",
    "aufgehoben": "Ohne Zuschlag beendet",
    "sonstiges": "Sonstige Bekanntmachung",
}
CATEGORY_LABELS = {
    "ausschreibung": "Ausschreibung", "aenderung": "Änderung/Berichtigung",
    "vorinformation": "Vorinformation", "ergebnis": "Ergebnis/Vergabe", "aufhebung": "Aufhebung/ohne Zuschlag",
    "sonstiges": "Sonstige",
}
NATURE_LABELS = {"works": "Bauleistung", "supplies": "Lieferleistung", "services": "Dienstleistung"}
CRITERIA_LABELS = {
    "slc-suit": "Befähigung zur Berufsausübung (z. B. Register, Erlaubnisse)",
    "slc-stand": "Wirtschaftliche und finanzielle Leistungsfähigkeit",
    "slc-abil": "Technische und berufliche Leistungsfähigkeit (z. B. Referenzen)",
    "sui-act": "Befähigung zur Berufsausübung",
    "ef-stand": "Wirtschaftliche und finanzielle Leistungsfähigkeit",
    "tp-abil": "Technische und berufliche Leistungsfähigkeit",
}
STATUS_SETS = {
    "teilnahme": {"offen", "frist_unbekannt"},
    "offen": {"offen"},
    "alle": set(STATUS_LABELS),
}


def criteria_label(code: str) -> str:
    for prefix, label in CRITERIA_LABELS.items():
        if code.startswith(prefix):
            return label
    return "Sonstige Eignungskriterien"


def parse_cpv(text: str | None) -> list[str]:
    """CPV-Codes (z. B. 45000000-7 oder 72) in Präfixe ohne Prüfziffer und Nullen umwandeln."""
    out = []
    for code in re.findall(r"\d{2,8}", text or ""):
        prefix = code.rstrip("0") or code[:2]
        if len(prefix) < 2:
            prefix = code[:2]
        if prefix not in out:
            out.append(prefix)
    return out


@dataclass
class AuftragQuery:
    offering: str
    lands: list[str] = field(default_factory=list)   # leer = bundesweit
    keywords: str | None = None
    cpv: str | None = None
    qualifications: str | None = None
    status: str = "teilnahme"
    land_filter: str | None = None
    keyword: str | None = None


def _fmt_deadline(dl: dict | None) -> str | None:
    if not dl:
        return None
    ts = datetime.fromisoformat(dl["utc"]).astimezone(config.TZ)
    return ts.strftime("%d.%m.%Y, %H:%M Uhr") if dl.get("time_given") else ts.strftime("%d.%m.%Y")


def _lot_deadlines(lots: list[dict], now: datetime) -> list[dict]:
    out = []
    for lot in lots:
        for kind, label in (("tender_deadline", "Angebotsfrist"), ("participation_deadline", "Teilnahmefrist")):
            dl = lot.get(kind)
            if dl:
                out.append({"lot": lot.get("id"), "lot_title": lot.get("title"), "kind": label,
                            "text": _fmt_deadline(dl),
                            "expired": datetime.fromisoformat(dl["utc"]) < now})
    return out


def _places(lots: list[dict]) -> list[str]:
    out = []
    for lot in lots:
        for loc in lot.get("locations") or []:
            land = regions.nuts_to_land(loc.get("nuts")) or regions.city_to_land(loc.get("city"))
            land_name = regions.BUNDESLAENDER.get(land, "") if land else ""
            city = loc.get("city") or ""
            parts = [city, land_name if land_name.casefold() != city.casefold() else ""]
            if not any(parts):
                if regions.nuts_is_foreign(loc.get("nuts")) or (loc.get("country") and loc.get("country") != "DEU"):
                    parts = [f"Ausland ({loc.get('country') or loc.get('nuts')})"]
                elif loc.get("description"):
                    parts = [short(loc["description"], 80)]
            label = ", ".join(p for p in parts if p)
            if label and label not in out:
                out.append(label)
    return out


def _value_text(value, currency) -> str:
    if value is None:
        return "Nicht angegeben"
    txt = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{txt} {currency or ''} (geschätzt)".strip()


def notice_links(notice_id: str, version: str) -> dict:
    return {
        "notice_url": config.VERGABE_NOTICE_URL.format(notice_id=notice_id, version=version),
        "xml_url": config.VERGABE_NOTICE_API_URL.format(notice_id=notice_id, version=version),
    }


def search_procedures(conn, q: AuftragQuery, limit: int = 100, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    for land in q.lands + ([q.land_filter] if q.land_filter else []):
        if land not in regions.BUNDESLAENDER:
            raise ValueError("Unbekanntes Bundesland")
    statuses = STATUS_SETS.get(q.status)
    if statuses is None:
        raise ValueError("Unbekannter Statusfilter")
    terms = build_terms(q.offering, q.keywords or "")
    keyword_terms = build_terms(q.keyword) if q.keyword else []
    cpv_prefixes = parse_cpv(q.cpv)
    # Qualifikationen als ganze Phrasen vergleichen ("ISO 9001", "Präqualifikation")
    qual_phrases = [(p.strip(), norm(p.strip())) for p in re.split(r"[,;\n]", q.qualifications or "")
                    if len(p.strip()) >= 3]

    candidate_ids = set()
    apply_idf(conn, "procedures_fts", terms)
    query = fts_query(terms)
    if query:
        candidate_ids = {r["procedure_id"] for r in conn.execute(
            "SELECT procedure_id FROM procedures_fts WHERE procedures_fts MATCH ?", (query,))}
    rows = conn.execute(
        "SELECT p.*, n.title, n.description, n.buyer_name, n.buyer_city, n.nature, n.cpv, n.lots,"
        " n.estimated_value, n.currency, n.document_url, n.notice_type, n.pub_day, n.change_reason"
        " FROM procedures p JOIN notices n ON n.notice_id=p.notice_id AND n.version=p.version"
    ).fetchall()

    wanted_lands = set(q.lands)
    if q.land_filter:
        wanted_lands = {q.land_filter} if not wanted_lands else wanted_lands & {q.land_filter}
        if not wanted_lands:
            return {"total": 0, "results": [], "excluded": {"region": 0}, "terms": [t.text for t in terms]}
    excluded = {"status": 0, "region": 0, "keyword": 0}
    results = []
    for r in rows:
        cpv = db.jload(r["cpv"])
        cpv_hits = [c for c in cpv if any(c.startswith(p) for p in cpv_prefixes)]
        if r["procedure_id"] not in candidate_ids and not cpv_hits:
            continue
        if r["status"] not in statuses:
            excluded["status"] += 1
            continue
        why, check = [], []
        region_factor = 1.0
        proc_lands = set(db.jload(r["regions"]))
        if wanted_lands:
            match = proc_lands & wanted_lands
            if match:
                why.append("Ausführungsort in: " + ", ".join(regions.BUNDESLAENDER[c] for c in sorted(match)))
            elif r["region_unknown"] and not r["foreign_place"]:
                check.append("Ausführungsort nicht eindeutig angegeben – Region in den Unterlagen prüfen.")
                region_factor = 0.8
            else:
                excluded["region"] += 1
                continue
        elif r["region_unknown"]:
            check.append("Ausführungsort nicht eindeutig angegeben.")

        lots = db.jload(r["lots"])
        lot_text = " ".join(f"{l.get('title', '')} {l.get('description', '')}" for l in lots)
        fields = [("Titel", norm(r["title"]), 6.0), ("Beschreibung", norm(r["description"]), 3.0),
                  ("Losbeschreibung", norm(lot_text), 2.0)]
        if keyword_terms and not score_fields(keyword_terms, fields)[1]:
            excluded["keyword"] += 1
            continue
        score, hits = score_fields(terms, fields)
        if cpv_hits:
            score += 10
            why.append("CPV-Code passt: " + ", ".join(cpv_hits[:5]))
        if not cpv_hits and coverage(terms, hits) < MIN_COVERAGE:
            continue
        score *= region_factor
        why = _explain_hits(hits) + why

        deadlines = _lot_deadlines(lots, now)
        if r["status"] == "frist_unbekannt":
            check.append("Frist nicht angegeben – Angebots- bzw. Teilnahmefrist in den Vergabeunterlagen prüfen.")
        if len({d["text"] for d in deadlines}) > 1:
            check.append("Die Lose haben unterschiedliche Fristen.")
        crit_codes, crit_text = [], []
        for lot in lots:
            for c in lot.get("criteria") or []:
                label = criteria_label(c.get("code") or "")
                if label not in crit_codes:
                    crit_codes.append(label)
                crit_text.append(norm(c.get("description")))
        if crit_codes:
            check.append("Geforderte Eignungsnachweise: " + "; ".join(crit_codes) + ".")
        else:
            check.append("Eignungskriterien sind in der Bekanntmachung nicht strukturiert angegeben – Unterlagen prüfen.")
        if qual_phrases:
            joined = " ".join(crit_text)
            named = [shown for shown, key in qual_phrases if key in joined]
            if named:
                check.append("Deine Angaben (" + ", ".join(named) + ") werden in den Eignungskriterien erwähnt – "
                             "Anforderungen im Detail prüfen.")
            else:
                check.append("Ob deine Qualifikationen ausreichen, lässt sich aus den Daten nicht ableiten.")
        if r["category"] == "aenderung":
            check.append("Diese Bekanntmachung wurde geändert/berichtigt"
                         + (f": „{short(r['change_reason'], 200)}“" if r["change_reason"] else "") + ".")
        check.append("Teilnahmebedingungen und Leistungsbeschreibung in den Vergabeunterlagen prüfen.")

        results.append({
            "id": r["procedure_id"],
            "score": round(score, 2),
            "title": r["title"] or "Ohne Titel",
            "description": short(r["description"], 400) or "Nicht angegeben",
            "buyer": r["buyer_name"] or "Nicht angegeben",
            "buyer_city": r["buyer_city"] or None,
            "places": _places(lots) or ["Nicht angegeben"],
            "nature": NATURE_LABELS.get(r["nature"], r["nature"] or "Nicht angegeben"),
            "value": _value_text(r["estimated_value"], r["currency"]),
            "status": r["status"],
            "status_label": STATUS_LABELS.get(r["status"], r["status"]),
            "category_label": CATEGORY_LABELS.get(r["category"], r["category"]),
            "deadlines": deadlines,
            "next_deadline": r["next_deadline"],
            "lot_count": len(lots),
            "why": why,
            "check": check,
            "document_url": r["document_url"],
            "published": r["pub_day"],
            **notice_links(r["notice_id"], r["version"]),
        })
    results.sort(key=lambda x: (-x["score"], x["next_deadline"] or "9999", x["published"]), reverse=False)
    # Mehrfach veröffentlichte identische Bekanntmachungen (gleicher Titel, gleiche Frist,
    # gleiche Unterlagen) zusammenfassen
    unique, seen = [], {}
    for res in results:
        key = (re.sub(r"\W+", "", norm(res["title"])), res["next_deadline"], res["document_url"] or res["id"])
        if key in seen:
            seen[key]["duplicates"].append({"notice_url": res["notice_url"], "id": res["id"]})
            continue
        res["duplicates"] = []
        seen[key] = res
        unique.append(res)
    return {"total": len(unique), "results": unique[:limit], "excluded": excluded,
            "terms": [t.text for t in terms]}


def procedure_detail(conn, procedure_id: str, now: datetime | None = None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    p = conn.execute("SELECT * FROM procedures WHERE procedure_id=?", (procedure_id,)).fetchone()
    if not p:
        return None
    n = conn.execute("SELECT * FROM notices WHERE notice_id=? AND version=?", (p["notice_id"], p["version"])).fetchone()
    lots = db.jload(n["lots"])
    history = db.jload(p["history"])
    for h in history:
        h["category_label"] = CATEGORY_LABELS.get(h["category"], h["category"])
        h.update(notice_links(h["notice_id"], h["version"]))
    return {
        "id": procedure_id,
        "title": n["title"],
        "description": n["description"],
        "buyer": n["buyer_name"],
        "buyer_city": n["buyer_city"],
        "nature": NATURE_LABELS.get(n["nature"], n["nature"] or "Nicht angegeben"),
        "value": _value_text(n["estimated_value"], n["currency"]),
        "cpv": db.jload(n["cpv"]),
        "status_label": STATUS_LABELS.get(p["status"], p["status"]),
        "category_label": CATEGORY_LABELS.get(n["category"], n["category"]),
        "notice_type": n["notice_type"],
        "change_reason": n["change_reason"],
        "places": _places(lots) or ["Nicht angegeben"],
        "lots": [{
            "id": l.get("id"), "title": l.get("title"), "description": l.get("description"),
            "cpv": l.get("cpv"), "value": _value_text(l.get("value"), l.get("currency")),
            "places": _places([l]) or ["Nicht angegeben"],
            "tender_deadline": _fmt_deadline(l.get("tender_deadline")) or "Frist nicht angegeben",
            "participation_deadline": _fmt_deadline(l.get("participation_deadline")),
            "criteria": [{"label": criteria_label(c.get("code") or ""), "description": c.get("description")}
                         for c in l.get("criteria") or []],
            "document_url": l.get("document_url"),
            "result": l.get("result"),
        } for l in lots],
        "history": history,
        "document_url": n["document_url"],
        "published": n["pub_day"],
        "source_id": f"{n['notice_id']}-{n['version']}",
        **notice_links(n["notice_id"], n["version"]),
    }
