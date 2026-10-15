// Contrato de la API de Community Lens.
//
// Estos tipos describen EXACTAMENTE lo que devuelve el backend en Python. No se confía en que
// coincidan: `npm run schema` genera web/src/api-schema.json a partir de este archivo y la prueba
// tests/test_contract.py valida las respuestas reales de la API contra ese esquema (sin permitir
// campos de más ni de menos). Si el backend cambia y este archivo no, esa prueba falla.
//
// Los tipos `…Request` son los cuerpos que envía el frontend. El backend declara los mismos campos
// (modelos de community_lens/api.py) y la misma prueba compara unos con otros.

export type CategoryKey =
  | "pregunta"
  | "oferta_de_colaboracion"
  | "propuesta_de_proyecto"
  | "recurso_compartido"
  | "anuncio"
  | "conversacion_general";

export type DateOrder = "MDY" | "DMY" | "YMD";
export type RecordType = "message" | "system" | "unrecognized";
export type ProviderName = "rules" | "jev" | "openai_decisions";
export type RunStatus = "running" | "completed" | "partial" | "failed";

/** Fecha local "AAAA-MM-DD HH:MM:SS". */
export type LocalTimestamp = string;
/** Día "AAAA-MM-DD". */
export type IsoDay = string;
/** Mes "AAAA-MM". */
export type IsoMonth = string;

// ------------------------------------------------------------------ config --

export interface Category {
  key: CategoryKey;
  label: string;
  definition: string;
}

export interface ProviderInfo {
  name: ProviderName;
  label: string;
  available: boolean;
  external: boolean;
  model: string | null;
  reason: string | null;
  description: string;
  // Solo los informa el proveedor Jev:
  threshold?: number;
  context_messages?: number;
  max_messages?: number;
  score_meaning?: string;
  data_sent?: string;
}

export interface Config {
  default_timezone: string;
  host: string;
  port: number;
  loopback: boolean;
  taxonomy: { version: string; categories: Category[] };
  providers: ProviderInfo[];
}

// ------------------------------------------------------------------- chats --

export interface ChatRow {
  id: number;
  name: string;
  timezone: string;
  created_at: string;
  active_run_id: number | null;
}

export interface Chat extends ChatRow {
  first_record: LocalTimestamp | null;
  last_record: LocalTimestamp | null;
  records: number;
  participant_messages: number;
  flagged_for_review: number;
  imports: number;
}

/** Cambios a un grupo: solo se envía lo que cambia. */
export interface ChatUpdateRequest {
  name?: string;
  timezone?: string;
  reinterpret_imports?: boolean;
}

export interface DeleteChatResult {
  deleted: boolean;
  records_removed: number;
}

// ----------------------------------------------------------------- imports --

export interface ImportIssue {
  line_start: number;
  line_end: number;
  kind: "preamble" | "invalid_date";
  detail: string;
  excerpt: string;
}

export interface Diagnostics {
  lines_total: number;
  records_detected: number;
  participant_messages: number;
  system_notices: number;
  system_by_subtype: Record<string, number>;
  unrecognized: number;
  invalid_date_records: number;
  preamble_blocks: number;
  flagged_for_review: number;
  multiline_records: number;
  media_omitted_messages: number;
  media_by_kind: Record<string, number>;
  deleted_messages: number;
  edited_messages: number;
  distinct_author_labels: number;
  period_start: LocalTimestamp | null;
  period_end: LocalTimestamp | null;
  format_variant: "bracket" | "dash" | null;
  clock: "12h" | "24h" | null;
  has_seconds: boolean;
  warnings: string[];
  issues: ImportIssue[];
}

export interface Reconciliation {
  matched_existing: number;
  added: number;
  only_in_previous: number;
  ambiguous_pairs: number;
  window_existing: number;
  precision: "segundo" | "minuto";
}

export interface DateEvidence {
  /** Presente solo cuando el orden es YMD (año de 4 dígitos al inicio). */
  reason?: string;
  first_field_gt12?: number;
  second_field_gt12?: number;
  example_line_first?: number | null;
  example_line_second?: number | null;
  example_raw?: string;
  backward_steps?: { MDY: number | null; DMY: number | null };
  /** Pista débil por orden cronológico; solo cuando el estado es "ambiguous". */
  hint?: DateOrder | null;
}

