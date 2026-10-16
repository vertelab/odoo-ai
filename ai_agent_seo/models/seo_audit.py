# -*- coding: utf-8 -*-
"""Hjälpkod för SEO/GEO-verktygen.

Verktygen (ai.tool) körs i en sandbox och definierar sin egen `execute`.
Den här modulen samlar den logik som är för stor för att ligga i XML och
som behöver kunna testas: HTML-granskning, llms.txt-kontroll och
JSON-LD-validering.

Ingen av funktionerna skriver till databasen — de är rena granskare.
"""
import json
import re

from odoo import models, api


# Rubriker och deras förväntade nivåer.
_HEADING_RE = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.I | re.S)
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)
_META_RE = re.compile(
    r'<meta\s+[^>]*name=["\']([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']',
    re.I | re.S)
_META_RE_REV = re.compile(
    r'<meta\s+[^>]*content=["\']([^"\']*)["\'][^>]*name=["\']([^"\']+)["\']',
    re.I | re.S)
_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _text(html):
    """Synlig text ur HTML, på en rad."""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html or "")).strip()


def _clip(value, limit=160):
    value = (value or "").strip()
    return value if len(value) <= limit else value[:limit].rsplit(" ", 1)[0]


class SeoAudit(models.AbstractModel):
    """Rena granskningsfunktioner. Ingen databas, ingen sidoeffekt."""
    _name = "seo.audit"
    _description = "SEO/GEO audit helpers"

    # ------------------------------------------------------------------
    # HTML-granskning
    # ------------------------------------------------------------------

    @api.model
    def audit_html(self, html, url=""):
        """Granska en HTML-sida mot SEO- och GEO-kriterier.

        Returnerar en dict med fynd: vad som finns, vad som saknas och vad
        som är fel. Varje fynd har `level` (ok/warn/error) så agenten kan
        prioritera.
        """
        html = html or ""
        findings = []

        def add(level, code, message):
            findings.append({"level": level, "code": code, "message": message})

        # --- Titel ---
        titles = _TITLE_RE.findall(html)
        title = _text(titles[0]) if titles else ""
        if not title:
            add("error", "title_missing", "Ingen <title> — sökmotorn har inget att visa.")
        elif len(title) > 60:
            add("warn", "title_long",
                "Titeln är %d tecken (mål 50-60): %s" % (len(title), _clip(title)))
        elif len(title) < 20:
            add("warn", "title_short",
                "Titeln är bara %d tecken: %s" % (len(title), _clip(title)))

        # --- Meta description ---
        metas = {}
        for name, content in _META_RE.findall(html):
            metas.setdefault(name.lower(), content)
        for content, name in _META_RE_REV.findall(html):
            metas.setdefault(name.lower(), content)
        desc = metas.get("description", "")
        if not desc:
            add("error", "description_missing",
                "Ingen meta description — sökresultatet får en slumpad text.")
        elif len(desc) > 160:
            add("warn", "description_long",
                "Beskrivningen är %d tecken (mål 150-160)." % len(desc))
        elif len(desc) < 70:
            add("warn", "description_short",
                "Beskrivningen är bara %d tecken." % len(desc))

        # --- Rubriker ---
        headings = [(int(lvl), _text(txt)) for lvl, txt in _HEADING_RE.findall(html)]
        h1s = [t for lvl, t in headings if lvl == 1]
        if not h1s:
            add("error", "h1_missing", "Ingen <h1> — sidan har ingen huvudrubrik.")
        elif len(h1s) > 1:
            add("warn", "h1_multiple",
                "%d <h1> på samma sida: %s" % (len(h1s), " | ".join(_clip(h) for h in h1s[:3])))

        # Hopp i nivåer (h1 -> h3 utan h2)
        levels = [lvl for lvl, _ in headings]
        for prev, cur in zip(levels, levels[1:]):
            if cur - prev > 1:
                add("warn", "heading_skip",
                    "Rubriknivå hoppar från h%d till h%d." % (prev, cur))
                break

        # --- GEO: strukturerad data ---
        ld_blocks = _LD_RE.findall(html)
        if not ld_blocks:
            add("error", "json_ld_missing",
                "Ingen JSON-LD — generativa motorer får inga fakta att citera. "
                "Lägg till Article/HowTo/FAQPage/BreadcrumbList.")
        else:
            types = []
            for block in ld_blocks:
                try:
                    data = json.loads(block)
                except (ValueError, TypeError):
                    add("error", "json_ld_invalid", "Ett JSON-LD-block går inte att tolka.")
                    continue
                for item in (data if isinstance(data, list) else [data]):
                    if isinstance(item, dict):
                        t = item.get("@type")
                        if isinstance(t, list):
                            types.extend(t)
                        elif t:
                            types.append(t)
            if types:
                add("ok", "json_ld_types", "Strukturerad data: %s" % ", ".join(sorted(set(types))))

        # --- GEO: llms.txt-länk ---
        if "llms.txt" not in html:
            add("warn", "llms_txt_unlinked",
                "Sidan länkar inte till /llms.txt — AI-motorn hittar inte "
                "innehållskartan från sidan.")

        # --- GEO: självständigt lead ---
        if h1s:
            h1_pos = html.find(">%s<" % h1s[0]) if h1s[0] else -1
            if h1_pos > 0:
                after = html[h1_pos:h1_pos + 1200]
                if "<p" not in after[:400]:
                    add("warn", "lead_missing",
                        "Inget stycke direkt under <h1> — modellen citerar "
                        "passager, inte sidor. Sätt svaret först.")

        # --- Övrigt ---
        if "<html" in html.lower() and not re.search(r'<html[^>]*lang=', html, re.I):
            add("warn", "lang_missing",
                "Inget lang-attribut på <html> — otydligt språk för både "
                "sökmotor och AI.")
        if url and not url.startswith("https://"):
            add("warn", "not_https", "Sidan är inte HTTPS: %s" % url)

        errors = sum(1 for f in findings if f["level"] == "error")
        warns = sum(1 for f in findings if f["level"] == "warn")
        return {
            "url": url,
            "summary": {"errors": errors, "warnings": warns,
                        "ok": sum(1 for f in findings if f["level"] == "ok")},
            "findings": findings,
            "title": title,
            "description": desc,
            "h1": h1s[0] if h1s else "",
        }

    # ------------------------------------------------------------------
    # llms.txt-granskning
    # ------------------------------------------------------------------

    @api.model
    def audit_llms_txt(self, text):
        """Granska en llms.txt mot llmstxt.org-formatet."""
        text = text or ""
        findings = []
        lines = text.splitlines()

        def add(level, code, message):
            findings.append({"level": level, "code": code, "message": message})

        # Rubrik
        if not lines or not lines[0].startswith("# "):
            add("error", "h1_missing", "Första raden ska vara '# Sajtnamn'.")
        else:
            add("ok", "h1", "Rubrik: %s" % lines[0][2:].strip())

        # Blockquote
        if len(lines) < 3 or not lines[2].startswith("> "):
            add("warn", "purpose_missing",
                "Rad 3 ska vara '> en mening om vad sajten är' (llmstxt.org).")

        # Sektioner och länkar
        sections = [l[3:].strip() for l in lines if l.startswith("## ")]
        if not sections:
            add("error", "sections_missing", "Inga '## Sektion'-rubriker.")
        else:
            add("ok", "sections", "Sektioner: %s" % ", ".join(sections))

        link_re = re.compile(r"^- \[[^\]]+\]\(https?://[^)\s]+\)(: .+)?$")
        links = [l for l in lines if l.startswith("- ")]
        bad = [l for l in links if not link_re.match(l)]
        if not links:
            add("error", "links_missing", "Inga länkrader ('- [Titel](url): mening').")
        elif bad:
            add("error", "links_malformed",
                "%d länkrader följer inte formatet. Exempel: %s"
                % (len(bad), _clip(bad[0], 120)))
        else:
            add("ok", "links", "%d välformade länkrader." % len(links))

        # Relativa adresser
        rel = [l for l in links if re.search(r"\]\(/", l)]
        if rel:
            add("error", "links_relative",
                "%d länkar är relativa — modellen har ingen bas-URL. Exempel: %s"
                % (len(rel), _clip(rel[0], 120)))

        if "## Optional" not in text:
            add("warn", "optional_missing",
                "Ingen '## Optional'-sektion (llmstxt.org rekommenderar den).")

        errors = sum(1 for f in findings if f["level"] == "error")
        return {"summary": {"errors": errors,
                            "warnings": sum(1 for f in findings if f["level"] == "warn"),
                            "links": len(links), "sections": len(sections)},
                "findings": findings}

    # ------------------------------------------------------------------
    # JSON-LD-granskning
    # ------------------------------------------------------------------

    @api.model
    def audit_json_ld(self, raw):
        """Granska JSON-LD: giltig JSON, kända typer, obligatoriska fält."""
        findings = []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as exc:
            return {"valid": False,
                    "findings": [{"level": "error", "code": "invalid_json",
                                  "message": "Ogiltig JSON: %s" % exc}]}

        items = data if isinstance(data, list) else [data]
        required = {
            "Article": ["headline", "author", "datePublished"],
            "HowTo": ["name", "step"],
            "FAQPage": ["mainEntity"],
            "BreadcrumbList": ["itemListElement"],
            "Organization": ["name", "url"],
            "Product": ["name"],
            "WebSite": ["url"],
            "Event": ["name", "startDate"],
        }
        for item in items:
            if not isinstance(item, dict):
                findings.append({"level": "error", "code": "not_object",
                                 "message": "Ett element är inte ett objekt."})
                continue
            t = item.get("@type")
            if not t:
                findings.append({"level": "error", "code": "type_missing",
                                 "message": "Saknar @type."})
                continue
            if not item.get("@context"):
                findings.append({"level": "warn", "code": "context_missing",
                                 "message": "%s saknar @context." % t})
            if t in required:
                missing = [f for f in required[t] if not item.get(f)]
                if missing:
                    findings.append({"level": "error", "code": "fields_missing",
                                     "message": "%s saknar: %s" % (t, ", ".join(missing))})
                else:
                    findings.append({"level": "ok", "code": "complete",
                                     "message": "%s är komplett." % t})
            else:
                findings.append({"level": "warn", "code": "unknown_type",
                                 "message": "@type '%s' är inte en känd typ." % t})

        return {"valid": not any(f["level"] == "error" for f in findings),
                "types": [i.get("@type") for i in items if isinstance(i, dict)],
                "findings": findings}
