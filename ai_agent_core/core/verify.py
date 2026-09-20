# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Output Verifier — three-layer validation (TASK-004).

Every quest output MUST pass three validation layers:
- **Schema**: output format matches expected structure
- **Requirements**: all required fields present, dependencies satisfied
- **Tests**: test cases pass (valid inputs → expected outputs)

Max 3 verify-fix cycles, then escalate.
Per-layer error reporting.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

_logger = logging.getLogger(__name__)


class ValidationStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


@dataclass
class ValidationError:
    """A single validation error."""
    layer: str            # schema | requirements | tests
    field: str            # Which field/area failed
    message: str          # Human-readable error
    expected: str = ""    # What was expected
    actual: str = ""      # What was found
    severity: str = "error"  # error | warning


@dataclass
class VerifyResult:
    """Complete verification result."""
    status: ValidationStatus = ValidationStatus.PASS
    schema_errors: list[ValidationError] = field(default_factory=list)
    requirement_errors: list[ValidationError] = field(default_factory=list)
    test_errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)
    score: float = 1.0    # 1.0 = all pass, 0.0 = all fail
    needs_fix: bool = False
    fix_suggestions: list[str] = field(default_factory=list)
    # Innehållslig verifiering (innehallsverifiering): täckning mot en källa.
    # None = ingen innehållskontroll kördes (avtalet begärde den inte).
    coverage: Optional[float] = None
    coverage_threshold: Optional[float] = None
    coverage_total: int = 0
    coverage_found: int = 0

    @property
    def all_errors(self) -> list[ValidationError]:
        return self.schema_errors + self.requirement_errors + self.test_errors

    @property
    def passed(self) -> bool:
        return self.status == ValidationStatus.PASS


