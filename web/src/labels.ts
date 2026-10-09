// Nombres legibles de los códigos que devuelve la API.
import type { ProviderName } from "./types.js";

export const PROVIDER: Readonly<Record<ProviderName, string>> = { rules: "reglas", jev: "Jev", openai_decisions: "OpenAI Decisions" };
export const SUBTYPE: Readonly<Record<string, string>> = {
  join: "entradas", leave: "salidas", security_code: "códigos de seguridad", group_change: "cambios del grupo",
  e2e_notice: "aviso de cifrado", generic_notice: "aviso genérico", other: "otros",
};
export const REVIEW: Readonly<Record<string, string>> = {
  aviso_no_reconocido: "aviso no reconocido", marcador_desconocido: "marcador desconocido del exportador",
  solape_ambiguo: "solape ambiguo entre exportaciones",
};
