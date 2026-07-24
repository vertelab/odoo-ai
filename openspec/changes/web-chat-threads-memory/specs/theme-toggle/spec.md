# Spec: Theme Toggle

The web chat SHALL support light and dark themes with user preference persistence.

## ADDED Requirements

### Requirement: Theme toggle button
The chat UI SHALL include a theme toggle button (🌗) in the header area. Clicking it SHALL switch between dark and light themes.

#### Scenario: Toggle switches theme
- **WHEN** the user clicks the theme toggle button
- **AND** the current theme is dark
- **THEN** the theme SHALL switch to light
- **AND** the toggle button SHALL update its appearance

#### Scenario: Toggle switches back
- **WHEN** the user clicks the theme toggle button
- **AND** the current theme is light
- **THEN** the theme SHALL switch to dark

### Requirement: Theme persistence via localStorage
The selected theme SHALL be persisted in `localStorage` under the key `ai-chat-theme`. On page load, the stored theme SHALL be applied before rendering.

#### Scenario: Theme survives page reload
- **WHEN** the user selects light theme
- **AND** reloads the page
- **THEN** the light theme SHALL be applied immediately

### Requirement: System preference detection
On first visit (no localStorage value), the theme SHALL default to the user's system preference (`prefers-color-scheme`). If the system preference is unavailable, dark theme SHALL be the default.

#### Scenario: System dark preference
- **WHEN** a user visits `/ai/chat` for the first time
- **AND** their system preference is dark mode
- **THEN** the dark theme SHALL be applied

#### Scenario: System light preference
- **WHEN** a user visits `/ai/chat` for the first time
- **AND** their system preference is light mode
- **THEN** the light theme SHALL be applied

### Requirement: CSS custom properties for theming
Themes SHALL be implemented using CSS custom properties under `[data-theme="dark"]` and `[data-theme="light"]` selectors. All colors MUST be defined as variables.

#### Scenario: Theme variables are complete
- **WHEN** either theme is active
- **THEN** all UI elements SHALL use colors from the active theme
- **AND** no hardcoded colors SHALL appear outside the `:root`/`[data-theme]` blocks