class OutputVerifier:
    """Three-layer output validation.

    Usage:
        verifier = OutputVerifier()

        # Schema validation
        schema = {"type": "object", "required": ["name", "value"]}

        # Requirements
        requirements = [
            "Output must be in Swedish",
            "Must include at least 3 items",
        ]

        # Tests
        tests = [
            {"input": "Q2 sales", "expected_contains": ["Q2", "sales"]},
            {"input": "customers", "expected_contains": ["customer", "kund"]},
        ]

        result = verifier.verify(output, schema, requirements, tests)
        if not result.passed:
            for err in result.all_errors:
                print(f"{err.layer}: {err.message}")
    """

    def verify(
        self,
        output: str,
        schema: Optional[dict] = None,
        requirements: Optional[list[str]] = None,
        tests: Optional[list[dict]] = None,
    ) -> VerifyResult:
        """Run all three validation layers.

        Args:
            output: The output to validate
            schema: JSON Schema to validate against (optional)
            requirements: List of requirement strings (optional)
            tests: List of test case dicts with 'input' and 'expected_contains' keys (optional)

        Returns:
            VerifyResult with all errors and pass/fail status
        """
        result = VerifyResult()

        # Layer 1: Schema validation
        if schema is not None:
            result.schema_errors = self._validate_schema(output, schema)

        # Layer 2: Requirements validation
        if requirements is not None:
            result.requirement_errors = self._validate_requirements(
                output, requirements
            )

        # Layer 3: Test case validation
        if tests is not None:
            result.test_errors = self._validate_tests(output, tests)

        # Calculate overall status
        total_errors = len(result.all_errors)
        if total_errors == 0:
            result.status = ValidationStatus.PASS
            result.score = 1.0
        elif len(result.schema_errors) > 0:
            result.status = ValidationStatus.FAIL
            result.score = max(0.0, 1.0 - total_errors * 0.15)
        elif len(result.requirement_errors) > 0:
            result.status = ValidationStatus.FAIL
            result.score = max(0.0, 1.0 - total_errors * 0.1)
        else:
            result.status = ValidationStatus.WARN
            result.score = max(0.0, 1.0 - total_errors * 0.05)

        result.needs_fix = total_errors > 0

        # Generate fix suggestions
        if result.needs_fix:
            result.fix_suggestions = self._generate_fix_suggestions(result)

        return result

    def verify_with_fix(
        self,
        output: str,
        schema: Optional[dict] = None,
        requirements: Optional[list[str]] = None,
        tests: Optional[list[dict]] = None,
        fix_func=None,
        max_cycles: int = 3,
    ) -> tuple[str, VerifyResult]:
        """Verify and auto-fix up to max_cycles times.

        Args:
            output: Initial output
            schema, requirements, tests: Validation criteria
            fix_func: async function(output, errors) → fixed_output
            max_cycles: Maximum verify-fix cycles before escalating

        Returns:
            (final_output, final_verify_result)
        """
        current = output

        for cycle in range(1, max_cycles + 1):
            result = self.verify(current, schema, requirements, tests)

            if result.passed:
                _logger.info("Verify passed after %d cycles", cycle)
                return current, result

            _logger.info(
                "Verify cycle %d/%d — %d errors found",
                cycle, max_cycles, len(result.all_errors),
            )

            if fix_func and cycle < max_cycles:
                try:
                    current = fix_func(current, result.all_errors)
                except Exception as e:
                    _logger.warning("Fix function failed: %s", e)
                    break  # Escalate

        # Max cycles reached or fix failed
        result = self.verify(current, schema, requirements, tests)
        return current, result

    # -----------------------------------------------------------------------
    # Layer 1: Schema
    # -----------------------------------------------------------------------

    def _validate_schema(self, output: str, schema: dict) -> list[ValidationError]:
        """Validate output against a JSON Schema."""
        errors = []

        # Try parsing as JSON
        try:
            data = json.loads(output)
        except json.JSONDecodeError as e:
            errors.append(ValidationError(
                layer="schema",
                field="root",
                message=f"Output is not valid JSON: {e}",
                expected="Valid JSON",
                actual=output[:200],
            ))
            return errors

        # Required fields
        if isinstance(schema, dict):
            required = schema.get("required", [])
            if isinstance(data, dict):
                for field in required:
                    if field not in data:
                        errors.append(ValidationError(
                            layer="schema",
                            field=field,
                            message=f"Missing required field: {field}",
                            expected=f"Field '{field}' present",
                            actual="Missing",
                        ))

            # Type check
            schema_type = schema.get("type", "")
            if schema_type:
                type_map = {
                    "object": dict,
                    "array": list,
                    "string": str,
                    "number": (int, float),
                    "integer": int,
                    "boolean": bool,
                }
                expected_type = type_map.get(schema_type)
                if expected_type and not isinstance(data, expected_type):
                    errors.append(ValidationError(
                        layer="schema",
                        field="root",
                        message=f"Expected type {schema_type}, got {type(data).__name__}",
                        expected=schema_type,
                        actual=type(data).__name__,
                    ))

        return errors

    # -----------------------------------------------------------------------
    # Layer 2: Requirements
    # -----------------------------------------------------------------------

    def _validate_requirements(
        self, output: str, requirements: list[str]
    ) -> list[ValidationError]:
        """Validate output against human-readable requirements."""
        errors = []
        output_lower = output.lower()

        for i, req in enumerate(requirements):
            req_lower = req.lower()

            # Pattern: "must contain X"
            contains_match = re.search(
                r'must\s+(contain|include|have)\s+["\']?(.+?)["\']?$',
                req_lower,
            )
            if contains_match:
                needle = contains_match.group(2).strip('"\'')
                if needle.lower() not in output_lower:
                    errors.append(ValidationError(
                        layer="requirements",
                        field=f"req_{i}",
                        message=f"Output must contain '{needle}' but doesn't",
                        expected=f"Contains: {needle}",
                    ))

            # Pattern: "must be in X language"
            lang_match = re.search(r'must be in\s+(\w+)', req_lower)
            if lang_match:
                lang = lang_match.group(1)
                # Simple check: look for common words
                lang_words = {
                    "swedish": ["och", "att", "det", "som", "är", "med", "för"],
                    "english": ["the", "and", "that", "with", "for", "are"],
                }
                check_words = lang_words.get(lang, [])
                if check_words:
                    found = sum(1 for w in check_words if w in output_lower.split())
                    if found == 0:
                        errors.append(ValidationError(
                            layer="requirements",
                            field=f"req_{i}",
                            message=f"Output should be in {lang} but no {lang} words detected",
                            severity="warning",
                        ))

            # Pattern: "must have at least N items/elements/entries"
            count_match = re.search(
                r'must have at least (\d+)\s+(items?|elements?|entries?|records?)',
                req_lower,
            )
            if count_match:
                min_count = int(count_match.group(1))
                # Count lines or list items
                lines = [
                    l for l in output.split("\n")
                    if l.strip().startswith(("- ", "* ", "• ", "1. ", "2. ", "3. "))
                ]
                if len(lines) < min_count:
                    errors.append(ValidationError(
                        layer="requirements",
                        field=f"req_{i}",
                        message=f"Output must have at least {min_count} items, found {len(lines)}",
                        expected=f">= {min_count} items",
                        actual=f"{len(lines)} items",
                    ))

            # Pattern: "must not contain X"
            not_match = re.search(
                r'must not contain\s+["\']?(.+?)["\']?$', req_lower
            )
            if not_match:
                forbidden = not_match.group(1).strip('"\'')
                if forbidden.lower() in output_lower:
                    errors.append(ValidationError(
                        layer="requirements",
                        field=f"req_{i}",
                        message=f"Output must not contain '{forbidden}' but it does",
                        expected=f"Does NOT contain: {forbidden}",
                    ))

        return errors

    # -----------------------------------------------------------------------
    # Layer 3: Tests
    # -----------------------------------------------------------------------

    def _validate_tests(
        self, output: str, tests: list[dict]
    ) -> list[ValidationError]:
        """Validate output against test cases."""
        errors = []
        output_lower = output.lower()

        for i, test in enumerate(tests):
            expected_contains = test.get("expected_contains", [])
            if isinstance(expected_contains, str):
                expected_contains = [expected_contains]

            for item in expected_contains:
                if item.lower() not in output_lower:
                    errors.append(ValidationError(
                        layer="tests",
                        field=f"test_{i}",
                        message=(
                            f"Test case {i}: expected output to contain "
                            f"'{item}' but not found"
                        ),
                        expected=f"Contains: {item}",
                    ))

            # Check expected_not_contains
            expected_not = test.get("expected_not_contains", [])
            if isinstance(expected_not, str):
                expected_not = [expected_not]

            for item in expected_not:
                if item.lower() in output_lower:
                    errors.append(ValidationError(
                        layer="tests",
                        field=f"test_{i}",
                        message=(
                            f"Test case {i}: output should NOT contain "
                            f"'{item}' but it does"
                        ),
                        expected=f"Does NOT contain: {item}",
                    ))

        return errors

    # -----------------------------------------------------------------------
    # Fix suggestions
    # -----------------------------------------------------------------------

    def _generate_fix_suggestions(self, result: VerifyResult) -> list[str]:
        """Generate fix suggestions based on errors."""
        suggestions = []

        for err in result.schema_errors:
            if "Missing required field" in err.message:
                suggestions.append(
                    f"Add missing field '{err.field}' with an appropriate value"
                )
            elif "not valid JSON" in err.message:
                suggestions.append("Ensure output is valid JSON format")

        for err in result.requirement_errors:
            if "must contain" in err.message:
                suggestions.append(err.expected)
            if "must have at least" in err.message:
                suggestions.append(f"Add more items to meet minimum count")
            if "must not contain" in err.message:
                suggestions.append(f"Remove or replace all instances of forbidden content")

        for err in result.test_errors:
            if "expected output to contain" in err.message:
                suggestions.append(err.expected)

        return suggestions[:10]


