# Spec: Skills & Verify (placeholder)

## Note

This spec is a placeholder. The full skills/verify subsystem (Taskless-inspired detect→route→improve→verify loop) will be specified in a separate change proposal. The current change only builds the agent loop and provider layer — the foundation that skills and verification will build upon.

## Provisional Requirements

### SKILL-001: Skill definition
The system SHOULD support defining skills as reusable agent configurations.
- A skill MUST have: name, description, trigger keywords
- A skill MAY have: recipe files, tool bindings, preferred model
- A skill MAY have: verify test cases

### SKILL-002: Skill binding
The system SHOULD support binding skills to agents and quests.
- An agent MAY have multiple skills
- A quest MAY override agent skills per invocation

### VERIFY-001: Three-layer validation
The system SHOULD implement three-layer validation for quest outputs.
- Schema layer: output format matches expected structure
- Requirements layer: all required fields present
- Tests layer: test cases pass

### VERIFY-002: Feedback loop
The system SHOULD implement a feedback loop for iterative improvement.
- Max 3 verification attempts before escalating to human
- Each attempt MUST produce structured error information
- After 3 failures, the system MUST surface errors and request human guidance

## Non-requirements for this change
- These are provisional — full spec in future change
