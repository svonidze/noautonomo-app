import { expect, test, vi } from 'vitest';
import { createAccountingHelp } from '../../src/help/registry.ts';

test('help source documents and review links use the injected public destination', () => {
  document.body.innerHTML =
    '<button id="opener"></button><dialog id="status-help-dialog"><h2 id="status-help-title"></h2><button id="status-help-close"></button><div id="status-help-body"></div></dialog>';
  const dialog = document.querySelector('dialog')!;
  dialog.showModal = () => {
    dialog.open = true;
  };
  dialog.close = () => {
    dialog.open = false;
  };
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  const help = createAccountingHelp();
  let language = 'en';
  help.setHistoryAdapter({
    push: vi.fn(),
    back: vi.fn(),
    clear: vi.fn(),
    navigate: vi.fn(),
    href: (url) => `/demo/?lang=${language}#${url}`,
    documentUrl: (id) => `/demo/api/document/${id}/content`,
  });
  const id = '11111111-1111-4111-8111-111111111111';
  const scope = help.createScope({
    domain: 'transaction',
    state: 'needs_review',
    actions: [{ kind: 'review', transaction_id: id, period: '2026-Q3' }],
    documents: [{ document_id: id, document_number: 'SAMPLE', available: true }],
  });
  const opener = document.getElementById('opener')!;
  opener.dataset.statusHelp = scope.id;
  opener.click();
  expect(dialog.open).toBe(true);
  expect(document.querySelector('[data-help-review]')?.getAttribute('href')).toBe(
    `/demo/?lang=en#/review/${id}?period=2026-Q3`,
  );
  expect(document.querySelector('.status-sources a')?.getAttribute('href')).toBe(
    `/demo/api/document/${id}/content`,
  );
  language = 'es';
  dialog.close();
  help.setLocale('es');
  opener.click();
  expect(document.querySelector('.status-sources a')?.getAttribute('href')).toBe(
    `/demo/api/document/${id}/content`,
  );
  scope.dispose();
  help.dispose();
  document.body.innerHTML = '';
});
