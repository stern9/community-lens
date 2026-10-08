// Observable Plot y D3 se cargan como scripts clásicos desde web/vendor (sin CDN ni empaquetador),
// así que en tiempo de ejecución son variables globales. Los tipos vienen de los paquetes npm,
// que solo se instalan para desarrollar.
declare const Plot: typeof import("@observablehq/plot");
declare const d3: typeof import("d3");
