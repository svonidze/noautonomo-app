import { createApp, h } from 'vue';
import { expect, test, vi } from 'vitest';
import AnnualAdjustments from '../../src/features/overview/AnnualAdjustments.vue';
import { setLocale } from '../../src/core/i18n.ts';

test('annual contributions distinguish provisional income and exclude withdrawn rows', async () => {
  setLocale('en');
  const root = document.createElement('div');
  document.body.append(root);
  const request = vi.fn(async () => ({
    status: 'provisional',
    annual_adjustments: [
      {
        annual_adjustment_id: 'synthetic-active',
        adjustment_kind: 'income',
        recognized_on: '2026-06-06',
        amount_minor: 1234,
        currency: 'EUR',
        counterparty_name: 'Synthetic contributor',
        evidence_status: 'provisional',
        document_id: 'synthetic-document',
        voided: 0,
      },
      {
        annual_adjustment_id: 'synthetic-withdrawn',
        adjustment_kind: 'income',
        amount_minor: 99000,
        currency: 'EUR',
        counterparty_name: 'Withdrawn contribution',
        voided: 1,
      },
    ],
  }));
  const app = createApp({
    render: () => h(AnnualAdjustments, { year: 2026, services: { request, navigate: vi.fn() } }),
  });
  app.mount(root);
  try {
    await vi.waitFor(() => expect(root.textContent).toContain('Synthetic contributor'));
    expect(root.textContent).toContain('Awaiting earnings evidence');
    expect(root.textContent).toContain('Includes provisional annual amounts');
    expect(root.textContent).not.toContain('Withdrawn contribution');
    expect(root.querySelector('a')?.getAttribute('href')).toBe(
      '/api/document/synthetic-document/content',
    );
    expect(request).toHaveBeenCalledWith('/api/annual-adjustments?year=2026');
  } finally {
    app.unmount();
    root.remove();
    setLocale('ru');
  }
});
