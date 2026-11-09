---
name: Vauxr management
version: alpha
description: Quiet violet management UI, with semantic dark and light themes.
colors:
  status-ok: "rgb(16, 185, 129)"
  status-ok-dot: "rgb(52, 211, 153)"
  status-ok-ink-dark: "rgb(110, 231, 183)"
  status-ok-ink-light: "rgb(18, 101, 77)"
  status-error: "rgb(220, 84, 95)"
  voice-start: "rgb(228, 210, 255)"
  voice-mid: "rgb(177, 139, 234)"
  voice-end: "rgb(157, 119, 220)"
  action-start: "rgb(135, 80, 208)"
  action-mid: "rgb(133, 68, 225)"
  action-end: "rgb(98, 38, 184)"
  action-hover-start: "rgb(146, 86, 223)"
  action-hover-mid: "rgb(139, 78, 219)"
  action-hover-end: "rgb(118, 52, 208)"
  canvas-dark: "#111214"
  panel-dark: "#1b1c20"
  panel-raised-dark: "#24252b"
  line-dark: "#34353e"
  text-dark: "#f0eff5"
  muted-dark: "#a6a5b2"
  accent-dark: "#bda0ff"
  accent-ink-dark: "#21113e"
  neutral-50-dark: "rgb(250, 250, 252)"
  neutral-100-dark: "rgb(241, 240, 245)"
  neutral-200-dark: "rgb(222, 221, 230)"
  neutral-300-dark: "rgb(198, 197, 209)"
  neutral-400-dark: "rgb(171, 170, 185)"
  neutral-500-dark: "rgb(153, 152, 168)"
  neutral-600-dark: "rgb(119, 118, 135)"
  neutral-700-dark: "rgb(66, 66, 78)"
  neutral-800-dark: "rgb(43, 44, 51)"
  neutral-900-dark: "rgb(29, 30, 35)"
  neutral-950-dark: "rgb(19, 20, 24)"
  accent-100-dark: "rgb(237, 226, 255)"
  accent-200-dark: "rgb(219, 199, 255)"
  accent-300-dark: "rgb(199, 171, 255)"
  accent-400-dark: "rgb(183, 146, 251)"
  accent-500-dark: "rgb(139, 92, 226)"
  accent-600-dark: "rgb(115, 70, 200)"
  accent-700-dark: "rgb(95, 53, 172)"
  accent-800-dark: "rgb(72, 41, 129)"
  accent-900-dark: "rgb(49, 31, 83)"
  accent-950-dark: "rgb(30, 20, 48)"
  canvas-light: "#f5f4f8"
  panel-light: "#fff"
  panel-raised-light: "#efedf5"
  line-light: "#d9d6e3"
  text-light: "#24212d"
  muted-light: "#656071"
  accent-light: "#6c3bc2"
  accent-ink-light: "#fff"
  neutral-50-light: "rgb(20, 18, 27)"
  neutral-100-light: "rgb(36, 33, 45)"
  neutral-200-light: "rgb(48, 44, 59)"
  neutral-300-light: "rgb(65, 60, 78)"
  neutral-400-light: "rgb(88, 82, 104)"
  neutral-500-light: "rgb(103, 97, 118)"
  neutral-600-light: "rgb(121, 115, 136)"
  neutral-700-light: "rgb(195, 190, 207)"
  neutral-800-light: "rgb(232, 229, 239)"
  neutral-900-light: "rgb(246, 244, 250)"
  neutral-950-light: "rgb(255, 255, 255)"
  accent-100-light: "rgb(58, 26, 111)"
  accent-200-light: "rgb(74, 34, 137)"
  accent-300-light: "rgb(95, 44, 171)"
  accent-400-light: "rgb(112, 65, 190)"
  primary: "#bda0ff"
