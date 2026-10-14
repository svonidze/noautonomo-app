import { formatPeriod } from './format.ts';
import { ApiError } from './http.ts';
import { formatMessage, messageIds, type UiMessage } from './i18n.ts';
/** Older/newer servers may send only diagnostic text or unknown additive IDs. */
export function errorMessage(value: unknown, locale: unknown): string {
  if (value instanceof ApiError && value.messageCode && messageIds.includes(value.messageCode)) {
    try {
      // Servers send quarters as keys («2026-Q3»): name them the way the header does.
      const params = Object.fromEntries(
        Object.entries(value.params ?? {}).map(([name, param]) => [
          name,
          typeof param === 'string' ? formatPeriod(param, locale) : param,
        ]),
      );
      return formatMessage(value.messageCode, params, locale);
    } catch {
      return value.message;
    } // Additive metadata must not hide a server diagnostic.
  }
  const text = value instanceof Error ? value.message : String(value || '');
  try {
    return messageIds.includes(text) ? formatMessage(text, {}, locale) : text;
  } catch {
    return text;
  }
}

/** Stored form for toasts and statuses: a key+params descriptor re-renders after a locale change. */
export function errorDescriptor(value: unknown): UiMessage | string {
  if (value instanceof ApiError && value.messageCode && messageIds.includes(value.messageCode)) {
    try {
      formatMessage(value.messageCode, value.params);
      // Runtime-checked key: cast through unknown to keep the 1k-member union out of the type checker.
      return { key: value.messageCode, params: value.params } as unknown as UiMessage;
    } catch {
      return value.message;
    }
  }
  const text = value instanceof Error ? value.message : String(value || '');
  if (messageIds.includes(text)) {
    try {
      formatMessage(text, {});
      return { key: text } as unknown as UiMessage;
    } catch {
      return text;
    }
  }
  return text;
}
