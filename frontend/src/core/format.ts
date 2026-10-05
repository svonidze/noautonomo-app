import { formatMessage, localeTag, messageIds } from './i18n.ts';

export function escapeHtml(value: unknown): string {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
export function eur(value: unknown, locale: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return new Intl.NumberFormat(localeTag(locale), {
    style: 'currency',
    currency: 'EUR',
    minimumFractionDigits: 2,
  }).format(number);
}
export function formatDateText(value: unknown, locale: unknown): string {
  if (!value) return '—';
  const date = new Date(`${String(value)}T12:00:00`);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(localeTag(locale), {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  }).format(date);
}
export function formatDateHtml(value: unknown, locale: unknown): string {
  return escapeHtml(formatDateText(value, locale));
}
export function formatDateTime(value: unknown, locale: unknown): string {
  if (!value) return '—';
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return formatDateHtml(text, locale);
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return new Intl.DateTimeFormat(localeTag(locale), {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}
/** Catalog label for a server code under `prefix`; the code itself when the catalog has none. */
export function codeLabel(prefix: string, code: unknown, locale: unknown): string {
  const value = String(code ?? '');
  const key = `${prefix}.${value}`;
  return value && messageIds.includes(key) ? formatMessage(key, {}, locale) : value;
}
/** «Label (code)» for a review tax code, «AEAT code …» without a label, the unknown label without a code. */
export function taxCodeLabel(code: unknown, locale: unknown): string {
  const value = String(code ?? '');
  if (!value || value === 'unknown') return formatMessage('taxCodeLabels.unknown', {}, locale);
  const key = `taxCodeLabels.${value}`;
  return messageIds.includes(key)
    ? `${formatMessage(key, {}, locale)} (${value})`
    : formatMessage('common.aeatCode', { code: value }, locale);
}
/** «F1 · Полный счёт» for an AEAT invoice type, qualification or exemption; «Тип AEAT …» otherwise. */
export function aeatCodeLabel(
  group: 'invoiceType' | 'qualification' | 'exemption',
  code: string,
  locale: unknown,
): string {
  const key = `aeat.${group}.${code}`;
  return messageIds.includes(key)
    ? `${code} · ${formatMessage(key, {}, locale)}`
    : formatMessage('aeat.unknownType', { code }, locale);
}
