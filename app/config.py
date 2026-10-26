"""Zentrale Konfiguration. Werte lassen sich über Umgebungsvariablen anpassen."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("CHANCENFINDER_DATA_DIR", BASE_DIR.parent / "data"))
DB_PATH = Path(os.environ.get("CHANCENFINDER_DB", DATA_DIR / "chancenfinder.sqlite3"))

TZ = ZoneInfo("Europe/Berlin")

# Förderdatenbank des Bundes (BMWE) – offizieller XML-Export, siehe
# https://www.foerderdatenbank.de/FDB/Content/DE/FAQ/05-export-foerderdatenbank.html
FDB_EXPORT_URL = os.environ.get("FDB_EXPORT_URL", "https://www.foerderdatenbank.de/FDB/WS/export")
FDB_BASE_URL = "https://www.foerderdatenbank.de"
FDB_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/deed.de"

# Bekanntmachungsservice – Open-Data-API, siehe
# https://oeffentlichevergabe.de/documentation/swagger-ui/opendata/index.html
VERGABE_EXPORT_URL = os.environ.get(
    "VERGABE_EXPORT_URL", "https://oeffentlichevergabe.de/api/notice-exports"
)
# Detailansicht laut Routing der offiziellen Oberfläche (notices/:noticeId?noticeVersion=…)
VERGABE_NOTICE_URL = "https://oeffentlichevergabe.de/ui/de/search/details?noticeId={notice_id}&noticeVersion={version}"
# Originaldatensatz (eForms-XML) einer Bekanntmachungsversion
VERGABE_NOTICE_API_URL = "https://oeffentlichevergabe.de/api/notices/{notice_id}?noticeVersion={version}"
VERGABE_INITIAL_DAYS = int(os.environ.get("VERGABE_INITIAL_DAYS", "7"))

# Netzwerk- und Größenbegrenzungen
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "120"))
HTTP_RETRIES = int(os.environ.get("HTTP_RETRIES", "3"))
MAX_DOWNLOAD_BYTES = int(os.environ.get("MAX_DOWNLOAD_BYTES", str(150 * 1024 * 1024)))
MAX_UNZIPPED_BYTES = int(os.environ.get("MAX_UNZIPPED_BYTES", str(600 * 1024 * 1024)))
MAX_ZIP_MEMBERS = int(os.environ.get("MAX_ZIP_MEMBERS", "50000"))
MAX_XML_MEMBER_BYTES = int(os.environ.get("MAX_XML_MEMBER_BYTES", str(10 * 1024 * 1024)))

# Plausibilitätsprüfung für den Förderexport: Erst wenn mindestens so viele
# Programme fehlerfrei gelesen wurden, wird der alte Bestand ersetzt.
FDB_MIN_PROGRAMS = int(os.environ.get("FDB_MIN_PROGRAMS", "500"))
FDB_MAX_ERROR_RATE = float(os.environ.get("FDB_MAX_ERROR_RATE", "0.05"))

# Zeitplan
SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "1") == "1"
SCHEDULER_CHECK_SECONDS = int(os.environ.get("SCHEDULER_CHECK_SECONDS", "1800"))
REFRESH_AFTER_HOURS = float(os.environ.get("REFRESH_AFTER_HOURS", "23"))

USER_AGENT = "Chancenfinder/0.1 (+lokale Open-Data-Anwendung)"
