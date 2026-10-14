<script setup lang="ts">
import { useLocale } from '../../vue/locale.ts';
import { focusById } from '../../core/focus.ts';
/** «Что осталось»: each open item links to the control that settles it. */
export interface RemainingItem {
  id: string;
  text: string;
}
defineProps<{ items: RemainingItem[] }>();
const { t } = useLocale();
</script>
<template>
  <section v-if="items.length" class="wf-remaining">
    <h3>{{ t('workflow.remaining.title') }}</h3>
    <ul>
      <li v-for="(item, index) in items" :key="index">
        <a :href="`#${item.id}`" @click.prevent="focusById(item.id)">{{ item.text }}</a>
      </li>
    </ul>
  </section>
</template>
