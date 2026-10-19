import type { Connection } from "./api";
import { providerLogos } from "./providerLogos";

const providerNames: Record<string, string> = { lidl: "Lidl", mixit: "Mixit", fermato: "Fermato", nejkafe: "Nejkafe", bolt: "Bolt Food", kaufland: "Kaufland" };
export const SHOPTET_SHOPS = ["fermato", "nejkafe"];
// Connector-generated adjustment rows are stored as slugs; source names stay as printed.
const sourceNameKeys: Record<string, string> = {
  shipping_and_fees: "shippingAndFees",
  discounts_and_credits: "discountsAndCredits",
};

export function providerName(code: string): string {
  return providerNames[code] ?? code;
}

export function accountName(connection: Pick<Connection, "name" | "account_label">): string {
  return connection.name || connection.account_label || "";
}

export function connectionLabel(connection: Pick<Connection, "provider" | "name" | "account_label">): string {
  const provider = providerName(connection.provider);
  const account = accountName(connection);
  return account ? `${provider} · ${account}` : provider;
}

export function sourceName(name: string, t: (key: string) => string): string {
  const key = sourceNameKeys[name];
  return key ? t(key) : name;
}

export function accountOptions(connections: Connection[], allLabel: string): Array<{ value: string; label: string; icon?: string }> {
  return [
    { value: "", label: allLabel },
    ...connections.map((connection) => ({ value: connection.id, label: connectionLabel(connection), icon: providerLogos[connection.provider] })),
  ];
}
