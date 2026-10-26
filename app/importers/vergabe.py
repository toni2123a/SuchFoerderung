"""Import der Tagesexporte des Bekanntmachungsservice (Open-Data-API, Format eforms.zip).

Laut OpenAPI-Beschreibung: GET /api/notice-exports?pubDay=YYYY-MM-DD&format=eforms.zip
liefert alle an diesem Tag veröffentlichten Bekanntmachungsversionen; nur abgeschlossene
Tage (vor heute, Zeitzone Europe/Berlin) sind abrufbar.
"""
import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from defusedxml import ElementTree as SafeET

from .. import config, db, net, regions
from ..textutil import clean_text, fold, validate_url

log = logging.getLogger(__name__)
SOURCE = "vergabe"
MAX_DAY_ATTEMPTS = 10

# Codes aus der eForms-Codeliste "notice-type" / Formulartyp (listName von NoticeTypeCode)
RESULT_FORMS = {"result", "dir-awa-pre", "cont-modif"}


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
        if el.attrib:
            for key in list(el.attrib):
                if "}" in key:
                    el.attrib[key.split("}", 1)[1]] = el.attrib.pop(key)
    return root


def parse_xml(data: bytes):
    root = SafeET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    return _strip_ns(root)


def _text(el, path) -> str:
    if el is None:
        return ""
    found = el.find(path)
    return clean_text(found.text) if found is not None and found.text else ""


def _all_text(el, path) -> list[str]:
    if el is None:
        return []
    return [clean_text(x.text) for x in el.findall(path) if x.text and x.text.strip()]


def _deadline(period) -> dict | None:
    """Kombiniert EndDate/EndTime (jeweils mit Zeitzonenangabe) zu einem Zeitpunkt."""
    if period is None:
        return None
    d = _text(period, "EndDate")
    t = _text(period, "EndTime")
    if not d:
        return None
    try:
        day_part, tz_part = d[:10], d[10:]
        day = date.fromisoformat(day_part)
        if t:
            tm_part = t[:8]
            tz = t[8:] or tz_part
            tm = time.fromisoformat(tm_part)
            exact = True
        else:
            tm, tz, exact = time(23, 59, 59), tz_part, False
        dt = datetime.combine(day, tm)
        if tz in ("Z", ""):
            dt = dt.replace(tzinfo=timezone.utc if tz == "Z" else config.TZ)
        else:
            sign = 1 if tz[0] == "+" else -1
            hh, mm = tz[1:].split(":")
            dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=int(hh), minutes=int(mm))))
        return {"utc": dt.astimezone(timezone.utc).isoformat(timespec="seconds"), "time_given": exact}
    except (ValueError, IndexError):
        return None


def _amount(el, path):
    if el is None:
        return None, None
    found = el.find(path)
    if found is None or not (found.text or "").strip():
        return None, None
    try:
        return float(found.text.strip()), found.get("currencyID")
    except ValueError:
        return None, None


def _locations(project) -> list[dict]:
    out = []
    if project is None:
        return out
    for loc in project.findall("RealizedLocation"):
        nuts = _text(loc, "Address/CountrySubentityCode")
        country = _text(loc, "Address/Country/IdentificationCode")
        city = _text(loc, "Address/CityName")
        desc = _text(loc, "Description")
        region = _text(loc, "Address/Region")
        out.append({"nuts": nuts, "country": country, "city": city, "description": desc or region})
    return out


def _cpv(project) -> list[str]:
    codes = []
    if project is None:
        return codes
    for path in ("MainCommodityClassification/ItemClassificationCode",
                 "AdditionalCommodityClassification/ItemClassificationCode"):
        for el in project.findall(path):
            if (el.get("listName") or "cpv") == "cpv" and el.text and el.text.strip():
                code = el.text.strip()
                if code not in codes:
                    codes.append(code)
    return codes


