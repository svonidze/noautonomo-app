import { createWebHashHistory } from 'vue-router';
import { ApiError, type JsonOptions } from '../core/http.ts';
import { getLocale, localeRegistry } from '../core/i18n.ts';
import { createAppRouter } from './create-router.ts';
import { request } from './request.ts';
import type { AppRuntime } from './runtime.ts';

export const publicLanguages = ['en', 'es', 'ru'] as const;
export interface PublicRuntimeConfig {
  mode: 'public_demo';
  basePath: '/demo';
  readOnly: true;
  locales: readonly (typeof publicLanguages)[number][];
  defaultLocale: 'en';
}
export function readRuntimeConfig(
  element = document.getElementById('autonomo-runtime'),
): PublicRuntimeConfig | null {
  const value: unknown = JSON.parse(element?.textContent || '{}');
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Invalid application runtime configuration');
  const config = value as Record<string, unknown>;
  if (Object.keys(config).length === 0) return null;
  if (
    config.mode !== 'public_demo' ||
    config.basePath !== '/demo' ||
    config.readOnly !== true ||
    config.defaultLocale !== 'en' ||
    !Array.isArray(config.locales) ||
    config.locales.length !== publicLanguages.length ||
    !config.locales.every((locale, index) => locale === publicLanguages[index])
  ) {
    throw new Error('Invalid public application runtime configuration');
  }
  return config as unknown as PublicRuntimeConfig;
}
export function publicLocale(search = window.location.search): (typeof publicLanguages)[number] {
  const requested = new URLSearchParams(search).get('lang');
  return publicLanguages.includes(requested as (typeof publicLanguages)[number])
    ? (requested as (typeof publicLanguages)[number])
    : 'en';
}
export function savePublicLocale(locale: string) {
  const url = new URL(window.location.href);
  url.searchParams.set(
    'lang',
    publicLanguages.includes(locale as (typeof publicLanguages)[number]) ? locale : 'en',
  );
  window.history.replaceState(window.history.state, '', url);
}
function localUrl(url: string): URL {
  const target = new URL(url, window.location.origin);
  if (target.origin !== window.location.origin) throw new Error('External public application URL');
  return target;
}
export function publicApiUrl(url: string, basePath = '/demo'): string {
  const target = localUrl(url);
  const path = target.pathname;
  if (path === `${basePath}/api` || path.startsWith(`${basePath}/api/`))
    return path + target.search;
  if (path === '/api' || path.startsWith('/api/')) return basePath + path + target.search;
  throw new Error('Public requests must use the application API');
}
export function publicHref(url: string, locale: string, basePath = '/demo'): string {
  const target = localUrl(url);
  if (
    target.pathname === '/api' ||
    target.pathname.startsWith('/api/') ||
    target.pathname.startsWith(`${basePath}/api/`)
  ) {
    return publicApiUrl(url, basePath);
  }
  const language = publicLanguages.includes(locale as (typeof publicLanguages)[number])
    ? locale
    : 'en';
  if (target.pathname === basePath + '/' && target.hash.startsWith('#/')) {
    return `${basePath}/?lang=${language}${target.hash}`;
  }
  return `${basePath}/?lang=${language}#${target.pathname}${target.search}${target.hash}`;
}
function readOnlyError() {
  return Object.assign(new ApiError('Read-only demo'), {
    status: 405,
    code: 'public_demo_read_only',
    messageCode: 'demo.readOnlyReason',
  });
}
export function createPublicRuntime(config: PublicRuntimeConfig): AppRuntime {
  return {
    router: createAppRouter(createWebHashHistory(config.basePath + '/')),
    readOnly: true,
    locales: config.locales.map((code) => localeRegistry.find((item) => item.code === code)!),
    initialLocale: () => publicLocale(),
    saveLocale: savePublicLocale,
    href: (url) => publicHref(url, getLocale(), config.basePath),
    documentUrl: (id, preview = false) =>
      publicApiUrl(
        `/api/document/${encodeURIComponent(id)}/${preview ? 'preview' : 'content'}`,
        config.basePath,
      ),
    request: (url: string, options: JsonOptions = {}) => {
      if (!['GET', 'HEAD'].includes((options.method || 'GET').toUpperCase()))
        return Promise.reject(readOnlyError());
      return request(publicApiUrl(url, config.basePath), options);
    },
    async refreshCalculation() {
      throw readOnlyError();
    },
  };
}