export interface DateDetection {
  status: "detected" | "ambiguous" | "conflict" | "empty";
  order: DateOrder | null;
  evidence: DateEvidence;
}

export interface ImportCandidate {
  chat_id: number;
  name: string;
  same_file_import_id: number | null;
  name_matches: boolean;
  existing_in_window: number;
  new_in_window: number;
  matched: number;
  ratio: number | null;
}

export interface ImportRecommendation {
  action: "new" | "update" | "duplicate";
  chat_id: number | null;
  reason: string;
}

export interface ImportPreview {
  token: string;
  file_name: string;
  file_bytes: number;
  suggested_name: string;
  timezone: string;
  date_detection: DateDetection;
  date_order: DateOrder | null;
  date_order_source: "user" | "detected" | null;
  needs_date_order: boolean;
  /** null mientras falte elegir el orden de fecha. */
  diagnostics: Diagnostics | null;
  candidates: ImportCandidate[];
  recommendation: ImportRecommendation | null;
  date_order_warning?: string;
}

export interface ImportCommitImported {
  status: "imported";
  chat_id: number;
  import_id: number;
  diagnostics: Diagnostics;
  reconciliation: Reconciliation;
}

export interface ImportCommitDuplicate {
  status: "duplicate";
  chat_id: number;
  import_id: number;
  message: string;
}

export type ImportCommitResult = ImportCommitImported | ImportCommitDuplicate;

export interface ImportCommitRequest {
  token: string;
  file_name: string;
  chat_id: number | null;
  chat_name: string;
  timezone: string;
  date_order: DateOrder;
  confirm_low_overlap: boolean;
}

export interface ImportRecord {
  id: number;
  chat_id: number;
  file_name: string;
  file_sha256: string;
  file_bytes: number;
  imported_at: string;
  date_order: DateOrder;
  date_order_source: "user" | "detected";
  clock: "12h" | "24h" | null;
  has_seconds: number;
  timezone: string;
  format_variant: "bracket" | "dash" | null;
  diagnostics: Diagnostics;
  reconciliation: Reconciliation;
  issues: ImportIssue[];
}

// ------------------------------------------------------------ estadísticas --

export interface Totals {
  records: number;
  participant_messages: number;
  system_notices: number;
  unrecognized: number;
  flagged_for_review: number;
  messages_with_text: number;
  active_authors: number;
  author_labels: number;
  attributed_messages: number;
  active_days: number;
  calendar_days: number;
  conversation_days: number;
  silent_days: number;
  messages_with_links: number;
  total_links: number;
  distinct_domains: number;
  media_omitted_messages: number;
  deleted_messages: number;
  edited_messages: number;
  polls: number;
}

export interface TopShare {
  n: number;
  messages: number;
  denominator: number;
  share: number | null;
}

export interface CurvePoint {
  rank: number;
  messages: number;
  share: number | null;
}

export interface Concentration {
  top5: TopShare;
  top10: TopShare;
  curve: CurvePoint[];
  half_rank: number | null;
  denominator: number;
}

export interface Averages {
  per_day: number | null;
  per_day_denominator: number;
  per_active_day: number | null;
  per_month: number | null;
  per_month_denominator: number;
  per_month_messages: number;
  per_hour: number | null;
  per_hour_denominator: number;
  /** Hora del día (0–23) con más mensajes; null si no hay mensajes. */
  peak_hour: number | null;
  peak_hour_messages: number;
  peak_hour_per_day: number | null;
}

export interface SilentDay {
  day: IsoDay;
  /** 0 = domingo … 6 = sábado. */
  weekday: number;
}

export interface Conversation {
  first_day: IsoDay | null;
  last_day: IsoDay | null;
  days: number;
  silent_days: SilentDay[];
}

export interface DailyPoint {
  day: IsoDay;
  messages: number;
  authors: number;
}

export interface MonthlyPoint {
  month: IsoMonth;
  messages: number;
  authors: number;
  active_days: number;
  partial: boolean;
  covered_from: IsoDay;
  covered_to: IsoDay;
  days_in_month: number;
  days_covered: number;
}

