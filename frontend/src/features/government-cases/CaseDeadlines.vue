<script setup lang="ts">
import { onBeforeUnmount, onMounted, shallowRef, ref } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import type { ViewServices } from '../../vue/services.ts';
import type { CaseAction, CaseList } from './model.ts';
const props = defineProps<{ services: ViewServices }>();
const { t } = useLocale();
const actions = shallowRef<CaseAction[]>([]),
  failed = ref(false),
  loading = ref(true);
let active = true;
onBeforeUnmount(() => {
  active = false;
});
async function load() {
  failed.value = false;
  try {
    const result = (await props.services.request('/api/government-cases')) as CaseList;
    if (active) actions.value = result.actions.slice(0, 5);
  } catch {
    if (active) failed.value = true;
  } finally {
    if (active) loading.value = false;
  }
}
onMounted(load);
</script>
<template>
  <section v-if="!services.readOnly" class="panel">
    <h2>{{ t('government.nextActions') }}</h2>
    <p v-if="failed" role="alert">
      {{ t('government.loadFailed') }}
      <button class="secondary-button" @click="load">{{ t('common.reload') }}</button>
    </p>
    <p v-else-if="loading" role="status">{{ t('common.loading') }}</p>
    <p v-else-if="!actions.length">{{ t('government.noActions') }}</p>
    <ul>
      <li v-for="action in actions" :key="action.action_id">
        <a :href="`/government-cases?case=${action.case_id}`"
          >{{ action.title }}: {{ action.description }}</a
        >
        · {{ action.due_on || t('government.noDeadline') }}
        <strong v-if="!action.confirmed">{{ t('government.unconfirmed') }}</strong>
      </li>
    </ul>
  </section>
</template>
