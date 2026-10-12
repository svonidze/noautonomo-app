export interface TaxAttribution {
  attribution_id: string;
  revision: number;
  reason: string;
  historical_correction: boolean;
  stale?: boolean;
  decision: {
    irpf: {
      date: string;
      income_minor: number;
      deductible_minor: number;
      withholding_minor: number;
    } | null;
    iva: {
      date: string;
      base_minor: number | null;
      vat_minor: number | null;
      deductible_minor: number | null;
      pending_reason: string | null;
    } | null;
    fx: { event_key: string; date: string; amount_minor: number }[];
  };
}
