<script setup lang="ts">
import { useLocale } from '../../vue/locale.ts';
import { messageText, type UiMessage } from '../../core/i18n.ts';
import { followSpaLink } from '../../vue/services.ts';
import type { DashboardTask } from './tasks.ts';
defineProps<{ tasks: DashboardTask[]; emptyText: string; note?: string; busy?: boolean }>();
const emit = defineEmits<{
  navigate: [url: string];
  action: [action: NonNullable<DashboardTask['action']>];
}>();
const { locale, t } = useLocale();
const text = (value: UiMessage) => messageText(value, locale.value);
const navigate = (url: string) => emit('navigate', url);
</script>
<template>
  <section class="task-list" aria-labelledby="dashboard-tasks-title">
    <h2 id="dashboard-tasks-title">{{ t('dashboard.tasksTitle') }}</h2>
    <ul v-if="tasks.length">
      <li v-for="task in tasks" :key="task.id">
        <a
          v-if="task.href"
          class="task-row"
          :href="task.href"
          data-spa
          @click="followSpaLink($event, navigate)"
          ><span
            ><strong>{{ text(task.title) }}</strong
            ><small>{{ text(task.hint) }}</small></span
          ><span class="badge" :class="`status-${task.tone}`">{{ text(task.tag) }}</span></a
        >
        <button
          v-else
          type="button"
          class="task-row"
          :disabled="busy"
          @click="task.action && emit('action', task.action)"
        >
          <span
            ><strong>{{ text(task.title) }}</strong
            ><small>{{ text(task.hint) }}</small></span
          ><span class="badge" :class="`status-${task.tone}`">{{ text(task.tag) }}</span>
        </button>
      </li>
    </ul>
    <template v-else>
      <p>{{ emptyText }}</p>
      <p v-if="note">{{ note }}</p>
    </template>
  </section>
</template>
