<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { formatMessage, localeTag } from '../../core/i18n.ts';
import { fieldId, type Scalar, type FieldSpec } from './workflow-model.ts';
const props = defineProps<{
  field: FieldSpec;
  modelValue?: Scalar;
  currency?: string;
  readOnly?: boolean;
}>();
const emit = defineEmits<{ 'update:modelValue': [value: Scalar] }>();
const { locale, t } = useLocale();
const display = (value: Scalar | undefined) =>
  value == null
    ? ''
    : String(
        props.field.kind === 'money' || props.field.kind === 'rate'
          ? Number(value) / 100
          : props.field.kind === 'ratio'
            ? Number(value) * 100
            : value,
      );
const raw = ref(display(props.modelValue));
const read = () =>
  raw.value === ''
    ? props.field.kind && props.field.kind !== 'text'
      ? null
      : ''
    : props.field.kind === 'money' || props.field.kind === 'rate'
      ? Math.round(Number(raw.value) * 100)
      : props.field.kind === 'ratio'
        ? Number(raw.value) / 100
        : raw.value;
watch(
  () => props.modelValue,
  (value) => {
    if (value !== read()) raw.value = display(value);
  },
);
// An amount is typed in units and stored in minor units; the preview shows what is stored.
const preview = computed(() => {
  const value = read();
  const currency = (props.currency || 'EUR').toUpperCase();
  if (typeof value !== 'number' || !Number.isFinite(value) || !/^[A-Z]{3}$/.test(currency))
    return '';
  const amount = new Intl.NumberFormat(localeTag(locale.value), {
    style: 'currency',
    currency,
  }).format(value / 100);
  return t('review.irpfPreview', { amount });
});
// A hint explains the field without becoming part of its name.
const id = computed(() => fieldId(props.field.path));
</script>
<template>
  <label
    ><span :id="`${id}-label`"
      >{{ formatMessage(field.label, {}, locale) }}
      <output v-if="field.kind === 'money'" class="review-irpf-preview" data-eur-preview>{{
        preview
      }}</output></span
    ><input
      :id="id"
      v-model="raw"
      :readonly="readOnly"
      :data-p="field.path"
      :data-kind="field.kind || 'text'"
      :type="field.type || 'text'"
      :step="field.type === 'number' ? '0.01' : undefined"
      :aria-labelledby="field.hint ? `${id}-label` : undefined"
      :aria-describedby="field.hint ? `${id}-hint` : undefined"
      @input="!readOnly && emit('update:modelValue', read())"
    /><small v-if="field.hint" :id="`${id}-hint`" class="wf-hint">{{
      formatMessage(field.hint, {}, locale)
    }}</small></label
  >
</template>
