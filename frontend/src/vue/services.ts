import type { LocaleSpec } from '../core/i18n.ts';
import type { JsonOptions } from '../core/http.ts';
export interface ViewServices {
  readOnly?: boolean;
  locales?: readonly LocaleSpec[];
  href?(url: string): string;
  documentUrl?(id: string, preview?: boolean): string;
  request(url: string, options?: JsonOptions): Promise<unknown>;
  navigate(url: string): void;
}
export function followSpaLink(event: MouseEvent, navigate: (url: string) => void) {
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey
  )
    return;
  const link = event.currentTarget as HTMLAnchorElement;
  if (link.target || link.hasAttribute('download')) return;
  if (link.origin !== window.location.origin) return;
  event.preventDefault();
  navigate(link.pathname + link.search + link.hash);
}

export function viewHref(services: ViewServices | undefined, url: string): string {
  return services?.href?.(url) ?? url;
}
export function documentHref(
  services: ViewServices | undefined,
  id: string,
  preview = false,
): string {
  return (
    services?.documentUrl?.(id, preview) ??
    `/api/document/${encodeURIComponent(id)}/${preview ? 'preview' : 'content'}`
  );
}
