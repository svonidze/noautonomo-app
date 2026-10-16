<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useShell } from './controller.ts';
import { routeUrl, views } from './routes.ts';
import { useLocale } from '../vue/locale.ts';
import { formatMessage, messageText } from '../core/i18n.ts';
import { errorMessage } from '../core/error-message.ts';
import { formatPeriod } from '../core/format.ts';
import { followSpaLink } from '../vue/services.ts';
const {
  runtime,
  bootstrap,
  period,
  reviewTab,
  route,
  screen,
  view,
  intake,
  loading,
  failure,
  busy,
  resolved,
  revision,
  toast,
  locale,
  changeLocale,
  navigate,
  add,
  refresh,
  init,
  selectPeriod,
  intakeServices,
} = useShell();
const { t } = useLocale();
const href = (url: string) => runtime.href?.(url) ?? url;
const publicLinks = computed(() => ({
  landing: locale.value === 'en' ? '/' : `/${locale.value}/`,
  blog: locale.value === 'en' ? '/blog/' : `/blog/${locale.value}/`,
}));
const navigationViews = computed(() =>
  runtime.readOnly ? views.map((v) => (v === 'government-cases' ? 'aeat-documents' : v)) : views,
);
const icons = ['▦', '↗', '↙', '✓', '◇', '%', '◎', '▤', '⚙'];
const shellMessageKey = (prefix: 'nav' | 'titles', value: string) =>
  `${prefix}.${value === 'aeat-documents' ? 'aeatDocuments' : value === 'government-cases' ? 'governmentCases' : value}`;
const detail = computed(() => Boolean(route.value?.id));
const navView = computed(() =>
  route.value?.view === 'contact-detail'
    ? 'contacts'
    : route.value?.view === 'expense-detail'
      ? 'expenses'
      : route.value?.view,
);
const title = computed(() =>
  route.value?.view === 'contact-detail'
    ? t('contacts.cardTitle')
    : route.value?.view === 'expense-detail'
      ? t('expense.title')
      : formatMessage(
          shellMessageKey('titles', route.value?.view || 'dashboard'),
          {},
          locale.value,
        ),
);
// Without a profile the server names the brand, which the line above already shows.
const profileName = computed(() =>
  bootstrap.value?.profile_configured ? bootstrap.value.profile_name : t('brand.subtitle'),
);
const lead = computed(() =>
  route.value?.view === 'contact-detail'
    ? t('contacts.cardLead')
    : route.value?.view === 'expense-detail'
      ? t('expense.lead')
      : route.value?.view === 'review' && detail.value
        ? t('review.detailLead')
        : formatMessage(
            `${shellMessageKey('titles', route.value?.view || 'dashboard')}Lead`,
            {},
            locale.value,
          ),
);
// Narrow screens fold the navigation behind «Меню». Closing it from inside the bar returns focus to
// the toggle, so the keyboard never lands on a hidden link.
const menuOpen = ref(false),
  sidebar = ref<HTMLElement>(),
  menuToggle = ref<HTMLButtonElement>();
