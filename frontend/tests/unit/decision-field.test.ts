import { createApp, h, nextTick, ref } from 'vue';
import { afterEach, expect, test } from 'vitest';
import DecisionField, { type DecisionFieldSpec } from '../../src/features/review/DecisionField.vue';
import { setLocale, t } from '../../src/core/i18n.ts';

const money: DecisionFieldSpec = {
  path: 'tax_treatment.deductible_irpf_minor',
  label: 'fields.deductibleIrpfMinor',
  type: 'number',
  valueType: 'integer',
  minor: true,
};
const rate: DecisionFieldSpec = {
  path: 'tax_treatment.rate_basis_points',
  label: 'fields.rateBasisPoints',
  type: 'number',
  valueType: 'integer',
  basisPoints: true,
};
const mounted: (() => void)[] = [];
afterEach(() => {
  mounted.splice(0).forEach((unmount) => unmount());
  setLocale('ru');
});
function mount(field: DecisionFieldSpec, initial: unknown) {
  setLocale('en');
  const root = document.createElement('div');
  document.body.append(root);
  const value = ref(initial);
  const emitted: unknown[] = [];
  const app = createApp({
    render: () =>
      h(DecisionField, {
        field,
        value: value.value,
        onChange: (next: unknown) => emitted.push(next),
      }),
  });
  app.mount(root);
  mounted.push(() => {
    app.unmount();
    root.remove();
  });
  const input = root.querySelector('input')!;
  return {
    input,
    value,
    emitted,
    preview: () => root.querySelector('output')!.textContent,
    error: () => root.querySelector('[role="alert"]')?.textContent ?? '',
    async type(text: string) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await nextTick();
    },
  };
}

test('amounts are typed in euros with either separator and stored in minor units', async () => {
  const field = mount(money, 12345);
  expect(field.input.value).toBe('123.45');
  expect(field.input.type).toBe('text');
  expect(field.input.getAttribute('inputmode')).toBe('decimal');
  await field.type('1.5');
  expect(field.emitted.at(-1)).toBe(150);
  expect(field.preview()).toContain('€1.50');
  await field.type('100,50');
  expect(field.emitted.at(-1)).toBe(10050);
  await field.type('');
  expect(field.emitted.at(-1)).toBeNull();
  expect(field.preview()).toBe('');
  await field.type('0');
  expect(field.emitted.at(-1)).toBe(0);
  expect(field.preview()).toContain('€0.00');
  await field.type('-2.5');
  expect(field.emitted.at(-1)).toBe(-250);
});

test('a known zero shows 0, a missing amount stays empty, and saved values redisplay', async () => {
  expect(mount(money, 0).input.value).toBe('0');
  const missing = mount(money, null);
  expect(missing.input.value).toBe('');
  missing.value.value = 250;
  await nextTick();
  expect(missing.input.value).toBe('2.5');
});

test('rates are typed in percent and stored in basis points', async () => {
  const field = mount(rate, 2100);
  expect(field.input.value).toBe('21');
  await field.type('10,5');
  expect(field.emitted.at(-1)).toBe(1050);
  expect(field.preview()).toBe('≈ 10.5%');
  await field.type('');
  expect(field.emitted.at(-1)).toBeNull();
  await field.type('0');
  expect(field.emitted.at(-1)).toBe(0);
});

test('an unreadable amount keeps the stored value and says so', async () => {
  const field = mount(money, 12345);
  await field.type('7-');
  expect(field.emitted).toEqual([]);
  expect(field.error()).toBe(t('fields.unreadableAmount', {}, 'en'));
  expect(field.preview()).toBe('');
  await field.type('7');
  expect(field.emitted).toEqual([700]);
  expect(field.error()).toBe('');
});
