# Inspiration report — sidebar + auth screens (GCP QC Trace)

**Date**: 2026-09-03
**Subject**: sidebar navigation, login screen, admin user-creation form
**Project design truth**: no `DESIGN.md`. `app/static/css/style.css` is the single token system
(navy `#0f2044`, teal `#0891b2`, Inter / IBM Plex Sans Arabic, radius 8/12, no `!important`
outside `@media print`). RTL is a first-class requirement — Arabic is a shipped locale.

**Queries** (Dribbble, `web-design`, popular):
1. `dashboard sidebar navigation` — 8 shots
2. `login page saas dashboard` — 6 shots
3. `industrial manufacturing quality dashboard` — 6 shots

## References

| # | Author | Shot | File |
|---|--------|------|------|
| 01 | Outcrowd | [WeStud — Dashboard sidebar navigation](https://dribbble.com/shots/21973938-WeStud-Dashboard-sidebar-navigation-for-the-education-platform) | `01-21973938-…webp` |
| 02 | Isaac Sanchez | [Dashboard Sidebar Navigation Component](https://dribbble.com/shots/24636563-Dashboard-Sidebar-Navigation-Component) | `02-24636563-…webp` |
| 03 | Nasir Uddin | [Dashboard Sidebar Navigation + Tooltip (Dark/Light)](https://dribbble.com/shots/20695016-Dashboard-Sidebar-Navigation-Tooltip-Dark-Light) | `03-20695016-…webp` |
| 04 | Mondaysys | [Enterprise ERP Dashboard Sidebar Navigation](https://dribbble.com/shots/27445231-Enterprise-ERP-Dashboard-Sidebar-Navigation) | `04-27445231-…webp` |
| 05 | Musemind | [Dashboard Sidebar Navigation + Tooltip (Dark/Light)](https://dribbble.com/shots/17122423-Dashboard-Sidebar-Navigation-Tooltip-Dark-Light) | `05-17122423-…webp` |
| 06 | Waffle Space | [Dashboard Sidebar Navigation — Taurus](https://dribbble.com/shots/22474831-Dashboard-Sidebar-Navigation-Taurus) | `06-22474831-…webp` |
| 07 | Zafor | [Dashboard Sidebar Navigation](https://dribbble.com/shots/15994018-Dashboard-Sidebar-Navigation) | `07-15994018-…webp` |
| 08 | Wardha Raheem | [Simple Dashboard Sidebar Navigation — Multi-Theme](https://dribbble.com/shots/27604012-Simple-Dashboard-Sidebar-Navigation-Multi-Theme-UI-Exploration) | `08-27604012-…webp` |
| 09 | Astrolab | [Riskora — Login Page Dashboard SaaS](https://dribbble.com/shots/26652273-Riskora-Login-Page-Dashboard-Saas) | `09-26652273-…webp` |
| 10 | Nextagrid™ | [Mailflow — Login Page](https://dribbble.com/shots/27499976-Mailflow-Login-Page-Email-Marketing-Saas-Dashboard) | `10-27499976-…webp` |
| 11 | OnPoint Studio | [Sellora — Login Page](https://dribbble.com/shots/25878160-Sellora-Login-Page-Marketplace-Dashboard-Saas) | `11-25878160-…webp` |
| 12 | Pixelcot ✪ | [SaaS Dashboard Login Page Design](https://dribbble.com/shots/26622037-SaaS-Dashboard-Login-Page-Design) | `12-26622037-…webp` |
| 13 | Angeline Nathania Huang | [Nexora — HRIS SaaS Login Page](https://dribbble.com/shots/27251278-Nexora-HRIS-SaaS-Dashboard-Login-Page) | `13-27251278-…webp` |
| 14 | Zahra Hashemi | [tCRM Asset Management — Login Page UI](https://dribbble.com/shots/27089379-Enter-your-tCRM-Asset-Management-Login-Page-UI) | `14-27089379-…webp` |
| 15 | MD.Rakibul Islam | [FlowTix — AI QC & Safety Dashboard](https://dribbble.com/shots/27597088-FlowTix-AI-QC-Safety-Dashboard) | `15-27597088-…webp` |
| 16 | Design Dundies | [Mountain West Lethal Precision](https://dribbble.com/shots/27502769-Mountain-West-Lethal-Precision-Defense-Supplier-Website-Design) | `16-27502769-…webp` |
| 17 | Shahriar Sultan | [FortSI — Smart Manufacturing Dashboard UI](https://dribbble.com/shots/25718119-FortSI-Smart-Manufacturing-Dashboard-UI) | `17-25718119-…webp` |
| 18 | MD.Rakibul Islam | [Manufacturing Dashboard / Factory ERP](https://dribbble.com/shots/26577382-Manufacturing-Dashboard-UI-UX-Design-Factory-ERP-Dashboard) | `18-26577382-…webp` |
| 19 | Ashar Waseem | [Field SaaS — Real-Time Production Intelligence](https://dribbble.com/shots/27665611-Field-SaaS-Real-Time-Production-Intelligence-Dashboard) | `19-27665611-…webp` |
| 20 | Creava Agency | [Dashboard for PCB Quality Tracking](https://dribbble.com/shots/26198146-Dashboard-for-PCB-Quality-Tracking) | `20-26198146-…webp` |

## Findings

**F1 — A saturated fill is not how large sidebars mark the active page.**
Shots 04, 03, 07 all carry 20–50 nav items and none of them fills the active row with the brand
accent. They use a *low-alpha* surface plus a 3px accent rail on the inline-start edge, and let
the accent survive only in the icon and the text weight. 15 and 08, which have 5–8 items, can
afford the saturated pill. Our sidebar has 40+ reachable links: it is a 04, not a 15.

**F2 — Top-level section eyebrows, not just sub-menu ones.**
04 groups its 52 modules under `OPERATIONS` / `FINANCE & PEOPLE`; 03 under `ANALYTICS` /
`APPLICATION` / `OTHERS`. Ours only labels sections *inside* the Reports sub-menu. The top level
is one undifferentiated 10-item run.

**F3 — Sub-items drop the icon and gain a rail.**
04 and 06 render children as text with a small bullet dot against a vertical hairline, not as a
nested filled panel. Ours paints each sub-menu on a lighter navy block (`--brand-navy-2`), which
reads as a second surface and fights the parent.

**F4 — The identity block is pinned to the bottom of the sidebar.**
04 and 03 put avatar + name + role in a bottom-pinned card. 08 puts it top. Either way it lives
in the sidebar, not only behind a topbar dropdown as ours does.

**F5 — Login is a split: brand panel + form card.**
09, 10, 11, 13 and 14 all use the same skeleton — one half carries product identity and a value
line, the other a narrow (≈360px) form column. None of them is a centered 400px box on a purple
gradient, which is what we ship today.

**F6 — Password fields carry a reveal toggle; "remember me" sits on the same row as the help link.**
13 and 09 both do this. Ours has neither.

**F7 — Industrial QC dashboards stay achromatic and reserve color for state.**
15, 17, 19, 20 use one accent and let red/amber/green mean *only* defect / warning / pass. Our
teal is currently spent on sidebar chrome, which weakens it as a signal elsewhere.

## Recommendations

| # | Recommendation | Verdict | Deciding rule |
|---|---|---|---|
| R1 | Re-tone the sidebar to a deeper, slightly desaturated navy ramp (`#0a1830` → `#0f2044`) with a hairline inline-end border | **adopt** | F1/F7 — a calmer ground makes the teal readable as signal |
| R2 | Active row = `rgba(8,145,178,.16)` surface + 3px teal rail on the inline-start edge + white 600 text, teal icon. Drop the solid teal fill | **adopt** | F1 |
| R3 | Add top-level section eyebrows (Operations / Quality / Analysis / System) | **adopt** | F2 |
| R4 | Sub-menu becomes transparent with a vertical hairline rail, no `--brand-navy-2` block | **adopt** | F3 |
| R5 | Bottom-pinned user card (initials avatar, name, role, logout) | **adopt** | F4 |
| R6 | Login → split screen: navy brand panel (logo, bilingual name, three plant-value lines) + form card | **adopt** | F5 |
| R7 | Password reveal toggle; "remember me" on one row | **adopt** | F6 |
| R8 | Sidebar search field with ⌘K (04) | **reject** | The topbar already has pipe-code search; a second search box in the sidebar would be two search affordances with different scopes |
| R9 | Social / Google sign-in (09, 13) | **reject** | Closed internal system, accounts are admin-issued |
| R10 | Illustration panel (13) | **adapt** | Use a CSS-only grid/glow, not stock art — no imagery budget and no illustration in the design system |
| R11 | Admin "Create user" form gets the same field styling as login (labelled inputs, reveal toggle, role cards) | **adopt** | User decision: register = admin-issued accounts, no public signup |

## Risks

- **R2 over-subtlety.** A 16% alpha surface can fail contrast against the new darker navy on cheap
  plant monitors. The 3px rail plus white text carries the state independently of the fill.
- **R3 churn.** Section eyebrows must be permission-aware or an operator sees an empty
  `ANALYSIS` heading. Render the eyebrow only when at least one child link survives `can()`.
- **R6 RTL.** The split must use logical properties, not `left`/`right`, or Arabic gets the brand
  panel on the wrong side.
- **R10 trend churn.** Gradient-glow auth screens date fast. Keep it to two tokens deep so a
  re-tone is a variable change.

## Hand-off brief

Implement against `app/static/css/style.css` (tokens first, then the sidebar block),
`app/templates/base.html` (section eyebrows, user card, toggle JS),
`app/templates/auth/login.html` (rewrite as split screen, extending the token file rather than
carrying its own `<style>` block), `app/templates/admin/user_form.html` (R11).

Four sidebar-toggle defects are in scope alongside the visual work — see the session task log.

*Images in this folder are third-party reference only. They never become product assets.*
