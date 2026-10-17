import type { QueryClient } from "@tanstack/vue-query";
import { createRouter, createWebHistory } from "vue-router";
import { clearSession, sessionOptions } from "./auth";
import LoginView from "./views/LoginView.vue";
import OverviewView from "./views/OverviewView.vue";
import ReceiptsView from "./views/ReceiptsView.vue";
import ReceiptDetailView from "./views/ReceiptDetailView.vue";
import ConnectionsView from "./views/ConnectionsView.vue";

export function createAppRouter(client: QueryClient) {
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      { path: "/login", name: "login", component: LoginView },
      { path: "/", name: "overview", component: OverviewView },
      { path: "/receipts", name: "receipts", component: ReceiptsView },
      { path: "/receipts/:id", name: "receipt-detail", component: ReceiptDetailView },
      { path: "/connections", name: "connections", component: ConnectionsView },
      { path: "/:pathMatch(.*)*", redirect: "/" },
    ],
    scrollBehavior: () => ({ top: 0 }),
  });
  router.beforeEach(async (to) => {
    try {
      const user = await client.fetchQuery(sessionOptions);
      if (!user) {
        await clearSession(client);
        if (to.name !== "login") return { name: "login" };
      }
      if (user && to.name === "login") return { name: "overview" };
    } catch {
      if (to.name !== "login")
        return { name: "login", query: { reason: "unavailable" } };
    }
    return true;
  });
  return router;
}
