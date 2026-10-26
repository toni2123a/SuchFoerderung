"""Import des offiziellen XML-Exports der Förderdatenbank des Bundes.

Format laut Schnittstellenbeschreibung (BMWE): ZIP mit einzelnen XML-Dateien im
GSB-Repository-Format (<document><property name=… type=…>…</property></document>).
"""
import hashlib
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from defusedxml import ElementTree as SafeET

from .. import config, db, net
from ..textutil import clean_text, fold, html_to_text, validate_url

log = logging.getLogger(__name__)

XLINK = "{http://www.w3.org/1999/xlink}href"
SOURCE = "foerder"

PROGRAM_PREFIXES = ("/BMWI/FDB/Content/DE/Foerderprogramm", "/BMWI/FDB/Content/DE/Foerdergeber")
CLASSIFIERS = {
    "Foerderart": "funding_types",
    "Foerdergebiet": "regions",
    "Foerdergeber": "funder_levels",
    "Foerderberechtigte": "eligible",
    "Foerderbereich": "areas",
    "Unternehmensgroesse": "company_sizes",
}


class ExportInvalid(Exception):
    pass


@dataclass
class Program:
    id: str
    title: str
    teaser: str
    summary: str
    eligibility: str
    legal_text: str
    procedure_text: str
    keywords: str
    source_url: str
    date_of_issue: str | None
    content_hash: str
    raw_xml: bytes
    funding_types: list = field(default_factory=list)
    regions: list = field(default_factory=list)
    funder_levels: list = field(default_factory=list)
    eligible: list = field(default_factory=list)
    areas: list = field(default_factory=list)
    company_sizes: list = field(default_factory=list)
    organisation_refs: list = field(default_factory=list)
    contact_refs: list = field(default_factory=list)
    link_refs: list = field(default_factory=list)


def _target(href: str | None) -> str | None:
    if href and href.startswith("target:"):
        return href[len("target:"):]
    return None


def _props(root) -> dict:
    out = {}
    for prop in root.findall("property"):
        out[prop.get("name")] = prop
    return out


def _rich(props, name) -> str:
    prop = props.get(name)
    if prop is None:
        return ""
    return html_to_text(prop.findtext("text"))


def _string(props, name) -> str:
    prop = props.get(name)
    if prop is None:
        return ""
    return clean_text(prop.findtext("value"))


def _classified(prop) -> list[tuple[str | None, list[str]]]:
    """Liefert (Klassifikator, [Ziele]) für eine ClassifiedLinkLists-Property."""
    result = []
    if prop is None:
        return result
    for cl in prop.iter("classifiedLinkList"):
        cls_links = cl.find("classifierLinks")
        classifier = None
        if cls_links is not None:
            for link in cls_links:
                t = _target(link.get(XLINK))
                if t:
                    classifier = t.rsplit("/", 1)[-1]
                    break
        targets = []
        links = cl.find("links")
        if links is not None:
            for link in links:
                t = _target(link.get(XLINK))
                if t:
                    targets.append(t)
        result.append((classifier, targets))
    return result


def parse_document(data: bytes):
    """Parst ein einzelnes GSB-XML sicher (keine DTD, keine externen Entitäten)."""
    return SafeET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)


def doc_key(root) -> str:
    return f"{root.get('path')}/{root.get('name')}"


def parse_program(root, raw: bytes) -> Program | None:
    path = root.get("path") or ""
    name = root.get("name") or ""
    if root.get("type") != "gsb:ServiceOffer" or not path.startswith(PROGRAM_PREFIXES):
        return None
    props = _props(root)
    if _string(props, "gsb:subType") != "ServiceOffer-FundingProgram":
        return None
    title = _rich(props, "gsb:title")
    if not title:
        raise ValueError(f"{path}/{name}: Titel fehlt")
    public_path = path[len("/BMWI"):] if path.startswith("/BMWI/") else path
    prog = Program(
        id=f"{path}/{name}",
        title=title,
        teaser=_rich(props, "gsb:teaserText"),
        summary=_rich(props, "gsb:summary"),
        eligibility=_rich(props, "gsb:regulatoryFWork"),
        legal_text=_rich(props, "gsb:bodyText"),
        procedure_text=_rich(props, "gsb:procDescription"),
        keywords=_string(props, "gsb:keywords"),
        source_url=f"{config.FDB_BASE_URL}{public_path}/{name}.html",
        date_of_issue=_string(props, "gsb:dateOfIssue") or None,
        content_hash=hashlib.sha256(raw).hexdigest(),
        raw_xml=raw,
    )
    for classifier, targets in _classified(props.get("gsb:cl2Processes")):
        if classifier in CLASSIFIERS:
            attr = getattr(prog, CLASSIFIERS[classifier])
            for t in targets:
                slug = t.rsplit("/", 1)[-1]
                if slug not in attr:
                    attr.append(slug)
        elif classifier == "Foerderorganisation":
            prog.organisation_refs.extend(targets)
    for _, targets in _classified(props.get("gsb:cl2Contacts")):
        prog.contact_refs.extend(targets)
    for _, targets in _classified(props.get("gsb:cl2CustServices")):
        prog.link_refs.extend(targets)
    return prog


