import { createApp, nextTick } from 'vue';
import { expect, test, vi } from 'vitest';
import TaxAttributionDetails from '../../src/features/review/TaxAttributionDetails.vue';
import ReviewDetailView from '../../src/features/review/ReviewDetailView.vue';
import { setLocale } from '../../src/core/i18n.ts';
import type { TaxAttribution } from '../../src/features/review/tax-attribution-model.ts';

const attribution: TaxAttribution = {
  attribution_id: 'synthetic-attribution',
  revision: 1,
  historical_correction: true,
  reason: 'Synthetic reviewed service period',
  decision: {
    irpf: { date: '2025-09-22', income_minor: 92000, deductible_minor: 0, withholding_minor: 0 },
    iva: {
      date: '2025-11-10',
      base_minor: null,
      vat_minor: null,
      deductible_minor: null,
      pending_reason: 'Unreviewed future observation',
    },
    fx: [{ event_key: 'receipt-a', date: '2025-01-17', amount_minor: -300 }],
  },
};

test('shows independent dates and pending VAT without presenting a zero or editing source facts', async () => {
  setLocale('en');
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp(TaxAttributionDetails, {
    attribution,
    sourceDate: '2025-10-09',
    entryType: 'income',
  });
  app.mount(root);
  await nextTick();
  expect(root.textContent).toContain('IRPF recognition date');
  expect(root.textContent).toContain('Amount needs confirmation');
  expect(root.textContent).toContain('filed returns are preserved');
  expect(root.querySelector('input,select,button')).toBeNull();
  const vat = Array.from(root.querySelectorAll('dl > div')).find(
    (field) => field.querySelector('dt')?.textContent === 'IVA taxable base',
  );
  expect(vat?.querySelector('dd')?.textContent).toBe('Amount needs confirmation');
  expect(root.textContent).not.toContain('synthetic-attribution');
  app.unmount();
  root.remove();
});

test('posted income shows attribution without opening an editable review workflow', async () => {
  const request = vi.fn(async () => ({
    transaction: {
      transaction_id: 'synthetic-income',
      entry_type: 'income',
      lifecycle_status: 'posted',
      transaction_date: '2025-10-09',
      currency: 'EUR',
    },
    document: { document_id: 'synthetic-source', document_number: 'DEMO-NORTH-41' },
    period: { period_key: '2025-Q4' },
    tax_attribution: attribution,
  }));
  const settled = vi.fn();
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp(ReviewDetailView, {
    context: {
      transactionId: 'synthetic-income',
      period: '2025-Q4',
      returnUrl: '/income?period=2025-Q4',
      knownPeriods: ['2025-Q4'],
      services: { request, navigate: vi.fn() },
      resolved: () => '/income?period=2025-Q4',
      complete: vi.fn(),
      posted: vi.fn(),
      settled,
      notify: vi.fn(),
    },
  });
  app.mount(root);
  for (let i = 0; i < 4; i++) {
    await Promise.resolve();
    await nextTick();
  }
  expect(root.querySelector('[data-testid="tax-attribution"]')).not.toBeNull();
  expect(root.querySelector('form')).toBeNull();
  expect(request).toHaveBeenCalledTimes(1);
  expect(settled).toHaveBeenCalled();
  app.unmount();
  root.remove();
});
