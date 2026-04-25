# Pending Work — flare-defi-model
---

## Sprint 2026-04-24 — UI/UX full redesign (handoff from Cowork)

**Handoff doc:** `../shared-docs/CLAUDE-CODE-HANDOFF.md`
**Design system:** `../common/ui_design_system.py` — copy to `ui/design_system.py`
**Research:** `../shared-docs/design-research/2026-04-23-redesign-research.md`
**Mockups for this app** (all in `../shared-docs/design-mockups/`):
  - `sibling-family-flare-defi-PORTFOLIO.html — Portfolio`
  - `sibling-family-flare-defi-OPPORTUNITIES.html — Opportunities`
  - `sibling-family-flare-defi-MARKET-INTELLIGENCE.html — Market Intelligence`
  - `sibling-family-flare-defi-AGENT.html — Agent`

**Family:** sibling
**Accent:** #1d4ed8 (flare-blue)
**Priority note:** Third in order. Reuse the sibling design system proven on crypto-signal-app.

### Redesign tasks — work in order, commit after each

- [ ] 1. Copy `common/ui_design_system.py` → `ui/design_system.py`. Import in `app.py`. Call `inject_theme("flare-defi-model")` at the top of every page (after `set_page_config`, before `apply_theme`).
- [ ] 2. Replace `ui/sidebar.py` with the new left-rail design per mockup. Include: brand header, user-level selector, theme toggle, refresh button, mode indicators. Shared sidebar must render on every page (DV-1 pattern — reuse `render_sidebar()`).
- [ ] 3. Port the landing/home page per its mockup. First page to commit + verify end-to-end.
- [ ] 4. Port each remaining page, one per commit. Match the mockup for that page in layout, spacing, component choice.
- [ ] 5. Replace every hard-coded hex color in component code with a CSS variable reference or a `tokens`/`ACCENTS` lookup.
- [ ] 6. Verify both dark and light themes on every page. Verify mobile viewport (≤768px) on every page.
- [ ] 7. Ensure every data-consuming card has a `data_source_badge()` call per master template §10.
- [ ] 8. Run the post-change audit per CLAUDE.md §24 after each commit — 7 criteria pass, commit message has short summary, `MEMORY.md` has full findings.
- [ ] 9. Run `python tests/verify_deployment.py --env prod` after every push to `redesign/ui-2026-05` branch. Walk the 20-point checklist when the branch deploys to a test URL.
- [ ] 10. When all pages are done + user-approved: open a PR `redesign/ui-2026-05` → `main`. Do NOT merge without explicit user approval.

### Acceptance criteria (all must be ✓ before merge to main)

- [ ] Every page renders in the new design language
- [ ] Dark + light mode pass visually on every page
- [ ] Mobile viewport (≤768px) degrades gracefully on every page
- [ ] All existing unit tests pass; new tests added for new UI components
- [ ] Deploy verifier passes 100% on `redesign/ui-2026-05` deployed to a test URL
- [ ] Full 20-point browser checklist ✓ on test deploy
- [ ] `MEMORY.md` has "Redesign complete — 2026-XX-XX" entry with per-page audit
- [ ] User has reviewed the live test deploy and approved the look
