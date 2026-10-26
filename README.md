# Chancenfinder

Kleine deutsche Webanwendung: Nutzer beschreiben ihre Situation und finden passende
**Förderprogramme** (Förderdatenbank des Bundes) oder **öffentliche Ausschreibungen**
(Bekanntmachungsservice). Die Daten werden serverseitig aus den offiziellen Quellen geladen,
lokal in SQLite gespeichert und regelbasiert durchsucht – ohne KI-Dienst, ohne Konto, ohne Tracker.

## Start

```bash
docker compose up -d --build
```

Danach im Browser: <http://localhost:8000>

Beim ersten Start sind noch keine Daten vorhanden. Der Import startet automatisch im Hintergrund
(Förderexport ca. 20 MB, dazu 7 Tagesexporte des Bekanntmachungsservice). Er dauert je nach
Verbindung einige Minuten. Die Oberfläche zeigt den Fortschritt an. Danach prüft die Anwendung
alle 30 Minuten, ob eine Aktualisierung fällig ist (Förderdaten: einmal täglich; Vergabedaten:
jeder neue abgeschlossene Tag sowie fehlgeschlagene Tage).

Die Daten liegen im Docker-Volume `chancenfinder-data` (`/data/chancenfinder.sqlite3`).

## Manuelle Aktualisierung (CLI)

```bash
# beide Quellen
docker compose exec chancenfinder python -m app.cli update
# nur eine Quelle
docker compose exec chancenfinder python -m app.cli update --source foerder
docker compose exec chancenfinder python -m app.cli update --source vergabe
# bestimmte Veröffentlichungstage (nach)laden, mehrfach möglich
docker compose exec chancenfinder python -m app.cli update --source vergabe --day 2026-10-20 --day 2026-10-21
# Datenstand anzeigen
docker compose exec chancenfinder python -m app.cli status
```

Die CLI und der Hintergrundimport sperren sich gegenseitig, sodass nicht parallel importiert wird.
Der Exit-Code ist ≠ 0, wenn ein Import fehlschlägt.

Tests: `docker compose exec chancenfinder python -m pytest -q`
(lokal: `pip install -r requirements-dev.txt && python -m pytest -q`).

## Datenquellen und Lizenzen