def _criteria(lot) -> list[dict]:
    out = []
    for crit in lot.iter("SelectionCriteria"):
        code = _text(crit, "TendererRequirementTypeCode") or _text(crit, "CriterionTypeCode")
        desc = _text(crit, "Description")
        if code or desc:
            out.append({"code": code, "description": desc})
    return out


def _organisations(root) -> dict:
    orgs = {}
    for org in root.iter("Organization"):
        company = org.find("Company")
        if company is None:
            continue
        oid = _text(company, "PartyIdentification/ID")
        if oid:
            orgs[oid] = {
                "name": _text(company, "PartyName/Name"),
                "city": _text(company, "PostalAddress/CityName"),
                "nuts": _text(company, "PostalAddress/CountrySubentityCode"),
            }
    return orgs


def _category(form_type: str, notice_type: str, has_changes: bool, lot_results: dict) -> str:
    if form_type == "planning":
        return "ausschreibung" if notice_type.startswith("pin-cfc") else "vorinformation"
    if form_type == "change" or (form_type == "competition" and has_changes):
        return "aenderung"
    if form_type == "competition":
        return "ausschreibung"
    if form_type == "cont-modif":
        return "aenderung"
    if form_type in ("result", "dir-awa-pre"):
        if lot_results and all(v == "clos-nw" for v in lot_results.values()):
            return "aufhebung"
        return "ergebnis"
    return "sonstiges"


