import { expect, test, vi } from 'vitest';
import { createAccountingHelp } from '../../src/help/registry.ts';
import { createPresentation } from '../../src/help/presentation.ts';
import { formatDateText } from '../../src/core/format.ts';
for (const locale of ['ru', 'en'] as const)
  test(`help presents amounts, blockers and hostile text safely in ${locale}`, () => {
    const help = createPresentation(locale);
    expect(help.money(0)).not.toBe(help.word('missing'));
    expect(help.money(null)).toBe(help.word('missing'));
    expect(help.money('')).toBe(help.word('missing'));
    const context = {
      domain: 'asset',
      state: 'needs_review',
      reasons: [{ code: 'advance_documents_overlap' }, { code: 'iva_prior_deduction_unconfirmed' }],
      source_message: '<img src=x onerror="alert(1)">',
    };
    expect(help.label(context)).toBe(help.text('help.inline.awaitingAdvanceAndVatReview'));
    expect(help.tone(context)).toBe('attention');
    expect(help.tone({ domain: 'transaction', state: 'posted' })).toBe('positive');
    expect(help.content(context)).toContain('&lt;img');
    expect(help.content(context)).not.toContain('<img');
    expect(help.reasonInfo({ code: 'unknown' })).toHaveLength(3);
  });
for (const locale of ['ru', 'en'] as const)
  test(`the status link names what the explanation offers, and its dates read as elsewhere, in ${locale}`, () => {
    const help = createPresentation(locale);
    // Healthy records have nothing to explain: no link.
    expect(help.link({ domain: 'transaction', state: 'posted' })).toBe('');
    expect(help.link({ domain: 'obligation', state: 'filed' })).toBe('');
    expect(help.link({ domain: 'counterparty', state: 'registered' })).toBe('');
    // A reviewed row without a reason has no reason line either.
    expect(help.summary({ domain: 'transaction', state: 'ready' })).toBe('');
    const blocked = {
      domain: 'transaction',
      state: 'blocked',
      posting: { preview_bucket: 'blocked', blockers: [{ code: 'fx_missing' }] },
    };
    expect(help.link(blocked)).toBe(help.word('why'));
    // Reasons without an action: the panel says who takes the next step.
    expect(help.content(blocked)).toContain(help.word('noAction'));
    const id = '11111111-1111-4111-8111-111111111111';
    expect(help.link({ ...blocked, actions: [{ kind: 'review', transaction_id: id }] })).toBe(
      help.word('whatToDo'),
    );
    // An action the panel would not offer does not count.
    const odd = [{ kind: 'review', transaction_id: 'not-an-id' }];
    expect(help.link({ domain: 'transaction', state: 'ready', actions: odd })).toBe('');
    const deferred = {
      domain: 'transaction',
      state: 'deferred',
      posting: { preview_bucket: 'deferred', posting_deferred_until: '2026-11-02' },
      facts: { transaction_date: '2026-11-02' },
    };
    const date = formatDateText('2026-11-02', locale);
    expect(help.summary(deferred)).toContain(date);
    expect(help.content(deferred)).toContain(`<dd>${date}</dd>`);
    expect(help.content({ ...deferred, facts: { transaction_date: null } })).toContain(
      `<dd>${help.word('missing')}</dd>`,
    );
  });
test('scoped help survives history and locale changes, and releases disposed records', async () => {
  document.body.innerHTML =
    '<button id="opener">Open</button><dialog id="status-help-dialog"><h2 id="status-help-title"></h2><div id="status-help-body"></div><button id="status-help-close"></button></dialog><div id="accounting-tooltip" hidden></div>';
  const dialog = document.querySelector('dialog')!,
    button = document.querySelector<HTMLButtonElement>('#opener')!;
  dialog.showModal = () => {
    dialog.open = true;
  };
  dialog.close = () => {
    dialog.open = false;
  };
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  const helper = createAccountingHelp();
  let id: string | undefined;
  const state = (value?: string) => history.replaceState({ accountingHelp: value }, '');
  helper.setHistoryAdapter({
    push(value) {
      id = value;
      state(value);
    },
    back() {
      state();
      helper.handlePopState();
    },
    clear() {
      state();
    },
    navigate: vi.fn(),
  });
  const scope = helper.createScope({
    domain: 'asset',
    state: 'needs_review',
    reasons: [{ code: 'advance_documents_overlap' }],
  });
  button.dataset.statusHelp = scope.id;
  button.click();
  expect(dialog.open).toBe(true);
  const clipboard = vi.fn(async () => {});
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: clipboard },
  });
  helper.setLocale('en');
  document.querySelector<HTMLButtonElement>('[data-copy-question]')!.click();
  await Promise.resolve();
  expect(clipboard).toHaveBeenCalledTimes(1);
  expect(document.querySelector('.copy-feedback')!.textContent).toBe(helper.word('copied'));
  clipboard.mockRejectedValueOnce(new Error('Denied'));
  document.querySelector<HTMLButtonElement>('[data-copy-question]')!.click();
  await Promise.resolve();
  expect(document.querySelector('.copy-feedback')!.textContent).not.toBe(helper.word('copied'));
  state();
  helper.handlePopState();
  expect(dialog.open).toBe(false);
  expect(document.activeElement).toBe(button);
  state(id);
  helper.handlePopState();
  expect(dialog.open).toBe(true);
  const sibling = helper.createScope({ domain: 'transaction', state: 'ready' });
  scope.dispose();
  scope.dispose();
  expect(dialog.open).toBe(false);
  state(id);
  expect(helper.handlePopState()).toBe(false);
  sibling.update({ domain: 'transaction', state: 'posted' });
  button.dataset.statusHelp = sibling.id;
  button.click();
  expect(dialog.open).toBe(true);
  helper.dispose();
  expect(dialog.open).toBe(false);
  document.body.innerHTML = '';
  state();
});
