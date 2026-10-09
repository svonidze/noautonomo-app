import { nextTick } from 'vue';
import { afterEach, expect, test, vi } from 'vitest';
import { mountView } from '../support/mount.ts';
import ReviewDetailView from '../../src/features/review/ReviewDetailView.vue';
import type { ReviewDetailContext } from '../../src/features/review/guided-model.ts';
import type { WorkflowDraft } from '../../src/features/review/workflow-model.ts';
import { REVIEW_ID, nativeFixture, ruleFixture } from '../fixtures/review.ts';
import workflow from '../../src/locales/ru/workflow.json' with { type: 'json' };

const SUPPLIER = '33333333-3333-4333-8333-333333333333';
const ACTIVITY = '44444444-4444-4444-8444-444444444444';
async function flush() {
  for (let count = 0; count < 8; count++) {
    await Promise.resolve();
    await nextTick();
  }
}
afterEach(() => {
  document.body.replaceChildren();
});
/** The default state: EUR, the source's supplier matched, one activity, an ordinary expense and
 * no rule chosen. */
function defaultDraft(rules: boolean): WorkflowDraft {
  const draft = nativeFixture();
  for (const values of [draft.payload, draft.source_values])
    values.facts.counterparty_id = SUPPLIER;
  draft.payload.facts.business_activity_id = ACTIVITY;
  draft.payload.decision.tax_treatment.rate_basis_points = 2100;
  draft.activities = [{ business_activity_id: ACTIVITY, description: 'Synthetic activity' }];
  if (rules) draft.deduction_rules = ruleFixture().deduction_rules;
  return draft;
}
async function mount(draft: WorkflowDraft) {
  const request = vi.fn(async (url: string) => {
    if (url.startsWith('/api/transactions/'))
      return {
        transaction: {
          transaction_id: REVIEW_ID,
          entry_type: 'expense',
          lifecycle_status: 'needs_review',
        },
        period: { period_key: '2026-Q3' },
      };
    if (url === '/api/counterparties')
      return [
        { counterparty_id: SUPPLIER, display_name: 'Synthetic supplier', tax_id: 'B00000000' },
      ];
    return draft;
  });
  const context: ReviewDetailContext = {
    transactionId: REVIEW_ID,
    period: '2026-Q3',
    returnUrl: '/review?period=2026-Q3',
    knownPeriods: ['2026-Q3'],
    services: { request, navigate: vi.fn() },
    resolved: () => '/review?period=2026-Q3',
    complete: vi.fn(),
    posted: vi.fn(),
    settled: vi.fn(),
    notify: vi.fn(),
  };
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, ReviewDetailView, context);
  await flush();
  return { root, host };
}
/** Controls a reader meets without opening anything: none inside a closed <details>. */
const visibleControls = (root: HTMLElement) =>
  [...root.querySelectorAll<HTMLElement>('input, select, textarea')]
    .filter((control) => !control.closest('details:not([open])'))
    .map((control) => control.dataset.p || control.id || control.tagName);

test('the expense review shows at most 14 controls by default and three numbered steps', async () => {
  for (const rules of [false, true]) {
    const { root, host } = await mount(defaultDraft(rules));
    const controls = visibleControls(root);
    expect(controls.length, controls.join(', ')).toBeLessThanOrEqual(14);
    expect(controls).not.toContain('wf-supplier-search');
    expect(controls).not.toContain('change_reason');
    const steps = [...root.querySelectorAll<HTMLAnchorElement>('nav.wf-steps a')];
    expect(steps.map((link) => link.getAttribute('href'))).toEqual([
      '#wf-document',
      '#wf-taxes',
      '#wf-preview',
    ]);
    for (const link of steps) expect(root.querySelector(link.getAttribute('href')!)).not.toBeNull();
    host.dispose();
    document.body.replaceChildren();
  }
});

