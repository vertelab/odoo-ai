"""DSML-parser for DeepSeek tool-calls written as content text.

DeepSeek (via OpenRouter/Bifrost) may emit tool calls as XML-like markup
in the content string instead of the structured tool_calls field. The
format uses fullwidth vertical bars (U+FF5C) and — notably — closing
tags WITHOUT a leading slash:

    <|DSML| calls>
    <|DSML| invoke name="odoo_search">
    <|DSML| parameter name="model" string="true">project.project<|DSML| parameter>
    <|DSML| parameter name="domain">[["id", "=", 240]]<|DSML| parameter>
    <|DSML| invoke>
    <|DSML| calls>

This module converts that text into OpenAI-style tool_calls dicts so the
normal execution path can handle them.
"""
import json
import re

# Tag prefix: <||DSML||  (fullwidth vertical bars)
_P = '\uff5c'
_PREFIX = '<' + _P + _P + 'DSML' + _P + _P
# ASCII fallback some proxies emit
_PREFIX_ASCII = '<||DSML||'

_PREFIXES = (_PREFIX, _PREFIX_ASCII)

# A tag is: <prefix> NAME ...>   or   </prefix> NAME ...>
# Closing tags have no slash in this dialect, so we match both.
_TAG = r'</?(?:\uff5c\uff5c|\|\|)DSML(?:\uff5c\uff5c|\|\|)\s*'

_INVOKE_OPEN = re.compile(_TAG + r'invoke\s+name="([^"]+)"[^>]*>')
_INVOKE_CLOSE = re.compile(_TAG + r'invoke\s*>')
_PARAM = re.compile(_TAG + r'parameter\s+name="([^"]+)"[^>]*>(.*?)(?=' + _TAG + r'parameter\s*>|' + _TAG + r'invoke\s*>|\Z)', re.DOTALL)
_ANY_TAG = re.compile(_TAG + r'(?:calls|invoke|parameter)\s*>')


def contains_dsml(text):
    """True if the text looks like it carries DSML tool-call markup."""
    if not text:
        return False
    return any(p in text for p in _PREFIXES)


def _coerce(value):
    """Best-effort JSON coercion of a DSML parameter value."""
    if value is None:
        return None
    v = value.strip()
    if v == '':
        return ''
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        pass
    low = v.lower()
    if low == 'true':
        return True
    if low == 'false':
        return False
    if low == 'null':
        return None
    return v


def parse_dsml(text):
    """Parse DSML markup into a list of OpenAI-style tool_call dicts.

    Returns [] when no DSML tool calls are found.
    """
    if not contains_dsml(text):
        return []

    calls = []
    opens = list(_INVOKE_OPEN.finditer(text))
    for idx, om in enumerate(opens):
        name = (om.group(1) or '').strip()
        if not name:
            continue
        # Body runs from end of the open tag to the next invoke-open or close
        start = om.end()
        nxt = opens[idx + 1].start() if idx + 1 < len(opens) else len(text)
        body = text[start:nxt]
        cm = _INVOKE_CLOSE.search(body)
        if cm:
            body = body[:cm.start()]

        args = {}
        for pm in _PARAM.finditer(body):
            pname = (pm.group(1) or '').strip()
            if not pname:
                continue
            args[pname] = _coerce(pm.group(2))

        calls.append({
            'id': 'call_dsml_%d' % idx,
            'type': 'function',
            'function': {
                'name': name,
                'arguments': json.dumps(args, ensure_ascii=False),
            },
        })
    return calls


def strip_dsml(text):
    """Remove DSML markup, returning only the human-readable remainder."""
    if not contains_dsml(text):
        return text
    out = text
    # Drop whole invoke blocks first (open tag through close tag)
    out = re.sub(
        _TAG + r'invoke\s+name="[^"]+"[^>]*>.*?(?:' + _TAG + r'invoke\s*>|\Z)',
        '', out, flags=re.DOTALL)
    # Then any remaining standalone tags
    out = _ANY_TAG.sub('', out)
    out = re.sub(r'\n{3,}', '\n\n', out)
    return out.strip()
