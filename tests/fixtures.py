"""Realistisch aufgebaute Testdaten nach dem Muster echter Exporte.

* Förderdatenbank: GSB-Repository-XML (<document>/<property>), RichText als maskiertes CDATA
* Bekanntmachungsservice: eForms-DE (UBL mit efac/efbc-Erweiterungen) und eforms-sdk-0.1
"""
import io
import zipfile

# --- Förderdatenbank ----------------------------------------------------------

def _rich(name, html):
    esc = html.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<property name="{name}" type="RichText"><text>&lt;![CDATA[&lt;div xmlns:xlink="http://www.w3.org/1999/xlink"&gt;{esc}&lt;/div&gt;]]&gt;</text></property>'


def _cl(classifier, targets):
    links = "".join(f'<link xlink:href="target:{t}"/>' for t in targets)
    return (f'<classifiedLinkList><classifierText></classifierText><classifierLinks>'
            f'<link xlink:href="target:/BMWI/SiteGlobals/Classifications/Classifier/Common/{classifier}"/>'
            f'</classifierLinks><links>{links}</links></classifiedLinkList>')


def _cat(kind, slugs):
    return [f"/BMWI/SiteGlobals/Categories/FDB/{kind}/{s}" for s in slugs]


def fdb_program(name, folder, title, *, teaser="", summary="", eligibility="", body="",
                keywords="", art=("zuschuss",), gebiet=("_bundesweit",), geber=("bund",),
                berechtigte=("unternehmen",), bereich=(), groesse=(), org=None, contact=None, links=()):
    processes = "".join([
        _cl("Foerderart", _cat("Foerderart", art)),
        _cl("Foerdergebiet", _cat("Foerdergebiet", gebiet)),
        _cl("Foerdergeber", _cat("Foerdergeber", geber)),
        _cl("Foerderberechtigte", _cat("Foerderberechtigte", berechtigte)),
        _cl("Foerderbereich", _cat("Foerderbereich", bereich)),
        _cl("Unternehmensgroesse", _cat("Unternehmensgroesse", groesse)),
        _cl("Foerderorganisation", [org] if org else []),
    ])
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<document xmlns:xlink="http://www.w3.org/1999/xlink" name="{name}" path="/BMWI/FDB/Content/DE/Foerderprogramm/{folder}" type="gsb:ServiceOffer">
    {_rich("gsb:bodyText", body)}
    {_rich("gsb:challenge", "")}
    <property name="gsb:cl2Contacts" type="ClassifiedLinkLists"><classifiedLinkLists>{_cl("Kontakt", [contact] if contact else [])}</classifiedLinkLists></property>
    <property name="gsb:cl2CustServices" type="ClassifiedLinkLists"><classifiedLinkLists>{_cl("ExternerLink", list(links))}</classifiedLinkLists></property>
    <property name="gsb:cl2Processes" type="ClassifiedLinkLists"><classifiedLinkLists>{processes}</classifiedLinkLists></property>
    <property name="gsb:keywords" type="String"><value>{keywords}</value></property>
    {_rich("gsb:regulatoryFWork", eligibility)}
    <property name="gsb:subType" type="String"><value>ServiceOffer-FundingProgram</value></property>
    {_rich("gsb:summary", summary)}
    {_rich("gsb:teaserText", teaser)}
    {_rich("gsb:title", "<p>" + title + "</p>")}
</document>"""


def fdb_contact(name, letter, title, email):
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<document xmlns:xlink="http://www.w3.org/1999/xlink" name="{name}" path="/BMWI/FDB/Content/DE/Kontakt/{letter}" type="gsb:ContactData">
    <property name="gsb:email" type="String"><value>{email}</value></property>
    <property name="gsb:subType" type="String"><value>ContactData-FundingOrganisation</value></property>
    {_rich("gsb:title", "<p><span>" + title + "</span></p>")}
</document>"""


def fdb_link(name, letter, title, url):
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<document xmlns:xlink="http://www.w3.org/1999/xlink" name="{name}" path="/BMWI/FDB/Content/DE/ExternerLink/{letter}" type="gsb:ExternalLink">
    {_rich("gsb:title", "<p>" + title + " (externer Link)</p>")}
    <property name="gsb:url" type="String"><value>{url}</value></property>