export interface WeekdayHourCell {
  /** 0 = domingo … 6 = sábado. */
  weekday: number;
  hour: number;
  messages: number;
}

export interface DomainRow {
  domain: string;
  messages: number;
  links: number;
}

export interface Definitions {
  participant_messages: string;
  system_notices: string;
  active_authors: string;
  active_days: string;
  averages: string;
  conversation_days: string;
  silent_days: string;
  calendar_days: string;
  concentration: string;
  messages_with_links: string;
  total_links: string;
  domains: string;
  media_omitted: string;
  deleted: string;
  partial_month: string;
  weekday_hour: string;
  concentration_curve: string;
  silent_members: string;
}

export interface Stats {
  chat: { id: number; name: string; timezone: string };
  period: {
    from: IsoDay | null;
    to: IsoDay | null;
    first_message: LocalTimestamp | null;
    last_message: LocalTimestamp | null;
    first_day: IsoDay | null;
    last_day: IsoDay | null;
    calendar_days: number;
  };
  coverage: { first_record: LocalTimestamp | null; last_record: LocalTimestamp | null; records: number; note: string };
  totals: Totals;
  averages: Averages;
  conversation: Conversation;
  concentration: Concentration;
  daily: DailyPoint[];
  monthly: MonthlyPoint[];
  /** 7 valores; índice 0 = domingo. */
  weekday: number[];
  /** 24 valores; índice = hora. */
  hour: number[];
  weekday_hour: WeekdayHourCell[];
  domains: DomainRow[];
  media_by_kind: Record<string, number>;
  system_by_subtype: Record<string, number>;
  definitions: Definitions;
}

export type AuthorKind = "phone" | "profile_name" | "contact_name" | "self";

export interface AuthorAlias {
  id: number;
  label: string;
  kind: AuthorKind;
}

export interface AuthorRow {
  id: number;
  label: string;
  kind: AuthorKind;
  messages: number;
  share: number | null;
  active_days: number;
  first_day: IsoDay;
  last_day: IsoDay;
  aliases: AuthorAlias[];
}

export interface MergeAuthorRequest {
  /** Identificador del alias que queda como principal. */
  into: number;
}

export interface AuthorsResult {
  denominator: number;
  authors: AuthorRow[];
  note: string;
}

// ------------------------------------------------------ búsqueda / mensajes --

export interface AutoLabel {
  category: CategoryKey;
  assigned: boolean;
  /** Solo si el proveedor devuelve puntuación (Jev: valor "noul" 0–1). */
  score: number | null;
  provider: ProviderName;
  model: string;
  run_id: number;
  evidence: {
    /** Reglas disparadas por categoría (proveedor de reglas). */
    rules?: Record<string, string[]>;
    threshold?: number;
    context_sent?: number;
  } | null;
}

export interface HumanLabel {
  categories: CategoryKey[];
  note: string | null;
  created_at: string;
}

export interface MessageSource {
  file_name: string;
  import_id: number;
  line_start: number;
  line_end: number;
}

export interface Message {
  id: number;
  uid: string;
  chat_id: number;
  chat_name: string;
  seq: number;
  author_id: number | null;
  author_raw: string | null;
  author_label: string | null;
  canonical_label: string | null;
  ts_local: LocalTimestamp;
  ts_written: LocalTimestamp;
  ts_utc: string;
  record_type: RecordType;
  subtype: string;
  text: string;
  media_kind: string | null;
  /** Banderas 0/1 tal como salen de SQLite. */
  is_deleted: number;
  is_edited: number;
  is_poll: number;
  link_count: number;
  needs_review: number;
  review_reason: string | null;
  ref: string;
  auto: AutoLabel[];
  human: HumanLabel | null;
  effective: CategoryKey[];
  effective_source: "human" | "auto" | null;
  sources: MessageSource[];
  /** Solo en búsquedas con texto; delimita coincidencias con U+0001 … U+0002. */
  snippet?: string;
}

export interface SearchResult {
  total: number;
  limit: number;
  offset: number;
  fts_query: string | null;
  items: Message[];
}

export interface ContextResult {
  message_id: number;
  chat_id: number;
  items: Message[];
  note: string;
}

