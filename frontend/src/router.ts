import type { QueryClient } from "@tanstack/vue-query";
import { createRouter, createWebHistory } from "vue-router";
import type { RouteLocationRaw } from "vue-router";
import type { User } from "./api";
import { clearSession, sessionOptions } from "./auth";
import LoginView from "./views/LoginView.vue";
import OverviewView from "./views/OverviewView.vue";
import ReceiptsView from "./views/ReceiptsView.vue";
import ReceiptDetailView from "./views/ReceiptDetailView.vue";
import ItemsView from "./views/ItemsView.vue";
import CategoriesView from "./views/CategoriesView.vue";
import ItemDetailView from "./views/ItemDetailView.vue";
import CalculatorView from "./views/CalculatorView.vue";
import ConnectionsView from "./views/ConnectionsView.vue";
import SettingsView from "./views/SettingsView.vue";
import OAuthConsentView from "./views/OAuthConsentView.vue";
import ChangeCredentialsView from "./views/ChangeCredentialsView.vue";
import AccountsView from "./views/AccountsView.vue";

/** Administrators only manage accounts; everyone must replace issued credentials first. */
export function accountRedirect(user: User, name: unknown): RouteLocationRaw | null {
  if (user.must_change_password)
    return name === "change-credentials" ? null : { name: "change-credentials" };
  if (name === "change-credentials" || name === "login")
    return { name: user.is_admin ? "accounts" : "overview" };
  if (user.is_admin && name !== "accounts" && name !== "settings") return { name: "accounts" };
  if (!user.is_admin && name === "accounts") return { name: "overview" };
  return null;
}

export function createAppRouter(client: QueryClient) {
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      { path: "/login", name: "login", component: LoginView },
      { path: "/", name: "overview", component: OverviewView },
      { path: "/receipts", name: "receipts", component: ReceiptsView },
      { path: "/receipts/:id", name: "receipt-detail", component: ReceiptDetailView },
      { path: "/items", name: "items", component: ItemsView },
      { path: "/categories", name: "categories", component: CategoriesView },
      { path: "/items/detail", name: "item-detail", component: ItemDetailView },
      { path: "/calculator", name: "calculator", component: CalculatorView },
      { path: "/connections", name: "connections", component: ConnectionsView },
      { path: "/settings", name: "settings", component: SettingsView },
      { path: "/accounts", name: "accounts", component: AccountsView },
      { path: "/change-credentials", name: "change-credentials", component: ChangeCredentialsView },
      { path: "/oauth/consent", name: "oauth-consent", component: OAuthConsentView },
      { path: "/:pathMatch(.*)*", redirect: "/" },
    ],
    scrollBehavior: () => ({ top: 0 }),
  });
  router.beforeEach(async (to) => {
    try {
      const user = await client.fetchQuery(sessionOptions);
      if (!user) {
        await clearSession(client);
        if (to.name !== "login")
          return { name: "login", query: to.fullPath === "/" ? {} : { redirect: to.fullPath } };
      }
      if (user) return accountRedirect(user, to.name) ?? true;
    } catch {
      if (to.name !== "login")
        return { name: "login", query: { reason: "unavailable" } };
    }
    return true;
  });
  return router;
}
