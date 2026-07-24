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
