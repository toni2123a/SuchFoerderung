"""Bundesländer und Zuordnungen der Quellcodes."""
import re

BUNDESLAENDER = {
    "BW": "Baden-Württemberg",
    "BY": "Bayern",
    "BE": "Berlin",
    "BB": "Brandenburg",
    "HB": "Bremen",
    "HH": "Hamburg",
    "HE": "Hessen",
    "MV": "Mecklenburg-Vorpommern",
    "NI": "Niedersachsen",
    "NW": "Nordrhein-Westfalen",
    "RP": "Rheinland-Pfalz",
    "SL": "Saarland",
    "SN": "Sachsen",
    "ST": "Sachsen-Anhalt",
    "SH": "Schleswig-Holstein",
    "TH": "Thüringen",
}

# NUTS-1-Codes der deutschen Länder (Eurostat NUTS 2021/2024)
NUTS1 = {
    "DE1": "BW", "DE2": "BY", "DE3": "BE", "DE4": "BB", "DE5": "HB", "DE6": "HH",
    "DE7": "HE", "DE8": "MV", "DE9": "NI", "DEA": "NW", "DEB": "RP", "DEC": "SL",
    "DED": "SN", "DEE": "ST", "DEF": "SH", "DEG": "TH",
}


def nuts_to_land(code: str | None) -> str | None:
    """Ordnet einen NUTS-Code einem Bundesland zu. Nicht zuordenbare Codes ergeben None."""
    if not code:
        return None
    code = code.strip().upper()
    return NUTS1.get(code[:3])


# Stadtstaaten lassen sich eindeutig über den Ortsnamen zuordnen (wenn kein NUTS-Code vorliegt).
_CITY_STATES = {"berlin": "BE", "hamburg": "HH", "bremen": "HB", "bremerhaven": "HB"}


def city_to_land(city: str | None) -> str | None:
    if not city:
        return None
    name = re.sub(r"^\s*(D-)?\d{5}\s*", "", city).strip().casefold()
    return _CITY_STATES.get(name)


def nuts_is_foreign(code: str | None) -> bool:
    return bool(code) and len(code.strip()) >= 2 and not code.strip().upper().startswith("DE")


# Fördergebiet-Slugs der Förderdatenbank (Schreibweisen exakt wie im Export)
FDB_GEBIET = {
    "baden_wuerttemberg": "BW",
    "bayern": "BY",
    "berlin": "BE",
    "brandenburg": "BB",
    "bremen": "HB",
    "hamburg": "HH",
    "hessen": "HE",
    "mecklenburg_vorpommern": "MV",
    "de_ni": "NI",
    "niedersachsen": "NI",
    "nordrhein_westfalen": "NW",
    "rheinland_pfalz": "RP",
    "saarland": "SL",
    "sachsen": "SN",
    "de_st": "ST",
    "sachsen_anhalt": "ST",
    "schlesig_holstein": "SH",
    "schleswig_holstein": "SH",
    "thueringen": "TH",
}
FDB_BUNDESWEIT = {"_bundesweit", "bundesweit"}


def fdb_region_label(slug: str) -> str:
    if slug in FDB_BUNDESWEIT:
        return "bundesweit"
    code = FDB_GEBIET.get(slug)
    if code:
        return BUNDESLAENDER[code]
    if slug == "sonstige":
        return "sonstiges Gebiet"
    return slug