/** `categories = null` vuelve a la clasificación automática. */
export interface LabelsRequest {
  categories: CategoryKey[] | null;
  note?: string;
}

export interface LabelsResult {
  ok: boolean;
  categories: CategoryKey[] | null;
}

// ----------------------------------------------------------------- recursos --

export interface DocumentVersion {
  /** Mensaje más reciente en que se compartió este archivo; abre la vista de contexto. */
  message_id: number;
  /** Nombre del archivo tal como figura en la exportación. */
  name: string;
  /** Número de versión leído del nombre ("1.6"); null si el nombre no trae versión. */
  version: string | null;
  ts_local: LocalTimestamp;
  author: string | null;
  times_shared: number;
  first_shared: LocalTimestamp;
}

export interface DocumentResource {
  kind: "document";
  key: string;
  title: string;
  shares: number;
  first_shared: LocalTimestamp;
  last_shared: LocalTimestamp;
  sharers: string[];
  /** Versión con el número más alto (o el envío más reciente si no hay números). */
  latest: DocumentVersion;
  /** De la más nueva a la más vieja. */
  versions: DocumentVersion[];
  has_versions: boolean;
  /** false si lo último que se envió fue una versión anterior a la más alta. */
  latest_is_most_recent: boolean;
}

export interface LinkResource {
  kind: "link";
  key: string;
  url: string;
  domain: string;
  /** Mensajes distintos que incluyen el enlace. */
  shares: number;
  first_shared: LocalTimestamp;
  last_shared: LocalTimestamp;
  /** Mensaje más reciente que lo incluye. */
  message_id: number;
  author: string | null;
  sharers: string[];
}

export interface ResourceLibrary {
  kind: "document" | "link";
  total: number;
  limit: number;
  offset: number;
  items: (DocumentResource | LinkResource)[];
  counts: { documents: number; document_shares: number; unnamed_documents: number; links: number; link_shares: number };
  note: string;
}

// ---------------------------------------------------------------- periódico --

export type NewspaperLinkKind = "article" | "social" | "tool";

/** Un enlace compartido en el rango, ya clasificado por regla fija (no por un modelo). */
export interface NewspaperLink {
  url: string;
  domain: string;
  /** Día local en que se compartió por última vez. */
  shared: IsoDay;
  shares: number;
  kind: NewspaperLinkKind;
}

/** Proveedor de modelo de lenguaje configurado en .env. La key nunca viaja al navegador. */
export interface LlmProvider {
  name: "anthropic" | "openai" | "gemini";
  label: string;
  model: string;
}

export interface NewspaperFacts {
  messages: number;
  participants: number;
  messages_with_links: number;
  busiest_day: IsoDay | null;
  busiest_day_messages: number;
}

/** Qué entraría en una edición y qué saldría de la máquina. Calcularla no envía nada. */
export interface NewspaperPreview {
  /** Rango ya resuelto: si la consulta no traía fechas, el rango por defecto. */
  from: IsoDay;
  to: IsoDay;
  /** Último día con mensajes de participantes: desde ahí cuentan "última semana" y los demás atajos. */
  last_day: IsoDay | null;
  facts: NewspaperFacts;
  links: { article: NewspaperLink[]; social: NewspaperLink[]; tool: NewspaperLink[] };
  links_total: { article: number; social: number; tool: number };
  links_skipped: number;
  providers: LlmProvider[];
  /** Proveedores con key pero sin nombre de modelo en .env. */
  missing_model: string[];
  transcript: { messages: number; characters: number };
}

/** Lo que salió de la máquina al generar una edición. */
export interface NewspaperSent {
  pages_to: string;
  /** Modelos que recibieron el texto de las páginas y fallaron antes del que redactó. */
  pages_tried: string[];
  pages: number;
  chat_messages: number;
  chat_to: string[];
}

export interface NewspaperRequest {
  chat_id: number;
  from: IsoDay;
  to: IsoDay;
  editor: string;
  readers: string[];
  consent_chat: boolean;
  order: "recent" | "shared";
  max_articles: number;
}

export interface NewspaperResult {
  id: number;
  /** Toda edición recién generada queda como borrador: entra al sitio cuando alguien la aprueba. */
  status: "borrador";
  sent: NewspaperSent;
  warnings: string[];
  articles: number;
  socials: number;
  tools: number;
  unread: number;
  readings: number;
}