| Quelle | Zugriff | Lizenz / Hinweis |
|---|---|---|
| **Förderdatenbank des Bundes** (BMWE) | XML-Export `GET https://www.foerderdatenbank.de/FDB/WS/export` (ZIP mit einer GSB-XML-Datei je Dokument), laut [FAQ](https://www.foerderdatenbank.de/FDB/Content/DE/FAQ/05-export-foerderdatenbank.html) und Schnittstellenbeschreibung (PDF) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.de). Namensnennung „Förderdatenbank des Bundes, BMWE“ erscheint auf jeder Ergebniskarte, in den Details und im Footer. Texte werden technisch aufbereitet (HTML → Text, gekürzt). Die Website selbst ist nicht Gegenstand der Lizenz und wird nicht gescrapt. |
| **Bekanntmachungsservice** (oeffentlichevergabe.de) | Open-Data-API `GET /api/notice-exports?pubDay=YYYY-MM-DD&format=eforms.zip` laut [OpenAPI-Beschreibung](https://oeffentlichevergabe.de/documentation/swagger-ui/opendata/index.html) | Die OCDS-Variante desselben Exports weist `license: cc-zero` und die [Veröffentlichungsrichtlinie](https://oeffentlichevergabe.de/ui/de/publicationPolicy) aus. |

Warum eForms statt CSV/OCDS? Beim Prüfen echter Tagesexporte enthielten nur die eForms-XML-Dateien
die **Angebots- und Teilnahmefristen je Los** (`TenderSubmissionDeadlinePeriod`,
`ParticipationRequestReceptionPeriod`). Im CSV- und im OCDS-Export fehlten sie.

### Was beim Import passiert

* Downloads mit Timeout, bis zu 3 Versuchen mit Wartezeit, Größenlimit (150 MB) und
  ZIP-Prüfung (Anzahl Dateien, entpackte Gesamtgröße, Größe je Datei).
* XML wird mit `defusedxml` gelesen: keine DTDs, keine Entitäten, keine externen Verweise.
* Rohdaten (komprimiertes Original-XML) und Quellkennungen bleiben gespeichert.
* HTML aus den Quellen wird in reinen Text umgewandelt. Das Frontend fügt Quelltexte nur als Text
  ein. Links werden validiert (nur öffentliche http(s)-Ziele, keine Zugangsdaten in der URL).
* **Förderdaten:** Ein neuer Export wird erst übernommen, wenn er vollständig gelesen und plausibel ist
  (mindestens 500 Programme, höchstens 5 % fehlerhafte Programmdateien). Dann folgt eine Transaktion:
  Upsert über die Repository-ID, nicht mehr enthaltene Programme werden *deaktiviert* (nicht gelöscht).
  Bei jedem Fehler bleibt der letzte gültige Bestand unverändert.
* **Vergabedaten:** Upsert je (Bekanntmachungs-ID, Version), dadurch keine Duplikate bei erneutem Import.
  Jeder Tag wird mit Status protokolliert. Fehlgeschlagene Tage werden bei den nächsten Läufen erneut
  versucht (bis zu 10 Mal).
* Fehler werden mit Ursache protokolliert und in der Oberfläche zusammen mit dem letzten
  erfolgreichen Datenstand angezeigt. Beispieldaten werden nie eingesetzt.

## Suchlogik

Die Suche ist bewusst einfach und nachvollziehbar. Jede Ergebniskarte zeigt
„Warum wird dieser Treffer angezeigt?“ und „Das musst du noch prüfen“.

**Textabgleich (beide Bereiche)**
1. Aus der Beschreibung werden Suchwörter gebildet (Stoppwörter entfernt, einfache Wortstämme).
   Dazu kommt eine kleine, feste Synonymliste (`app/search.py`, `SYNONYMS`), z. B.
   PV → Photovoltaik/Solar oder Wallbox → Ladeinfrastruktur.
2. Ein SQLite-FTS5-Index mit Trigram-Tokenizer findet Kandidaten. Teilwörter funktionieren dadurch auch
   in Komposita („Reinigung“ findet „Fensterreinigung“). Ein Wortstamm zählt nur am Wortanfang, damit z. B.
   „reinig“ nicht auf den Ortsnamen „Breinig“ passt. Kurze Wörter (unter 6 Zeichen) zählen nur als ganzes
   Wort („Dach“ passt nicht auf „Dachfonds“).
3. Bewertung: Treffer in Titel > Stichwörtern > Kurztext > Zusammenfassung > Volltext.
   Häufige Wörter zählen weniger (IDF). Großgeschriebene Wörter (Substantive) tragen den Inhalt.
   Kleingeschriebene Wörter (Verben, Adjektive) zählen halb, außer die ganze Eingabe ist kleingeschrieben.
4. Mindestabdeckung: Ein Treffer muss mindestens 25 % der gewichteten Suchwörter abdecken.
   Treffer nur im langen Richtlinientext zählen dabei halb, Treffer nur über Synonyme zu 60 %.
   Unpassende Ergebnisse werden nicht zum Auffüllen verwendet. Es gibt keine Prozentangaben zur
   Erfolgschance.

**Förderprogramme**: Die strukturierten Klassifikationen der Förderdatenbank werden genutzt.
* *Fördergebiet*: Bundesweite Programme gelten für alle Länder. Programme, die ausschließlich anderen
  Ländern zugeordnet sind, werden ausgeschlossen. Fehlt die Angabe oder ist sie unklar („sonstige“),
  wird das Programm mit Prüfhinweis angezeigt. Maßgeblich ist das *Fördergebiet*, nicht der Sitz des
  Fördergebers.
* *Förderberechtigte*: Ausschluss nur, wenn die Datenbank Förderberechtigte nennt und keiner zum
  gewählten Antragsteller passt (Verein → Verbände/Vereinigungen, Kommune → Kommunen/öffentliche
  Einrichtungen, Forschung → Forschungseinrichtungen/Hochschulen …). Ohne Angabe gibt es einen Prüfhinweis.
* *Unternehmensgröße*: Ausschluss nur, wenn das Programm ausdrücklich nur KMU-Größenklassen nennt und
  mindestens 250 Beschäftigte angegeben sind. Ab dieser Grenze ist ein Unternehmen nach EU-Definition
  kein KMU. Unterhalb davon gibt es nur einen Hinweis, weil Umsatz, Bilanzsumme und verbundene
  Unternehmen mitzählen.
* *Vorhabensbeginn* und *Budget* führen nie zum Ausschluss. Falls vorhanden, wird ein passender
  Originalsatz aus der Quelle angezeigt.
* *Förderhöhe*: Die Datenbank hat dafür kein strukturiertes Feld. Angezeigt werden unveränderte
  Originalsätze mit Beträgen oder Prozentangaben, sonst „Nicht angegeben“.
* Ein optionaler *Themenbereich* wird auf die Förderbereich-Klassifikation abgebildet und stärkt die Relevanz.

**Öffentliche Aufträge**
* Je Verfahren (`ContractFolderID`, im vereinfachten Format die Bekanntmachungs-ID) wird der aktuelle
  bekannte Stand berechnet: neueste Version, Berichtigungen über `ChangedNoticeIdentifier`,
  Ergebnisse und Aufhebungen über die Losergebnisse (`selec-w`, `clos-nw`), jeweils je Los.
* Kategorien: Ausschreibung, Vorinformation, Änderung/Berichtigung, Ergebnis, Aufhebung
  (aus Formulartyp, Bekanntmachungsart und Losergebnissen).
* Standardanzeige: nur Verfahren, bei denen eine Teilnahme möglich erscheint, also mindestens ein
  offenes Los mit künftiger Frist („Frist läuft“) oder ohne Fristangabe („Frist nicht angegeben“).
  Filter: „Nur bekannte offene Fristen“ oder „Alle Bekanntmachungen“.
* Region: Maßgeblich ist der **Ausführungsort** je Los (NUTS-Code, bei Stadtstaaten auch der
  Ortsname), nicht der Sitz des Auftraggebers. Ohne Ortsangabe gibt es einen Prüfhinweis.
* CPV-Codes werden als Präfix verglichen (`90910000` → `9091…`).
* Qualifikationen werden nur mit dem Text der Eignungskriterien verglichen. Eine Eignung wird
  daraus nie abgeleitet.
* Identische Mehrfachveröffentlichungen (gleicher Titel, gleiche Frist, gleiche Unterlagen-URL)
  werden zu einem Treffer zusammengefasst.

## Datenschutz

Suchanfragen werden per POST gesendet und nicht gespeichert. Es gibt keine Zugriffs-Logs
(`--no-access-log`), keine Freitexte in Anwendungslogs, keine externen Skripte, Schriften oder
Tracker. Eine Content-Security-Policy erlaubt nur eigene Ressourcen.

## Bekannte Grenzen

* **Keine vollständige Abdeckung öffentlicher Aufträge.** Berücksichtigt werden nur Bekanntmachungen,
  die *im Importzeitraum* beim Bekanntmachungsservice veröffentlicht wurden (beim ersten Start die
  letzten 7 abgeschlossenen Tage, danach wächst der Zeitraum täglich). Ältere, noch laufende
  Verfahren fehlen. Ebenso fehlen Ausschreibungen, die nur auf anderen Plattformen oder nur in TED
  erscheinen. Der Importzeitraum wird in der Oberfläche angezeigt.
* Bekanntmachungen im vereinfachten Format (`eforms-sdk-0.1`) haben teilweise keinen NUTS-Code.
  Dann ist der Ausführungsort „nicht eindeutig“. Postleitzahlen werden nicht auf Bundesländer abgebildet.
* „Frist nicht angegeben“ heißt nur, dass die Bekanntmachung keine Frist enthält. Das Verfahren kann
  trotzdem bereits beendet sein.
* Die Förderdatenbank enthält keine strukturierten Antragsfristen und Förderhöhen. Das Feld
  `dateOfExpiration` ist erkennbar kein Programmende (z. B. bei aktuell laufenden Programmen in der
  Vergangenheit) und wird deshalb nicht ausgewertet.
* Einzelne Quelltexte enthalten bereits in der Quelle Fragezeichen statt typografischer Anführungszeichen
  oder Gedankenstriche. Sie werden unverändert übernommen.
* Die Teilwortsuche kann vereinzelt unpassende Treffer liefern. Synonyme decken nur häufige Themen ab.
* Exportiert werden nur Programme unter `Content/DE/Foerderprogramm` (Bund, Länder, EU).
  Archiv- und Altimportordner im Export (`Archiv`, `Import`) werden nicht übernommen.
* Der Förderexport wurde beim Test in unterschiedlichen Ständen ausgeliefert (einmal 1.933 Programme
  mit rund 1.100 Kontaktdokumenten, kurz darauf 2.481 Programme mit nur 185 Kontaktdokumenten).
  Kontakt- und Linkdokumente werden deshalb dauerhaft gespeichert (Tabelle `fdb_refs`) und auch bei
  späteren Exporten zur Auflösung genutzt. Fehlen sie in allen bisher geladenen Exporten, wird der
  Fördergeber als „Nicht angegeben“ angezeigt (betraf beim Test rund 15 % der Programme).
* Die Website der Förderdatenbank setzt einen Bot-Schutz ein. Der Export-Endpunkt war beim Test
  erreichbar. Liefert er stattdessen eine HTML-Seite, schlägt der Import mit einer klaren Meldung fehl
  und der alte Bestand bleibt erhalten.

## Projektstruktur

```
app/
  main.py            FastAPI (API + statisches Frontend)
  config.py          Konfiguration (Umgebungsvariablen)
  db.py              SQLite-Schema, Importprotokoll, Sperre
  net.py             Downloads (Timeout, Wiederholungen, Größenlimits), sichere ZIP-Verarbeitung
  textutil.py        HTML → Text, Link-Validierung
  regions.py         Bundesländer, NUTS- und Fördergebiet-Zuordnung
  search.py          Regelbasierte Suche und Erklärungen
  scheduler.py       Hintergrundimport
  cli.py             Manuelle Aktualisierung
  importers/         foerder.py (Förderdatenbank), vergabe.py (Bekanntmachungsservice)
  static/            index.html, app.css, app.js
tests/               Tests mit realistisch aufgebauten Quelldaten
```
