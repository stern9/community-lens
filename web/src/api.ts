// Cliente de la API local. Cada función fija la ruta junto con su tipo de respuesta, de modo que
// el resto del frontend no puede emparejar un endpoint con un tipo equivocado.
import type {
  ActivateRunResult, ApiErrorBody, AuthorsResult, CategoryKey, CategoryMonthly, CategorySummary, Chat, ChatRow, Config,
  ContextResult, DateOrder, DeleteChatResult, ImportCommitRequest, ImportCommitResult, ImportPreview, ImportRecord,
  LabelsResult, OkResult, ProviderName, ResourceLibrary, RunListItem, RunStartResult, SearchResult, Stats,
} from "./types.js";

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly body: ApiErrorBody | null) {
    super(message);
    this.name = "ApiError";
  }
}

type QueryValue = string | number | boolean | null | undefined;

/** Arma una query string omitiendo valores vacíos, null, undefined y false. */
export function qs(params: Readonly<Record<string, QueryValue>>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === "" || v == null || v === false) continue;
    p.set(k, String(v));
  }
  return p.toString();
}

function isErrorBody(x: unknown): x is ApiErrorBody {
  return typeof x === "object" && x !== null;
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(path, init);
  if (res.ok) return res;
  let body: ApiErrorBody | null = null;
  let detail = res.statusText;
  try {
    const parsed: unknown = await res.json();
    if (isErrorBody(parsed)) {
      body = parsed;
      detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
    }
  } catch { /* respuesta sin cuerpo JSON */ }
  throw new ApiError(detail, res.status, body);
}

// El tipo T de cada respuesta está respaldado por la prueba de contrato (tests/test_contract.py),
// que valida las respuestas reales contra el esquema generado desde types.ts.
async function getJson<T>(path: string): Promise<T> {
  return (await request(path)).json() as Promise<T>;
}
async function sendJson<T>(path: string, body: unknown, method: "POST" | "PATCH" = "POST"): Promise<T> {
  const res = await request(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return res.json() as Promise<T>;
}

export interface Scope {
  chat_id: number | null;
  from: string;
  to: string;
}
const scopeQs = (s: Scope): string => qs({ chat_id: s.chat_id, from: s.from, to: s.to });

export interface SearchParams {
  chat_id?: number | null;
  from?: string;
  to?: string;
  q?: string;
  author_id?: string;
  category?: string;
  record_type?: string;
  has_link?: boolean;
  has_media?: boolean;
  review_only?: boolean;
  human_only?: boolean;
  order?: string;
  limit?: number;
  offset?: number;
}

export const getConfig = (): Promise<Config> => getJson("/api/config");
export const getChats = (): Promise<Chat[]> => getJson("/api/chats");
export const updateChatTimezone = (chatId: number, timezone: string): Promise<ChatRow> =>
  sendJson(`/api/chats/${chatId}`, { timezone, reinterpret_imports: true }, "PATCH");
export const deleteChat = async (chatId: number): Promise<DeleteChatResult> =>
  (await request(`/api/chats/${chatId}`, { method: "DELETE" })).json() as Promise<DeleteChatResult>;
export const getChatImports = (chatId: number): Promise<ImportRecord[]> => getJson(`/api/chats/${chatId}/imports`);

export const previewImport = async (file: ArrayBuffer, filename: string, timezone: string, dateOrder: DateOrder | ""): Promise<ImportPreview> =>
  (await request("/api/imports/preview?" + qs({ filename, timezone, date_order: dateOrder }),
    { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" } })).json() as Promise<ImportPreview>;
export const commitImport = (req: ImportCommitRequest): Promise<ImportCommitResult> => sendJson("/api/imports/commit", req);

export const getStats = (s: Scope): Promise<Stats> => getJson("/api/stats?" + scopeQs(s));
export const getAuthors = (s: Scope): Promise<AuthorsResult> => getJson("/api/authors?" + scopeQs(s));
export const mergeAuthor = (authorId: number, into: number): Promise<OkResult> => sendJson(`/api/authors/${authorId}/merge`, { into });
export const unmergeAuthor = (authorId: number): Promise<OkResult> => sendJson(`/api/authors/${authorId}/unmerge`, {});

export const search = (p: SearchParams): Promise<SearchResult> => getJson("/api/search?" + qs({ ...p }));
export const getResources = (s: Scope, kind: "document" | "link", q: string, order: "recent" | "shared", offset: number): Promise<ResourceLibrary> =>
  getJson("/api/resources?" + qs({ chat_id: s.chat_id, from: s.from, to: s.to, kind, q, order, limit: 40, offset }));
export const getContext = (messageId: number, before: number, after: number): Promise<ContextResult> =>
  getJson(`/api/messages/${messageId}/context?` + qs({ before, after }));
/** `categories = null` vuelve a la clasificación automática. La predicción original nunca se modifica. */
export const setLabels = (messageId: number, categories: CategoryKey[] | null): Promise<LabelsResult> =>
  sendJson(`/api/messages/${messageId}/labels`, { categories });

export const getCategorySummary = (s: Scope): Promise<CategorySummary> => getJson("/api/classify/summary?" + scopeQs(s));
export const getCategoryMonthly = (s: Scope): Promise<CategoryMonthly> => getJson("/api/classify/monthly?" + scopeQs(s));
export const getRuns = (chatId: number): Promise<RunListItem[]> => getJson("/api/classify/runs?" + qs({ chat_id: chatId }));
export const startRun = (s: Scope, provider: ProviderName, consent: boolean): Promise<RunStartResult> =>
  sendJson("/api/classify/runs", { chat_id: s.chat_id, from: s.from, to: s.to, provider, consent });
export const activateRun = (runId: number): Promise<ActivateRunResult> => sendJson(`/api/classify/runs/${runId}/activate`, {});

export const reportUrl = (s: Scope, format: "html" | "txt", includeAuthors: boolean, download = false): string =>
  "/api/report?" + qs({ chat_id: s.chat_id, from: s.from, to: s.to, format, include_authors: includeAuthors && "true", download: download && "true" });
export const getReportText = async (s: Scope, includeAuthors: boolean): Promise<string> =>
  (await request(reportUrl(s, "txt", includeAuthors))).text();
