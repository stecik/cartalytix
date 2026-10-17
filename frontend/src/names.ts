import type { Connection } from "./api";

const providerNames: Record<string, string> = { lidl: "Lidl", mixit: "Mixit", fermato: "Fermato" };
// Connector-generated adjustment rows are stored in English; source names stay as printed.
const sourceNameKeys: Record<string, string> = {
  "Shipping and fees": "shippingAndFees",
  "Discounts and credits": "discountsAndCredits",
};

export function providerName(code: string): string {
  return providerNames[code] ?? code;
}

export function connectionLabel(connection: Pick<Connection, "provider" | "name">): string {
  const provider = providerName(connection.provider);
  return connection.name ? `${provider} · ${connection.name}` : provider;
}

export function sourceName(name: string, t: (key: string) => string): string {
  const key = sourceNameKeys[name];
  return key ? t(key) : name;
}
