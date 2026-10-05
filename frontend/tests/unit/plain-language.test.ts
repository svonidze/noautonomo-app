// @vitest-environment node
import { readdirSync, readFileSync } from 'node:fs';
import { parse, TYPE, type MessageFormatElement } from '@formatjs/icu-messageformat-parser';
import { expect, test } from 'vitest';
import { readMessages } from '../../scripts/build_locales.mts';
import references from '../../src/help/references.json' with { type: 'json' };

// Latin words the Russian UI may keep: Spanish tax and legal terms, formats, product names.
// prettier-ignore
export const ALLOWED_TERMS = [
  'IVA', 'IRPF', 'AEAT', 'Modelo', 'EUR', 'ECB', 'VIES', 'RETA', 'TGSS', 'Renta', 'WEB', 'ROI',
  'NIF', 'NIE', 'CIF', 'VAT', 'Sede', 'BOE', 'LIRPF', 'LIVA', 'LIS', 'RIRPF', 'TEAC', 'LGSS',
  'IRNR', 'IAE', 'DNI', 'IBAN', 'SEPA', 'Banco', 'España', 'Agencia', 'Tributaria', 'Seguridad',
  'Social', 'Hacienda', 'autónomo', 'autónomos', 'estimación', 'directa', 'simplificada',
  'vivienda', 'habitual', 'casilla', 'SQLite', 'Google', 'Drive', 'Picker', 'OAuth', 'PDF', 'CSV',
  'TXT', 'XLSX', 'JSON', 'OCR', 'Tesseract', 'URL', 'ID', 'Xolo', 'Windows', 'macOS', 'Linux',
  'ISO', 'autonomo-web', 'autonomo-tax',
  // Official Spanish terms printed on AEAT/TGSS documents: single official terms may be allowed; English and internal tokens never.
  'tramo', 'referencia', 'justificante', 'DUA', 'alta', 'tarifa', 'plana', 'societario',
  'societarios', 'familiar', 'familiares', 'colaborador', 'colaboradores',
];
const allowed = new Set(ALLOWED_TERMS.map((term) => term.toLowerCase()));

// Burn-down list of stage 1 of docs/plans/ui-clarity-redesign.md, emptied by that stage: keep it empty.
export const BASELINE: string[] = [];

/** Latin words in plain text that are not allowed; `settlement-` counts as the stem `settlement`. */
export function disallowedLatinWords(text: string): string[] {
  // Script=Latin, not [A-Za-z], and NFC first: accented terms such as `autónomo` must stay whole
  // words, also when the catalog stores the accent decomposed.
  const words = text
    .normalize('NFC')
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

test('Russian messages avoid disallowed Latin-script words beyond BASELINE', () => {
  const unlisted = [...flagged].filter(([key]) => !BASELINE.includes(key));
  expect(
    unlisted.map(([key, words]) => `${key}: ${words.join(', ')}`),
    'write these in Russian or add a term to ALLOWED_TERMS',
  ).toEqual([]);
});

test('BASELINE is sorted and names only keys that still have disallowed Latin-script words', () => {
  expect(BASELINE, 'keep BASELINE sorted and free of duplicates').toEqual(
    [...new Set(BASELINE)].sort(),
  );
  expect(
    BASELINE.filter((key) => !flagged.has(key)),
    'these keys are clean or no longer present: remove them from BASELINE',
  ).toEqual([]);
});

test('the scanner flags only disallowed Latin-script words', () => {
  const table: [string, string[]][] = [
    ['Дата settlement-курса', ['settlement-']],
    ['IVA-декларация', []],
    ['ставка iva', []],
    ['Autónomo', []],
    ['см. https://example.org/path', []],
  ];
  for (const [text, expected] of table) expect(disallowedLatinWords(text), text).toEqual(expected);
  expect(disallowedLatinWords('Auto\u0301nomo'), 'decomposed autónomo').toEqual([]);
});

// One status vocabulary (plan section 6): labels live in statuses.*, and the synonym keys stay deleted.
test('status labels come from the statuses catalog only', () => {
  const synonyms = [
    /^help\.statuses\./,
    /^review\.category\./,
    /^review\.(summary|status|posting)(NeedsReview|Ready|Later|Blocked)$/,
    /^(review\.needsReview|help\.words\.(ready|blocked)|dashboard\.(filed|filedOn|notCalculated))$/,
    /^review\.posting(Approved|BatchReady|Deferred|BatchBlocked)$/,
    /^taxes\.(filed|notFiled|filingNotRequired|calculationMissing|noCalculation)$/,
    /^review\.step/,
    /^(dashboard\.expensesForecast|help\.words\.calculation)$/,
  ];
  expect(messages.map(([key]) => key).filter((key) => synonyms.some((re) => re.test(key)))).toEqual(
    [],
  );
  expect(
    Object.values(references.Statuses).filter((key) => !key.startsWith('statuses.labels.')),
  ).toEqual([]);
});
