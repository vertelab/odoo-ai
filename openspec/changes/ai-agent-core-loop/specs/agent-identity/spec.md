# Spec: Agent Identity (SOUL.md + Memory + Skills)

## Requirements

### ID-001 [NOW] — Identity model
The system MUST provide an `ai.identity` model that bundles an agent's complete identity.
- An identity MUST contain: name, soul (personality + style + values + boundaries)
- An identity MAY contain: user_model, memory_ids, skill_ids
- An identity MUST generate a compiled `system_prompt` from its components
- Multiple quests MAY share the same identity (e.g., organization-wide specialist)
- A quest MAY have a unique identity (e.g., personal companion)

### ID-002 [NOW] — Soul definition
The system MUST support structured soul definitions with four dimensions:
- `personality`: character traits ("torr humor, rak, alltid korrekt")
- `style`: communication preferences ("säger 'du', korta svar, inga emojis")
- `values`: guiding principles ("korrekthet > snabbhet, transparens alltid")
- `boundaries`: explicit limits ("gör inga skattejuridiska tolkningar")
- The soul MUST be compiled into the agent's system prompt
- The soul MUST be editable by the quest owner

### ID-003 [NEXT] — User model
The system SHOULD maintain a persistent model of the user.
- `user_model`: prose description of the user's context, preferences, and patterns
- Updated via `/learn` or through observed interactions (Hermes pattern)
- Used as context in the system prompt for personal companions
- Disabled for organization/public identities (shared by many users)
- The user model MUST be private to the quest owner

### ID-004 [NOW] — Identity scope
The system MUST support three identity scopes:
- `personal`: owned by one user, includes user_model, uses personal memories
- `organization`: shared across the organization, no user_model, uses shared memories
- `public`: available to anyone, no user_model, minimal memories
- Scope MUST determine: visibility, editability, memory isolation

### ID-005 [NOW] — System prompt compilation
The system MUST compile the agent's system prompt from identity components at session start.
- Order: soul → user_model → memory_context → skills → tools
- If a component is disabled or empty, it MUST be omitted
- The compiled prompt MUST be cacheable per session (does not change mid-turn)
- Skill system prompts MUST be injected as separate sections with skill name headers

### ID-006 [NEXT] — Identity learning (Hermes /learn pattern)
The system SHOULD support learning and updating identity components through interaction.
- `/learn "remember that I prefer short answers"` → updates style.user_model
- `/learn "I work with Swedish accounting, focus on that"` → updates user_model
- `/learn "add a boundary: never suggest deleting records"` → updates boundaries
- Agent MAY proactively suggest identity updates based on observed patterns
- All identity changes MUST be confirmed by the user before saving

### ID-007 [NEXT] — Memory integration
The identity SHOULD bind to ai.memory for persistent context.
- Recent memories (last N) injected as context in system prompt
- Memory search available as a tool during conversations
- Memories tagged with identity_id for isolation
- Personal identities: memories are user-private
- Organization identities: memories are shared within org

### ID-008 [NOW] — Identity templates
The system MUST support identity templates for common agent types.
- Templates shipped with the module (e.g., "accountant", "analyst", "companion")
- Templates define default personality, style, values, boundaries
- Users can create quests from templates or define custom identities
- Templates are versioned and upgradable

## Non-requirements
- This spec does NOT cover the /learn implementation (separate change)
- This spec does NOT cover the skill system in detail (see skills-verify spec)
- This spec does NOT cover multi-user collaborative identities (future)
