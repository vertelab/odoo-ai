# Spec: Responsive Chat UI

The web chat SHALL adapt to mobile, tablet, and desktop viewports.

## ADDED Requirements

### Requirement: Mobile-responsive layout
The chat UI SHALL use CSS media queries to adapt to viewports narrower than 768px. On mobile, the sidebar MUST collapse to a slide-over panel triggered by a hamburger menu button.

#### Scenario: Mobile sidebar is hidden by default
- **WHEN** a user opens `/ai/chat` on a device with viewport width ≤ 768px
- **THEN** the sidebar MUST be hidden off-screen
- **AND** a hamburger menu button (≡) MUST be visible in the header

#### Scenario: Hamburger opens sidebar
- **WHEN** the user taps the hamburger menu button on mobile
- **THEN** the sidebar MUST slide in from the left
- **AND** a semi-transparent overlay MUST appear behind the sidebar

#### Scenario: Overlay tap closes sidebar
- **WHEN** the sidebar is open on mobile
- **AND** the user taps the overlay outside the sidebar
- **THEN** the sidebar MUST slide closed

### Requirement: Desktop layout preserved
On viewports wider than 768px, the existing two-panel layout (sidebar + chat) SHALL remain unchanged.

#### Scenario: Desktop shows persistent sidebar
- **WHEN** a user opens `/ai/chat` on a device with viewport width > 768px
- **THEN** the sidebar MUST be visible as a fixed 280px panel

### Requirement: Touch-optimized inputs on mobile
On mobile, input elements and buttons SHALL have minimum touch target sizes of 44px height and adequate spacing.

#### Scenario: Mobile inputs are larger
- **WHEN** the viewport width ≤ 768px
- **THEN** the send button MUST be at least 44px tall
- **AND** the text input MUST be at least 44px tall
