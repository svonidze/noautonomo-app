# Design

## Public read-only demo (NOA-19)

- `/demo/` is a separate static Vue entrypoint. Reuse the product palette,
  typography, table/card treatment, money formatting and `ChartHost` component.
  Overview, Income, Expenses, record details and synthetic documents are fully
  available in English (default), Spanish and Russian. A visible language
  selector remains available on every page, including mobile and detail pages.
  The explicit external `?lang=en|es|ru` query selects the language; missing or
  unknown values mean English. Keep hash routes and filters when switching,
  without browser language detection, cookies or storage.
- Keep “Демо, вымышленные данные” and “Только для чтения” visible in a sticky
  banner. No editors, upload, posting, account settings or filing controls.
  Read-only is enforced through an import boundary and a connection-blocking
  content security policy, not disabled buttons alone.
- All fixture names, identifiers, dates and amounts are invented from scratch.
  Documents are marked invalid teaching examples with deliberately invalid tax
  identifiers and no payment details. Never copy private source documents.
- Use integer cents. Period summaries and the monthly chart count only posted
  rows; unreviewed records remain visible but excluded. Document totals include
  IVA; income basis and deductible expenses exclude it. The net is before tax
  and additional deductions, never a tax calculation or payment assertion.
- Period, search and status filters live in the URL. Record/document links keep
  the originating filter context. Unknown routes show a read-only empty state.
  Use labeled native controls, visible focus, keyboard-operable charts and
  mobile table cards. Test 375px reflow and desktop separately.
- Bundle complete demo translations, including profile, descriptions, documents,
  chart/table/tooltip/dialog labels, metadata and locale-specific money/dates.
  The private app's RU/EN locale registry and defaults remain independent.
  Reuse `ChartHost` with a prepared `ChartSpec` and translated controls; its
  ordinary analytics/locale path remains the default for the private app.

## Source of truth
- Status: Active; refreshed 2026-10-10 (clarity redesign round 2 stages 0–7 implemented, stage 8 pending the owner).
- Surfaces: Expenses, dashboard expense metrics, recent operations, and the analytics charts documented below.
- Evidence: `local_web.py`, the Vue frontend under `frontend/src` (`features/`,
  `shell/`, `charts/`, `help/`), `web_ui/styles.css`, the accepted
  expense-clarity plan in `docs/plans/expense-clarity.md`, the user-supplied
  expense-chart screenshot dated 2026-09-04, and AEAT's 2026 `LSI.xlsx`
  registry-book design.

## Brand
- Calm, readable bookkeeping; preserve the existing green palette and system fonts.
- Trust comes from explicit labels and unchanged source records, not decorative badges.
- Avoid implying payment, tax approval, or automatic posting from a date or lifecycle badge.

## Product goals
- Distinguish purchases from depreciation of an earlier purchase without opening a help tooltip.
- Make source-document date, recognition date, original amount, and quarterly deduction unambiguous.
- Do not change tax calculations, source amounts, posting rules, or generate scheduled entries.

## Personas and jobs
- A self-employed owner reviews recorded expenses and prepares a quarter without accounting jargon.
- Desktop inspection and mobile checking must expose the same essential information.

## Information architecture
- Preserve navigation and routes. Expenses contains two expanded sections: purchases/services/other expenses, then depreciation.
- One search and one paginated result set cover both sections. Whole-quarter reviewed totals are independent of search.

## Design principles
- Recognition is not payment; reviewed is not posted; a future date is not a new purchase.
- For depreciation, the quarterly deduction is primary and original purchase information is secondary.
- Show uncertainty explicitly. Never choose an arbitrary asset from multiple candidates.
- Keep existing ledger/API values intact; introduce separate display aggregates.

## Visual language
- Reuse existing CSS variables, panels, badges, typography, spacing, and focus rings.
- Use text as well as color for status. Do not add external fonts, images, icons, or animation.

## Components
- Reuse tables, document actions, SPA links, metrics, loading/error messages.
- Add expense section headers, recognition notes, whole-quarter totals, and a load-more control.
- Scope responsive expense cards to both expense sections; preserve the existing scrollable recent-operations table and unrelated tables.

## Accessibility
- Persistent explanations, visible search label, keyboard-operable controls, readable focus states.
- Preserve table/row/cell semantics and column labels in the mobile layout.
- Escaped user content; no hover-only information or color-only status.

## Responsive behavior
- Desktop tables; at 700px and below use labelled stacked rows in both expense sections.
- Test at 375px and desktop width, with wrapping names and nonzero VAT.

## Interaction states
- Loading preserves context; failed search/load-more offers retry without presenting stale rows as current results.
- Empty sections distinguish no records, no matches, and records on subsequent pages.
- Reviewed totals say they cover the whole quarter, not just visible matches.
- Disabled load-more prevents duplicate requests. Route/search changes invalidate stale responses.
- Refresh the read-only expense view on foreground return and periodically while visible; server dates determine future markers.

## Content voice
- Plain RU/EN labels; no new accounting abbreviations beyond the existing IRPF/IVA terms.
- Use “Дата учёта”, “Дата документа”, “Амортизация за квартал”, “Проверено, ещё не проведено”.
- A source-book attachment is a source document, not necessarily the original supplier invoice.
- Asset matching inferred from party and document date is labelled as inferred.

## Implementation constraints
- Vanilla JS/CSS and Python/SQLite, no framework or schema migration.
- Only existing `historical_g03` expense transactions are depreciation in this iteration. This code is produced by the importer; asset ownership alone is not a classification rule.
- Preserve existing API fields and tax results. New display sums use integer minor units and retain missing/zero/negative values.
- Test data must be invented, never copied from operational accounting records.

