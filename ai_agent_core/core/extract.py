"""Extrahering av läsbar text ur HTML.

Ersätter ``soup.get_text()``, som gav menyrader, cookie-banners och
brödsmulor i stället för artikeltext. Modellen fick skräp och svarade
därefter.

Tre lager, i fallande kvalitet:

1. **trafilatura** (om installerat) — bäst på artiklar, tar bort boilerplate
   och behåller stycken. Pi använder Readability för samma sak.
2. **Inbyggd heuristik** — poängsätter ``<article>``/``<main>``/``<div>``
   efter textmängd och länktäthet, väljer det tyngsta blocket. Fungerar
   utan extra beroenden.
3. **Råtext** — sista utvägen, hela sidan utan script/style.

Hanterar även ``<meta http-equiv="refresh">``: docs.saltproject.io svarar
200 med en sådan i stället för HTTP 30x, och efter att script/style tagits
bort återstod 71 tecken.
"""

import re

# Taggar som aldrig innehåller artikeltext.
_BOILERPLATE_TAGS = (
    'script', 'style', 'noscript', 'nav', 'footer', 'header', 'aside',
    'form', 'iframe', 'svg', 'button', 'select', 'template',
)

# Klass/id-mönster som nästan alltid är boilerplate.
_BOILERPLATE_PATTERN = re.compile(
    r'(cookie|consent|banner|advert|\bads?\b|promo|newsletter|subscribe|'
    r'sidebar|breadcrumb|pagination|share|social|comment|related|'
    r'menu|navbar|toolbar|modal|popup|gdpr)',
    re.IGNORECASE,
)

# Block som är sannolikt artikeltext.
_CONTENT_TAGS = ('article', 'main', 'section', 'div', 'td')

_META_REFRESH = re.compile(
    r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]*'
    r'content=["\']?\s*\d+\s*;\s*url=([^"\'>\s]+)',
    re.IGNORECASE,
)


def extract_meta_refresh(html: str, base_url: str = '') -> str | None:
    """Returnera mål-URL om sidan är en meta-refresh-redirect.

    Vissa dokumentationssajter (t.ex. docs.saltproject.io) svarar 200 med
    ``<meta http-equiv="refresh" content="0; url=...">`` i stället för en
    HTTP 30x. Utan detta blir resultatet en tom sträng.
    """
    if not html:
        return None
    match = _META_REFRESH.search(html)
    if not match:
        return None
    target = match.group(1).strip()
    if not target:
        return None
    if base_url and not target.lower().startswith(('http://', 'https://')):
        from urllib.parse import urljoin
        target = urljoin(base_url, target)
    return target


def _strip_boilerplate(soup) -> None:
    """Ta bort taggar som aldrig är artikeltext, på plats."""
    for tag in soup(_BOILERPLATE_TAGS):
        tag.decompose()
    # Klass/id-baserad städning: cookie-banners m.m. ligger ofta i <div>.
    for tag in soup.find_all(True):
        if tag.attrs is None:
            continue
        marker = ' '.join(filter(None, [
            ' '.join(tag.get('class') or []),
            tag.get('id') or '',
        ]))
        if marker and _BOILERPLATE_PATTERN.search(marker):
            tag.decompose()


def _text_density_score(tag) -> float:
    """Poängsätt ett block: mycket text, lite markup, få länkar.

    Länktäthet är det som skiljer en meny från en artikel — en meny är
    nästan bara länkar.
    """
    text = tag.get_text(' ', strip=True)
    length = len(text)
    if length < 80:
        return 0.0
    link_text = sum(len(a.get_text(' ', strip=True)) for a in tag.find_all('a'))
    link_density = link_text / length if length else 1.0
    # Stycken väger tungt — artiklar har <p>, menyer har <li>.
    paragraphs = len(tag.find_all('p'))
    return length * (1.0 - min(link_density, 1.0)) * (1.0 + 0.15 * paragraphs)


def _extract_heuristic(html: str) -> str:
    """Inbyggd artikelutvinning utan externa beroenden."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, 'html.parser')
    _strip_boilerplate(soup)

    # Semantiska taggar vinner om de finns och har innehåll.
    for name in ('article', 'main'):
        node = soup.find(name)
        if node:
            text = node.get_text('\n', strip=True)
            if len(text) >= 200:
                return text

    # Annars: poängsätt kandidatblock och välj det tyngsta.
    best, best_score = None, 0.0
    for tag in soup.find_all(_CONTENT_TAGS):
        score = _text_density_score(tag)
        if score > best_score:
            best, best_score = tag, score

    if best is not None:
        return best.get_text('\n', strip=True)

    body = soup.body or soup
    return body.get_text('\n', strip=True)


def _extract_trafilatura(html: str, url: str = '') -> str | None:
    """trafilatura ger bäst resultat men är ett valfritt beroende."""
    try:
        import trafilatura
    except ImportError:
        return None
    try:
        text = trafilatura.extract(
            html,
            url=url or None,
            include_comments=False,
            include_tables=True,
            favor_precision=False,
        )
    except Exception:
        return None
    return text or None


def _tidy(text: str) -> str:
    """Normalisera blanksteg: kollapsa runs, behåll styckebrytningar."""
    text = re.sub(r'[ \t\u00a0]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_readable(html: str, url: str = '', max_length: int = 5000) -> str:
    """Extrahera läsbar text ur HTML.

    :param html: rå HTML
    :param url: sidans URL (för trafilaturas länkhantering)
    :param max_length: klipp till denna längd
    :return: text, eller tom sträng om inget kunde extraheras
    """
    if not html:
        return ''

    text = _extract_trafilatura(html, url)
    if not text or len(text) < 200:
        heuristic = _extract_heuristic(html)
        # Använd heuristiken om den gav mer — trafilatura kan ge upp på
        # sidor som inte är artiklar (dokumentation, API-referenser).
        if not text or len(heuristic) > len(text):
            text = heuristic

    text = _tidy(text or '')
    return text[:max_length] if len(text) > max_length else text
