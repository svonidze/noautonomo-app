import { createApp, h, nextTick, ref } from 'vue';
import { expect, test } from 'vitest';
import DecisionField, { type DecisionFieldSpec } from '../../src/features/review/DecisionField.vue';
import { setLocale } from '../../src/core/i18n.ts';

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
  const input = root.querySelector('input')!;
  return {
    input,
    value,
    emitted,
    preview: () => root.querySelector('output')!.textContent,
    async type(text: string) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await nextTick();
    },
    unmount() {
      app.unmount();
      root.remove();
      setLocale('ru');
    },
  };
}

test('amounts are typed in euros and stored in minor units with their sign', async () => {
  const field = mount(money, 12345);
  expect(field.input.value).toBe('123.45');
  expect(field.input.getAttribute('step')).toBe('0.01');
  expect(field.input.getAttribute('inputmode')).toBe('decimal');
  await field.type('1.5');
  expect(field.emitted.at(-1)).toBe(150);
  expect(field.preview()).toContain('€1.50');
  await field.type('');
  expect(field.emitted.at(-1)).toBeNull();
  expect(field.preview()).toBe('');
  await field.type('0');
  expect(field.emitted.at(-1)).toBe(0);
  expect(field.preview()).toContain('€0.00');
  await field.type('-2.5');
  expect(field.emitted.at(-1)).toBe(-250);
  field.unmount();
});

test('a known zero shows 0, a missing amount stays empty, and saved values redisplay', async () => {
  const zero = mount(money, 0);
  expect(zero.input.value).toBe('0');
  zero.unmount();
  const missing = mount(money, null);
  expect(missing.input.value).toBe('');
  missing.value.value = 250;
  await nextTick();
  expect(missing.input.value).toBe('2.5');
  missing.unmount();
});

test('rates are typed in percent and stored in basis points', async () => {
  const field = mount(rate, 2100);
  expect(field.input.value).toBe('21');
  expect(field.input.getAttribute('step')).toBe('0.01');
  await field.type('10.5');
  expect(field.emitted.at(-1)).toBe(1050);
  expect(field.preview()).toBe('≈ 10.5%');
  field.unmount();
});

test('an amount the browser cannot read leaves the stored value untouched', async () => {
  const field = mount(money, 12345);
  // A number input reports unreadable text as an empty value with validity.badInput.
  Object.defineProperty(field.input, 'validity', { value: { badInput: true } });
  await field.type('');
  expect(field.emitted).toEqual([]);
  field.unmount();
});
