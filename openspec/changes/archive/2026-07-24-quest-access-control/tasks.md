# Tasks: Quest Access Control

## 1. Model Changes

- [x] 1.1 Add `show_in_chat` Boolean field to `ai.quest` (default=True)
- [x] 1.2 Add `group_ids` Many2many field to `ai.quest` (to `res.groups`)
- [x] 1.3 Add `user_ids` Many2many field to `ai.quest` (to `res.users`)
- [x] 1.4 Set default values for existing records (show_in_chat=True, empty groups/users)
- **Depends on:** nothing

## 2. Access Check Helper

- [x] 2.1 Create `_quest_is_accessible(quest, user)` function in `models/ai_quest.py`
  - Returns True if user is admin (base.group_system)
  - Returns True if user is quest owner (user_id)
  - Returns False if show_in_chat=False (unless admin)
  - If group_ids is set: user must be in at least one group
  - If user_ids is set: user must be in user_ids list
  - Both empty = open access (return True)
- [x] 2.2 Add unit test for `_quest_is_accessible` covering all cases
- **Depends on:** 1.1, 1.2, 1.3

## 3. Controller Changes

- [x] 3.1 Update `/ai/chat` quest listing to use `_quest_is_accessible()` for filtering
- [x] 3.2 Add access check in `/ai/stream` before quest execution
- [x] 3.3 Return HTTP 403 with JSON error when quest not accessible
- [x] 3.4 Ensure default quest (no quest_id) always works regardless of access
- [x] 3.5 Handle non-authenticated users with public-only quest filtering
- **Depends on:** 2.1

## 4. Views

- [x] 4.1 Add `show_in_chat` field to `ai.quest` form view (with tooltip)
- [x] 4.2 Add `group_ids` field to `ai.quest` form view (Access tab)
- [x] 4.3 Add `user_ids` field to `ai.quest` form view
- **Depends on:** 1.1, 1.2, 1.3

## 5. Testing & Migration

- [x] 5.1 Run existing quest tests — verify no regressions
- [x] 5.2 Add integration test: hidden quest not in chat list
- [x] 5.3 Add integration test: group-restricted quest visible only to group members
- [x] 5.4 Add integration test: user-restricted quest visible only to specified users
- [x] 5.5 Add integration test: admin bypasses all restrictions
- [x] 5.6 Add integration test: owner always has access
- [x] 5.7 Add integration test: stream endpoint rejects unauthorized quests
- **Depends on:** 2.1, 3.1, 3.2
