/** Plain-language names for everything a plan can contain. Unknown values fall back to a
 * readable form of their id, so a newer API never shows a blank. */

function fallback(id: string): string {
  return id.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
}

function lookup(table: Record<string, string>, id: string | null | undefined): string {
  if (!id) return "";
  return table[id] ?? fallback(id);
}

const MEANING: Record<string, string> = {
  continuous: "Measure",
  nominal: "Category",
  datetime: "Date or time",
  currency: "Money",
  percentage: "Percentage",
  identifier: "Identifier",
  text: "Free text",
  geography: "Place",
};

const PERSONAL: Record<string, string> = {
  email: "Email addresses",
  phone: "Phone numbers",
  person_name: "People's names",
  national_id: "Identity numbers",
  address: "Street addresses",
  date_of_birth: "Dates of birth",
  ip_address: "IP addresses",
  payment_card: "Card numbers",
};

const FLAG: Record<string, string> = {
  missing_tokens: "Words meaning 'no value'",
  high_missing: "Mostly empty",
  constant: "Same value everywhere",
  unparsed_values: "Some values unreadable",
  ambiguous_day_month: "Day/month order unclear",
  mixed_day_month: "Mixed date orders",
  mixed_representation: "Mixed formats",
  outliers: "Outliers",
  sentinel_values: "Placeholder codes",
  rare_negatives: "A few negatives",
  skewed: "Strongly skewed",
  heaping: "Rounded values",
  case_variants: "Capitalisation varies",
  whitespace: "Stray spaces",
  non_nfc_unicode: "Mixed Unicode forms",
  high_cardinality: "Many categories",
  repeated_identifiers: "Repeated ids",
  future_dates: "Dates in the future",
};

const MINER: Record<string, string> = {
  distribution: "Distribution",
  relationship: "Relationship",
  trend: "Trend over time",
  segment: "Difference between groups",
  anomaly: "Unusual rows",
  driver: "What goes with the outcome",
  missing_as_signal: "Missing values as a signal",
  benchmark: "Groups against the overall",
  conditional: "Holds within groups?",
};

const CHART: Record<string, string> = {
  histogram: "Histogram",
  bar: "Bar chart",
  line: "Line over time",
  scatter: "Scatter plot",
  box: "Box plot",
  heatmap: "Heatmap",
  map: "Map",
};

const FEATURE: Record<string, string> = {
  date_part: "Part of a date",
  days_between: "Days between dates",
  ratio: "Ratio",
  difference: "Difference",
  log: "Log scale",
  band: "Bands",
  text_length: "Text length",
  word_count: "Word count",
  is_missing: "Is it missing?",
};

const FALLBACK_REASON: Record<string, string> = {
  disabled: "no language model is configured",
  budget_run: "this run's model budget was spent",
  budget_org: "the organisation's monthly model budget was spent",
  breaker_open: "the model provider has been failing",
  payload_blocked: "the request would have held personal data",
  timeout: "the model did not answer in time",
  rate_limited: "the model provider was busy",
  quota_exhausted: "the model provider's quota is used up for now",
  auth_failed: "the model provider rejected the API key",
  model_not_found: "the configured model is not available to this API key",
  provider_error: "the model provider returned an error",
  invalid_output: "the model's answer did not fit the plan language",
  refused: "the model declined the request",
  no_facts: "there were no facts to write about",
};

const PROVIDER: Record<string, string> = {
  gemini: "Gemini",
  openai: "OpenAI",
  anthropic: "Claude",
  deepseek: "DeepSeek",
};

const SOURCE: Record<string, string> = {
  rules: "Data rules",
  model: "Model",
  person: "You",
  cache: "Saved plan",
};

export const meaningLabel = (id: string) => lookup(MEANING, id);
export const personalLabel = (id: string | null | undefined) => lookup(PERSONAL, id);
export const flagLabel = (id: string) => lookup(FLAG, id);
export const minerLabel = (id: string) => lookup(MINER, id);
export const chartLabel = (id: string) => lookup(CHART, id);
export const featureLabel = (id: string) => lookup(FEATURE, id);
export const providerLabel = (id: string | null | undefined) => lookup(PROVIDER, id);
export const sourceLabel = (id: string) => lookup(SOURCE, id);
export const fallbackLabel = (id: string | null | undefined) => lookup(FALLBACK_REASON, id);
