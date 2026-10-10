// Contrato de la API de Community Lens.
//
// Estos tipos describen EXACTAMENTE lo que devuelve el backend en Python. No se confía en que
// coincidan: `npm run schema` genera web/src/api-schema.json a partir de este archivo y la prueba
// tests/test_contract.py valida las respuestas reales de la API contra ese esquema (sin permitir
// campos de más ni de menos). Si el backend cambia y este archivo no, esa prueba falla.
//
// Los tipos `…Request` son los cuerpos que envía el frontend. El backend declara los mismos campos
// (modelos de community_lens/api.py) y la misma prueba compara unos con otros.
export {};