test('«Изменить» opens a matched supplier or activity, and usual facts fold until they differ', async () => {
  const { root, host } = await mount(defaultDraft(true));
  const chosen = [...root.querySelectorAll('.wf-chosen')].map((line) => line.textContent);
  expect(chosen[0]).toContain('Synthetic supplier · B00000000');
  expect(chosen[1]).toContain('Synthetic activity');
  const supplier = root.querySelector<HTMLButtonElement>('.wf-chosen button')!;
  expect([supplier.textContent!.trim(), supplier.getAttribute('aria-label')]).toEqual([
    workflow['workflow.change'],
    workflow['workflow.changeSupplier'],
  ]);
  supplier.click();
  await flush();
  expect(document.activeElement?.id).toBe('wf-supplier-search');
  expect(root.querySelector('#wf-new-supplier')).not.toBeNull();
  root.querySelector<HTMLButtonElement>('.wf-chosen button')!.click();
  await flush();
  expect(document.activeElement?.id).toBe('wf-facts-business_activity_id');
  expect(root.querySelectorAll('.wf-chosen')).toHaveLength(0);
  const more = root.querySelector<HTMLDetailsElement>('details.wf-more')!;
  expect(more.open).toBe(false);
  expect(more.querySelector('summary')!.textContent).toContain('EUR');
  const date = root.querySelector<HTMLInputElement>('[data-p="facts.transaction_date"]')!;
  date.value = '2026-07-03';
  date.dispatchEvent(new Event('input', { bubbles: true }));
  await flush();
  expect(more.open).toBe(true);
  expect(root.querySelector<HTMLDetailsElement>('#wf-rule')!.open).toBe(false);
  host.dispose();
});

test('the three dates explain themselves in one line each', async () => {
  const { root, host } = await mount(defaultDraft(false));
  for (const [path, hint] of [
    ['facts.issued_on', 'workflow.hint.issueDate'],
    ['facts.transaction_date', 'workflow.hint.transactionDate'],
    ['facts.booking_date', 'workflow.hint.bookingDate'],
  ] as const) {
    const input = root.querySelector<HTMLInputElement>(`[data-p="${path}"]`)!;
    const described = document.getElementById(input.getAttribute('aria-describedby')!)!;
    expect([described.className, described.textContent]).toEqual(['wf-hint', workflow[hint]]);
    expect(
      document.getElementById(input.getAttribute('aria-labelledby')!)!.textContent,
    ).not.toContain(workflow[hint]);
  }
  host.dispose();
});

test('the correction reason appears for an approved record or a corrected fact only', async () => {
  const cases: [string, (draft: WorkflowDraft) => void, boolean][] = [
    ['first review', () => {}, false],
    ['approved', (draft) => (draft.source.transaction = { lifecycle_status: 'approved' }), true],
    ['corrected number', (draft) => (draft.payload.facts.document_number = 'SYN-2'), true],
    ['saved reason', (draft) => (draft.payload.change_reason = 'Synthetic correction'), true],
  ];
  for (const [name, change, shown] of cases) {
    const draft = defaultDraft(false);
    change(draft);
    const { root, host } = await mount(draft);
    expect(!!root.querySelector('[data-p="change_reason"]'), name).toBe(shown);
    host.dispose();
    document.body.replaceChildren();
  }
  const { root, host } = await mount(defaultDraft(false));
  const number = root.querySelector<HTMLInputElement>('[data-p="facts.document_number"]')!;
  number.value = 'SYN-2';
  number.dispatchEvent(new Event('input', { bubbles: true }));
  await flush();
  expect(root.querySelector('[data-p="change_reason"]')).not.toBeNull();
  host.dispose();
});

test('a missing IVA rate is named in «Что осталось» and its link opens the folded field', async () => {
  const draft = defaultDraft(false);
  draft.payload.decision.tax_treatment.rate_basis_points = null;
  const { root, host } = await mount(draft);
  const link = [...root.querySelectorAll<HTMLAnchorElement>('.wf-remaining a')].find(
    (item) => item.textContent === workflow['workflow.remaining.ivaRate'],
  )!;
  expect(link.getAttribute('href')).toBe('#wf-decision-tax_treatment-rate_basis_points');
  const details = document
    .getElementById('wf-decision-tax_treatment-rate_basis_points')!
    .closest('details')!;
  expect(details.open).toBe(false);
  link.click();
  await flush();
  expect(details.open).toBe(true);
  expect(document.activeElement?.id).toBe('wf-decision-tax_treatment-rate_basis_points');
  host.dispose();
});