/** Ciclo de vida de una edición. "heredada": guardada antes de que existiera la aprobación; se publica sin hash. */
export type NewspaperEditionStatus = "borrador" | "aprobada" | "descartada" | "heredada";

/** Registro de la última aprobación. Descartar y recuperar no lo borran; reaprobar lo reemplaza. */
export interface NewspaperApproval {
  /** "sha256:<hex>" de los bytes que se publican. */
  hash: string;
  by: string;
  via: string;
  /** Instante UTC, "AAAA-MM-DDTHH:MM:SSZ". */
  at: string;
  to: string | null;
}

export interface NewspaperEdition {
  id: number;
  from: IsoDay;
  to: IsoDay;
  /** Instante UTC, "AAAA-MM-DDTHH:MM:SSZ". */
  created_at: string;
  editor: LlmProvider;
  sent: NewspaperSent;
  status: NewspaperEditionStatus;
  approval: NewspaperApproval | null;
  /** Si los bytes aprobados siguen coincidiendo con su hash; null si no está aprobada. */
  hash_ok: boolean | null;
  /** Si los bytes aprobados son los que dibujaría hoy la plantilla (si no, reaprobar los actualiza); null si no está aprobada. */
  up_to_date: boolean | null;
  /** Si publicar ahora la escribiría en el sitio. */
  in_site: boolean;
}

export interface ApproveRequest {
  /** Quién aprobó y por qué canal: obligatorios, hasta 200 caracteres. */
  by: string;
  via: string;
  /** Adónde se publica (opcional). */
  to?: string | null;
}

// ------------------------------------------------------------ clasificación --

export interface RunConfig {
  eligible_messages?: number;
  context_window_minutes?: number;
  context_messages?: number;
  scores?: boolean;
  endpoint?: string;
  requested_model?: string;
  threshold?: number;
  max_messages?: number;
  question_type?: string;
  score_kind?: string;
  max_state_chars?: number;
}

export interface Run {
  id: number;
  chat_id: number;
  provider: ProviderName;
  model: string;
  taxonomy_version: string;
  config: RunConfig;
  date_from: IsoDay | null;
  date_to: IsoDay | null;
  started_at: string;
  finished_at: string | null;
  status: RunStatus;
  n_messages: number;
  n_errors: number;
  error: string | null;
}

export interface RunListItem extends Run {
  /** true en la ejecución que alimenta las categorías del chat. */
  active: boolean;
}

export type CategoryCounts = Record<CategoryKey, number>;

export interface CategorySummary {
  run: Run | null;
  participant_messages: number;
  classified_messages: number;
  corrected_messages: number;
  auto: CategoryCounts;
  human: CategoryCounts;
  effective: CategoryCounts;
  note: string;
}

export interface CategoryMonth {
  month: IsoMonth;
  participant_messages: number;
  classified: number;
  counts: CategoryCounts;
  shares: Record<CategoryKey, number | null>;
  partial: boolean;
}

export interface CategoryMonthly {
  run: Run | null;
  months: CategoryMonth[];
  /** Ausente cuando todavía no hay ninguna ejecución. */
  note?: string;
}

export interface RunStartRequest {
  chat_id: number;
  /** "" = sin límite. */
  from: IsoDay | "";
  to: IsoDay | "";
  provider: ProviderName;
  consent: boolean;
}

export interface RunStartResult {
  run_id: number;
  status: "completed" | "partial";
  provider: ProviderName;
  model: string;
  classified: number;
  errors: number;
  eligible: number;
  truncated: boolean;
  /** false si la ejecución quedó guardada pero no reemplazó a la vigente. */
  activated: boolean;
  active_run_id: number;
}

export interface ActivateRunResult {
  ok: boolean;
  active_run_id: number;
}

export interface OkResult {
  ok: boolean;
}

// ------------------------------------------------------------------ errores --

/** Cuerpo de error: FastAPI usa `detail`; los errores de importación agregan `error` y datos. */
export interface ApiErrorBody {
  detail?: unknown;
  error?: string;
  overlap?: { existing_in_window: number; new_in_window: number; matched: number; ratio: number | null };
}