## Open questions
- None for this iteration. Creating new depreciation operations is a separate accounting workflow.

## Counterparty list and detail

- The list's primary action is opening `/contacts/{id}`. Names are real links
  (including new-tab behavior); unmodified clicks on non-interactive row space
  also navigate. Text selection, links and controls keep their normal behavior.
- Keep existing columns, status explanations and the concentration chart.
  Remove persistent rename buttons and manual-correction labels from rows.
  A borderless 44px actions trigger at the right edge opens the single
  additional action, Correct name. The same menu appears in the detail header.
- One menu at a time, outside table overflow; clamp it to the viewport. Escape
  and clicks on noninteractive outside space restore focus to the trigger.
  Clicking another interactive control closes the menu without stealing focus.
- The detail page is read-only: existing identity/contact facts, linked
  transactions and collapsed name history. Do not add monetary totals or new
  accounting actions. Preserve unknown, zero and negative amounts.
- Operations default to all periods, newest first, in batches of 50. The local
  period filter belongs to the card and does not change other screens' period.
  Hide the global period selector on the card; return links preserve the card's
  filter and the list's scroll position. Preserve the originating global
  period across an expense excursion; the expense itself keeps its true period.
- Use the existing read-only expense page and document availability/actions.
  No income detail editor, schema change or import change is part of this work.
- Reuse the existing rename dialog from both surfaces, including direct links
  that have never loaded the list. Saving refreshes the active card or list,
  not another record reached while the request was in flight.
- Navigation uses the rename dialog's dirty/busy policy. A refused Back restores
  the current route and draft; while saving, navigation waits for the result.
- Preserve the palette, fonts, status-help and chart components. Check RU/EN,
  keyboard access, 375px width, long names, bottom-row menus, empty/error states,
  pagination and stale responses during navigation or filter changes.

## Reviewed IVA investment classification

The expense review now separates the IRPF asset decision from the IVA investment
goods decision. This authorized accounting change supersedes the earlier
display-only constraints for this field and its schema/calculation integration.
Keep existing components, routes, source amounts and unrelated design rules.

- Add a labelled, keyboard-accessible RU/EN select beside the expense decision:
  unknown, current purchase for IVA, or investment good for IVA. Unknown remains
  visibly blank and must not become false through truthiness or a default.
- Explain persistently that IRPF depreciation does not determine IVA treatment.
  Approval requires an explicit IVA choice; rejection does not.
- Preserve the selected boolean, including false, across edits, reloads and
  validation. Stale review packets must be reloaded, not silently upgraded.
- Show the saved classification in the read-only expense card. Legacy null
  means unreviewed classification, not an affirmative tax decision.
- Existing rows and snapshots are not automatically reclassified. Legacy
  calculation fallback must be accompanied by a visible report warning.

## Accounting status explanations

This contract also covers the read-only context
from `backend/src/autonomo_taxes/status_context.py` and its presentation in
`frontend/src/help/presentation.ts` (labels, tones, panel content) and
`frontend/src/help/registry.ts` (scoped records, dialog and history). For operator-facing meanings and
actions, see [Understanding accounting statuses](docs/user/ACCOUNTING_STATUSES.md).

- Present the short reason beside the status and make the next action readable
  in the explanation panel without hover. Tooltips define terms only; neither
  a required action nor the only explanation of a blocker belongs in a tooltip.
- Keep review lifecycle, posting readiness and permission to apply a review
  separate. The server posting preview is authoritative: `approved` is not
  `ready`. An approved transaction can be blocked or deferred. A future date
  must not hide other blockers or make the client infer readiness.
- Preserve structured blocker codes and subject references. Translate known
  reasons explicitly in RU and EN; unknown reasons retain an honest fallback.
  Do not infer a tax decision from free text, a missing decision or a generic
  status belonging to another domain.
- Distinguish a known zero, unknown/missing data and an inapplicable value.
  Do not format missing amounts as zero. Posting, schedule inclusion, annual
  evidence and confirmed filing remain distinct facts; the period scopes in
  [Documented deltas](#documented-deltas) apply to the asset table and chart.
- Viewing explanations and copying a question must not mutate accounting data
  or send messages. Only server-supported review links are offered; navigation
  does not grant permission to apply a change. Available local/catalog originals
  may be linked, while unavailable files need explicit recovery guidance.
- Keep the explanation dialog outside the rerendered app container. Escape
  and close restore focus; Back closes the panel before leaving the page.
  A page or period change must not leave stale record details open.

When resolving overlapping UI changes, preserve both status explanations and
analytics. A textual merge without conflicts is not evidence that shared
helpers or mounted components survived. Check the effective loaded scripts,
single helper declarations, combined asset status/chart rendering and stale
render handling using `frontend/tests/unit/help-lifetime.test.ts`,
`backend/tests/test_status_context.py`, and
`frontend/tests/e2e/review-detail.spec.ts`. Keyboard, mobile reflow and browser-native
zoom are separate checks; equivalent-width reflow does not certify 200% zoom.

## Interface states and review navigation

This contract covers the app-wide view states and the review screen's
navigation added by the clarity redesign.

- Views render through distinguishable states: a loading skeleton
  (`uiLoadingSkeleton`), then content, a contextual empty state, or an error
  state. Errors (`errorState`) carry the danger-toned `state-error` look,
  `role="alert"`, and a retry button — or a reload button when the session
  expired. An empty search result is not the same message as an empty
  period; the income register and the review queue say what is empty and
  what to do next.
- Chart slots reserve height while empty (`.chart-slot:empty`) so mounted
  charts do not shift the layout. Skeleton shimmer honors
  `prefers-reduced-motion`.
- The review overview is three tabs — queue, posting, documents — with the
  actionable counts on the labels (needs review / ready to post / open
  issues). The active tab travels in `?tab=`; `queue` is the implied default
  and never appears in the URL; unknown values normalize to queue in the
  current history entry; review and expense detail routes never carry a tab;
  a period switch preserves the active tab. The dashboard task list's
  posting row links to the posting tab.
- The queue's summary cards are filter toggles (`aria-pressed`; «Все»
  resets; component state, not the URL). Each queue row offers one next
  action chosen by its server state: «Провести» to the posting tab at
  `#posting-row-<id>` (ready), «Почему» (deferred), and for a row that still
  needs work (needs_review, received, extracted, blocked, unknown)
  «Открыть проверку» when the server offers its review action, otherwise
  «Что мешает» («Почему» for an unknown state). A row whose action opens
  the explanation shows no second link in its status cell. An unknown state
  counts as unknown, never as blocked. The aging chart draws from ten queued
  rows; below that one line names the oldest row's age.
