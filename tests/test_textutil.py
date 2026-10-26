from app import search
from app.textutil import fold, html_to_text, validate_url


def test_html_is_converted_to_plain_text():
    text = html_to_text('<![CDATA[<div><p>Hallo <strong>Welt</strong></p><script>alert(1)</script><ul><li>A</li></ul></div>]]>')
    assert text == "Hallo Welt\n\n• A"


def test_url_validation():
    assert validate_url("https://www.bund.de/x?y=1") == "https://www.bund.de/x?y=1"
    assert validate_url("www.merseburg.de") == "https://www.merseburg.de"
    for bad in ("javascript:alert(1)", "https://http://www.merseburg.de", "ftp://x.de", "http://localhost/a",
                "http://192.168.0.1/", "https://user:pw@example.com/", "https://exa mple.com"):
        assert validate_url(bad) is None, bad


def test_compound_and_stem_matching():
    terms = search.build_terms("Reinigung")
    t = next(t for t in terms if t.text == "Reinigung")
    assert t.found("fensterreinigung")        # Kompositum mit voller Wortform
    assert t.found("reinigungsleistungen")    # Stamm am Wortanfang
    assert not t.found("stolberg-breinig")    # Stamm mitten im Wort zählt nicht


def test_umlauts_are_kept():
    t = next(t for t in search.build_terms("Straßenbau") if t.weight == 1)
    assert t.found(fold("Tief- und Straßenbauarbeiten"))
    assert not t.found(fold("Pflege von Straßenbäumen"))
