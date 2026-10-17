import { createApp } from "vue";
import { QueryCache, QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import App from "./App.vue";
import { ApiError } from "./api";
import { clearSession } from "./auth";
import { createAppI18n } from "./i18n";
import { createAppRouter } from "./router";
import "./style.css";
import "@fontsource-variable/dm-sans/wght.css";
import "@fontsource-variable/manrope/wght.css";

const client = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: false, refetchOnWindowFocus: false },
  },
  queryCache: new QueryCache({
    onError: async (error: Error): Promise<void> => {
      if (error instanceof ApiError && error.status === 401) {
        await clearSession(client);
        await router.replace("/login");
      }
    },
  }),
});
const router = createAppRouter(client);
let locale: "en" | "cs" = "en";
try {
  if (localStorage.getItem("cartalytix-language") === "cs") locale = "cs";
} catch {
  /* Language persistence is optional in restricted browsers. */
}

const app = createApp(App)
  .use(createAppI18n(locale))
  .use(VueQueryPlugin, { queryClient: client })
  .use(router);
void router.isReady().then(() => app.mount("#app"));