- The posting tab lists the ready rows before anything is posted, then the
  deferred and blocked ones; the row named by `#posting-row-<id>` is
  highlighted and scrolled into view (a router navigation does not update
  `:target`, so the tab marks and focuses it). Posting rows offer only the
  server's actions (their queue rows'); the client never makes up a review
  link. Archive-transfer counts sit under «Технические детали». A row
  without a EUR amount shows «—» and says so; the posting preview sends
  `null` for it, never `0`.
- The guided review has one status block: the server's status cell (badge
  and one line) beside the title. Alerts below it say why the fields are
  locked and never repeat the status label or the same sentence twice. A
  missing rate is an item in «Что осталось»; «Курс не выбран» appears inline
  only after a confirmation attempt. The reject panel stays folded below
  the main action row.
- When a work item cannot be posted (later or blocked), every decision field
  is disabled and a banner explains why; the reject panel stays usable.
  Confirm and reject show a busy "Submitting" state while a request is in
  flight. Minor-unit amounts are typed in euros and basis-point rates in
  percent, with a comma or a dot; the decision keeps minor units and basis
  points, and a live preview (`minorUnitEurPreview`) shows the stored value: a
  known zero previews as 0.00 €, an empty field stores null and previews
  nothing, and unreadable input keeps the stored value and says so inline.
- The intake dialog keeps a versioned draft in
  `localStorage["autonomo.intake-draft"]` (`{schema: 1, values}`): restored
  on reopen unless a copy-flow prefill wins, cleared by a successful submit,
  discarded on schema mismatch. The document date is capped at today, and an
  expense whose base plus IVA disagrees with the total shows a live,
  non-blocking hint. Server failures report through the dialog status line
  only; toasts announce success. Error toasts persist until closed.

The behavior is pinned by `frontend/tests/unit/core.test.ts` (state helpers),
`frontend/tests/e2e/locale.spec.ts` (locale parity),
`frontend/tests/e2e/review-detail.spec.ts` (review tabs), and
`frontend/tests/e2e/baseline.spec.ts` (navigation).

## Clarity redesign (round 2)

- Status: Active (stages 0–7 implemented; stage 8 pending).
- Screen purpose: an owner without accounting training must see what to do
  now, what each figure means and where to act; an operator keeps dense
  tables, codes and facts on the same screens.
- Hard constraints: routes and URL parameters unchanged; tax semantics,
  amounts and posting rules unchanged; null is never shown as zero; RU/EN
  parity per key; statuses come from the server; strict CSP, no new
  dependencies, no external fonts or icons.
- Selected direction: one page grammar on every screen (title, one-line
  purpose, one primary action, technical detail collapsed) plus a task list
  on the dashboard. Rejected: a one-question-per-page wizard (breaks the
  URL/test contract and removes the facts-beside-source layout operators
  need); keeping the analytics-first dashboard (does not answer "what now").
- References: WCAG 2.2 SC 1.4.3, 1.4.11, 2.5.8; GOV.UK Design System task
  list component (whole-row links, one-sentence hints, status tags, grouped
  lists) and question pages pattern (grouped questions are acceptable for
  internal services; headings name the current step). Transferable: hierarchy
  and status feedback. Not transferable: GOV.UK typography, colours and the
  one-question flow.
- Acceptance: every route and form state (read-only detail pages have none)
  renders exactly one primary action outside open dialogs; RU catalogs
  contain no Latin service words outside the allowed term list; every
  blocking status shows an action or its owner; every empty
  state names the next step; charts render only with at least two non-zero
  points; dashboard first screen at 375 px shows tasks, not charts.
- Precedence: where this section disagrees with an earlier section, this
  section states the target state; the earlier text stays in force until the
  stage that ships the rule rewrites it. Stages 1, 3 and 4 rewrote the older
  text («Документ от» under Content voice, the reserved height of empty chart
  slots), so the note now covers only stage 8.

### Page grammar
- Header: h1 title, one-line purpose, one primary action. Sections start at
  h2, nested at h3; no heading repeats the title; no section without a heading.
- One primary button per screen; secondary and text buttons for the rest.
  Disabled controls explain why in visible text, never only in `title`. A
  `<summary>` styled as the primary button is acceptable when the page's main
  action is to open an inline form (AEAT documents).
- Technical values (AEAT codes, minor units, basis points, identifiers) live
  behind `<details>`; a shown code always follows its human label.

