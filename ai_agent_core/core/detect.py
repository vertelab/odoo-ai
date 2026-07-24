# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Environment Detector — scan before acting (TASK-001).

Before creating or modifying a quest, the system MUST scan the environment:
- Which quests already exist? (→ improve instead of create)
- Which Odoo modules are installed? (→ context)
- Which data quality issues exist? (→ quest candidates)
- Which providers/models are available? (→ capability matching)

Output MUST be deterministic JSON, offline-capable.
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

_logger = logging.getLogger(__name__)


@dataclass
class ModuleInfo:
    """Information about an installed module."""
    name: str
    version: str = ""
    state: str = "installed"  # installed | uninstalled | to_upgrade


@dataclass
class ModelInfo:
    """Information about a registered model."""
    name: str         # res.partner
    display_name: str  # Contact
    record_count: int = 0
    has_name_field: bool = True


@dataclass
class DataQualityIssue:
    """A detected data quality problem."""
    model: str          # res.partner
    field: str          # email
    issue_type: str     # missing | duplicate | invalid
    count: int = 0
    severity: str = "low"  # low | medium | high
    description: str = ""


@dataclass
class QuestInfo:
    """Information about an existing quest."""
    id: int = 0
    name: str = ""
    description: str = ""
    status: str = "draft"
    model_id: str = ""
    agent_count: int = 0


@dataclass
class ProviderInfo:
    """Available LLM provider/model."""
    provider_name: str = ""
    model_id: str = ""
    capabilities: dict = field(default_factory=dict)


@dataclass
class DetectResult:
    """Result of environment scanning."""
    timestamp: str = ""
    installed_modules: list[ModuleInfo] = field(default_factory=list)
    registered_models: list[ModelInfo] = field(default_factory=list)
    data_quality_issues: list[DataQualityIssue] = field(default_factory=list)
    existing_quests: list[QuestInfo] = field(default_factory=list)
    available_models: list[ProviderInfo] = field(default_factory=list)
    codebase_todos: list[str] = field(default_factory=list)
    recurring_patterns: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


