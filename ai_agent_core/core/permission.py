# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Permission Engine — decides allow / deny / ask-user for tool calls.

Inspired by OpenWorker's coworker.permissions module:
- Mode-gated tool execution (DISCUSS / PLAN / INTERACTIVE / AUTO)
- Risk classification (READ / WRITE_LOCAL / EXEC / EXTERNAL)
- Standing rules: per-session tool/command allowlisting
- Decision: allowed + reason + needs_user flag

Integrates with existing Tool.risk_level and InterruptHandler.
Backwards compatible: when no PermissionEngine is set, the existing
approval_threshold path in AgentLoop still works.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permission Mode
# ---------------------------------------------------------------------------


class PermissionMode(str, Enum):
    """How the agent handles tool permissions.

    DISCUSS: Read-only — replies but cannot modify anything.
    PLAN: Read-only + planning contract — explore first, then propose_plan
          for human approval before executing.
    INTERACTIVE: Auto-approve reads, ask on writes/commands (default).
    AUTO: Full access — all tools allowed (still path-scoped).
    CUSTOM: Interactive + a set of auto-allowed tools.
    """

    DISCUSS = "discuss"
    PLAN = "plan"
    INTERACTIVE = "interactive"
    AUTO = "auto"
    CUSTOM = "custom"


# Modes that are read-only (no writes allowed)
READ_ONLY_MODES = frozenset({PermissionMode.DISCUSS, PermissionMode.PLAN})


# ---------------------------------------------------------------------------
# Risk Classification
# ---------------------------------------------------------------------------


class RiskClass(str, Enum):
    """Intrinsic side-effect category for tools.

    READ: No side effects — always allowed (search, read, calculator).
    WRITE_LOCAL: Mutates workspace data — path/mode-gated (write, create).
    EXEC: Runs commands — mode-gated (shell, eval).
    EXTERNAL: Side effects off-machine — highest scrutiny (send_email, HTTP POST).
    """

    READ = "read"
    WRITE_LOCAL = "write_local"
    EXEC = "exec"
    EXTERNAL = "external"


# Mapping from existing Tool.risk_level → RiskClass
_RISK_LEVEL_TO_CLASS: dict[str, RiskClass] = {
    "safe": RiskClass.READ,
    "read_only": RiskClass.READ,
    "write": RiskClass.WRITE_LOCAL,
    "destructive": RiskClass.EXTERNAL,
    "execute": RiskClass.EXEC,
}

# Tool names that are always READ (bypass metadata)
_ALWAYS_READ = {
    "calculator", "web_search", "fetch_url", "echo",
    "search_read", "read", "todo_write", "load_skill",
    # Generic Odoo model tools (read-only)
    "describe_model", "odoo_search", "okf_search", "graph_query",
}

# Tool name prefixes that indicate WRITE_LOCAL
_WRITE_LOCAL_PREFIXES = ("write_", "create_")

# Generic Odoo model tools by name (odoo-model-tools change)
_ODOO_WRITE_TOOLS = {"odoo_create", "odoo_write"}
_ODOO_EXEC_TOOLS = {"odoo_call_method"}
_ODOO_EXTERNAL_TOOLS = {"odoo_unlink"}

# Tool name prefixes that indicate EXTERNAL / destructive
_EXTERNAL_PREFIXES = ("unlink_", "delete_")

# Shell/exec tool names
_EXEC_TOOLS = {"run_shell", "shell", "exec", "eval", "execute", "eval_code"}


def classify(
    tool_name: str,
    risk_level: str = "read_only",
    metadata: Any = None,
) -> RiskClass:
    """Classify a tool call's effective risk.

    Priority:
    1. Explicit by-name mapping (_ALWAYS_READ, _EXEC_TOOLS)
    2. Name prefix patterns
    3. Risk level from Tool dataclass
    4. Metadata hints

    Args:
        tool_name: The tool name (e.g. 'write_res_partner')
        risk_level: Existing Tool.risk_level string
        metadata: Optional metadata object (e.g. Tool instance)

    Returns:
        RiskClass enum value
    """
    # 1. Explicit by-name mapping
    base_name = tool_name.split("_", 1)[1] if "_" in tool_name else tool_name
    if tool_name in _ALWAYS_READ or base_name in _ALWAYS_READ:
        return RiskClass.READ
    if tool_name in _EXEC_TOOLS or base_name in _EXEC_TOOLS:
        return RiskClass.EXEC
    if tool_name in _ODOO_EXEC_TOOLS:
        return RiskClass.EXEC
    if tool_name in _ODOO_EXTERNAL_TOOLS:
        return RiskClass.EXTERNAL
    if tool_name in _ODOO_WRITE_TOOLS:
        return RiskClass.WRITE_LOCAL

    # 2. Name prefix patterns
    if tool_name.startswith(_EXTERNAL_PREFIXES):
        return RiskClass.EXTERNAL
    if tool_name.startswith(_WRITE_LOCAL_PREFIXES):
        return RiskClass.WRITE_LOCAL

    # 3. Risk level mapping
    risk_class = _RISK_LEVEL_TO_CLASS.get(risk_level)
    if risk_class is not None:
        return risk_class

    # 4. Metadata hints
    if metadata and getattr(metadata, "risk_level", None):
        return _RISK_LEVEL_TO_CLASS.get(metadata.risk_level, RiskClass.READ)

    # Default: read-only
    return RiskClass.READ