### Status vocabulary
- One catalog (`statuses.*`) supplies every lifecycle, readiness, filing and
  calculation label; see the canonical table in the plan. Synonym keys are
  removed. A status cell is a badge plus at most one line of reason; the
  link to the explanation ends that line only when the record still needs
  work (needs_review, received, extracted, blocked, unknown) and the server
  offers its review («Что сделать» / "What to do"), or when it has reasons
  («Почему» / "Why"). On a ready, deferred or finished record a review link
  is navigation, not a task: the explanation lists it under «Связанные
  действия» / "Related actions" instead of «Следующие действия».
- Unknown, zero and not applicable are worded, not dashed, whenever the
  missing piece has a known cause: "Not calculated yet. Calculate",
  "242.00 € plus 1 operation without a EUR amount".

### Empty states and first run
- Each empty state says what is empty, why, and offers the next step with a
  control. An empty ledger shows a four-step start list on the dashboard
  until the first posting; an empty review queue distinguishes "no documents
  in this quarter" from "everything reviewed".
- The start list («Начало работы» / "Getting started") replaces the task list
  while `/api/dashboard` reports `ledger_has_postings: false` (no posted or
  filed row in any quarter; a payload without the field keeps the task list).
  Its rows: fill in the profile (`/settings`; done when `/api/bootstrap`
  reports `profile_configured`), add a first document (opens the intake
  dialog; done once the quarter has a document or a transaction; disabled
  with the reason when the server does not accept documents), review and
  post it (`/review`; can start once a transaction or a document of the
  quarter waits for review) and calculate the quarter
  (`/taxes`; waits for the first posting, after which the task list offers
  the calculation). Each row has one tag from `statuses.steps.*`: «Сделано»
  (positive), «Можно начать» (pending) or «Не начато» (neutral, waiting for
  an earlier step). The rows are task rows, never primary buttons. The
  sidebar names the owner only when `profile_configured` is true.
- `/api/bootstrap` always offers the current calendar quarter: without a
  row it is listed as `status: "missing"` beside the quarters that have rows
  (no row is created). The default is the current quarter if it has a row,
  else the latest quarter with a row, else the current quarter. A ledger
  straight from `db init` has no quarter: it is offered the current quarter
  and the one before it, where the first document may be dated. The read
  endpoints answer a valid quarter without a row with empty data
  (`period.status: "missing"`, zero counts, empty lists), never 400/404;
  malformed keys still fail. The first document's intake creates the
  quarter's row. Calculation needs that row: until then
  `/api/dashboard/refresh` answers 409 `period_missing` with the text
  `taxes.periodEmpty`, and the posting tab reads the quarter as open with
  nothing to post. Intake refuses a document dated in another quarter than
  the selected one with 400 `quarter_mismatch` and the text
  `intake.quarterMismatch`, which names both quarters.
- An empty review queue reads «В этом квартале нет документов на проверку»
  when the quarter has no document, sends to the documents tab
  (`review.queueEmpty.documentsWaiting`) when documents still wait for
  review without a transaction (intake could not read their amount), and
  reads «Все документы квартала проверены» otherwise.
- `frontend/tests/unit/plain-language.test.ts` checks every RU key matching
  `/\.(empty|none|noRecords|queueEmpty)\b|Empty$/`: the text names the next
  step with an action word («Добавьте», «Откройте», «Нажмите», «Проверьте»,
  «Рассчитайте», «Заполните», «Выберите») or a form of «появиться». «Нет»
  and a second sentence count only when that sentence names the step
  («Открытых вопросов нет. Новых документов тоже.» does not). Chart empties
  (`charts.*`) are exempt: below the data threshold a chart prints one line,
  and the list above it names the next step.

#### Empty states

| Screen | Empty text key | Next step |
|---|---|---|
| Overview, first run | start list `dashboard.start.*` | the four steps: profile, first document, review and post it, calculate the quarter |
| Overview, task list | `dashboard.tasksEmpty`, then `dashboard.calculatedAsOf` | none due now: tasks appear with a document or a filing deadline |
| Overview, recent operations | `dashboard.recentEmpty` | «Добавить документ» in the header |
| Overview, analytics | `charts.*.empty`, `charts.empty.*` (one line) | the task or start list above |
| Income | `transactions.incomeEmpty`; search: `transactions.noMatches` | «Добавить документ»; change the search |
| Expenses | `expense.empty`; depreciation: `expense.amortizationEmpty`; search: `expense.noMatches`; later pages: `expense.moreRecords` | «Добавить документ»; post the equipment schedule on «Активы»; change the search; «Показать ещё» |
| Review, queue | `review.queueEmpty.noDocuments`, `review.queueEmpty.documentsWaiting` or `review.queueEmpty.allReviewed` | «Добавить документ»; the «Документы и вопросы» tab; new documents join the queue |
| Review, posting | `review.postingEmpty` | the «Очередь» tab |
| Review, documents and issues | `review.documentsEmpty`, `issues.none` | added documents and new issues appear there |
| Expense card | `expense.treatmentEmpty` | the review of the expense |
| Assets | `assets.listEmpty`; a cell of an asset without schedule entries this quarter reads `assets.noScheduleCell` (a cell label, not an empty state) | review an expense of the type «Оборудование» |
| Taxes | form card `statuses.labels.calculation_unavailable`; Calculate on a quarter without records: `taxes.periodEmpty`; RETA bases: `reta.basesEmpty` | «Рассчитать»; «Добавить документ» first; the operator records bases from the TGSS resolution |
| Counterparties | `contacts.listEmpty`; a counterparty's period: `contacts.operationsEmpty` | add a first document; choose another period |
| AEAT documents | `aeatDocuments.listEmpty`; filters: `transactions.noMatches` | «Добавить документ AEAT» below the table; change the filters |
| Settings | `settings.form.empty` | fill in and save the profile |