function closeMenu() {
  if (!menuOpen.value) return;
  const inside = sidebar.value?.contains(document.activeElement);
  menuOpen.value = false;
  if (inside) menuToggle.value?.focus();
}
watch(route, closeMenu);
const refreshLabel = computed(() =>
  t(
    route.value?.view === 'contact-detail'
      ? 'contacts.refresh'
      : route.value?.view === 'expense-detail'
        ? 'expense.refresh'
        : 'toolbar.refresh',
  ),
);
</script>
<template>
  <aside ref="sidebar" class="sidebar" :class="{ 'menu-open': menuOpen }" @keydown.esc="closeMenu">
    <div class="brand">
      <span class="brand-mark" aria-hidden="true">A</span
      ><span
        ><strong>Autónomo</strong><small id="profile-name">{{ profileName }}</small></span
      >
    </div>
    <span v-if="navView" class="nav-current" aria-hidden="true">{{
      formatMessage(shellMessageKey('nav', navView), {}, locale)
    }}</span>
    <button
      ref="menuToggle"
      type="button"
      class="nav-toggle"
      :aria-expanded="menuOpen"
      aria-controls="primary-nav"
      @click="menuOpen = !menuOpen"
    >
      <span aria-hidden="true">☰</span> {{ t('nav.menu') }}
    </button>
    <nav id="primary-nav" class="primary-nav" :aria-label="t('nav.aria')">
      <a
        v-for="(item, index) in navigationViews"
        :key="item"
        class="nav-item"
        :class="{ active: navView === item }"
        :aria-current="navView === item ? 'page' : undefined"
        :href="href(routeUrl(item, period, { tab: reviewTab }))"
        :data-view="item"
        data-spa
        @click="
          closeMenu();
          followSpaLink($event, navigate);
        "
        ><span aria-hidden="true">{{ icons[index] }}</span
        ><span :data-i18n="shellMessageKey('nav', item)">{{
          formatMessage(shellMessageKey('nav', item))
        }}</span></a
      >
    </nav>
  </aside>
  <main class="workspace">
    <aside v-if="runtime.readOnly" class="public-demo-banner" aria-label="Demo">
      <strong>{{ t('demo.banner') }}</strong>
      <span id="demo-readonly-reason">{{ t('demo.readOnlyReason') }}</span>
      <a :href="publicLinks.landing">{{ t('demo.about') }}</a>
      <a :href="publicLinks.blog">{{ t('demo.blog') }}</a>
    </aside>
    <header class="topbar">
      <div class="page-heading">
        <h1 id="page-title">{{ title }}</h1>
        <p id="page-lead">{{ lead }}</p>
      </div>
      <div class="topbar-actions">
        <div class="locale-control" role="group" :aria-label="t('locale.aria')">
          <button
            v-for="item in runtime.locales"
            :key="item.code"
            type="button"
            :data-locale="item.code"
            :class="{ active: locale === item.code }"
            :aria-pressed="locale === item.code"
            @click="changeLocale(item.code)"
          >
            {{ item.code.toUpperCase() }}
          </button>
        </div>
        <label
          class="period-control"
          :hidden="
            ['contact-detail', 'settings', 'aeat-documents', 'government-cases'].includes(
              route?.view || '',
            )
          "
          ><span>{{ t('toolbar.period') }}</span
          ><select
            id="period-select"
            :aria-label="t('toolbar.periodAria')"
            :value="detail && !resolved ? '' : period"
            :disabled="detail"
            :title="detail ? t('toolbar.periodLocked') : ''"
            @change="selectPeriod(($event.target as HTMLSelectElement).value)"
          >
            <option
              v-for="item in bootstrap?.periods"
              :key="item.period_key"
              :value="item.period_key"
            >
              {{ formatPeriod(item.period_key, locale) }}
            </option>
          </select></label
        >
        <button
          id="refresh-button"
          class="icon-button"
          :class="{ busy }"
          :title="runtime.readOnly ? t('demo.readOnlyReason') : refreshLabel"
          :aria-label="refreshLabel"
          :hidden="['settings', 'aeat-documents', 'government-cases'].includes(route?.view || '')"
          :disabled="
            runtime.readOnly || busy || (detail && route?.view !== 'contact-detail' && !resolved)
          "
          @click="refresh"
        >
          <span class="refresh-icon" aria-hidden="true">↻</span
          ><span class="refresh-label">{{ refreshLabel }}</span>
        </button>
        <div
          class="new-entry"
          :hidden="
            !route ||
            detail ||
            ['settings', 'aeat-documents', 'government-cases'].includes(route?.view || '') ||
            (route?.view === 'review' && reviewTab === 'posting')
          "
        >
          <button
            id="new-entry-button"
            class="primary-button"
            :disabled="runtime.readOnly || !bootstrap?.intake_enabled"
            :aria-describedby="
              runtime.readOnly || (bootstrap && !bootstrap.intake_enabled)
                ? 'new-entry-disabled'
                : undefined
            "
            @click="add"
          >
            <span aria-hidden="true">+</span> <span>{{ t('common.add') }}</span>
          </button>
          <small
            v-if="runtime.readOnly || (bootstrap && !bootstrap.intake_enabled)"
            id="new-entry-disabled"
            >{{ t(runtime.readOnly ? 'demo.readOnlyReason' : 'intake.disabledReason') }}</small
          >
        </div>
      </div>
    </header>
    <section id="app" class="content" data-vue-owned :aria-busy="loading">
      <div v-if="failure" class="empty-state" role="alert">
        <p>{{ errorMessage(failure, locale) }}</p>
        <button class="secondary-button" @click="init">{{ t('common.retry') }}</button>
      </div>
      <component
        v-else-if="screen"
        :is="screen.component"
        :key="revision"
        ref="view"
        :context="screen.context"
      />
      <div v-else-if="loading" class="empty-state" role="status">{{ t('common.loading') }}</div>
      <div v-else class="empty-state">
        <strong>{{ t('routes.unknownTitle') }}</strong>
        <p>{{ t('routes.unknownHint') }}</p>
        <a
          class="primary-button"
          :href="href('/dashboard')"
          data-spa
          @click="followSpaLink($event, navigate)"
          >{{ t('routes.backToDashboard') }}</a
        >
      </div>
    </section>
  </main>
  <component
    v-if="runtime.intakeComponent && !runtime.readOnly"
    :is="runtime.intakeComponent"
    ref="intake"
    :services="intakeServices"
  />
  <dialog id="status-help-dialog" class="status-help-dialog" aria-labelledby="status-help-title">
    <header>
      <h2 id="status-help-title"></h2>
      <button id="status-help-close" type="button" :aria-label="t('common.close')">×</button>
    </header>
    <div id="status-help-body"></div>
  </dialog>
  <div id="accounting-tooltip" class="accounting-tooltip" role="tooltip" hidden></div>
  <div
    id="toast"
    class="toast"
    :class="{ visible: !!toast, error: toast?.error }"
    :hidden="!toast"
    role="status"
    aria-live="polite"
  >
    <span id="toast-message">{{ messageText(toast?.value, locale) }}</span
    ><button
      id="toast-close"
      class="toast-close"
      type="button"
      :aria-label="t('common.close')"
      @click="toast = undefined"
    >
      ×
    </button>
  </div>
</template>