def parse_contact(root) -> dict | None:
    if root.get("type") != "gsb:ContactData":
        return None
    props = _props(root)
    title = _rich(props, "gsb:title")
    header = _rich(props, "gsb:header")
    return {
        "name": title,
        "unit": header if header and header != title else "",
        "email": _string(props, "gsb:email"),
        "phone": _string(props, "gsb:phone"),
    }


def parse_external_link(root) -> dict | None:
    if root.get("type") != "gsb:ExternalLink":
        return None
    props = _props(root)
    url = validate_url(_string(props, "gsb:url"))
    if not url:
        return None
    title = _rich(props, "gsb:title").replace("(externer Link)", "").strip()
    return {"title": title or url, "url": url}


def parse_export(zip_path: Path) -> tuple[list[Program], dict]:
    """Liest das gesamte Export-ZIP. Wirft ExportInvalid, wenn das Ergebnis nicht plausibel ist."""
    try:
        zf = net.open_zip_checked(zip_path)
    except net.DownloadError as exc:
        raise ExportInvalid(str(exc)) from exc
    programs: list[Program] = []
    contacts: dict[str, dict] = {}
    links: dict[str, dict] = {}
    errors: list[str] = []
    candidates = 0
    with zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".xml"):
                continue
            # Basis der Fehlerquote; im Ordner "Foerdergeber" liegen überwiegend Organisationsdaten
            is_program_file = "/Foerderprogramm/" in info.filename
            if is_program_file:
                candidates += 1
            try:
                raw = net.read_member(zf, info)
                root = parse_document(raw)
            except Exception as exc:  # defusedxml-Verstöße, Syntaxfehler, Größenlimit
                if is_program_file:
                    errors.append(f"{info.filename}: {type(exc).__name__}: {exc}"[:300])
                continue
            try:
                prog = parse_program(root, raw)
                if prog:
                    programs.append(prog)
                    continue
                contact = parse_contact(root)
                if contact:
                    contacts[doc_key(root)] = contact
                    continue
                link = parse_external_link(root)
                if link:
                    links[doc_key(root)] = link
            except Exception as exc:
                errors.append(f"{info.filename}: {type(exc).__name__}: {exc}"[:300])

    ids = {p.id for p in programs}
    stats = {
        "programs": len(programs),
        "unique_ids": len(ids),
        "program_files": candidates,
        "errors": len(errors),
        "error_examples": errors[:5],
        "contacts": len(contacts),
        "links": len(links),
    }
    if len(programs) < config.FDB_MIN_PROGRAMS:
        raise ExportInvalid(
            f"Export enthält nur {len(programs)} lesbare Förderprogramme "
            f"(Mindestwert {config.FDB_MIN_PROGRAMS}) – alter Bestand bleibt erhalten."
        )
    if candidates and len(errors) / candidates > config.FDB_MAX_ERROR_RATE:
        raise ExportInvalid(
            f"{len(errors)} von {candidates} Programmdateien fehlerhaft – alter Bestand bleibt erhalten."
        )
    stats["_refs"] = {"contact": contacts, "link": links}
    return programs, stats


def resolve_refs(programs: list[Program], contacts: dict, links: dict) -> None:
    """Löst Verweise auf Kontakte, Förderorganisationen und externe Links auf."""
    for prog in programs:
        orgs = []
        for ref in prog.organisation_refs:
            c = contacts.get(ref)
            if c and c["name"] and c["name"] not in orgs:
                orgs.append(c["name"])
        prog.organisation_refs = orgs
        cs = []
        for ref in prog.contact_refs:
            c = contacts.get(ref)
            if c and c not in cs:
                cs.append(c)
        prog.contact_refs = cs
        ls = []
        for ref in prog.link_refs:
            link = links.get(ref)
            if link and link not in ls:
                ls.append(link)
        prog.link_refs = ls