### Charts
- A chart renders only when its dataset has at least two non-zero values
  (each series counts per bucket); otherwise the slot shows one line of text.
  Dashboard charts sit below the task list and quarter figures; on narrow
  screens the data table is primary and the chart is collapsed. The queue
  aging chart renders from ten queued items.

### Narrow screens
- At 760 px and below the sidebar folds into one bar, no taller than 112 px
  at 375 px: the brand mark, the current section and «Меню» / "Menu" (a
  button with `aria-expanded` and `aria-controls="primary-nav"`). The brand
  name, the owner and the links open below the bar; folded, the name and the
  owner stay visually hidden but readable to screen readers. Escape while
  focus is inside the bar, a link or any route change closes the menu, and
  closing from inside the bar returns focus to the button. Routes, ids and
  `data-view` stay as on wide screens; the active link carries
  `aria-current="page"`.
- Every chart opens as its data table; the drawing and its legend wait in a
  closed «Показать график» / "Show chart" below the table. The dialog
  expansion is unchanged.
- Tables and filters stay inside their panels at every width: the AEAT
  filters are `repeat(auto-fit, minmax(min(180px, 100%), 1fr))` inside the
  panel padding, «Modelo» never breaks, and the queue's action column is as
  wide as its one control. Table rows become labelled cards whenever the
  content column is at most 736 px: on a phone, and beside the sidebar up to
  a 1008 px window (a 768 px tablet), where columns broke words letter by
  letter.
- `frontend/tests/e2e/mobile.spec.ts` pins the bar height, the menu by
  keyboard, folded charts and no horizontal scroll on five screens, cards at
  768 px, and the AEAT filters and the queue actions at 1440 px.

### Accessibility tokens
- Control borders at least 3:1 against the surface; focus ring
  `--accent-dark` 2 px; warning text token at least 4.5:1; disabled
  buttons use `cursor: not-allowed`; pointer targets at least 24×24 CSS px
  or spaced per SC 2.5.8.
- Ratios by the WCAG 2.2 relative-luminance formula, against the surface
  `#ffffff` / the page `#f4f6f5`:

  | Token | Value | Used for | Ratio |
  |---|---|---|---|
  | `--control-border` | `#828d89` | input, select and textarea borders | 3.43 / 3.16 (3.04 on `--surface-muted`) |
  | `--focus-ring` | `#0b6b59` (= `--accent-dark`) | every focus outline, 2 px solid, offset 2 px | 6.44 / 5.93 |
  | `--warning-text` | `#8a5110` | warning text (`.fx-card-status.warning`) | 6.43 / 5.93 |
  | `--muted-on-soft` | `#5c6663` | small text on the soft fills: queue filter-card labels, FX choice notes | 4.97 `--accent-soft` / 5.29 `--warning-soft` / 4.86 `--danger-soft` / 5.01 `--info-soft` |

  They replace field borders `#cbd4d0` (1.51:1), the ring `#65bfa8` (2.19:1)
  and warning text in `--warning` (4.23 / 3.90); `--muted` `#66706d` stays
  for small text on the surface and the page but reads 4.19 on
  `--danger-soft` and 4.28 on `--accent-soft`. The plan's example border
  `#8a9691` reaches only 2.82:1 on the page colour.
- Two dark surfaces take their own ring: the sidebar keeps `#6dd2b8`
  (8.21:1 on `--sidebar`; `--focus-ring` is 2.32:1 there), and the toast's
  close button rings in its own white text (13.45:1 on the toast, 7.91:1 on
  the error toast; `--focus-ring` is 2.09 / 1.23 there). The «Меню» button
  border uses `--sidebar-muted` (6.58:1).
- `--warning` stays the expense series colour in charts, a graphic at
  4.23:1 (≥ 3:1), never text. Badge text already passes: pending `#714307`
  on `--warning-soft` 7.45, attention 7.73, positive 7.19, neutral 7.19.
- Pointer targets: `.term-help` 26 px; a text action in a table cell and a
  status «Почему»/«Что сделать» button at least 24 px tall; a link inside a
  sentence (`.inline-button`) uses the inline exception.

## Analytics and charts

This file is the source of truth for measure definitions, status policy, chart
placement, color/pattern semantics, accessibility rules, and empty states used
by the analytics endpoint (`/api/analytics`, built by
`backend/src/autonomo_taxes/analytics_series.py`) and the web charts
(`frontend/src/charts/renderer.js` for geometry, `frontend/src/charts/builders.ts`
for domain mapping and `frontend/src/charts/ChartHost.vue` for lifecycle). When the SPA, generated artifacts, or
a future server-side SVG renderer disagree with each other, this document
decides. Change the definitions here first, then the code.

### Measures

- **Income (IRPF basis)**: `tax_treatments.taxable_base_minor` when present and
  non-zero, otherwise the stored EUR amount. This mirrors
  `calculate_modelo130_rows` (`backend/src/autonomo_taxes/tax_engine.py`), which falls
  back to the gross amount when the taxable base is zero.
- **Deductible expense**: `tax_treatments.deductible_irpf_minor`; missing
  treatment means 0 deductible, and the gross amount is reported separately in
  the expense-structure dataset.
