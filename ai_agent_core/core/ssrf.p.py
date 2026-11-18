"""SSRF-skydd för utgående HTTP-hämtningar.

Portat från Pi:s ``pi-web-access/ssrf-protection.ts``. Verktyg som
``fetch_url`` tar en URL från en LLM (som i sin tur kan ha fått den från
en webbsida — prompt injection) och hämtar den *inifrån Odoo-serverns
nätverk*. Utan skydd kan en sådan URL nå molnmetadata (``169.254.169.254``,
``fd00:ec2::254``), interna admin-gränssnitt (``192.168.x.x``,
``10.x.x.x``), loopback (``127.0.0.1`` — Odoo självt, PostgreSQL, Caddy)
samt ``file://`` och andra icke-HTTP-scheman.

Skyddet har två lager. Det första är en statisk kontroll av schemats värde
och literal-IP:n. Det andra är ett DNS-uppslag före anslutning: ett
hostnamn som pekar på en privat adress avvisas, eftersom ``evil.example``
kan resolva till ``127.0.0.1``.

Redirects följs INTE automatiskt: varje hopp valideras på nytt, annars
räcker det att en publik URL redirectar till en intern.
"""

import ipaddress
import socket
from urllib.parse import urlsplit

# Scheman som får hämtas. Allt annat (file:, ftp:, gopher:, data:) avvisas
# — ``data:`` och ``file:`` kan läsa lokal disk.
ALLOWED_SCHEMES = ("http", "https")

# Max antal redirects innan vi ger upp. Pi använder 5.
MAX_REDIRECTS = 5

REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class SsrfBlocked(Exception):
    """En URL avvisades av SSRF-skyddet.

    ``reason`` är en mänskligt läsbar förklaring som kan skickas tillbaka
    till modellen — den ska vara begriplig, inte en stack trace.
    """

    def __init__(self, url: str, reason: str):
        self.url = url
        self.reason = reason
        super().__init__(f"{reason}: {url}")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Returnera en anledning om IP:t är otillåtet, annars None.

    Täcker både IPv4 och IPv6. IPv4-mappade IPv6-adresser (``::ffff:127.0.0.1``)
    normaliseras av ipaddress och fastnar därför på rätt regel.
    """
    if ip.is_private:
        return "the address is in a private network range"
    if ip.is_loopback:
        return "the address is loopback"
    if ip.is_link_local:
        # Inkluderar 169.254.169.254 (AWS/GCP/Azure metadata) och fe80::/10.
        return "the address is link-local (cloud metadata endpoints live here)"
    if ip.is_reserved:
        return "the address is in a reserved range"
    if ip.is_multicast:
        return "the address is multicast"
    if ip.is_unspecified:
        return "the address is unspecified (0.0.0.0)"
    # IPv6 unique-local (fc00::/7) täcks av is_private i moderna Python, men
    # äldre versioner missar den — kontrollera explicit.
    if isinstance(ip, ipaddress.IPv6Address) and ip in ipaddress.ip_network("fc00::/7"):
        return "the address is in a private network range (IPv6 unique-local)"
    return None


def resolve_and_validate(hostname: str, port: int | None = None) -> None:
    """Slå upp ``hostname`` och avvisa om någon adress är otillåten.

    Alla svar kontrolleras: ett hostnamn med både en publik och en privat
    adress är en klassisk DNS-rebindning och avvisas helt.
    """
    if not hostname:
        raise SsrfBlocked(hostname, "the URL has no hostname")

    # Literal-IP går att kontrollera direkt utan DNS.
    try:
        literal = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        reason = _is_blocked_ip(literal)
        if reason:
            raise SsrfBlocked(hostname, reason)
        return

    try:
        infos = socket.getaddrinfo(hostname, port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SsrfBlocked(hostname, f"the hostname could not be resolved ({exc})") from exc

    if not infos:
        raise SsrfBlocked(hostname, "the hostname resolved to no addresses")

    for info in infos:
        addr = info[4][0]
        # IPv6-svar kan ha en zon-suffix (fe80::1%eth0) — strippa den.
        addr = addr.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise SsrfBlocked(hostname, f"the hostname resolved to an unparsable address ({addr})")
        reason = _is_blocked_ip(ip)
        if reason:
            raise SsrfBlocked(
                hostname,
                f"the hostname resolves to a blocked address ({ip}) — {reason}")


def validate_url(url: str) -> str:
    """Validera en URL och returnera den oförändrad, eller kasta SsrfBlocked.

    Kontrollerar schema, närvaro av hostname, och att värddatorn inte
    pekar på ett internt nät.
    """
    if not url or not isinstance(url, str):
        raise SsrfBlocked(str(url), "the URL is empty")

    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SsrfBlocked(
            url,
            f"the scheme '{scheme or '(none)'}' is not allowed — only http and https")

    if not parts.hostname:
        raise SsrfBlocked(url, "the URL has no hostname")

    try:
        port = parts.port
    except ValueError as exc:
        raise SsrfBlocked(url, f"the URL has an invalid port ({exc})") from exc

    resolve_and_validate(parts.hostname, port)
    return url


def validate_redirect(from_url: str, to_url: str, hop: int) -> str:
    """Validera ett redirect-hopp.

    ``to_url`` kan vara relativ — då ärvdes den från ``from_url`` och är
    redan validerad, så vi släpper igenom den.
    """
    if hop > MAX_REDIRECTS:
        raise SsrfBlocked(from_url, f"too many redirects (more than {MAX_REDIRECTS})")
    parts = urlsplit(to_url.strip())
    if not parts.scheme:
        # Relativ redirect — samma värd, redan kontrollerad.
        return to_url
    return validate_url(to_url)