</document>"""


def standard_programs():
    return {
        "BMWI/FDB/Content/DE/Foerderprogramm/Bund/BAFA/heizungsfoerderung.xml": fdb_program(
            "heizungsfoerderung", "Bund/BAFA", "Bundesförderung für effiziente Gebäude – Heizungstausch",
            teaser="<p>Sie erhalten einen Zuschuss, wenn Sie eine Wärmepumpe einbauen.</p>",
            summary="<p>Der Bund fördert den Einbau von Wärmepumpen. Der Zuschuss beträgt bis zu 70 Prozent der förderfähigen Kosten.</p><script>alert(1)</script>",
            eligibility="<p>Antragsberechtigt sind Eigentümerinnen und Eigentümer.</p><p>Sie erhalten keine Förderung, wenn Sie mit dem Vorhaben bereits begonnen haben.</p>",
            keywords="Heizung Wärmepumpe", gebiet=("_bundesweit",), berechtigte=("privatperson", "unternehmen"),
            bereich=("energieeffizienz_erneuerbare_energien",),
            org="/BMWI/FDB/Content/DE/Kontakt/B/bafa", contact="/BMWI/FDB/Content/DE/Kontakt/B/bafa",
            links=("/BMWI/FDB/Content/DE/ExternerLink/B/bafa-antrag", "/BMWI/FDB/Content/DE/ExternerLink/B/boese")),
        "BMWI/FDB/Content/DE/Foerderprogramm/Land/Bayern/digitalbonus.xml": fdb_program(
            "digitalbonus", "Land/Bayern", "Digitalbonus Bayern",
            teaser="<p>Zuschuss für Digitalisierung und Software in kleinen Unternehmen.</p>",
            summary="<p>Gefördert wird die Einführung von Software.</p>", keywords="Digitalisierung Software",
            gebiet=("bayern",), geber=("land",), berechtigte=("unternehmen",), bereich=("digitalisierung",),
            groesse=("kleinstunternehmen", "kleines_unternehmen", "mittleres_unternehmen")),
        "BMWI/FDB/Content/DE/Foerderprogramm/Land/Nordrhein-Westfalen/digital-nrw.xml": fdb_program(
            "digital-nrw", "Land/Nordrhein-Westfalen", "Digitalisierung im Mittelstand NRW",
            teaser="<p>Software und Digitalisierung für Unternehmen in NRW.</p>", keywords="Software",
            gebiet=("nordrhein_westfalen",), geber=("land",), berechtigte=("unternehmen",), bereich=("digitalisierung",)),
        "BMWI/FDB/Content/DE/Foerderprogramm/Bund/BMWi/software-offen.xml": fdb_program(
            "software-offen", "Bund/BMWi", "Innovationsprogramm Software",
            teaser="<p>Förderung von Software-Entwicklungsprojekten.</p>", keywords="Software",
            gebiet=(), berechtigte=(), bereich=("digitalisierung",)),
        "BMWI/FDB/Content/DE/Kontakt/B/bafa.xml": fdb_contact("bafa", "B", "Bundesamt für Wirtschaft und Ausfuhrkontrolle (BAFA)", "info@bafa.example"),
        "BMWI/FDB/Content/DE/ExternerLink/B/bafa-antrag.xml": fdb_link("bafa-antrag", "B", "Antragsportal", "https://www.bafa.de/antrag"),
        "BMWI/FDB/Content/DE/ExternerLink/B/boese.xml": fdb_link("boese", "B", "Ungültiger Link", "javascript:alert(1)"),
    }


def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content if isinstance(content, bytes) else content.encode("utf-8"))
    return buf.getvalue()


# --- eForms ---------------------------------------------------------------------

NS = ('xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
      'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" '
      'xmlns:efac="http://data.europa.eu/p27/eforms-ubl-extension-aggregate-components/1" '
      'xmlns:efbc="http://data.europa.eu/p27/eforms-ubl-extension-basic-components/1" '
      'xmlns:efext="http://data.europa.eu/p27/eforms-ubl-extensions/1" '
      'xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"')


def _lot(lot_id, title, description, deadline=None, nuts="DE212", city="München", cpv="90911200",
         doc="https://www.vergabe.example/unterlagen/1", criteria=("slc-abil-ref-services",), value=None):
    dl = ""
    if deadline:
        d, t = deadline
        dl = f"<cac:TenderSubmissionDeadlinePeriod><cbc:EndDate>{d}</cbc:EndDate>{f'<cbc:EndTime>{t}</cbc:EndTime>' if t else ''}</cac:TenderSubmissionDeadlinePeriod>"
    crit = "".join(f'<efac:SelectionCriteria><cbc:TendererRequirementTypeCode listName="selection-criterion">{c}</cbc:TendererRequirementTypeCode>'
                   f'<cbc:Description languageID="DEU">Nachweis ISO 9001 und drei Referenzen</cbc:Description></efac:SelectionCriteria>' for c in criteria)
    val = f'<cac:RequestedTenderTotal><cbc:EstimatedOverallContractAmount currencyID="EUR">{value}</cbc:EstimatedOverallContractAmount></cac:RequestedTenderTotal>' if value else ""
    loc = (f"<cac:RealizedLocation><cac:Address><cbc:CityName>{city}</cbc:CityName>"
           f'<cbc:CountrySubentityCode listName="nuts">{nuts}</cbc:CountrySubentityCode>'
           f'<cac:Country><cbc:IdentificationCode listName="country">DEU</cbc:IdentificationCode></cac:Country></cac:Address></cac:RealizedLocation>') if nuts or city else ""
    return f"""<cac:ProcurementProjectLot><cbc:ID schemeName="Lot">{lot_id}</cbc:ID>
      <cac:TenderingTerms><ext:UBLExtensions><ext:UBLExtension><ext:ExtensionContent><efext:EformsExtension>{crit}</efext:EformsExtension></ext:ExtensionContent></ext:UBLExtension></ext:UBLExtensions>
        <cac:CallForTendersDocumentReference><cbc:ID>TenderDoc</cbc:ID><cac:Attachment><cac:ExternalReference><cbc:URI>{doc}</cbc:URI></cac:ExternalReference></cac:Attachment></cac:CallForTendersDocumentReference>
      </cac:TenderingTerms>
      <cac:TenderingProcess>{dl}</cac:TenderingProcess>
      <cac:ProcurementProject><cbc:ID>{lot_id}</cbc:ID><cbc:Name languageID="DEU">{title}</cbc:Name><cbc:Description languageID="DEU">{description}</cbc:Description>
        <cbc:ProcurementTypeCode listName="contract-nature">services</cbc:ProcurementTypeCode>{val}
        <cac:MainCommodityClassification><cbc:ItemClassificationCode listName="cpv">{cpv}</cbc:ItemClassificationCode></cac:MainCommodityClassification>
        {loc}
      </cac:ProcurementProject></cac:ProcurementProjectLot>"""


def eforms_notice(notice_id, version, folder, title, lots, *, root="ContractNotice", list_name="competition",
                  notice_type="cn-standard", subtype="16", buyer=("Stadt Berlin, Vergabestelle", "Berlin", "DE300"),
                  changes=None, results=None):
    ext = f'<efac:NoticeSubType><cbc:SubTypeCode listName="notice-subtype">{subtype}</cbc:SubTypeCode></efac:NoticeSubType>'
    if changes:
        ref, reason = changes
        ext += (f"<efac:Changes><efbc:ChangedNoticeIdentifier>{ref}</efbc:ChangedNoticeIdentifier>"
                f'<efac:ChangeReason><cbc:ReasonCode listName="change-corrig-justification">update-add</cbc:ReasonCode>'
                f'<efbc:ReasonDescription languageID="DEU">{reason}</efbc:ReasonDescription></efac:ChangeReason></efac:Changes>')
    if results:
        ext += "<efac:NoticeResult>" + "".join(
            f'<efac:LotResult><cbc:ID schemeName="result">RES-{i}</cbc:ID><cbc:TenderResultCode listName="winner-selection-status">{code}</cbc:TenderResultCode>'
            f'<efac:TenderLot><cbc:ID schemeName="Lot">{lot}</cbc:ID></efac:TenderLot></efac:LotResult>'
            for i, (lot, code) in enumerate(results.items())) + "</efac:NoticeResult>"
    ext += (f"<efac:Organizations><efac:Organization><efac:Company><cac:PartyIdentification><cbc:ID schemeName=\"organization\">ORG-0001</cbc:ID></cac:PartyIdentification>"
            f"<cac:PartyName><cbc:Name languageID=\"DEU\">{buyer[0]}</cbc:Name></cac:PartyName>"
            f"<cac:PostalAddress><cbc:CityName>{buyer[1]}</cbc:CityName><cbc:CountrySubentityCode listName=\"nuts\">{buyer[2]}</cbc:CountrySubentityCode></cac:PostalAddress>"
            f"</efac:Company></efac:Organization></efac:Organizations>")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<{root} xmlns="urn:oasis:names:specification:ubl:schema:xsd:{root}-2" {NS}>
  <ext:UBLExtensions><ext:UBLExtension><ext:ExtensionContent><efext:EformsExtension>{ext}</efext:EformsExtension></ext:ExtensionContent></ext:UBLExtension></ext:UBLExtensions>
  <cbc:UBLVersionID>2.3</cbc:UBLVersionID>
  <cbc:CustomizationID>eforms-de-2.1</cbc:CustomizationID>
  <cbc:ID schemeName="notice-id">{notice_id}</cbc:ID>
  <cbc:ContractFolderID>{folder}</cbc:ContractFolderID>
  <cbc:IssueDate>2026-10-20+02:00</cbc:IssueDate><cbc:IssueTime>09:00:00+02:00</cbc:IssueTime>
  <cbc:VersionID>{version}</cbc:VersionID>
  <cbc:NoticeTypeCode listName="{list_name}">{notice_type}</cbc:NoticeTypeCode>
  <cac:ContractingParty><cac:Party><cac:PartyIdentification><cbc:ID schemeName="organization">ORG-0001</cbc:ID></cac:PartyIdentification></cac:Party></cac:ContractingParty>
  <cac:ProcurementProject><cbc:Name languageID="DEU">{title}</cbc:Name><cbc:Description languageID="DEU">{title} – Beschreibung</cbc:Description>
    <cbc:ProcurementTypeCode listName="contract-nature">services</cbc:ProcurementTypeCode></cac:ProcurementProject>
  {''.join(lots)}
</{root}>"""