typography:
  body:
    fontFamily: Inter, ui-sans-serif, system-ui, sans-serif
    fontSize: 16px
    lineHeight: "1.55"
  heading:
    fontFamily: Inter, ui-sans-serif, system-ui, sans-serif
    fontSize: 36px
    letterSpacing: -0.045em
  label:
    fontFamily: Inter, ui-sans-serif, system-ui, sans-serif
    fontSize: 13px
    fontWeight: 550
  menu:
    fontFamily: Inter, ui-sans-serif, system-ui, sans-serif
    fontSize: 14px
    fontWeight: 550
  mono:
    fontFamily: ui-monospace, SFMono-Regular, Menlo, monospace
    fontSize: 13px
rounded:
  control: 10px
  surface: 18px
  modal: 22px
  menu-row: 11px
  tooltip: 12px
spacing:
  tight: 8px
  compact: 12px
  control: 16px
  panel: 24px
components:
  button-primary:
    textColor: "#ffffff"
    rounded: "{rounded.control}"
    typography: "{typography.label}"
  button-primary-hover:
    textColor: "#ffffff"
    rounded: "{rounded.control}"
  button-primary-active:
    textColor: "#ffffff"
    rounded: "{rounded.control}"
  modal:
    backgroundColor: "{colors.panel-dark}"
    textColor: "{colors.text-dark}"
    rounded: "{rounded.modal}"
  modal-light:
    backgroundColor: "{colors.panel-light}"
    textColor: "{colors.text-light}"
    rounded: "{rounded.modal}"
  overflow-menu:
    backgroundColor: "{colors.panel-dark}"
    textColor: "{colors.text-dark}"
    width: "252px"
    rounded: "{rounded.surface}"
  overflow-menu-item:
    height: "48px"
    rounded: "{rounded.menu-row}"
    typography: "{typography.menu}"
  overflow-menu-item-hover:
    rounded: "{rounded.menu-row}"
  overflow-menu-item-active:
    rounded: "{rounded.menu-row}"
  popover:
    backgroundColor: "{colors.panel-dark}"
    textColor: "{colors.text-dark}"
  popover-light:
    backgroundColor: "{colors.panel-light}"
    textColor: "{colors.text-light}"
  switch:
    typography: "{typography.label}"
  pipeline-graph:
    typography: "{typography.label}"
  disabled-reason-tooltip:
    rounded: "{rounded.tooltip}"
---

## Overview

Ada owns this current design contract. Update it in the **same PR** as UI changes.
The external Vauxr web-client design spec is the decision log; this document
records the current rules, not superseded feedback. Existing React/CSS primitives
are the implementation: do not introduce a component framework to restyle them.

Before each UI deployment, review the actual diff and desktop/phone, light/dark
screenshots against these rules and the current spec with fresh eyes. An isolated,
read-only reviewer is appropriate. Fix actionable inconsistencies before cutover;
report a one-line design-review result and any acceptance limits. A documentation-only
update does not need an image rebuild or deployment.

