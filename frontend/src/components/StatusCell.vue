<script setup lang="ts">
import { computed } from 'vue';
import { useLocale } from '../vue/locale.ts';
import { helpAdapter, type HelpContext } from '../vue/help.ts';
import StatusHelpButton from './StatusHelpButton.vue';
/** A badge and at most one line: the reason, then the link to the explanation when the record has
 * an action («Что сделать») or a reason («Почему»). Healthy records show the badge only. `bare`
 * drops the link where the row's own action already opens the explanation. */
const props = defineProps<{ context: HelpContext; bare?: boolean }>();
const { locale } = useLocale();
const help = helpAdapter();
const presentation = computed(() => {
  void locale.value;
  return {
    label: help.label(props.context),
    summary: help.summary(props.context),
    tone: help.tone(props.context),
    link: props.bare ? '' : help.link(props.context),
  };
});
</script>
<template>
  <div class="status-context">
    <span class="badge" :class="`status-${presentation.tone}`">{{ presentation.label }}</span
    ><small v-if="presentation.summary || presentation.link"
      >{{ presentation.summary }}
      <StatusHelpButton v-if="presentation.link" :context="context" :label="presentation.link"
    /></small>
  </div>
</template>