def simple_notice(notice_id, version, title, deadline, *, changed=None, city="Hannover", nuts="DE9"):
    """Vereinfachtes Format eforms-sdk-0.1 (ohne ContractFolderID, Änderung = neue Version)."""
    change = ""
    if changed:
        change = (f'<ext:UBLExtensions><ext:UBLExtension><ext:ExtensionContent><efext:EformsExtension><efac:Change>'
                  f'<efbc:ChangedNoticeIdentifier>{changed}</efbc:ChangedNoticeIdentifier>'
                  f'<efac:ChangeReason><cbc:ReasonCode listName="change-corrig-justification">update-add</cbc:ReasonCode></efac:ChangeReason>'
                  f'</efac:Change></efext:EformsExtension></ext:ExtensionContent></ext:UBLExtension></ext:UBLExtensions>')
    return f"""<ns9:ContractNotice xmlns:ns9="urn:oasis:names:specification:ubl:schema:xsd:ContractNotice-2" {NS}>
  {change}
  <cbc:CustomizationID>eforms-sdk-0.1</cbc:CustomizationID>
  <cbc:ID>{notice_id}</cbc:ID><cbc:VersionID>{version}</cbc:VersionID>
  <cbc:NoticeTypeCode listName="{'change' if changed else 'competition'}">cn-standard</cbc:NoticeTypeCode>
  <cac:ContractingParty><cac:Party><cac:PartyName><cbc:Name>Landeshauptstadt Hannover</cbc:Name></cac:PartyName></cac:Party></cac:ContractingParty>
  <cac:ProcurementProject><cbc:Name>{title}</cbc:Name><cbc:Description>{title} im Stadtgebiet</cbc:Description>
    <cac:RealizedLocation><cac:Address><cbc:CityName>{city}</cbc:CityName><cbc:CountrySubentityCode listName="nuts">{nuts}</cbc:CountrySubentityCode></cac:Address></cac:RealizedLocation>
  </cac:ProcurementProject>
  <cac:ProcurementProjectLot><cbc:ID schemeName="Lot">LOT-0000</cbc:ID>
    <cac:TenderingProcess><cac:TenderSubmissionDeadlinePeriod><cbc:EndDate>{deadline}+01:00</cbc:EndDate><cbc:EndTime>11:00:00+01:00</cbc:EndTime></cac:TenderSubmissionDeadlinePeriod></cac:TenderingProcess>
    <cac:ProcurementProject><cbc:Name>{title}</cbc:Name><cbc:Description>{title} im Stadtgebiet</cbc:Description>
      <cac:RealizedLocation><cac:Address><cbc:CityName>{city}</cbc:CityName><cbc:CountrySubentityCode listName="nuts">{nuts}</cbc:CountrySubentityCode></cac:Address></cac:RealizedLocation>
    </cac:ProcurementProject>
  </cac:ProcurementProjectLot>
</ns9:ContractNotice>"""