def apply_programs(conn, programs: list[Program], refs: dict | None = None) -> dict:
    """Übernimmt einen vollständig geprüften Export in einer Transaktion."""
    now = db.now_iso()
    seen = {}
    for p in programs:
        seen[p.id] = p  # doppelte IDs im Export: letzter Eintrag gewinnt
    inserted = updated = unchanged = 0
    refs = refs or {"contact": {}, "link": {}}
    with db.transaction(conn):
        # Neue Kontakt-/Linkdokumente speichern, dann mit dem bekannten Bestand auflösen
        for kind, docs in refs.items():
            conn.executemany(
                "INSERT INTO fdb_refs(key, kind, data, last_seen) VALUES (?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET kind=excluded.kind, data=excluded.data, last_seen=excluded.last_seen",
                [(k, kind, db.jdump(v), now) for k, v in docs.items()],
            )
        known = {"contact": {}, "link": {}}
        for r in conn.execute("SELECT key, kind, data FROM fdb_refs"):
            known.setdefault(r["kind"], {})[r["key"]] = db.jload(r["data"])
        resolve_refs(list(seen.values()), known["contact"], known["link"])
        existing = {
            r["id"]: r["content_hash"] for r in conn.execute("SELECT id, content_hash FROM programs")
        }
        for p in seen.values():
            values = dict(
                id=p.id, title=p.title, teaser=p.teaser, summary=p.summary,
                eligibility=p.eligibility, legal_text=p.legal_text,
                procedure_text=p.procedure_text, keywords=p.keywords,
                funding_types=db.jdump(p.funding_types), regions=db.jdump(p.regions),
                funder_levels=db.jdump(p.funder_levels), eligible=db.jdump(p.eligible),
                areas=db.jdump(p.areas), company_sizes=db.jdump(p.company_sizes),
                organisations=db.jdump(p.organisation_refs), contacts=db.jdump(p.contact_refs),
                links=db.jdump(p.link_refs), source_url=p.source_url,
                date_of_issue=p.date_of_issue, content_hash=p.content_hash,
                raw_xml=db.compress(p.raw_xml), now=now,
            )
            if p.id not in existing:
                inserted += 1
            elif existing[p.id] != p.content_hash:
                updated += 1
            else:
                unchanged += 1
            conn.execute(
                """
                INSERT INTO programs(id, title, teaser, summary, eligibility, legal_text,
                    procedure_text, keywords, funding_types, regions, funder_levels, eligible,
                    areas, company_sizes, organisations, contacts, links, source_url,
                    date_of_issue, content_hash, raw_xml, active, first_seen, last_seen)
                VALUES (:id, :title, :teaser, :summary, :eligibility, :legal_text,
                    :procedure_text, :keywords, :funding_types, :regions, :funder_levels, :eligible,
                    :areas, :company_sizes, :organisations, :contacts, :links, :source_url,
                    :date_of_issue, :content_hash, :raw_xml, 1, :now, :now)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title, teaser=excluded.teaser, summary=excluded.summary,
                    eligibility=excluded.eligibility, legal_text=excluded.legal_text,
                    procedure_text=excluded.procedure_text, keywords=excluded.keywords,
                    funding_types=excluded.funding_types, regions=excluded.regions,
                    funder_levels=excluded.funder_levels, eligible=excluded.eligible,
                    areas=excluded.areas, company_sizes=excluded.company_sizes,
                    organisations=excluded.organisations, contacts=excluded.contacts,
                    links=excluded.links, source_url=excluded.source_url,
                    date_of_issue=excluded.date_of_issue, content_hash=excluded.content_hash,
                    raw_xml=excluded.raw_xml, active=1, last_seen=excluded.last_seen
                """,
                values,
            )
        # Nicht mehr im Export enthaltene Programme deaktivieren (nicht löschen)
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS seen_ids(id TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM seen_ids")
        conn.executemany("INSERT INTO seen_ids(id) VALUES (?)", [(i,) for i in seen])
        deactivated = conn.execute(
            "UPDATE programs SET active=0 WHERE active=1 AND id NOT IN (SELECT id FROM seen_ids)"
        ).rowcount
        conn.execute("DELETE FROM programs_fts")
        rows = conn.execute(
            "SELECT id, title, teaser, keywords, summary, eligibility, legal_text FROM programs WHERE active=1"
        ).fetchall()
        conn.executemany(
            "INSERT INTO programs_fts(id, title, teaser, keywords, body) VALUES (?,?,?,?,?)",
            [(r["id"], fold(r["title"]), fold(r["teaser"]), fold(r["keywords"]),
              fold(" ".join(x or "" for x in (r["summary"], r["eligibility"], r["legal_text"]))))
             for r in rows],
        )
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged, "deactivated": deactivated}


def run_import(conn, zip_path: Path | None = None) -> dict:
    """Kompletter Importlauf mit Protokoll. Bei Fehlern bleibt der alte Bestand unverändert."""
    if not db.acquire_lock(conn, SOURCE):
        return {"status": "skipped", "message": "Import läuft bereits"}
    run_id = db.start_run(conn, SOURCE)
    downloaded = None
    try:
        if zip_path is None:
            downloaded = net.download(config.FDB_EXPORT_URL, accept="application/zip")
            zip_path = downloaded
        if not zipfile.is_zipfile(zip_path):
            head = Path(zip_path).read_bytes()[:200].decode("utf-8", "replace").lower()
            hint = " (HTML-Seite statt ZIP – evtl. Bot-Schutz der Website)" if "<html" in head else ""
            raise ExportInvalid("Antwort ist kein ZIP-Archiv" + hint)
        programs, stats = parse_export(Path(zip_path))
        stats.update(apply_programs(conn, programs, stats.pop("_refs")))
        db.finish_run(conn, run_id, "ok", f"{stats['programs']} Förderprogramme importiert", stats)
        log.info("Förderimport erfolgreich: %s", {k: v for k, v in stats.items() if k != "error_examples"})
        return {"status": "ok", **stats}
    except Exception as exc:
        log.error("Förderimport fehlgeschlagen: %s", exc)
        db.finish_run(conn, run_id, "failed", str(exc))
        return {"status": "failed", "message": str(exc)}
    finally:
        db.release_lock(conn, SOURCE)
        if downloaded:
            downloaded.unlink(missing_ok=True)
