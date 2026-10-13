import { markRaw } from 'vue';
import IntakeDialog from '../features/intake/IntakeDialog.vue';
import { loadLocale, storeLocale, localeRegistry } from '../core/i18n.ts';
import { createProductionRouter } from './router.ts';
import type { Router } from 'vue-router';
import { request, refreshCalculation } from './request.ts';
import type { AppRuntime } from './runtime.ts';

export function createProductionRuntime(router: Router = createProductionRouter()): AppRuntime {
  return {
    router,
    request,
    readOnly: false,
    locales: localeRegistry.filter((item) => item.code !== 'es'),
    initialLocale: () => {
      const locale = loadLocale(() => localStorage);
      return locale === 'es' ? 'ru' : locale;
    },
    saveLocale: (value) => storeLocale(() => localStorage, value),
    refreshCalculation,
    intakeComponent: markRaw(IntakeDialog),
  };
}
