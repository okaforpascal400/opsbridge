# CLAUDE.md — PZ Command Wall
Permanent rules for every session in this repo. These are laws, not suggestions. When output conflicts with this file, this file wins. Read ROADMAP.md for what we are building and current status.

## Design constitution — "Quiet Institutional Luxury"
The bar: built by a top-tier firm for a board — never a dashboard template. Reference DNA: Bloomberg Terminal seriousness × Linear restraint × fine annual-report typography. If a screen could appear in a template marketplace, it has failed.

### Typography (the biggest differentiator)
- Display face: **Fraunces** (variable, optical sizing on), weight 560–620, tight tracking — ONLY page titles, category names, hero numbers.
- UI/body face: **Inter Tight**. Never Fraunces for UI chrome.
- **JetBrains Mono** 12px — timestamps and code only. No fourth face exists.
- ALL numerals: `tabular-nums`. Hero KPIs: Fraunces 40–56px, unit/currency as smaller muted Inter Tight suffix (₦85.1 with half-size "bn").
- Exactly 6 sizes: 12/14/16/20/28/44px. Nothing off-scale. Eyebrows: 12px uppercase, +0.14em tracking, muted.

### Color (discipline is the aesthetic)
- Paper `#FBFAF8` (warm off-white; never pure white or grey page bg). Cards `#FFFFFF`. Ink `#16181D`. Muted `#5A6070`. Hairline `#E8E6E1`.
- ONE accent: PZ red `#E31837` — ONLY on: active scope pill, primary buttons, critical alarm tier, logo. Anywhere else = remove it.
- Deep navy `#12233D`: Control Tower big-screen background and nav text only. Not a card color.
- Semantic (data only): green `#067647`, amber `#B54708`, danger `#B42318` — tinted chips (tint bg + dark text). Never borders, never large fills.
- FORBIDDEN: surface gradients, glassmorphism, colored card backgrounds, second accents, dark-mode-by-default, any hex not listed here.

### Layout & surfaces
- Whitespace is the luxury: 64px section spacing, 24px card padding, 8px grid, 1280px max content width.
- Cards: white, 1px hairline, radius 10px, shadow `0 1px 2px rgba(22,24,29,.04)` max. Hover = hairline darkens only. No lift/scale/glow.
- Prefer hairline-ruled report sections over card-soup; cards only where grouping demands.
- Tables: no zebra. 1px hairline row rules, 44px rows, numerics right-aligned tabular, headers 12px uppercase muted.

### Motion (barely there)
150–250ms ease-out only. Count-up numbers on load/scope-switch. Feed items slide in 200ms. Nothing bounces, floats, or moves uninvited; single permitted pulse: critical-alarm dot, soft, 2s. Respect `prefers-reduced-motion`.

### Signature elements (carry the "not common" impression — build distinctively)
1. **Scope switcher** (app bar, top-center): `All | Family Care | Electricals`. Active = red fill white text. Switching cross-fades every number on screen (200ms) with count-up. This interaction IS the multinational story.
2. **Intelligence Feed** (collapsible right column, every category page + Tower): priority-ordered alarms CRITICAL (red chip) / HIGH (amber) / WATCH (neutral); each item = one-line plain-English finding, JetBrains Mono timestamp, owner dot, → link to source view. Wire-service feel: hairline-separated, no cards.
3. **Control Tower big-screen** (`/tower/display`, lobby TV): navy bg, white Fraunces numerals readable at distance, alarm ticker at bottom, KPIs auto-cycle 12s, zero interactive chrome. The most beautiful screen in the building.
4. **People Hub assistant**: answers typeset editorially — Fraunces first-line summary, Inter Tight body — every answer footed with a source chip ("Dress Code Policy §4"). Suggested questions as quiet text links, never button-soup.

## Engineering rules
- **Design tokens in ONE place** (`tokens.css` / `theme.ts`): every color, size, duration above. No raw hex/px outside tokens.
- **SBU security is server-side law**: every table carries `sbu`; every session carries `sbu_scope` (FAMILY_CARE | ELECTRICALS | ALL); middleware injects scope into EVERY query — human or AI-generated. Never trust the caller. UI scope switcher only re-requests within permitted scope.
- **AI layer**: Azure OpenAI, schema-only prompts — the model NEVER sees data rows. SELECT-only guardrails, server-side scope injection, full query audit log. Eval suite gates go-live; run it before declaring any AI change done.
- Accessibility: WCAG AA, 16px body floor, keyboard-reachable everything. Audience includes 60+ executives.
- Every page passes: 1366×768 above-the-fold, 1920×1080, 375px, and `/tower/display` at 3840×2160. Fold budgets are asserted in verify scripts — recover height from spacing, never move a limit.

## Working discipline
- After verification runs, ALWAYS kill any dev server you started. Never leave a shell holding port 3000.
- If port 3000 is taken, kill the PID the error names.
- Build clean + lint clean + verify scripts green before declaring any step done; end steps with screenshots of key states.
- Do not touch `.env.local`. Secrets never appear in code, commits, or output.
- One instance, one platform: no standalone sub-apps. Every module lands behind the same SSO, scope model, audit log, and acceptance discipline.
