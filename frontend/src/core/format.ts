import { formatMessage, localeTag, messageIds, quarterNumber } from './i18n.ts';

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
/** «IV квартал 2026» / "Q4 2026" for a period key; any other shape is returned as is. The short
 * form («IV кв. 2026») keeps four quarters apart on a chart axis. */
export function formatPeriod(
  periodKey: unknown,
  locale: unknown,
  { short = false }: { short?: boolean } = {},
): string {
  const key = String(periodKey ?? '');
  const match = /^(\d{4})-Q([1-4])$/.exec(key);
  return match
    ? formatMessage(
        short ? 'common.quarterShort' : 'common.quarter',
        { year: match[1], quarter: quarterNumber(Number(match[2]), locale) },
        locale,
      )
    : key;
}
/** A quarter after today's: it has no calculation by design (DESIGN.md, Taxes-page headline). */
export function isFuturePeriod(periodKey: string, today = new Date()): boolean {
  const match = /^(\d{4})-Q([1-4])$/.exec(periodKey);
  const quarter = (year: number, number: number) => year * 4 + number;
  return (
    !!match &&
    quarter(Number(match[1]), Number(match[2])) >
      quarter(today.getFullYear(), Math.floor(today.getMonth() / 3) + 1)
  );
}
/** Catalog label for a server code under `prefix`; the code itself when the catalog has none. */
export function codeLabel(prefix: string, code: unknown, locale: unknown): string {
  const value = String(code ?? '');
  const key = `${prefix}.${value}`;
  return value && messageIds.includes(key) ? formatMessage(key, {}, locale) : value;
}
/** «Label (code)» for a review tax code, «Code …» without a label, the unknown label without a code. */
export function taxCodeLabel(code: unknown, locale: unknown): string {
  const value = String(code ?? '');
  if (!value || value === 'unknown') return formatMessage('taxCodeLabels.unknown', {}, locale);
  const key = `taxCodeLabels.${value}`;
  return messageIds.includes(key)
    ? `${formatMessage(key, {}, locale)} (${value})`
    : formatMessage('common.code', { code: value }, locale);
}
/** «F1 · Полный счёт» for an AEAT invoice type, qualification or exemption. AEAT invoice types the
 * repository has no description for read «Тип AEAT …»; any other unknown value reads «Значение …». */
export function aeatCodeLabel(
  group: 'invoiceType' | 'qualification' | 'exemption',
  code: string,
  locale: unknown,
): string {
  const key = `aeat.${group}.${code}`;
  if (messageIds.includes(key)) return `${code} · ${formatMessage(key, {}, locale)}`;
  return group === 'invoiceType' && ['SF', 'DV', 'AJ'].includes(code)
    ? formatMessage('aeat.unknownType', { code }, locale)
    : formatMessage('common.value', { code }, locale);
}
