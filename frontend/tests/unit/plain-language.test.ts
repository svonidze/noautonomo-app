// @vitest-environment node
import { readdirSync, readFileSync } from 'node:fs';
import { parse, TYPE, type MessageFormatElement } from '@formatjs/icu-messageformat-parser';
import { expect, test } from 'vitest';
import { readMessages } from '../../scripts/build_locales.mts';

// Latin words the Russian UI may keep: Spanish tax and legal terms, formats, product names.
// prettier-ignore
export const ALLOWED_TERMS = [
  'IVA', 'IRPF', 'AEAT', 'Modelo', 'EUR', 'ECB', 'VIES', 'RETA', 'TGSS', 'Renta', 'WEB', 'ROI',
  'NIF', 'NIE', 'CIF', 'VAT', 'Sede', 'BOE', 'LIRPF', 'LIVA', 'LIS', 'RIRPF', 'TEAC', 'LGSS',
  'IRNR', 'IAE', 'DNI', 'IBAN', 'SEPA', 'Banco', 'España', 'Agencia', 'Tributaria', 'Seguridad',
  'Social', 'Hacienda', 'autónomo', 'autónomos', 'estimación', 'directa', 'simplificada',
  'vivienda', 'habitual', 'casilla', 'SQLite', 'Google', 'Drive', 'Picker', 'OAuth', 'PDF', 'CSV',
  'TXT', 'XLSX', 'JSON', 'OCR', 'Tesseract', 'URL', 'ID', 'Xolo', 'Windows', 'macOS', 'Linux',
];
const allowed = new Set(ALLOWED_TERMS.map((term) => term.toLowerCase()));

// Burn-down list for stage 1 of docs/plans/ui-clarity-redesign.md: remove a key once its text is plain Russian.
export const BASELINE = [
  'app.inline.enGb',
  'app.inline.postedCalculationRefreshOrInboxCleanupIsPending',
  'dashboard.approvedNotPosted',
  'dashboard.snapshotAvailable',
  'errors.apiReturnedHtml',
  'fields.taxId',
  'help.reasons.inbox_roots_not_configured.action',
  'intake.googleDrivePlaceholder',
  'intake.googlePickerHint',
  'reta.baseRange',
  'reta.boundarySensitive',
  'reta.reasons.base_missing',
  'reta.reasons.period_missing',
  'reta.reasons.tarifa_plana_extension_unverified',
  'reta.reasons.window_empty',
  'reta.reasons.worker_kind_floor_unverified',
  'reta.regime',
  'reta.status.above_bracket',
  'reta.status.below_bracket',
  'reta.status.ok',
  'reta.statusDetail.above_bracket',
  'reta.statusDetail.below_bracket',
  'reta.statusDetail.ok',
  'reta.title',
  'reta.tramo',
  'reta.tramoValue',
  'reta.workerKind',
  'review.fxSettlementDate',
  'review.fxSettlementRate',
  'review.fxSettlementReference',
  'review.fxUnavailableHint',
  'review.fxUseManual',
  'review.postConfirmCleanupWarning',
  'review.postingCleanup',
  'settings.form.country',
  'taxes.filedValuesUnavailable',
  'taxes.filedValuesUnavailableExtract',
  'taxes.filedValuesUnavailablePdf',
  'workflow.refreshOrInboxCleanupNeedsRetryDoNotRepost',
];

/** Latin words in plain text that are not allowed; `settlement-` counts as the stem `settlement`. */
export function disallowedLatinWords(text: string): string[] {
  // Script=Latin, not [A-Za-z]: accented terms such as `autónomo` must stay whole words.
  const words = text
    .replace(/https?:\/\/\S+/g, '')
    .match(/\p{Script=Latin}[\p{Script=Latin}'’-]{2,}/gu);
  return (words ?? []).filter((word) => !allowed.has(word.replace(/-+$/, '').toLowerCase()));
}

// Argument names, select keys and plural categories are code, not text: only literals are scanned.
function literals(nodes: MessageFormatElement[]): string[] {
  return nodes.flatMap((node) => {
    if (node.type === TYPE.literal) return [node.value];
    if (node.type === TYPE.plural || node.type === TYPE.select) {
      return Object.values(node.options).flatMap((option) => literals(option.value));
    }
    return [];
  });
}

const directory = new URL('../../src/locales/ru/', import.meta.url);
const messages = readdirSync(directory)
  .filter((name) => name.endsWith('.json'))
  .sort()
  .flatMap((name) =>
    Object.entries(readMessages(readFileSync(new URL(name, directory), 'utf8'), `ru/${name}`)),
  );
const flagged = new Map(
  messages
    .map(([key, message]) => {
      const text = literals(parse(message, { captureLocation: false }));
      return [key, text.flatMap(disallowedLatinWords)] as const;
    })
    .filter(([, words]) => words.length > 0),
);

test('Russian messages avoid Latin words outside the allowed terms', () => {
  const unlisted = [...flagged].filter(([key]) => !BASELINE.includes(key));
  expect(
    unlisted.map(([key, words]) => `${key}: ${words.join(', ')}`),
    'write these in Russian or add a term to ALLOWED_TERMS',
  ).toEqual([]);
});

test('every burn-down key still has Latin words', () => {
  expect(
    BASELINE.filter((key) => !flagged.has(key)),
    'these keys are clean now: remove them from BASELINE',
  ).toEqual([]);
});
