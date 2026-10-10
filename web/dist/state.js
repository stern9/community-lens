// Estado compartido: qué grupo y período se están viendo, y lo que se guarda en memoria para no pedirlo de nuevo.
// Es el único lugar con datos mutables que comparten las vistas, y el único que decide cuándo lo guardado deja
// de valer: las vistas eligen grupo y período con selectChat() y setPeriod(), y piden los datos con las
// funciones de abajo; ninguna los asigna a mano.
import * as api from "./api.js";
export const state = { chats: [], chatId: null, from: "", to: "", config: null };
/** Lo guardado del grupo activo. Se descarta entero cuando cambia el grupo o cambian sus datos. */
let cached = null;
/** Grupo y rango de fechas elegidos en la barra superior. */
export const scope = () => ({ chat_id: state.chatId, from: state.from, to: state.to });
export function config() {
    if (!state.config)
        throw new Error("La configuración todavía no se cargó");
    return state.config;
}
export const categories = () => config().taxonomy.categories;
export const catLabel = (k) => categories().find((c) => c.key === k)?.label ?? k;
/** Elegir grupo: nada de lo guardado para el anterior sobrevive. */
export function selectChat(chatId) {
    if (chatId !== state.chatId)
        cached = null;
    state.chatId = chatId;
}
/** Cambiar el período. Lo guardado es de todo el historial del grupo, así que no depende de las fechas. */
export function setPeriod(from, to) {
    state.from = from;
    state.to = to;
}
/** Recarga la lista de grupos (después de importar o eliminar); si el elegido ya no existe, pasa al primero. */
export async function loadChats() {
    state.chats = await api.getChats();
    cached = null;
    if (!state.chats.some((c) => c.id === state.chatId))
        state.chatId = state.chats[0]?.id ?? null;
}
/** Autores del grupo activo en TODO su historial (no en el período elegido): los que ofrece el filtro de Explorar. */
export async function chatAuthors() {
    const chatId = state.chatId;
    if (chatId == null)
        return [];
    if (cached?.chatId !== chatId) {
        const mine = { chatId, authors: api.getAuthors({ chat_id: chatId, from: "", to: "" }).then((r) => r.authors) };
        cached = mine;
        mine.authors.catch(() => { if (cached === mine)
            cached = null; });
    }
    return cached.authors;
}
/** Unir o separar alias cambia la lista de autores: pasan por acá para que lo guardado no quede viejo. */
export async function mergeAuthor(authorId, into) {
    await api.mergeAuthor(authorId, into);
    cached = null;
}
export async function unmergeAuthor(authorId) {
    await api.unmergeAuthor(authorId);
    cached = null;
}
