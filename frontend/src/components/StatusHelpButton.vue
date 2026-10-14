<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue';
import { helpAdapter, type HelpContext } from '../vue/help.ts';
/** A text button that opens the status explanation of `context`; it owns that context's help record. */
const props = defineProps<{ context: HelpContext; label: string }>();
const scope = helpAdapter().createScope(props.context);
watch(
  () => props.context,
  (value) => scope.update(value),
  { deep: true, flush: 'sync' },
);
onBeforeUnmount(() => scope.dispose());
</script>
<template>
  <button
    type="button"
    class="text-button status-details-button"
    :data-status-help="scope.id"
    :data-status-subject="
      context.subject_id ? `${context.domain}:${context.subject_id}` : undefined
    "
    aria-haspopup="dialog"
  >
    {{ label }}
  </button>
</template>