- **Cumulative purchases and IRPF deductions**: `cumulative_expenses` sums
  stored EUR purchase amounts once, including equipment acquisitions. Native
  depreciation recognition transactions and `historical_g03` rows contribute
  only their reviewed IRPF deduction, never another purchase amount. Use the
  expense-register classification (`recognition_transaction_id` or
  `historical_g03`). Purchases use the accounting transaction date and do not
  imply payment evidence. Deductions from equipment bought in an earlier year
  can exceed purchases in the selected year; the difference is not a measure
  of non-deductible expenses. Signed corrections retain their signs.
  Four lines show purchases/deductions, actual/projected, using the existing
  analytics lifecycle policy. Missing-FX expense rows are excluded from both
  measures and counted in this dataset; display the warning even when the
  known totals are zero or other valid rows keep the chart non-empty.
- **Business net ("net before difficult-to-justify expenses")**: income (IRPF
  basis) minus deductible expenses. No difficult-expenses provision, no rates.
- **Tax cash due**: positive payables per form — Modelo 130 casilla `19`,
  Modelo 303 casilla `71` — never merged into a single "tax burden" number,
  and no effective-rate line (a VAT settlement is not an income-tax rate).
- **RETA bracket check**: separate decision support. Its amounts are never
  added to or netted against tax cash due, the Taxes-page headline, or any
  combined burden. It runs the TGSS annual regularisation (RD 2064/1995
  art. 44.2 and 46.2) on 1 January to a month-end `through`, without
  projection, against `reference/reta/<year>.json`;
  `backend/src/autonomo_taxes/reta.py` implements it with exact arithmetic
  and rounds for display only.
  - Regularisable days: alta days less tarifa plana days (Ley 20/2007
    art. 38 ter; regla 1.ª).
  - Monthly income: IRPF business net from posted rows only, plus the
    owner's contributions added back, less the generic deduction (7 %; 3 %
    only for LGSS art. 305.2.b/e workers), × 30 ÷ regularisable natural
    days (RD 2064/1995 art. 44.2.a, regla 2.ª). The full income stays in the numerator. The tramo is looked
    up on the exact value.
  - Bases compare as totals (LGSS art. 308.1.c reglas 3.ª–4.ª): the
    provisional bases of the window against the tramo minimum and maximum
    times the month weights. A full month weighs 1 at its monthly base; a partial
    month weighs d/30 and pays d/30 of its base (RD 2064/1995 art. 47.1, the
    monthly amount "se dividirá por treinta en todo caso"), with the limits
    prorated alike (regla 5.ª.d). Inside the limits it is `ok`; below the
    minimum `below_bracket`; above the maximum `above_bracket`. The estimate
    is the rate applied to the sum of the monthly differences to that
    minimum or maximum, positive and negative netted (regla 5.ª b/c), shown
    as `estimated_additional_minor` or `estimated_refund_minor`. Per-month
    differences are detail only.
  - `additional_locked_in_minor`: the netted shortfall against the tramo
    minimum from January to the month before `next_base_change.effective_on`
    (months a request made on `as_of` cannot reach, RD 2064/1995 art. 45.1),
    assuming the minimum afterwards; never negative.
  - `boundary_sensitive`: the monthly income is within 10 % of the tramo
    width from one of its bounds (open tramos use the neighbouring width).
  - `unknown` with reason codes and `null` estimates, never `ok`: missing
    bases, unposted rows or missing periods in the window, a missing table,
    no regularisable days, bases below the table minimum (Orden art. 18.6 and
    18.11 special bases) or above its maximum, and societarios or
    familiares colaboradores, whose grupo 7 floor (art. 44.3.b) and, for
    305.2.b/e, entity income (LGSS art. 308.1.c regla 1.ª) the ledger does
    not hold. Ledger-side reasons also null the income, tramo and
    sensitivity, and so does the societario kind, whose income is incomplete
    without the entity part.
  - `GET /api/reta-check?year=YYYY[&through=YYYY-MM-DD]` is read-only: the
    check as of today plus the live bases (`elections`, without source hash
    or profile id). Every status is HTTP 200; a check that cannot run at all
    is `unknown` with `window_empty`, `profile_unavailable` and/or
    `table_unavailable`. The Taxes page shows it as its own section after the
    AEAT forms, outside the headline, with the estimates as separate values
    and the bases read-only (they are entered with `reta base add`).
- **Taxes-page headline**: the large amount is the cash expected to leave for
  AEAT in the selected quarter, with IRPF and IVA kept as separate components
  immediately below it. A negative IVA result never offsets positive IRPF in
  this headline. For a current quarter the headline is explicitly a dated
  preview; for a past quarter it says `paid` only for evidence-backed EUR
  payments linked to the filed obligation. Filed, payable and confirmed-paid
  amounts remain separate values.
- **Money encoding**: every API money field ends in `_minor` and is an integer
  of EUR cents or `null`. `0` means a known zero; `null` means
  unavailable/not calculated. Rates use basis points. The API returns machine
  keys only; the client owns translation and formatting.

### Lifecycle status policy

The analytics `policy` block is authoritative; `analytics_series.py` implements
it. `date` below is `transactions.transaction_date` compared with `as_of`.

| Series | Statuses | Date rule |
|---|---|---|
| `actual` | `posted`, `included_in_snapshot` | date ≤ as_of |
| (quality only) `future_posted` | `posted`, `included_in_snapshot` | date > as_of — data-quality anomaly, excluded from datasets |
| `approved_unposted` (backlog) | `approved` | date ≤ as_of |
| `approved_future` (forecast) | `approved` | date > as_of |
| review queue (counts only) | `received`, `extracted`, `needs_review` | any — never in money/tax projections |
| excluded | `duplicate`, `rejected`, `void` | any |

