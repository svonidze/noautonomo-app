import { createAccountingHelp } from '../help/registry.ts';
export interface HelpContext {
  domain: string;
  state: string;
  subject_id?: string;
  [key: string]: unknown;
}
interface HelpScope {
  id: string;
  update(context: HelpContext): void;
  dispose(): void;
}
export interface HelpAdapter {
  createScope(context: HelpContext): HelpScope;
  label(context: HelpContext): string;
  summary(context: HelpContext): string;
  tone(context: HelpContext): string;
  /** «Что сделать», «Почему», or '' when the record has neither an action nor a reason. */
  link(context: HelpContext): string;
  /** The review the server offers for the record (the one review-availability rule), if any. */
  reviewAction(context: HelpContext): { transaction_id: string; period?: string } | undefined;
  word(key: string): string;
  termLabel?(key: string): string;
  reasonInfo(reason: Record<string, unknown>): string[];
  text(key: string): string;
}
let instance: ReturnType<typeof createAccountingHelp> | undefined;
export const helpAdapter = () => (instance ??= createAccountingHelp());

export function disposeHelp() {
  const current = instance;
  instance = undefined;
  current?.dispose();
}
