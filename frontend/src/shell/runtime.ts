import { inject, type Component, type InjectionKey } from 'vue';
import type { Router } from 'vue-router';
import type { LocaleSpec } from '../core/i18n.ts';
import type { ViewServices } from '../vue/services.ts';

/** Entrypoints supply transport and browser policy; all screens stay shared. */
export interface AppRuntime extends Omit<ViewServices, 'navigate'> {
  router: Router;
  readOnly: boolean;
  locales: readonly LocaleSpec[];
  initialLocale(): string;
  saveLocale(locale: string): void;
  refreshCalculation(period: string): Promise<void>;
  intakeComponent?: Component;
}
export const appRuntimeKey: InjectionKey<AppRuntime> = Symbol('app-runtime');
export function useAppRuntime(): AppRuntime {
  const runtime = inject(appRuntimeKey);
  if (!runtime) throw new Error('The application entrypoint must provide its runtime');
  return runtime;
}