Additional rules:

- EUR amounts come from `amount_eur_minor`, falling back to `amount_minor`
  only when `currency = 'EUR'`. A foreign-currency row without FX is excluded
  from money datasets and counted in `quality.missing_fx_transaction_count`.
- `tax_treatments` may hold several rows per transaction; they are collapsed
  into one per-transaction projection first. Conflicting values exclude the
  transaction and increment `quality.conflicting_treatment_count` (unlike
  `tax_row_loader`, analytics must not raise on user data).
- Signed corrections sum through; the API never inverts expense signs.
- Periods: open quarters report actuals and previews separately; closed
  quarters prefer filed snapshot values; amended quarters keep the original
  filed result and annotate `amendment_period_key` — history is not restated.
  A quarter absent from the `periods` table reports `status: "missing"`.
- `GET /api/analytics` is strictly read-only: it opens SQLite in `mode=ro` and
  never launches CLI subprocesses or refreshes caches.
- `GET /api/taxes` adds `period_state` and `tax_summary` without removing the
  legacy form fields. `period_state.phase` is calendar-derived (`future`,
  `current`, `past`) and stays separate from the ledger period status
  (`open`, `closed`, `amended`). Each supported form exposes preview, filed,
  payable and confirmed-paid minor-unit amounts, plus outstanding/overpaid and
  IVA carry-forward/refund disposition where applicable.
- A future quarter has no payable total (`null`), not a known zero. Missing or
  unsupported required calculations also make the total unavailable. Partial
  payment and overpayment are explicit states. A direct-debit cutoff is only a
  deadline; the UI must not infer that a debit is scheduled without explicit
  payment-method evidence.
- Modelo 303 distinguishes credit generated in the quarter (casilla `72`),
  total credit carried forward, and a requested refund (casilla `73`). These
  are explanatory amounts and do not increase the cash-payment headline.

### Chart placement

| View | Charts |
|---|---|
| Dashboard (in «Аналитика» after the task list, quarter figures and recent operations; folded on phones) | monthly income vs deductible expenses; quarterly tax cash due; IVA position (27/45/71 + disposition); tax reserve bullet; cumulative business net; cumulative purchases and IRPF deductions immediately after business net |
| Taxes | year-over-year YTD comparison (decision support, never final Renta) |
| Expenses | expense structure by AEAT concept (deductible / non-deductible / unclassified) |
| Review | queue aging by age bucket and status |
| Contacts | top customers with an "Other" fold |
| Assets | amortization per quarter (`include_in_books = 1` only) |