Format: [google-labs-code/design.md](https://github.com/google-labs-code/design.md).

## Colors

Source: [index.css](web-client/src/index.css) and
[Tailwind configuration](web-client/tailwind.config.js). Use semantic variables
for new surfaces and controls; avoid adding near-duplicate colors.

| Token | Dark | Light | Purpose |
| --- | --- | --- | --- |
| `--canvas` | `#111214` | `#f5f4f8` | Application background |
| `--panel` | `#1b1c20` | `#ffffff` | Cards, dialogs, menu panels |
| `--panel-raised` | `#24252b` | `#efedf5` | Raised areas, hover surfaces, tooltips |
| `--line` | `#34353e` | `#d9d6e3` | Quiet boundaries |
| `--text` | `#f0eff5` | `#24212d` | Primary text |
| `--muted` | `#a6a5b2` | `#656071` | Secondary text |
| `--accent` | `#bda0ff` | `#6c3bc2` | Violet emphasis and focus |
| `--accent-ink` | `#21113e` | `#ffffff` | Text on semantic accent fills |

CSS also defines RGB ramps `--neutral-50` through `--neutral-950` and
`--accent-100` through `--accent-950`. Tailwind's legacy `zinc` and `indigo`
names map to those ramps; `indigo` is the violet system, not a second palette.
`bg`, `surface`, and `surface-2` map to canvas, panel, and raised panel.
Consult the declarations for theme-specific ramp values rather than duplicating them.

Primary gradients currently use explicit shared-class stops (below). The older
`--primary-gradient` declaration is not the final `.button-primary` background.
Legacy fixed-color utilities remain; this contract does not claim a completed
all-token migration or an automated contrast audit. Maintain readable text and
visible state/focus in both themes; never use color as the only state cue.

## Typography

Use the YAML typography scale and the existing Tailwind font families. The heading token is the desktop maximum: page headings remain responsive at `clamp(28px, 3vw, 36px)`. Body line height is 1.55. Mobile inputs use 16px type to prevent zoom. Monospace is reserved for IDs and diagnostics.

## Layout

Prefer the existing 4/8px rhythm: 8px tight gaps, 12–16px control spacing, 20–24px panel padding. These spacing values describe conventions, not new CSS variables. One shell heading per page; no duplicate workspace title. Icon hit targets are normally 44px even when artwork is smaller.

## Elevation & Depth

`--surface-shadow: 0 4px 24px #00000012` is quiet card elevation. Overlays can use deeper shadows and thin `--line` borders. Avoid heavy nested boxes. The legacy Tailwind glow utilities are not new default elevations.

## Shapes

Use `--radius-control: 10px` and `--radius-surface: 18px`; preserve intentional component distinctions: dialogs 22px, menu rows 11px, tooltips 12px. Existing `.card` uses 14px; do not claim every surface has already migrated.

## Components

### Primary and secondary actions

Use the shared `.button-primary` class for primary actions, including **+ Add Agent**
at the top right above the list. White text, violet gradient, subtle top highlight:

| State | Current shared treatment |
| --- | --- |
| Rest | `linear-gradient(125deg, #8750d0 0%, #8544e1 42%, #6226b8 100%)` |
| Hover | `linear-gradient(110deg, #9256df 0%, #8b4edb 42%, #7634d0 100%)`, brighter highlight/glow |
| Press | Opacity `.9`, inset shadow and quieter outer shadow |
| Focus | Visible 2px accent outline with 4px offset |

Background position and shadow may transition; **no lift, translation, or scale**
on hover or press. Disabled actions retain recognizable styling without hover
feedback. Secondary controls are quieter, using panel/line/raised tokens.
Refresh actions are small icon-only controls with accessible names/tooltips,
adequate touch targets, and retained loading/error feedback.

### Dialogs and loading

Use [Modal](web-client/src/components/Modal.tsx): center horizontally **and**
vertically, standard × close, Escape dismissal, contained keyboard focus, inert
background, and focus return to the visible launching control.

Data-loading dialogs reserve their final viewport-constrained footprint immediately.
Show a centered [LoadingIndicator](web-client/src/components/LoadingIndicator.tsx)
spinner with an accessible loading name, not visible “Loading …” placeholder prose.
Do not expand from a short loading box after data arrives.

At 1366×768 desktop and 390×844 phone, in both themes, dialogs must fit without
inner scrollbars **or hidden/clipped content**. The no-page-scroll restriction
applies to modal content, not long management pages: normal vertical page scrolling
is permitted. Horizontal page overflow remains forbidden, including horizontal
scroll containers. Background page content being taller than the viewport while a
modal is open does not itself violate the modal constraint. The voice dialog currently uses
`min(700px, calc(100dvh - 24px))`; this is not a universal height for every dialog.
Compact graph spacing and layouts responsively. Use deliberate accessible steps
or tabs for lengthy setup/diagnostics, not `overflow: hidden` to conceal content.
Test errors, populated controls, open popovers, and loading as well as the happy path.
Other viewport sizes still require usable access; the standard matrix is not proof
that arbitrary content or mobile keyboards have been verified.

Create, delete, and pipeline save show processing feedback. Errors stay in the
same dialog with the draft intact and an actionable message; success closes it.
Deletion confirmation names the agent. Block deletion of the current default and
bundled agents; removed-agent device assignments fall back to the default route.

### Agent identity and overflow menu

[AgentsPanel](web-client/src/components/AgentsPanel.tsx) has **no whole-row action**
and no identity editor modal. Keep the pencil immediately beside the name for
inline rename; the avatar itself opens a file/dropzone dialog. Preserve upload
validation, authenticated persistence, rename save/cancel/error, and reload behavior.
Large agent-type choices in Add Agent use the canonical OpenClaw logo and approved
Vauxr Support artwork, not generic substitute glyphs.

The current default agent shows only its Default pill, not a disabled duplicate
Default/Set as default action in the list.

Anchor the ⋯ menu below its trigger with a 6px gap; flip upward only when
the panel would clip the viewport (16px margin).

The ⋯ menu contains icon-led **Voice pipeline**, **Runtime settings**, and
**Delete agent** actions. Use a comfortable raised panel: current width 252px,
viewport margin 16px, 48px rows, generous 12px icon/label gaps, quiet violet tint,
thin border, and soft elevation. Do not turn it into a tiny utility dropdown.

Disabled reasons belong in hover/focus/touch-accessible tooltips, not inline menu
prose. A disabled button needs a focusable wrapper and described reason. Position
those tooltips clear of sticky headers (above a downward-opening menu; below an upward-opening menu on phones).
Escape/outside interaction dismisses the menu. Selecting an action closes it
before opening a dialog; return focus to the visible ⋯ trigger, not a hidden item.
If a close fade retains native `<details>` content, make it inert synchronously so
invisible controls cannot catch clicks or Tab during the animation.

### Tabs, switches, and popovers

Use explicit selected/enabled/default states with text/icons as well as color.
Tabs support arrow-key navigation and Home/End; switches expose checked state and
keyboard activation. Keep at least one voice mode enabled and exactly one valid,
enabled default. Mark the default Standard/Live tab with a checkmark.

Small entity-local popovers contain labeled radio groups for provider, voice, or
model selection. Anchor to the activating label, fit the viewport, support keyboard
selection/Escape, and restore focus. Do not make users cross-reference controls in
a second provider form below the graph. Use existing hand-rolled primitives,
not a new Radix dependency.

### Voice pipeline graph

Reuse [AgentPipeline](web-client/src/components/AgentPipeline.tsx) and its
[styles](web-client/src/components/AgentPipeline.css) for Agents and Devices.
Standard has **one Voice Device left and one actual named Agent right**, with STT
and TTS stacked in the middle. Directed split/join paths are:

- Voice Device → Speech To Text → Agent.
- Agent → Text To Speech → Voice Device.

Live uses Voice Device ⇄ Realtime Provider ⇄ Agent. Never duplicate endpoint nodes
on phone. Keep entity icons distinct and cohesive with the violet system.
Enabled/Default controls live inside the graph box, not in an extra switch strip.
Only the provider/entity label opens its mini-editor; no clickable card border or
invisible card-sized hit box.

The **existing TTS speaker icon itself** plays/stops a sample through the connected
browser voice client. No extra Test button. Keep it visible but disabled with a
reason when disconnected. Never test on a physical device or silently save a voice
selection. Stop state stays inside the icon; playback status below the label must
not move the label or graph. Preserve cancellation and error feedback. A browser
play promise is not evidence that a human heard the audio.

Keep **Save pipeline configuration** at the bottom of the Agents dialog; it saves
all dialog edits. Runtime connection/configuration/default routing and technical
provenance belong in the separate **Agent runtime settings** dialog, not this graph.
Device overrides remain subordinate to agent assignment; label inherited routing
**Use default agent (<name>)**. Metrics must show real telemetry and timeframe,
never invented history or synthetic counters.

Theme changes apply ink and surfaces atomically; suppress transitions for the
first painted theme frame, then restore ordinary hover/focus motion. Primary
gradient stops use the named `action-*` RGB tokens. Management and avatar
tints derive from the theme-aware accent ramps rather than extra violet literals.
The voice orb retains its pale-violet stops as `voice-*` tokens; the volume track
uses accent/neutral ramps. Companion status labels must retain AA contrast.

### Motion and interaction

Use short, restrained opacity/color/shadow transitions to clarify state, not decorate
layout: surfaces/status about 160ms, native menu fade 140ms, primary gradient/shadow
about 200ms. No positional hover or press movement. Maintain layout during loading,
save feedback, and audio playback.

Honor `prefers-reduced-motion` throughout, including pseudo-elements such as
`::details-content` and `::backdrop`; a universal element selector alone misses
those. Do not delay interaction safety until an animation finishes.

### Review and verification

Before opening/updating a PR, run `npm --prefix web-client run design:lint` (or `npx @google/design.md lint DESIGN.md`); CI runs the same lightweight format check. Keep YAML tokens synchronized with `web-client/src/index.css` and `web-client/tailwind.config.js`. Light ramps inherit unspecified dark entries; semantic aliases `primary`, `bg`, `surface`, and `surface-2` must not introduce a second palette. `npx @google/design.md export --format json-tailwind DESIGN.md` can help compare values; do not replace the existing variable-backed Tailwind config with generated static colors.

For each UI pass: inspect desktop/phone light/dark screenshots and the actual diff,
then run focused tests for touched components, affected browser flows, and the
[every-modal no-overflow matrix](e2e/modal-layout.spec.ts). Check keyboard/focus,
visible disabled reasons, loading/error states, reload persistence, and graph
geometry. [Menu/motion checks](e2e/agent-menu-motion.spec.ts) cover retained-menu
focus, tooltips, and stationary primary actions.

Target a focused local verification loop of about 15 minutes. Deploy authorized
Nova test builds after that gate passes; CI runs the full suite once per PR update
in the background and remains the merge gate. Do not repeat the full suite locally,
rebuild for a pure test rerun, or claim pending CI passed. Run `node scripts/design-check.mjs` locally before deploy; the same disposable-browser gate runs in CI. Any FAIL or missing rendered evidence blocks deploy. Attempts are counted per commit in Git worktree metadata (including failed or interrupted attempts), at most three. After fixes, rerun with `--round=2` then `--round=3`; stop and report unresolved failures rather than looping or bypassing them. WARN means incomplete analysis or a review limitation, not a proven pass. Report live source revision
separately from a newer documentation-only PR head.

## Do's and Don'ts

- Do update DESIGN.md in the same PR as UI changes and check tokens against CSS and Tailwind.
- Do use label-only mini-editor triggers, accessible disabled-reason tooltips, and fixed-height centered loading.
- Do review the actual diff and screenshots against DESIGN.md and the current spec before each deploy.
- No duplicate page headings, whole-row Agent click targets, or identity-only editor.
- No duplicated graph endpoints, wrong arrow directions, extra TTS Test button,
  bordered entity hit boxes, or redundant provider form below the graph.
- No inner modal scrollbar at standard sizes, hidden clipping, collapsing loading
  footprint, or visible “Loading …” placeholder.
- No positional lift/scale, gratuitous animation, or ignored reduced-motion setting.
- No inline disabled-reason clutter, inaccessible disabled hints, or focus returned
  into a closed menu.
- No simulated persistence/audio/telemetry presented as real behavior; no framework
  replacement, unapproved runtime rearchitecture, or claim of untested acceptance.