# ---------------------------------------------------------------------------
# Write-verify (improve-ai-coworker-memory-and-tools 3.2/4.1)
# Efter en skrivande verktygsoperation läses nyckelfält tillbaka och jämförs
# med det avsedda. Resultatet matas in i kvalitetsloopen som ett lager
# (pass/fail/warn + fix-förslag) — samma VerifyResult-struktur som
# trelagers-verifieringen använder.
# ---------------------------------------------------------------------------

def _resolve_path(data: dict, path: str):
    """Slå upp 'a.b.c' i en nästlad dict. Returnerar (found, value)."""
    cur = data
    for part in (path or '').split('.'):
        if not part:
            continue
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


# Odoo:s html-widget skriver en platshållare när fältet saknar innehåll.
# Den är inte tom som sträng men betyder "inget innehåll".
_PLACEHOLDER_HTML_RE = re.compile(
    r'^\s*(?:<(?:p|div|span|br|h[1-6])\s*/?>|&nbsp;|\s)*$',
    re.IGNORECASE)


def _is_placeholder_html(value):
    """Är värdet tom HTML (t.ex. ``<p><br></p>``) snarare än innehåll?

    Odoo:s html-widget skriver ``<p><br></p>`` för ett tomt fält. En naiv
    tomhetskontroll ser 11 tecken och godkänner — vilket är hur document.page
    id 82 passerade write-verify i session 18204.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return True
    # Ta bort alla taggar och entiteter — återstår inget är det platshållare.
    stripped = re.sub(r'<[^>]*>', '', text)
    stripped = re.sub(r'&(?:nbsp|#160|#xA0);', ' ', stripped, flags=re.I)
    return not stripped.strip()


def _verify_source_coverage(result, check, field, actual, source):
    """Innehållslig verifiering av ett fält mot en källa (D5).

    Rapporterar täckning och tröskel i resultatet, och lägger avvikelsen i
    ``requirement_errors`` — inte ``warnings``. Att lägga den i warnings
    skulle låta den passera, vilket är hela problemet: ett "klart" som
    bygger på ofullständigt innehåll upptäcks annars först efteråt.
    """
    content = actual if isinstance(actual, str) else str(actual or '')
    threshold = check.get('coverage')
    try:
        threshold = float(threshold) if threshold is not None else 0.6
    except (TypeError, ValueError):
        threshold = 0.6
    threshold = max(0.0, min(1.0, threshold))

    if not source:
        # Avtalet begär källjämförelse men ingen källa nåddes fram.
        # Det är ett fel, inte en tyst pass — annars vore kravet verkningslöst.
        result.requirement_errors.append(ValidationError(
            layer='write_verify', field=field,
            message=(f'{field} requires source conformance but no source '
                     'was available to compare against'),
            expected='a source to compare against', actual='no source',
        ))
        return

    cov = measure_coverage(source, content, threshold=threshold)

    # Täckningen rapporteras alltid när kontrollen körs (krav: "hur stor del
    # av källan som återfinns" ska framgå av resultatet).
    result.coverage = cov.coverage
    result.coverage_threshold = cov.threshold
    result.coverage_total = cov.total
    result.coverage_found = cov.found

    if not cov.passed:
        missing = ', '.join(cov.missing[:8]) if cov.missing else '(okänt)'
        result.requirement_errors.append(ValidationError(
            layer='write_verify', field=field,
            message=(f'{field} covers only {cov.coverage:.0%} of the source '
                     f'(threshold {cov.threshold:.0%}); missing: {missing}'),
            expected=f'coverage >= {cov.threshold:.0%}',
            actual=f'{cov.coverage:.0%}',
        ))
        result.fix_suggestions.append(
            f'Lägg till det som saknas i {field}: {missing}')

    # Påstådd källa som inte finns i sammanhanget (3.1–3.3).
    claimed = _extract_claimed_sources(content)
    if claimed:
        missing_claims = _find_unbacked_claims(claimed, source)
        for claim in missing_claims:
            result.requirement_errors.append(ValidationError(
                layer='write_verify', field=field,
                message=(f'{field} cites a source not present in the '
                         f'context: {claim!r}'),
                expected='a source found in the context', actual=claim,
            ))
            result.fix_suggestions.append(
                f'Ta bort eller belägg källhänvisningen: {claim}')


# Källhänvisning i innehållet: "Källa: X", "Source: X", "(X)" efter Källa.
_CLAIMED_SOURCE_RE = re.compile(
    r'(?:källa|källor|source|sources|referens|reference)\s*[:\-]\s*'
    r'([^\n.;]{3,120})',
    re.IGNORECASE)


def _extract_claimed_sources(content):
    """Plocka ut påstådda källor ur innehållet."""
    if not content:
        return []
    claims = []
    for m in _CLAIMED_SOURCE_RE.finditer(content):
        # Dela på komma — "TV-guiden 2025/26 (SweClockers), Smart TV (Wikipedia)"
        for piece in re.split(r'[,;]', m.group(1)):
            piece = piece.strip(' \t()[]"\'')
            if len(piece) >= 3:
                claims.append(piece)
    return claims


def _find_unbacked_claims(claims, source):
    """Vilka påstådda källor som inte återfinns i källmaterialet."""
    if not source:
        return list(claims)
    haystack = re.sub(r'\s+', ' ', source).lower()
    unbacked = []
    for claim in claims:
        # Jämför på namnets kärna (före parentes/år) — "TV-guiden 2025/26
        # (SweClockers)" ska matcha om "SweClockers" finns i källan.
        core = re.sub(r'[()\[\]]', ' ', claim)
        core = re.sub(r'\b\d{4}(?:/\d{2,4})?\b', ' ', core)
        core = re.sub(r'\s+', ' ', core).strip().lower()
        if not core:
            continue
        if core in haystack:
            continue
        # Pröva också enskilda ord (>=5 tecken) ur påståendet.
        words = [w for w in core.split() if len(w) >= 5]
        if words and any(w in haystack for w in words):
            continue
        unbacked.append(claim)
    return unbacked


def verify_write_outcome(contract: dict, result_data: dict,
                         env=None, source: str = None) -> VerifyResult:
    """Verifiera ett verktygsutfall mot ett deklarativt avtal.

    Args:
        contract: {'model', 'id_path', 'checks': [{'field', ...}]}
        result_data: verktygets resultat (dict, t.ex. {'ok', 'id', ...})
        env: Odoo env (krävs för att läsa tillbaka posten)
        source: källmaterial för innehållslig verifiering (t.ex. sessionens
            rader). Läses BARA när en check begär det (`source`-villkor) —
            annars är kostnaden noll (design D1).

    Returns:
        VerifyResult — pass när alla checks stämmer, annars fail med
        per-fält-fel och fix-förslag.
    """
    result = VerifyResult()
    if not contract or not contract.get('checks'):
        return result  # inget avtal → pass (ingen write-verify)

    model = contract.get('model') or ''
    # model_path: modellen varierar per anrop (t.ex. odoo_create) — läs den
    # ur resultatet/argumenten i stället för ur avtalet.
    if not model and contract.get('model_path'):
        _found, model = _resolve_path(result_data, contract['model_path'])
        model = model or ''
    id_path = contract.get('id_path') or 'id'
    found, rec_id = _resolve_path(result_data, id_path)

    if not found or not rec_id:
        result.status = ValidationStatus.FAIL
        result.schema_errors.append(ValidationError(
            layer='write_verify', field=id_path,
            message=f'No record id at {id_path!r} in tool result',
            expected='an existing record id', actual=str(rec_id),
        ))
        result.needs_fix = True
        result.fix_suggestions.append(
            'Ensure the tool returns the created record id (e.g. {"id": N})')
        return result

    if env is None or model not in env.registry:
        result.status = ValidationStatus.WARN
        result.warnings.append(ValidationError(
            layer='write_verify', field='model',
            message=f'Cannot read back {model} (env/model unavailable)',
            severity='warning',
        ))
        return result

    record = env[model].browse(int(rec_id))
    if not record.exists():
        result.status = ValidationStatus.FAIL
        result.schema_errors.append(ValidationError(
            layer='write_verify', field='id',
            message=f'{model} {rec_id} does not exist after write',
            expected='existing record', actual='missing',
        ))
        result.needs_fix = True
        result.fix_suggestions.append(
            f'Re-create the {model} record and verify the returned id')
        return result

    for check in contract.get('checks') or []:
        field = check.get('field') or ''
        if not field or field not in record._fields:
            # Fältet finns inte på DENNA modell — avtalet är generiskt
            # (t.ex. odoo_create) och checken är då inte tillämplig.
            # Det är en varning, inte ett fel: vi ska inte fälla ett
            # giltigt utfall för att en check hör till en annan modell.
            result.warnings.append(ValidationError(
                layer='write_verify', field=field or '?',
                message=(f'Verification field {field!r} not on {model} — '
                         'check skipped'),
                severity='warning',
            ))
            continue

        actual = record[field]

        # Innehållslig verifiering (innehallsverifiering): avtalet kan begära
        # att fältet motsvarar en källa, inte bara är ifyllt. Körs bara när
        # checken begär det OCH en källa finns — annars oförändrat beteende.
        if check.get('source'):
            _verify_source_coverage(
                result, check, field, actual, source)
            continue

        if check.get('non_empty'):
            empty = (actual is False or actual is None
                     or actual == '' or actual == [])
            # HTML-platshållare räknas som tomt. Odoo:s html-widget skriver
            # "<p><br></p>" när ett fält saknar innehåll — det är 11 tecken
            # och passerar en naiv tomhetskontroll. Det var precis så
            # document.page id 82 godkändes i session 18204: content var
            # platshållaren, inte innehåll.
            if not empty and _is_placeholder_html(actual):
                empty = True
            if empty:
                result.requirement_errors.append(ValidationError(
                    layer='write_verify', field=field,
                    message=f'{field} is empty after write',
                    expected='non-empty', actual=repr(actual)[:80],
                ))
            continue

        if 'equals_path' in check:
            exp_found, expected = _resolve_path(
                result_data, check['equals_path'])
            if not exp_found:
                result.requirement_errors.append(ValidationError(
                    layer='write_verify', field=field,
                    message=(f'Expected value path {check["equals_path"]!r} '
                             'not found in tool result'),
                    expected=check['equals_path'], actual='missing',
                ))
                continue
            # many2one: jämför id; annars strängform
            actual_cmp = actual.id if hasattr(actual, 'id') else actual
            expected_cmp = expected
            if hasattr(expected, 'id'):
                expected_cmp = expected.id
            if str(actual_cmp) != str(expected_cmp):
                result.requirement_errors.append(ValidationError(
                    layer='write_verify', field=field,
                    message=f'{field} does not match intended value',
                    expected=str(expected_cmp), actual=str(actual_cmp),
                ))
            continue

        if 'equals' in check:
            if str(actual) != str(check['equals']):
                result.requirement_errors.append(ValidationError(
                    layer='write_verify', field=field,
                    message=f'{field} does not match intended value',
                    expected=str(check['equals']), actual=str(actual),
                ))

    total = len(result.all_errors)
    if total == 0:
        result.status = ValidationStatus.PASS
        result.score = 1.0
    else:
        result.status = ValidationStatus.FAIL
        result.score = max(0.0, 1.0 - total * 0.2)
        result.needs_fix = True
        for err in result.all_errors:
            result.fix_suggestions.append(
                f'{err.field}: expected {err.expected!r}, got {err.actual!r}')
    return result


# ---------------------------------------------------------------------------
# Innehållslig verifiering (innehallsverifiering)
# ---------------------------------------------------------------------------
# Ett utfall kan vara strukturellt giltigt och ändå innehållsligt fel: fältet
# är ifyllt men innehållet motsvarar inte det det påstås sammanfatta. Dessa
# hjälpare mäter täckning av en källas VÄSENTLIGA DELAR — deterministiskt och
# utan LLM (design D2). Kontrollen körs bara när avtalet begär den (D1).

# Namngivna entiteter: versal-inledda ord (Gamers Nexus, webosbrew, LG).
_ENTITY_RE = re.compile(r'\b[A-ZÅÄÖ][A-Za-zÅÄÖåäö0-9]*(?:[ -][A-ZÅÄÖ0-9][A-Za-zÅÄÖåäö0-9]*)*\b')
# Modellnamn: versaler + siffror (QM65C, UM5N, SQ1, 65EW960H, webOS 4.0).
# Modellnamn: versaler+siffror (QM65C, 65EW960H, UR640S). Bindestreck och
# punkt tillåtna INUTI namnet men inte i kanten — annars blir "UR640-" och
# "65EW960H-" egna falska modeller.
_MODEL_RE = re.compile(
    r'\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*[0-9])'
    r'[A-Z0-9][A-Z0-9.-]*[A-Z0-9]\b'
    r'|\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*[0-9])[A-Z0-9]{3,}\b')
# Citat: "...", '...', “...”, «...». Får inte spänna över radbrytning.
# Korthetsfiltret sköts av _is_noise, inte av regexen — annars tappas korta
# men giltiga citat som "we own the glass".
#
# Kravet att citatet ska se ut som en citering (innehålla minst ett ord av
# rimlig längd, och inte börja/sluta med skiljetecken) hindrar att två
# åtskilda citattecken paras ihop och att texten MELLAN dem blir ett falskt
# citat — det hände när korta citat stod tätt ("ok" ... "we own the glass").
# Citat: "...", '...', “...”, «...». Citattecken paras i tur och
# ordning (första med andra, tredje med fjärde ...) i stället för med en
# regex — en regex kan inte veta vilket citattecken som är öppning och
# vilket som är stängning, och parade då ihop två åtskilda citat så att
# texten MELLAN dem blev ett falskt citat.
_QUOTE_CHARS = '\"\'\u201c\u201d\u00ab\u00bb'
_QUOTE_OPEN = '\"\'\u201c\u00ab'
_QUOTE_CLOSE = '\"\'\u201d\u00bb'


def _iter_quotes(source):
    """Ge (citat) för varje parat citattecken-par i källan.

    Citattecken paras i tur och ordning: ett öppnande följt av nästa
    stängande. Det gör att korta citat tätt intill varandra inte smälter
    samman till ett falskt citat av texten mellan dem.
    """
    if not source:
        return
    open_at = None
    for i, ch in enumerate(source):
        if ch not in _QUOTE_CHARS:
            continue
        if open_at is None:
            open_at = i
            continue
        # Stängande citattecken: avsluta paret.
        inner = source[open_at + 1:i]
        if '\n' not in inner and 8 <= len(inner) <= 180:
            yield inner.strip()
        open_at = None
# URL:er.
_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')
# Sifferfakta: 3 860, 2 h 15 min, 4.0+.
_NUMBER_RE = re.compile(r'\b\d[\d\s.,:]*\d\b|\b\d+\b')

# Kvot per typ: hur många delar av varje slag som mäts. Summan är taket.
# Modellnamn och URL:er är få men tunga belägg — de får inte trängas ut av
# de talrika entiteterna.
_TYPE_QUOTA = [
    ('model', 40),
    ('url', 40),
    ('quote', 60),
    ('number', 40),
    ('entity', 220),
]


def _significance(item):
    """Rangordna en del inom sin typ — högre är mer signifikant.

    Signifikans = längd (längre namn/belägg är mer specifika) med ett lyft
    för delar som förekommer flera gånger i källan (upprepning är ett tecken
    på att tråden lade vikt vid dem).
    """
    text, freq = item if isinstance(item, tuple) else (item, 1)
    return (freq, len(text))


# Vanliga ord som inte är entiteter (meningars början, pronomen, vanliga
# svenska/engelska ord). Utan detta blir varje meningsinledning en "entitet".
_ENTITY_STOPWORDS = {
    'det', 'den', 'de', 'en', 'ett', 'och', 'men', 'som', 'att', 'för',
    'med', 'till', 'från', 'vid', 'när', 'hur', 'vad', 'vem', 'där',
    'här', 'denna', 'detta', 'dessa', 'han', 'hon', 'den', 'vi', 'ni',
    'jag', 'du', 'man', 'så', 'även', 'men', 'eller', 'om', 'av', 'på',
    'i', 'är', 'var', 'kan', 'ska', 'har', 'hade', 'blir', 'blev',
    'the', 'and', 'but', 'that', 'this', 'these', 'those', 'with',
    'from', 'when', 'how', 'what', 'who', 'where', 'here', 'there',
    'for', 'not', 'are', 'was', 'were', 'has', 'have', 'had', 'will',
    'would', 'can', 'could', 'should', 'may', 'might', 'must', 'its',
    'it', 'is', 'be', 'been', 'being', 'as', 'at', 'by', 'in', 'on',
    'to', 'of', 'or', 'if', 'so', 'no', 'yes', 'all', 'any', 'each',
}

# Värdelösa som "väsentliga delar": sökmotor-omdirigeringar, annonsnät,
# katalogdomäner och liknande. Utan filtret dominerar de mätningen — i
# session 18204 var 8 av 12 URL:er Bing-omdirigeringar med 400-teckens-tokens.
_NOISE_URL_PATTERNS = re.compile(
    r'(bing\.com/ck/|google\.com/url|duckduckgo\.com/l/|'
    r'facebook\.com/l\.php|t\.co/|bit\.ly/|/redirect|utm_)',
    re.IGNORECASE)
# Domäner som är kataloger/uppslagsverk utan beläggvärde i en sammanfattning.
_NOISE_DOMAINS = re.compile(
    r'^(?:www\.)?(?:tv\.nu|allatvkanaler\.se|synonymer\.se|'
    r'wikipedia\.org|bing\.com|google\.com|youtube\.com|youtu\.be)/?$',
    re.IGNORECASE)
# Annons-/sponsringsord som inte är belägg.
_NOISE_WORDS = {
    'sponsored', 'sponsrade', 'sponsrad', 'annons', 'annonser', 'reklam',
    'advertisement', 'advertising', 'cookie', 'cookies', 'integritetspolicy',
    'privacy policy', 'läs mer', 'read more', 'klicka här', 'click here',
}


def _is_noise(kind, text):
    """Är denna 'väsentliga del' brus snarare än ett belägg?"""
    if not text:
        return True
    low = text.lower().strip()
    if kind == 'url':
        if _NOISE_URL_PATTERNS.search(text):
            return True
        # Domän utan sökväg och med katalogkaraktär.
        m = re.match(r'https?://([^/]+)', text)
        if m and _NOISE_DOMAINS.match(m.group(1)):
            return True
        # Extremt långa URL:er är nästan alltid spårnings-omdirigeringar.
        if len(text) > 200:
            return True
        return False
    if kind == 'quote':
        # Korta fragment är inte belägg ("ok", "ja", "se ovan"). Gränsen är
        # låg nog att behålla korta men giltiga citat ("we own the glass").
        return len(text.strip()) < 12
        # Ett citat som innehåller meningsavslutning är ingen sammanhållen
        # citering — det är text mellan två citattecken på olika ställen.
        if re.search(r'[.!?]\s+[A-ZÅÄÖ]', text) and len(text) > 120:
            return True
        return False
    if kind == 'entity':
        if low in _NOISE_WORDS:
            return True
        # En "entitet" som ser ut som en domän är ingen entitet.
        if re.match(r'^[\w.-]+\.(se|com|org|net|nu|io|co\.uk)$', low):
            return True
        return False
    if kind == 'number':
        # Ensamma små tal (1–31) är oftast listnummer/datum, inte fakta.
        digits = re.sub(r'\D', '', text)
        return len(digits) <= 2 and len(text.strip()) <= 2
    return False


@dataclass
class SourceCoverage:
    """Resultatet av en täckningsmätning mot en källa."""
    coverage: float = 1.0           # 0.0–1.0, andel funna väsentliga delar
    threshold: float = 0.0          # avtalets tröskel
    total: int = 0                  # antal väsentliga delar i källan
    found: int = 0                  # antal som återfinns i innehållet
    missing: list = field(default_factory=list)   # exempel på det som saknas

    @property
    def passed(self) -> bool:
        return self.coverage >= self.threshold


def extract_essential_parts(source: str, limit: int = 400) -> list:
    """Plocka ut källans väsentliga delar (design D2).

    Väsentligt = det som en sammanfattning inte får tappa: namngivna
    entiteter, modellnamn, citat, URL:er och sifferfakta. Det är en grov men
    förutsägbar mätning — den fångar exakt det fall session 18204 visade
    (utelämnade namn och belägg) utan att kosta ett LLM-anrop.

    Urvalet är **kvoterat per typ** (se ``_TYPE_QUOTA``): utan kvoter fyller
    de talrika entiteterna hela utrymmet och tränger ut modellnamn, citat
    och URL:er — just de belägg som är mest värda att mäta. Inom varje typ
    rangordnas delarna efter signifikans (se ``_significance``).

    Returnerar en lista av (typ, text), avdubblerad.
    """
    if not source:
        return []
    buckets = {}
    seen = set()

    def _add(kind, text):
        key = (kind, text.lower())
        if key in seen:
            return
        seen.add(key)
        buckets.setdefault(kind, []).append(text)

    for m in _URL_RE.finditer(source):
        _add('url', m.group(0))
    for q in _iter_quotes(source):
        _add('quote', q)
    for m in _MODEL_RE.finditer(source):
        _add('model', m.group(0))
    for m in _ENTITY_RE.finditer(source):
        tok = m.group(0).strip()
        # Entiteter: minst 2 tecken, inte stoppord, inte ren siffra.
        if len(tok) < 2 or tok.lower() in _ENTITY_STOPWORDS:
            continue
        if _NUMBER_RE.fullmatch(tok):
            continue
        _add('entity', tok)
    for m in _NUMBER_RE.finditer(source):
        _add('number', m.group(0).strip())

    # Brusfilter: sökmotor-omdirigeringar, katalogdomäner, annonsord och
    # korta fragment är inte belägg. Utan detta dominerar de mätningen.
    for kind in buckets:
        buckets[kind] = [t for t in buckets[kind] if not _is_noise(kind, t)]

    # Kvotera per typ så ingen typ tränger ut en annan. Ordningen är stabil
    # (url, quote, model, entity, number) så mätningen är deterministisk.
    out = []
    for kind, quota in _TYPE_QUOTA:
        items = buckets.get(kind, [])
        # Rangordna inom typ: mest signifikanta först.
        items = sorted(items, key=_significance, reverse=True)
        out.extend((kind, t) for t in items[:quota])
    return out[:limit]


def measure_coverage(source: str, content: str,
                     threshold: float = 0.0,
                     limit: int = 400) -> SourceCoverage:
    """Mät hur stor del av källans väsentliga delar som finns i innehållet.

    Jämförelsen är skiftlägesokänslig och normaliserar whitespace, så att
    "Gamers  Nexus" och "gamers nexus" räknas som samma belägg.
    """
    parts = extract_essential_parts(source, limit=limit)
    if not parts:
        # Ingen källa att mäta mot → betrakta som full täckning (inget att
        # underkänna). Avtalet kan ändå ha andra checks.
        return SourceCoverage(coverage=1.0, threshold=threshold,
                              total=0, found=0, missing=[])

    def _norm(s):
        return re.sub(r'\s+', ' ', (s or '').lower()).strip()

    haystack = _norm(content)
    missing = []
    found = 0
    for kind, text in parts:
        if _norm(text) in haystack:
            found += 1
        else:
            missing.append(text)

    total = len(parts)
    return SourceCoverage(
        coverage=(found / total) if total else 1.0,
        threshold=threshold,
        total=total,
        found=found,
        missing=missing[:20],
    )
