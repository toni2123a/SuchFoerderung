"""Downloads mit Timeout, begrenzten Wiederholungen und Größenlimit sowie sichere ZIP-Verarbeitung."""
import logging
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import config

log = logging.getLogger(__name__)


class DownloadError(Exception):
    pass


class PermanentDownloadError(DownloadError):
    """Fehler, bei dem eine Wiederholung nichts ändert (z. B. HTTP 400)."""


def download(url: str, *, max_bytes: int | None = None, timeout: float | None = None,
             retries: int | None = None, accept: str | None = None) -> Path:
    """Lädt eine URL in eine temporäre Datei. Der Aufrufer löscht die Datei."""
    max_bytes = max_bytes or config.MAX_DOWNLOAD_BYTES
    timeout = timeout or config.HTTP_TIMEOUT
    retries = config.HTTP_RETRIES if retries is None else retries
    last_error = None
    for attempt in range(1, retries + 1):
        tmp = tempfile.NamedTemporaryFile(prefix="cf-", suffix=".download", delete=False)
        try:
            headers = {"User-Agent": config.USER_AGENT}
            if accept:
                headers["Accept"] = accept
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                length = resp.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise PermanentDownloadError(
                        f"Download zu groß ({int(length)} Bytes, Limit {max_bytes})"
                    )
                total = 0
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise PermanentDownloadError(f"Download überschreitet Limit von {max_bytes} Bytes")
                    tmp.write(chunk)
            tmp.close()
            return Path(tmp.name)
        except urllib.error.HTTPError as exc:
            tmp.close()
            Path(tmp.name).unlink(missing_ok=True)
            body = ""
            try:
                body = exc.read(500).decode("utf-8", "replace").strip()
            except Exception:
                pass
            msg = f"HTTP {exc.code} von {url}" + (f": {body}" if body else "")
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                raise PermanentDownloadError(msg) from exc
            last_error = msg
        except PermanentDownloadError:
            tmp.close()
            Path(tmp.name).unlink(missing_ok=True)
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            tmp.close()
            Path(tmp.name).unlink(missing_ok=True)
            last_error = f"Netzwerkfehler bei {url}: {exc}"
        log.warning("Download-Versuch %s/%s fehlgeschlagen: %s", attempt, retries, last_error)
        if attempt < retries:
            time.sleep(min(60, 5 * 2 ** (attempt - 1)))
    raise DownloadError(last_error or f"Download fehlgeschlagen: {url}")


def open_zip_checked(path: Path) -> zipfile.ZipFile:
    """Öffnet ein ZIP und prüft Anzahl und entpackte Gesamtgröße (Schutz vor ZIP-Bomben)."""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise DownloadError(f"Ungültiges ZIP-Archiv: {exc}") from exc
    infos = zf.infolist()
    if len(infos) > config.MAX_ZIP_MEMBERS:
        zf.close()
        raise DownloadError(f"ZIP enthält zu viele Dateien ({len(infos)})")
    total = sum(i.file_size for i in infos)
    if total > config.MAX_UNZIPPED_BYTES:
        zf.close()
        raise DownloadError(f"ZIP entpackt zu groß ({total} Bytes)")
    return zf


def read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Liest eine ZIP-Datei mit hartem Größenlimit (unabhängig von den Header-Angaben)."""
    if info.file_size > config.MAX_XML_MEMBER_BYTES:
        raise ValueError(f"{info.filename}: Datei zu groß")
    with zf.open(info) as fh:
        data = fh.read(config.MAX_XML_MEMBER_BYTES + 1)
    if len(data) > config.MAX_XML_MEMBER_BYTES:
        raise ValueError(f"{info.filename}: Datei zu groß")
    return data
