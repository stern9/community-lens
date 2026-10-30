export class ApiError extends Error {
    status;
    body;
    constructor(message, status, body) {
        super(message);
        this.status = status;
        this.body = body;
        this.name = "ApiError";
    }
}
/** Arma una query string omitiendo valores vacíos, null, undefined y false. */
export function qs(params) {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
        if (v === "" || v == null || v === false)
            continue;
        p.set(k, String(v));
    }
    return p.toString();
}
function isErrorBody(x) {
    return typeof x === "object" && x !== null;
}
async function request(path, init) {
    const res = await fetch(path, init);
    if (res.ok)
        return res;
    let body = null;
    let detail = res.statusText;
    try {
        const parsed = await res.json();
        if (isErrorBody(parsed)) {
            body = parsed;
            detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
        }
    }
    catch { /* respuesta sin cuerpo JSON */ }
    throw new ApiError(detail, res.status, body);
}
// El tipo T de cada respuesta está respaldado por la prueba de contrato (tests/test_contract.py),
// que valida las respuestas reales contra el esquema generado desde types.ts.
async function getJson(path) {
    return (await request(path)).json();
}
async function sendJson(path, body, method = "POST") {
    const res = await request(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return res.json();
}
const scopeQs = (s) => qs({ chat_id: s.chat_id, from: s.from, to: s.to });
export const getConfig = () => getJson("/api/config");
export const getChats = () => getJson("/api/chats");
export const updateChatTimezone = (chatId, timezone) => sendJson(`/api/chats/${chatId}`, { timezone, reinterpret_imports: true }, "PATCH");
export const deleteChat = async (chatId) => (await request(`/api/chats/${chatId}`, { method: "DELETE" })).json();
export const getChatImports = (chatId) => getJson(`/api/chats/${chatId}/imports`);
export const previewImport = async (file, filename, timezone, dateOrder) => (await request("/api/imports/preview?" + qs({ filename, timezone, date_order: dateOrder }), { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" } })).json();
export const commitImport = (req) => sendJson("/api/imports/commit", req);
export const getStats = (s) => getJson("/api/stats?" + scopeQs(s));
export const getAuthors = (s) => getJson("/api/authors?" + scopeQs(s));
export const mergeAuthor = (authorId, into) => sendJson(`/api/authors/${authorId}/merge`, { into });
export const unmergeAuthor = (authorId) => sendJson(`/api/authors/${authorId}/unmerge`, {});
export const search = (p) => getJson("/api/search?" + qs({ ...p }));
export const getResources = (s, kind, q, order, offset) => getJson("/api/resources?" + qs({ chat_id: s.chat_id, from: s.from, to: s.to, kind, q, order, limit: 40, offset }));
export const getStickers = (s) => getJson("/api/stickers?" + scopeQs(s));
/** Dirección de la imagen de un sticker; se usa directo en <img src>. */
export const stickerSrc = (sha256) => `/api/stickers/${sha256}`;
export const getContext = (messageId, before, after) => getJson(`/api/messages/${messageId}/context?` + qs({ before, after }));
/** `categories = null` vuelve a la clasificación automática. La predicción original nunca se modifica. */
export const setLabels = (messageId, categories) => sendJson(`/api/messages/${messageId}/labels`, { categories });
export const getCategorySummary = (s) => getJson("/api/classify/summary?" + scopeQs(s));
export const getCategoryMonthly = (s) => getJson("/api/classify/monthly?" + scopeQs(s));
export const getRuns = (chatId) => getJson("/api/classify/runs?" + qs({ chat_id: chatId }));
export const startRun = (req) => sendJson("/api/classify/runs", req);
export const activateRun = (runId) => sendJson(`/api/classify/runs/${runId}/activate`, {});
export const reportUrl = (s, format, includeAuthors, download = false) => "/api/report?" + qs({ chat_id: s.chat_id, from: s.from, to: s.to, format, include_authors: includeAuthors && "true", download: download && "true" });
export const getReportText = async (s, includeAuthors) => (await request(reportUrl(s, "txt", includeAuthors))).text();
/** `from` o `to` vacíos: el backend completa el rango por defecto (termina el último día con mensajes). */
export const previewNewspaper = (chatId, from, to, order, maxArticles) => getJson("/api/newspaper/preview?" + qs({ chat_id: chatId, from, to, order, max_articles: maxArticles }));
export const generateNewspaper = (req) => sendJson("/api/newspaper/editions", req);
export const getEditions = (chatId) => getJson("/api/newspaper/editions?" + qs({ chat_id: chatId }));
export const approveEdition = (id, req) => sendJson(`/api/newspaper/editions/${id}/approve`, req);
export const discardEdition = (id) => sendJson(`/api/newspaper/editions/${id}/discard`, {});
export const restoreEdition = (id) => sendJson(`/api/newspaper/editions/${id}/restore`, {});
/** `draft` fuerza la banda de borrador (para mandar a revisar una aprobada); sin él la decide el estado.
 *  `publicVersion` pide la versión que se publica: sin lecturas, cifras ni nombre del grupo. */
export const editionUrl = (id, download = false, draft = false, publicVersion = false) => {
    const q = qs({ download, borrador: draft ? 1 : null, publico: publicVersion ? 1 : null });
    return `/periodico/${id}` + (q ? "?" + q : "");
};