def _changed_ref(notice_id: str, ref: str) -> str | None:
    """ChangedNoticeIdentifier ist entweder "<uuid>-<version>" oder nur eine Versionsnummer."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if "-" in ref and len(ref) > 12:
        return ref.rsplit("-", 1)[0]
    return notice_id


def parse_notice(data: bytes, pub_day: str) -> dict:
    root = parse_xml(data)
    notice_id = _text(root, "ID")
    version = _text(root, "VersionID") or "1"
    if not notice_id:
        raise ValueError("Bekanntmachung ohne ID")
    nt_el = root.find("NoticeTypeCode")
    notice_type = clean_text(nt_el.text) if nt_el is not None and nt_el.text else ""
    form_type = (nt_el.get("listName") if nt_el is not None else "") or ""
    subtype = _text(root, ".//NoticeSubType/SubTypeCode") or None

    changes = root.find(".//Changes")
    if changes is None:
        changes = root.find(".//UBLExtensions//Change")
    changed_ref = change_reason = None
    if changes is not None:
        changed_ref = _changed_ref(notice_id, _text(changes, "ChangedNoticeIdentifier"))
        change_reason = (_text(changes, ".//ChangeReason/ReasonDescription")
                         or _text(changes, ".//ChangeDescription") or _text(changes, ".//ChangeReason/ReasonCode"))

    orgs = _organisations(root)
    party = root.find("ContractingParty/Party")
    buyer = {}
    buyer_ref = _text(party, "PartyIdentification/ID")
    if buyer_ref and buyer_ref in orgs:
        buyer = orgs[buyer_ref]
    elif party is not None:
        buyer = {
            "name": _text(party, "PartyName/Name"),
            "city": _text(party, "PostalAddress/CityName"),
            "nuts": _text(party, "PostalAddress/CountrySubentityCode"),
        }

    project = root.find("ProcurementProject")
    value, currency = _amount(project, "RequestedTenderTotal/EstimatedOverallContractAmount")
    proc_locations = _locations(project)
    cpv = _cpv(project)

    lot_results = {}
    for res in root.iter("LotResult"):
        lot_id = _text(res, "TenderLot/ID")
        code = _text(res, "TenderResultCode")
        if lot_id:
            lot_results[lot_id] = code or "unbekannt"

    lots = []
    doc_url = None
    for lot in root.findall("ProcurementProjectLot"):
        lp = lot.find("ProcurementProject")
        lv, lc = _amount(lp, "RequestedTenderTotal/EstimatedOverallContractAmount")
        lot_doc = None
        for uri in lot.findall("TenderingTerms/CallForTendersDocumentReference/Attachment/ExternalReference/URI"):
            lot_doc = validate_url(uri.text)
            if lot_doc:
                break
        doc_url = doc_url or lot_doc
        lot_cpv = _cpv(lp)
        for c in lot_cpv:
            if c not in cpv:
                cpv.append(c)
        tender = _deadline(lot.find("TenderingProcess/TenderSubmissionDeadlinePeriod"))
        partic = _deadline(lot.find("TenderingProcess/ParticipationRequestReceptionPeriod"))
        lot_id = _text(lot, "ID")
        lots.append({
            "id": lot_id,
            "title": _text(lp, "Name"),
            "description": _text(lp, "Description"),
            "cpv": lot_cpv,
            "value": lv,
            "currency": lc,
            "locations": _locations(lp) or proc_locations,
            "tender_deadline": tender,
            "participation_deadline": partic,
            "document_url": lot_doc,
            "criteria": _criteria(lot),
            "result": lot_results.get(lot_id),
        })

    title = _text(project, "Name") or next((l["title"] for l in lots if l["title"]), "")
    description = _text(project, "Description") or next((l["description"] for l in lots if l["description"]), "")
    if value is None:
        lot_values = [l["value"] for l in lots if l["value"] is not None]
        currencies = {l["currency"] for l in lots if l["value"] is not None}
        if lot_values and len(currencies) == 1:
            value, currency = sum(lot_values), currencies.pop()

    nature = _text(project, "ProcurementTypeCode") or next(
        (_text(l, "ProcurementProject/ProcurementTypeCode") for l in root.findall("ProcurementProjectLot")), "")
    issue = _text(root, "IssueDate")[:10]
    issue_time = _text(root, "IssueTime")[:8]

    return {
        "notice_id": notice_id,
        "version": version,
        "version_num": int(version) if version.isdigit() else 0,
        "procedure_id": _text(root, "ContractFolderID") or notice_id,
        "form_type": form_type,
        "notice_type": notice_type,
        "subtype": subtype,
        "category": _category(form_type, notice_type, changes is not None, lot_results),
        "pub_day": pub_day,
        "issue_datetime": f"{issue}T{issue_time}" if issue and issue_time else issue or None,
        "title": title,
        "description": description,
        "buyer_name": buyer.get("name") or "",
        "buyer_city": buyer.get("city") or "",
        "buyer_nuts": buyer.get("nuts") or "",
        "nature": nature,
        "cpv": cpv,
        "estimated_value": value,
        "currency": currency,
        "document_url": doc_url,
        "changed_notice": changed_ref,
        "change_reason": change_reason,
        "lots": lots,
    }


def store_notice(conn, n: dict, raw: bytes) -> None:
    conn.execute(
        """
        INSERT INTO notices(notice_id, version, version_num, procedure_id, form_type, notice_type,
            subtype, category, pub_day, issue_datetime, title, description, buyer_name, buyer_city,
            buyer_nuts, nature, cpv, estimated_value, currency, document_url, changed_notice,
            change_reason, lots, raw_xml, imported_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(notice_id, version) DO UPDATE SET
            procedure_id=excluded.procedure_id, form_type=excluded.form_type,
            notice_type=excluded.notice_type, subtype=excluded.subtype, category=excluded.category,
            pub_day=excluded.pub_day, issue_datetime=excluded.issue_datetime, title=excluded.title,
            description=excluded.description, buyer_name=excluded.buyer_name,
            buyer_city=excluded.buyer_city, buyer_nuts=excluded.buyer_nuts, nature=excluded.nature,
            cpv=excluded.cpv, estimated_value=excluded.estimated_value, currency=excluded.currency,
            document_url=excluded.document_url, changed_notice=excluded.changed_notice,
            change_reason=excluded.change_reason, lots=excluded.lots, raw_xml=excluded.raw_xml,
            imported_at=excluded.imported_at
        """,
        (
            n["notice_id"], n["version"], n["version_num"], n["procedure_id"], n["form_type"],
            n["notice_type"], n["subtype"], n["category"], n["pub_day"], n["issue_datetime"],
            n["title"], n["description"], n["buyer_name"], n["buyer_city"], n["buyer_nuts"],
            n["nature"], db.jdump(n["cpv"]), n["estimated_value"], n["currency"], n["document_url"],
            n["changed_notice"], n["change_reason"], db.jdump(n["lots"]), db.compress(raw), db.now_iso(),
        ),
    )


def import_zip(conn, zip_path: Path, pub_day: str) -> dict:
    """Importiert ein Tages-ZIP in einer Transaktion. Fehlerhafte Einzeldateien werden gezählt."""
    zf = net.open_zip_checked(zip_path)
    count = errors = 0
    examples = []
    with zf, db.transaction(conn):
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".xml"):
                continue
            try:
                raw = net.read_member(zf, info)
                notice = parse_notice(raw, pub_day)
                store_notice(conn, notice, raw)
                count += 1
            except Exception as exc:
                errors += 1
                if len(examples) < 5:
                    examples.append(f"{info.filename}: {type(exc).__name__}: {exc}"[:300])
        total = count + errors
        if total and errors / total > 0.2:
            raise ValueError(f"{errors} von {total} Bekanntmachungen nicht lesbar: {examples[:2]}")
    return {"notices": count, "errors": errors, "error_examples": examples}


# --- Aktueller Stand je Verfahren ------------------------------------------

def _latest_versions(rows):
    latest = {}
    for r in rows:
        cur = latest.get(r["notice_id"])
        if cur is None or r["version_num"] > cur["version_num"]:
            latest[r["notice_id"]] = r
    return list(latest.values())


def _order_key(r):
    return (r["pub_day"], r["issue_datetime"] or "", r["version_num"])


def compute_procedure(rows, now_utc: datetime) -> dict:
    """Leitet aus allen Bekanntmachungen eines Verfahrens den aktuellen Stand ab."""
    notices = sorted(_latest_versions(rows), key=_order_key)
    # Verweise auf eine andere Bekanntmachung; Selbstverweise (neue Version derselben ID) zählen nicht
    superseded = {r["changed_notice"] for r in notices
                  if r["changed_notice"] and r["changed_notice"] != r["notice_id"]}
    competition = [r for r in notices if r["category"] in ("ausschreibung", "aenderung")
                   and r["form_type"] in ("competition", "change", "planning")]
    results = [r for r in notices if r["form_type"] in RESULT_FORMS]
    pins = [r for r in notices if r["category"] == "vorinformation"]

    current = None
    if competition:
        # neueste Ausschreibung/Berichtigung, die nicht selbst durch eine spätere ersetzt wurde
        candidates = [r for r in competition if r["notice_id"] not in superseded
                      or r is competition[-1]]
        current = candidates[-1] if candidates else competition[-1]

    closed_lots = {}
    for r in results:
        rlots = db.jload(r["lots"])
        codes = {l["id"]: l.get("result") for l in rlots}
        for lot_id, code in codes.items():
            if code in ("selec-w", "clos-nw") or (code is None and r["category"] == "ergebnis"):
                closed_lots[lot_id] = code or "selec-w"
            if r["form_type"] == "cont-modif":
                closed_lots[lot_id] = "selec-w"

    status, next_deadline = "sonstiges", None
    display = current or (results[-1] if results else (pins[-1] if pins else notices[-1]))
    if current is not None:
        lots = db.jload(current["lots"])
        open_lots = [l for l in lots if l["id"] not in closed_lots] if results else lots
        if lots and not open_lots:
            status = "aufgehoben" if all(v == "clos-nw" for v in closed_lots.values()) else "vergeben"
        else:
            future, past, unknown = [], [], 0
            for lot in open_lots or [{}]:
                dl = (lot.get("tender_deadline") or lot.get("participation_deadline"))
                if not dl:
                    unknown += 1
                    continue
                ts = datetime.fromisoformat(dl["utc"])
                (future if ts >= now_utc else past).append(ts)
            if future:
                status, next_deadline = "offen", min(future).isoformat(timespec="seconds")
            elif unknown:
                status = "frist_unbekannt"
            else:
                status = "frist_abgelaufen"
    elif results:
        last = results[-1]
        status = "aufgehoben" if last["category"] == "aufhebung" else "vergeben"
    elif pins:
        status = "vorinformation"

    lands, region_unknown, foreign = set(), False, False
    for lot in db.jload(display["lots"]) or [{}]:
        locs = lot.get("locations") or []
        if not locs:
            region_unknown = True
        for loc in locs:
            land = regions.nuts_to_land(loc.get("nuts")) or regions.city_to_land(loc.get("city"))
            if land:
                lands.add(land)
            elif regions.nuts_is_foreign(loc.get("nuts")) or (
                    loc.get("country") and loc.get("country") != "DEU"):
                foreign = True
            else:
                region_unknown = True
    history = [
        {"notice_id": r["notice_id"], "version": r["version"], "category": r["category"],
         "notice_type": r["notice_type"], "pub_day": r["pub_day"],
         "superseded": r["notice_id"] in superseded and r is not display}
        for r in notices
    ]
    return {
        "procedure_id": display["procedure_id"],
        "notice_id": display["notice_id"],
        "version": display["version"],
        "status": status,
        "category": display["category"],
        "next_deadline": next_deadline,
        "notice_count": len(notices),
        "regions": sorted(lands),
        "region_unknown": int(region_unknown and not lands),
        "foreign_place": int(foreign),
        "history": history,
    }


def rebuild_procedures(conn, now_utc: datetime | None = None) -> int:
    now_utc = now_utc or datetime.now(timezone.utc)
    rows = conn.execute(
        "SELECT notice_id, version, version_num, procedure_id, form_type, notice_type, category, "
        "pub_day, issue_datetime, changed_notice, lots, title, description, buyer_name, cpv "
        "FROM notices"
    ).fetchall()
    by_proc: dict[str, list] = {}
    for r in rows:
        by_proc.setdefault(r["procedure_id"], []).append(r)
    with db.transaction(conn):
        conn.execute("DELETE FROM procedures")
        conn.execute("DELETE FROM procedures_fts")
        for proc_id, prow in by_proc.items():
            p = compute_procedure(prow, now_utc)
            conn.execute(
                "INSERT INTO procedures(procedure_id, notice_id, version, status, category, next_deadline,"
                " notice_count, regions, region_unknown, foreign_place, history)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (proc_id, p["notice_id"], p["version"], p["status"], p["category"], p["next_deadline"],
                 p["notice_count"], db.jdump(p["regions"]), p["region_unknown"], p["foreign_place"],
                 db.jdump(p["history"])),
            )
            disp = next(r for r in prow if r["notice_id"] == p["notice_id"] and r["version"] == p["version"])
            lots = db.jload(disp["lots"])
            body = " ".join(
                [disp["description"] or "", disp["buyer_name"] or "", " ".join(db.jload(disp["cpv"]))]
                + [f"{l.get('title', '')} {l.get('description', '')}" for l in lots]
            )
            conn.execute("INSERT INTO procedures_fts(procedure_id, title, body) VALUES (?,?,?)",
                         (proc_id, fold(disp["title"]), fold(body)))
    return len(by_proc)


# --- Tagessteuerung ---------------------------------------------------------

def yesterday_berlin(today: date | None = None) -> date:
    today = today or datetime.now(config.TZ).date()
    return today - timedelta(days=1)


def days_to_import(conn, today: date | None = None) -> list[str]:
    """Erster Start: die letzten N abgeschlossenen Tage. Danach: alle fehlenden oder fehlgeschlagenen
    Tage vom ersten bekannten Tag bis gestern."""
    last = yesterday_berlin(today)
    rows = {r["day"]: r for r in conn.execute("SELECT * FROM vergabe_days")}
    if not rows:
        start = last - timedelta(days=config.VERGABE_INITIAL_DAYS - 1)
    else:
        start = date.fromisoformat(min(rows))
    out = []
    d = start
    while d <= last:
        key = d.isoformat()
        r = rows.get(key)
        if r is None or (r["status"] != "ok" and r["attempts"] < MAX_DAY_ATTEMPTS):
            out.append(key)
        d += timedelta(days=1)
    return out


def _mark_day(conn, day: str, status: str, count=None, error=None):
    conn.execute(
        """INSERT INTO vergabe_days(day, status, attempts, notice_count, error, updated_at)
           VALUES (?, ?, 1, ?, ?, ?)
           ON CONFLICT(day) DO UPDATE SET status=excluded.status, attempts=vergabe_days.attempts+1,
             notice_count=coalesce(excluded.notice_count, vergabe_days.notice_count),
             error=excluded.error, updated_at=excluded.updated_at""",
        (day, status, count, error, db.now_iso()),
    )


def export_url(day: str) -> str:
    return f"{config.VERGABE_EXPORT_URL}?{urlencode({'pubDay': day, 'format': 'eforms.zip'})}"


def run_import(conn, days: list[str] | None = None, today: date | None = None, fetch=None) -> dict:
    """Importiert fehlende Tage. `fetch(day) -> Path` ist für Tests austauschbar."""
    if not db.acquire_lock(conn, SOURCE):
        return {"status": "skipped", "message": "Import läuft bereits"}
    run_id = db.start_run(conn, SOURCE)
    fetch = fetch or (lambda d: net.download(export_url(d), accept="application/vnd.bekanntmachungsservice.eforms.zip+zip"))
    results, failed = {}, {}
    try:
        days = days if days is not None else days_to_import(conn, today)
        for day in days:
            path = None
            try:
                path = fetch(day)
                stats = import_zip(conn, path, day)
                _mark_day(conn, day, "ok", stats["notices"])
                results[day] = stats["notices"]
                log.info("Vergabe %s: %s Bekanntmachungen (%s Fehler)", day, stats["notices"], stats["errors"])
            except Exception as exc:
                failed[day] = str(exc)[:500]
                _mark_day(conn, day, "failed", error=str(exc)[:500])
                log.error("Vergabe %s fehlgeschlagen: %s", day, exc)
            finally:
                if path and Path(path).name.startswith("cf-"):  # nur eigene Downloads löschen
                    Path(path).unlink(missing_ok=True)
        procs = rebuild_procedures(conn)
        status = "ok" if not failed else ("partial" if results else "failed")
        msg = f"{sum(results.values())} Bekanntmachungen aus {len(results)} Tag(en)"
        if failed:
            msg += f"; fehlgeschlagen: {', '.join(sorted(failed))} – {next(iter(failed.values()))}"
        if not days:
            msg = "Keine neuen Tage zu importieren"
        stats = {"days_ok": results, "days_failed": failed, "procedures": procs}
        db.finish_run(conn, run_id, status, msg, stats)
        return {"status": status, "message": msg, **stats}
    except Exception as exc:
        log.error("Vergabeimport fehlgeschlagen: %s", exc)
        db.finish_run(conn, run_id, "failed", str(exc))
        return {"status": "failed", "message": str(exc)}
    finally:
        db.release_lock(conn, SOURCE)


def coverage(conn) -> dict:
    rows = conn.execute("SELECT day, status, notice_count, error FROM vergabe_days ORDER BY day").fetchall()
    ok = [r["day"] for r in rows if r["status"] == "ok"]
    return {
        "first_day": ok[0] if ok else None,
        "last_day": ok[-1] if ok else None,
        "days_ok": len(ok),
        "failed_days": [{"day": r["day"], "error": r["error"]} for r in rows if r["status"] != "ok"],
        "notices": sum(r["notice_count"] or 0 for r in rows if r["status"] == "ok"),
    }
