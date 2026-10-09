// Estado compartido: qué grupo y período se están viendo, y lo que se carga una sola vez.
// Es el único lugar con datos mutables que comparten las vistas.
import * as api from "./api.js";
export const state = { chats: [], chatId: null, from: "", to: "", config: null, authors: [] };
/** Grupo y rango de fechas elegidos en la barra superior. */
export const scope = () => ({ chat_id: state.chatId, from: state.from, to: state.to });
export function config() {
    if (!state.config)
        throw new Error("La configuración todavía no se cargó");
    return state.config;
}
export const categories = () => config().taxonomy.categories;
export const catLabel = (k) => categories().find((c) => c.key === k)?.label ?? k;
/** Recarga la lista de grupos; si el elegido ya no existe, pasa al primero. */
export async function loadChats() {
    state.chats = await api.getChats();
    if (!state.chats.some((c) => c.id === state.chatId))
        state.chatId = state.chats[0]?.id ?? null;
}