Rendering is gated by the threshold policy in [Clarity redesign (round 2)](#clarity-redesign-round-2): a chart draws only when its dataset has at least two non-zero values; the queue aging chart draws from ten queued items; below the threshold the slot shows one line of text. Dashboard charts sit below the task list and quarter figures.

### Chart sizing and expansion

A chart is drawn at the width it actually occupies, never scaled to fit.
The mount site measures its host, subtracts the figure padding, and passes
that width into the scene, so one viewBox unit equals one CSS pixel and axis
and value text stay at a plain 12px in every slot. Requested widths are
clamped to 280–1600; when no measurement is available (a hidden tab, a
non-browser test harness) the scene keeps the 640-unit default. A window
resize or a return to the tab re-measures live charts and redraws only those
whose width changed.

Narrow charts thin their bucket labels deterministically — every Nth label
when a band falls under 34px — and the horizontal-bar label column shrinks
from 150 toward 90 below roughly 500px. Thinning removes labels only; every
value stays in the data table and in the mark titles. Quarterly charts have
too few buckets to thin; their buckets use the short period form («III кв. 2026»,
"Q3 2026") so four quarters fit a half-width chart. A chart drawn narrower than 400
units (a phone, or any narrow slot) names the year once, in the quarter
column header («Квартал, 2026»), and reads «III кв.» / "Q3" under each bar
and in its table.

Each non-empty chart carries one expand control in its caption. It re-renders
the same spec in the shared `#chart-dialog` at dialog width — no nested
expansion, no second copy of a slot id. The dialog closes on any view
re-render, returns focus to the control that opened it, and pauses the
periodic refresh while open. Empty charts show no control, and the control is
hidden in print. No animation accompanies the expansion.

The expense-structure chart uses plain-language category names from the
versioned [2026 AEAT registry-book list](https://sede.agenciatributaria.gob.es/static_files/AEAT/LSI.xlsx),
with the original G-code as a secondary label for audit and export
cross-checking. Unknown future codes remain visible
beside an honest "unknown AEAT category" label; an absent code is "category not
assigned". The table is initially expanded, adds the gross total, and shows
each deductible/non-deductible amount with its share of a positive gross total.
Shares are unavailable for zero or negative totals, negative components, or a
component sum that does not equal the gross total. On narrow screens and at
browser zoom that triggers the same breakpoint, the labelled table is the
primary view and the plot folds behind «Показать график», as for every chart
(see Narrow screens). Every table cell retains its column label when rows
reflow into cards.

### Color and pattern semantics

Chart series colors bind to the existing CSS custom properties via
`chart-tone-*` classes; meaning is never carried by color alone.

- Income → `--info`; ordinary expense series → `--warning`; primary result
  lines and current-year values → `--accent`; `--danger` is reserved for alert
  states (overdue approved rows), never an ordinary series; `--muted` is for
  annotations and bands only. The expense-structure breakdown is the explicit
  exception: the portion that reduces the IRPF base is solid `--accent`, while
  the portion that does not is hatched `--warning`.
- Actual = solid fill. Approved-not-posted = hatched fill (SVG `<pattern>`
  keyed by chart id). Forecast = outlined fill. Projected lines = dashed.
- Validated with the color-vision checks: the pair `--info`/`--warning` and
  the chain `info → warning → accent → danger` pass CVD separation on the
  light surface. **Do not place a `--muted` mark adjacent to an `--accent`
  mark** — that pair measured ΔE 1.8 under deuteranopia and is illegible.
- Legends appear for two or more series; single-series charts rely on the
  caption. Value text uses text tokens, not series colors.

### Chart accessibility

Every chart is a `<figure>` with a visible `<figcaption>`, an SVG with
`role="img"` and an `aria-label`, and a `<details>` data table with the same
numbers, so the content survives screen readers, printing, and forced colors.
Chart data cells carry their column header through `data-label` so the mobile
card layout does not turn amounts into unidentified numbers.
Marks carry native `<title>` tooltips. The renderer builds DOM exclusively via
`createElementNS`/`textContent` with an attribute allowlist: no inline styles,
no inline handlers, no `innerHTML`, no `Date.now`/`Math.random` (scene output
is deterministic for a given spec).

### Chart empty states

Distinguish, in this order: analytics fetch failed → "could not load"; some
transactions lack FX → missing-FX message; only unreviewed rows exist →
unreviewed message; forms filed without extractable values → filed-without-
values message; calculation blocked (reserve) → blocked message; otherwise →
plain no-data message. Below the data threshold the slot shows one line instead
of a figure: without a non-zero value, the chart title and this message (all-zero
data included; the cumulative-purchases slot names missing exchange rates first);
with exactly one, that the chart needs a second value (`charts.singleValue`).

### Documented deltas

- Expense-list and metric depreciation amounts are quarterly deductions, while
  the expense-structure chart retains the analytics dataset's source gross
  amounts. A visible note explains that a depreciation row's original purchase
  cost is not a new charge. Dashboard refresh retains expanded chart tables,
  their keyboard focus and scroll position.
- The assets table scopes schedule amounts to the selected quarter and shows
  book-included and excluded rows separately. Annual evidence is displayed
  separately in the explanation panel and is never added to quarterly totals.
  The amortization chart shows the year's quarters using only
  `include_in_books = 1` schedule/adjustment rows, excluding annual evidence;
  its note explains why its scope differs from the table.
- `backend/src/autonomo_taxes/amortization_chain.py` is a CSV audit tool for Xolo
  artifacts and does not back the web analytics; the chart reads
  `amortization_entries` directly.
- The tax reserve bullet reports `not_checked` when no explicit available-cash
  figure exists (`cash_check.py` receives `available_eur=None`); available
  cash is never inferred from payment rows.
- Chart geometry lives in `frontend/src/charts/renderer.js`, the only JavaScript
  module of the frontend, typed through `renderer.d.ts` and `types.ts`.
  `createChartBuilders` in `charts/builders.ts` adapts analytics data to chart
  specs and `ChartHost.vue` owns fetching, locale redraws, resize and the expand
  dialog. Contextual explanations live in `frontend/src/help/`. The former
  one-declaration guard for `app.js` helpers retired with the monolith; its
  disposition is recorded in `tests/contracts/ui-python-test-map.json`.

## Expense and equipment workflow

The approved expense workflow extends the earlier display-only scope. Routine
bookkeeping uses the existing authenticated service, never sudo/SSH, deployments,
service stops or full-root backups. Keep the current palette, components and RU/EN voice.

- Unposted expenses offer a durable server draft: original alongside editable
  facts, supplier/activity selection, business purpose and reviewed tax treatment.
- Ordinary expense and equipment branches share one final preview and explicit
  Confirm and post action. Only the selected purchase and immediately eligible
  depreciation are posted. Saving a changed approved draft invalidates approval
  visibly and preserves its audit trail. Posted/closed records remain read-only.
- Equipment offers immediate low-value or calendar-day linear depreciation;
  reviewed parameters and a versioned schedule are persisted. Plans are not actuals.
  Future periods require a later explicit action from the asset page.
- Native schedule-to-transaction links identify depreciation; do not infer new
  journal types from descriptions or pretend they are historical imports.
- The final summary distinguishes purchase amount, IVA, current IRPF deduction
  and later depreciation. Business use and IVA investment status stay separate.
- Errors preserve the draft. Repeated requests return the saved outcome;
  post-commit refresh/cleanup failures offer repair without reposting.
- Reuse label/focus/error patterns at desktop and 375px width. The original must
  remain accessible if embedding fails. One expense or asset per workflow.
- The form reads as three in-page steps under a strip of anchors («1 Документ ·
  2 Налоги · 3 Итог»; no new routes). «Что осталось» above them lists the
  source's open questions, a missing IVA or EUR rate and the rule's missing
  facts, each a link that focuses the control settling it (opening a fold on
  the way). By default at most 14 controls show: a matched supplier or
  activity and the expense type are text with «Изменить», the currency and
  booking date fold while they are EUR and the transaction date, and the rule
  folds until one is chosen. The IVA rate stays visible beside the invoice
  IVA, because the server requires it on every preview. The three dates carry
  one-line hints; the correction reason appears only for an approved record
  or a corrected source fact, the cases in which the server asks for it, and
  while it holds text. After «Проверить итог» the result scrolls into view
  and takes focus, and «Подтвердить и провести» becomes the one primary
  action. Without a previewable original (PDF, JPEG, PNG) the source is one
  compact line with «Открыть документ».
