<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { formatMessage, localeTag, type MessageId } from '../../core/i18n.ts';
import { eur } from '../../core/format.ts';
export interface DecisionFieldSpec {
  path: string;
  label: MessageId;
  type?: 'text' | 'number' | 'textarea' | 'select' | 'checkbox';
  valueType?: 'string' | 'nullable-string' | 'integer' | 'number' | 'boolean' | 'nullable-boolean';
  options?: { value: string; label: string }[];
  min?: string;
  max?: string;
  step?: string;
  minor?: boolean;
  basisPoints?: boolean;
  full?: boolean;
  descriptionId?: string;
}
const props = defineProps<{
  field: DecisionFieldSpec;
  value?: unknown;
  disabled?: boolean;
  guided?: boolean;
  error?: string;
}>();
const emit = defineEmits<{ change: [value: unknown] }>();
const { locale, t } = useLocale();
// Amounts are typed in euros and rates in percent; the decision keeps minor units and basis points.
const scaled = computed(() => !!(props.field.minor || props.field.basisPoints));
const display = (value: unknown) =>
  value == null || value === '' ? '' : scaled.value ? String(Number(value) / 100) : String(value);
const raw = ref(display(props.value));
function read() {
  const type = props.field.valueType || 'string';
  if (scaled.value) {
    const text = String(raw.value).trim();
    if (!text) return null;
    const number = Number(text);
    return Number.isFinite(number) ? Math.round(number * 100) : undefined;
  }
  if (raw.value === '') return type.startsWith('nullable-') ? null : '';
  if (type === 'integer') return Number.parseInt(raw.value, 10);
  if (type === 'number') return Number(raw.value);
  if (type === 'boolean' || type === 'nullable-boolean') return raw.value === 'true';
  return raw.value;
}
watch(
  () => props.value,
  (value) => {
    if (read() !== value) raw.value = display(value);
  },
);
// A number input reports unreadable text as an empty value with badInput: keep the stored value.
function changed(event: Event) {
  const value = read();
  if (
    value !== undefined &&
    !(scaled.value && (event.target as HTMLInputElement).validity?.badInput)
  )
    emit('change', value);
}
// The preview shows what the typed text stores, rounded to cents or basis points.
const preview = computed(() => {
  const value = read();
  if (typeof value !== 'number') return '';
  const amount = props.field.basisPoints
    ? new Intl.NumberFormat(localeTag(locale.value), {
        style: 'percent',
        maximumFractionDigits: 2,
      }).format(value / 10000)
    : eur(value / 100, locale.value);
  return t('review.irpfPreview', { amount });
});
</script>
<template>
  <label
    :class="{
      'review-guided-field': guided,
      'full-span': field.full,
      'checkbox-label': field.type === 'checkbox',
    }"
  >
    <input
      v-if="field.type === 'checkbox'"
      type="checkbox"
      :checked="Boolean(value)"
      :disabled="disabled"
      :data-decision-path="field.path"
      data-value-type="boolean"
      @change="emit('change', ($event.target as HTMLInputElement).checked)"
    />
    <span
      >{{ formatMessage(field.label, {}, locale) }}
      <output v-if="scaled" class="review-irpf-preview" data-eur-preview>{{
        preview
      }}</output></span
    >
    <textarea
      v-if="field.type === 'textarea'"
      v-model="raw"
      rows="2"
      :disabled="disabled"
      :data-decision-path="field.path"
      :data-value-type="field.valueType"
      @input="emit('change', read())"
    ></textarea>
    <select
      v-else-if="field.type === 'select'"
      v-model="raw"
      :disabled="disabled"
      :data-decision-path="field.path"
      :data-value-type="field.valueType"
      :aria-describedby="field.descriptionId"
      @change="emit('change', read())"
    >
      <option v-for="option in field.options || []" :key="option.value" :value="option.value">
        {{ option.label }}
      </option>
    </select>
    <input
      v-else-if="field.type !== 'checkbox'"
      v-model="raw"
      :type="field.type || 'text'"
      :disabled="disabled"
      :data-decision-path="field.path"
      :data-value-type="field.valueType"
      :min="field.min"
      :max="field.max"
      :step="scaled ? '0.01' : field.step"
      :inputmode="
        scaled
          ? 'decimal'
          : field.valueType === 'integer'
            ? 'numeric'
            : field.type === 'number'
              ? 'decimal'
              : undefined
      "
      @input="changed"
    />
    <slot />
    <p v-if="error" class="review-inline-error" role="alert">{{ error }}</p>
  </label>
</template>