def is_consequential(risk: RiskClass) -> bool:
    """Anything but a pure read needs the permission engine's attention."""
    return risk is not RiskClass.READ


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    """The result of a permission check for one tool call.

    Attributes:
        allowed: Whether the tool call is allowed to proceed.
        reason: Human-readable explanation.
        needs_user: True if the surface should prompt the user for approval.
        rule: If a standing rule auto-allowed this, cite it for auditing.
    """

    allowed: bool
    reason: str = ""
    needs_user: bool = False
    rule: str = ""


# ---------------------------------------------------------------------------
# PermissionEngine
# ---------------------------------------------------------------------------


@dataclass
class PermissionEngine:
    """Decides allow / deny / ask-user for each proposed tool call.

    The engine only *decides*. The turn engine handles user prompting
    and recording the outcome.

    Usage:
        engine = PermissionEngine(mode=PermissionMode.INTERACTIVE)
        decision = engine.evaluate("write_res_partner", {"ids": [1], "values": {...}})
        if decision.needs_user:
            # Prompt user, then:
            engine.allow_tool_for_session("write_res_partner")
    """

    mode: PermissionMode = PermissionMode.INTERACTIVE

    # Per-session standing rules
    session_allow_tools: set[str] = field(default_factory=set)
    session_allow_commands: set[str] = field(default_factory=set)

    # Task-scoped standing rules: {tool_name: {allowed targets}}
    task_rules: dict[str, set[str]] = field(default_factory=dict)

    # CUSTOM mode: tools that are auto-allowed
    auto_allow_tools: set[str] = field(default_factory=set)

    # Access-grupper (ai-tool-access-capabilities): Odoo group ids för den
    # användare vars vägnar loopen körs. Verktyg vars group_ids inte korsar
    # dessa nekas (defense-in-depth-lager; primärfiltrering sker vid
    # registrering i ai.coworker.run()). Tom = ingen känd användare →
    # gruppbundna verktyg nekas.
    user_group_ids: set = field(default_factory=set)

    # Write tool names that require path scoping (from OpenWorker)
    _WRITE_TOOLS = {"write", "write_file", "create"}
    _SHELL_TOOLS = {"run_shell", "shell", "exec", "eval"}

    def evaluate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        metadata: Any = None,
    ) -> Decision:
        """Evaluate whether a tool call is allowed.

        Args:
            tool_name: The name of the tool (e.g. 'write_res_partner')
            arguments: The tool call arguments
            metadata: Optional metadata (Tool instance or similar)

        Returns:
            Decision with allowed flag, reason, and needs_user hint
        """
        # Extract risk level from metadata if available
        risk_level = "read_only"
        if metadata:
            if hasattr(metadata, "risk_level"):
                risk_level = metadata.risk_level

        risk = classify(tool_name, risk_level, metadata)

        # -- Access-grupper (ai-tool-access-capabilities) --
        # Verktyg med group_ids kräver att användarens grupper korsar dem.
        # Tom user_group_ids (ingen känd användare) → neka gruppbundna
        # verktyg; obundna (tom group_ids) passerar.
        if metadata and getattr(metadata, 'group_ids', None):
            tool_groups = set(metadata.group_ids)
            if tool_groups and not (self.user_group_ids & tool_groups):
                return Decision(
                    allowed=False,
                    reason=(f"Tool '{tool_name}' requires an access group "
                            "the current user lacks"),
                )

        # -- Hårda stopp (odoo-model-tools 3.3) --
        # odoo_call_method och odoo_unlink kräver ALLTID mänskligt
        # godkännande — oavsett trust-ladder-steg eller auto-läge.
        if tool_name in _ODOO_EXEC_TOOLS or tool_name in _ODOO_EXTERNAL_TOOLS:
            if self.mode == PermissionMode.AUTO:
                # Automation kan aldrig köra dessa utan människa → neka
                return Decision(
                    allowed=False,
                    reason=(f"{tool_name} kräver alltid mänskligt godkännande "
                            "(hårt stopp — ingen auto-approval)"),
                )
            return Decision(
                allowed=True,
                needs_user=True,
                reason=(f"{tool_name} kräver alltid mänskligt godkännande "
                        "(hårt stopp)"),
            )

        # -- AUTO mode: everything allowed --
        if self.mode == PermissionMode.AUTO:
            return Decision(allowed=True, reason="auto mode — full access")

        # -- CUSTOM mode: auto-allow listed tools, interactive for rest --
        if self.mode == PermissionMode.CUSTOM:
            if tool_name in self.auto_allow_tools:
                return Decision(allowed=True, reason="auto-allowed by custom config")
            # Fall through to interactive logic

        # -- DISCUSS / PLAN mode: read-only --
        if self.mode in READ_ONLY_MODES:
            if risk == RiskClass.READ:
                return Decision(allowed=True, reason="read-only tool in discuss/plan mode")
            return Decision(
                allowed=False,
                reason=(
                    f"tool '{tool_name}' requires writes, but session is in "
                    f"{self.mode.value} mode (read-only)"
                ),
                needs_user=False,
            )

        # -- Standing rule check (INTERACTIVE / CUSTOM / AUTO) --
        rule = self._check_standing_rule(tool_name, arguments)
        if rule:
            return Decision(allowed=True, reason=f"allowed by standing rule: {rule}", rule=rule)

        # -- Session allowlist --
        if tool_name in self.session_allow_tools:
            return Decision(allowed=True, reason="session-allowed tool", rule=f"session:{tool_name}")

        # -- RISK-based decisions for INTERACTIVE / CUSTOM --
        if risk == RiskClass.READ:
            return Decision(allowed=True, reason="read-only tool — auto-allowed")

        if risk == RiskClass.WRITE_LOCAL:
            return Decision(
                allowed=True,
                reason="write tool — requires user approval",
                needs_user=True,
            )

        if risk == RiskClass.EXEC:
            # Check command allowlisting
            command = str(arguments.get("command", arguments.get("cmd", "")))
            if command and self._command_allowed(command):
                return Decision(allowed=True, reason="session-allowed command")
            return Decision(
                allowed=True,
                reason="exec tool — requires user approval",
                needs_user=True,
            )

        if risk == RiskClass.EXTERNAL:
            return Decision(
                allowed=True,
                reason="external tool — requires user approval",
                needs_user=True,
            )

        # Fallback
        return Decision(
            allowed=True,
            reason="unclassified tool — requires user approval",
            needs_user=True,
        )

    # -- Standing rules --

    def allow_tool_for_session(self, tool_name: str) -> None:
        """Allow a specific tool for the remainder of this session."""
        self.session_allow_tools.add(tool_name)
        _logger.info("Session rule: allow tool '%s'", tool_name)

    def allow_command_for_session(self, command: str) -> None:
        """Allow a specific command for the remainder of this session."""
        self.session_allow_commands.add(command.strip())
        _logger.info("Session rule: allow command '%s'", command[:80])

    def add_task_rule(self, tool_name: str, target: str) -> None:
        """Add a task-scoped standing rule: allow tool for this target."""
        if tool_name not in self.task_rules:
            self.task_rules[tool_name] = set()
        self.task_rules[tool_name].add(target)
        _logger.info("Task rule: allow '%s' for target '%s'", tool_name, target)

    def _check_standing_rule(self, tool_name: str, arguments: dict) -> str:
        """Check if a task-scoped standing rule matches.

        Returns the rule string if matched, empty string otherwise.
        """
        if tool_name not in self.task_rules:
            return ""

        allowed_targets = self.task_rules[tool_name]

        # Check common target argument names
        for target_arg in ("email", "recipient", "channel", "target", "url", "path"):
            value = str(arguments.get(target_arg, "")).strip()
            if value and value in allowed_targets:
                return f"{tool_name} → {value}"

        return ""

    def _command_allowed(self, command: str) -> bool:
        """Check if a shell command is in the session allowlist.

        Matches by prefix: if 'git status' is in the allowlist,
        'git status --short' is also allowed (but 'git push' is not).
        """
        cmd_stripped = command.strip()
        for allowed in self.session_allow_commands:
            if cmd_stripped.startswith(allowed) or cmd_stripped == allowed:
                return True
        return False

    # -- Mode management --

    def set_mode(self, mode: PermissionMode) -> None:
        """Switch permission mode."""
        old = self.mode
        self.mode = mode
        _logger.info("Permission mode: %s → %s", old.value, mode.value)

    def apply_quest_rules(
        self,
        auto_allowed_tools: list[str] | None = None,
        auto_allowed_commands: list[str] | None = None,
    ) -> None:
        """Apply standing rules from a quest/automation configuration.

        Args:
            auto_allowed_tools: List of "tool target" or bare tool names
            auto_allowed_commands: List of command prefixes or full commands
        """
        for entry in (auto_allowed_tools or []):
            entry = entry.strip()
            if " " in entry:
                tool, target = entry.split(" ", 1)
                self.add_task_rule(tool.strip(), target.strip())
            else:
                self.auto_allow_tools.add(entry)

        for cmd in (auto_allowed_commands or []):
            self.session_allow_commands.add(cmd.strip())