class EnvironmentDetector:
    """Scans the Odoo environment and codebase for context.

    Usage:
        detector = EnvironmentDetector()
        result = detector.scan()  # Quick scan (no Odoo)
        # With Odoo env:
        result = detector.scan_full(env)  # Full scan with ORM
    """

    def __init__(self, codebase_paths: Optional[list[str]] = None):
        self.codebase_paths = codebase_paths or [
            "/usr/share/odoo-ai/",
            "/usr/share/odoo-account/",
            "/usr/share/odoo-l10n_se/",
        ]

    def scan(self) -> DetectResult:
        """Quick scan (no Odoo ORM needed). Detects codebase issues only."""
        import datetime
        result = DetectResult(
            timestamp=datetime.datetime.now().isoformat(),
        )
        result.codebase_todos = self._scan_codebase_todos()
        result.recurring_patterns = self._scan_recurring_patterns()
        return result

    def scan_full(self, env=None) -> DetectResult:
        """Full scan with Odoo environment (requires ORM)."""
        import datetime
        result = DetectResult(
            timestamp=datetime.datetime.now().isoformat(),
        )

        if env:
            result.installed_modules = self._scan_modules(env)
            result.registered_models = self._scan_models(env)
            result.data_quality_issues = self._scan_data_quality(env)
            result.existing_quests = self._scan_quests(env)
            result.available_models = self._scan_providers(env)

        result.codebase_todos = self._scan_codebase_todos()
        result.recurring_patterns = self._scan_recurring_patterns()

        return result

    # -- Module scanning --

    def _scan_modules(self, env) -> list[ModuleInfo]:
        """Detect installed Odoo modules."""
        try:
            modules = env['ir.module.module'].search([
                ('state', '=', 'installed'),
            ])
            return [
                ModuleInfo(
                    name=m.name,
                    version=m.latest_version or '',
                    state=m.state,
                )
                for m in modules
            ]
        except Exception as e:
            _logger.warning("Module scan failed: %s", e)
            return []

    # -- Model scanning --

    def _scan_models(self, env) -> list[ModelInfo]:
        """Detect registered Odoo models and their record counts."""
        try:
            models = env['ir.model'].search([
                ('transient', '=', False),
            ])
            result = []
            for m in models[:100]:  # Limit to avoid timeout
                try:
                    count = env[m.model].search_count([])
                except Exception:
                    count = 0

                result.append(ModelInfo(
                    name=m.model,
                    display_name=m.name,
                    record_count=count,
                ))
            return result
        except Exception as e:
            _logger.warning("Model scan failed: %s", e)
            return []

    # -- Data quality scanning --

    def _scan_data_quality(self, env) -> list[DataQualityIssue]:
        """Detect data quality problems."""
        issues = []

        # Check common patterns
        checks = [
            ("res.partner", "email", "missing", "low",
             "Partners without email"),
            ("res.partner", "name", "missing", "high",
             "Partners without name"),
            ("account.move", "date", "missing", "medium",
             "Journal entries without date"),
            ("product.product", "list_price", "zero", "low",
             "Products with zero price"),
            ("sale.order", "partner_id", "missing", "high",
             "Sales orders without partner"),
        ]

        for model, field, issue_type, severity, desc in checks:
            try:
                if issue_type == "missing":
                    count = env[model].search_count([
                        (field, '=', False),
                    ])
                elif issue_type == "zero":
                    count = env[model].search_count([
                        (field, '<=', 0),
                    ])
                else:
                    count = 0

                if count > 0:
                    issues.append(DataQualityIssue(
                        model=model,
                        field=field,
                        issue_type=issue_type,
                        count=count,
                        severity=severity,
                        description=desc,
                    ))
            except Exception:
                pass  # Model or field not available

        return issues

    # -- Quest scanning --

    def _scan_quests(self, env) -> list[QuestInfo]:
        """Detect existing AI quests."""
        try:
            quests = env['ai.quest'].search([])
            return [
                QuestInfo(
                    id=q.id,
                    name=q.name,
                    description=(q.description or "")[:200],
                    status=q.status,
                    agent_count=q.agent_count,
                )
                for q in quests[:50]
            ]
        except Exception as e:
            _logger.warning("Quest scan failed: %s", e)
            return []

    # -- Provider scanning --

    def _scan_providers(self, env) -> list[ProviderInfo]:
        """Detect available LLM providers and models."""
        try:
            models = env['ai.model'].search([('status', '=', 'active')])
            return [
                ProviderInfo(
                    provider_name=m.provider_id.name,
                    model_id=m.name,
                    capabilities={
                        "vision": m.is_vision,
                        "tools": m.has_tools,
                        "json_mode": m.has_json_mode,
                        "streaming": m.has_streaming,
                        "context_window": m.context_window,
                    },
                )
                for m in models[:50]
            ]
        except Exception as e:
            _logger.warning("Provider scan failed: %s", e)
            return []

    # -- Codebase scanning (no Odoo needed) --

    def _scan_codebase_todos(self) -> list[str]:
        """Scan codebase for TODOs, FIXMEs, and HACKs."""
        todos = []
        todo_markers = ["TODO", "FIXME", "HACK", "XXX"]

        for path in self.codebase_paths:
            if not os.path.isdir(path):
                continue
            for root, dirs, files in os.walk(path):
                # Skip hidden dirs, .git, __pycache__, node_modules
                dirs[:] = [d for d in dirs if not d.startswith('.')
                          and d not in ('__pycache__', 'node_modules', 'graphify-out')]

                for f in files:
                    if not f.endswith(('.py', '.xml', '.md')):
                        continue
                    try:
                        filepath = os.path.join(root, f)
                        with open(filepath, 'r', errors='ignore') as fh:
                            for i, line in enumerate(fh, 1):
                                for marker in todo_markers:
                                    if marker in line:
                                        clean = line.strip().lstrip('#').strip()
                                        todos.append(f"{filepath}:{i}: {clean[:120]}")
                                        break
                    except (OSError, UnicodeDecodeError):
                        pass

                if len(todos) > 200:  # Limit
                    return todos[:200]

        return sorted(todos)

    def _scan_recurring_patterns(self) -> list[str]:
        """Detect recurring patterns that suggest automation candidates."""
        patterns = []

        # Check for frequently run scripts
        script_dirs = [
            "/srv/salt/scripts/",
            "/home/waland/salt/scripts/",
        ]
        for sd in script_dirs:
            if os.path.isdir(sd):
                count = len([
                    f for f in os.listdir(sd)
                    if f.endswith(('.sh', '.py'))
                ])
                if count > 5:
                    patterns.append(
                        f"Script directory {sd} has {count} scripts — "
                        f"candidates for quest automation"
                    )

        # Check for memory files (agent learns from patterns)
        memory_dir = "/home/waland/.pi/agent/memory/"
        if os.path.isdir(memory_dir):
            daily_dir = os.path.join(memory_dir, "daily")
            if os.path.isdir(daily_dir):
                daily_count = len(os.listdir(daily_dir))
                if daily_count > 30:
                    patterns.append(
                        f"{daily_count} daily memory files — "
                        f"rich source for agent learning"
                    )

        return patterns
