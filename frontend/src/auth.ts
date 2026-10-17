import type { QueryClient } from "@tanstack/vue-query";
import { ApiError, request } from "./api";
import type { User } from "./api";

export const sessionOptions = {
  queryKey: ["session"],
  queryFn: async ({
    signal,
  }: {
    signal: AbortSignal;
  }): Promise<User | null> => {
    try {
      return await request<User>("/auth/me", { signal });
    } catch (error: unknown) {
      if (error instanceof ApiError && error.status === 401) return null;
      throw error;
    }
  },
  staleTime: 30_000,
  retry: false,
};

export async function authenticate(
  client: QueryClient,
  email: string,
  password: string,
): Promise<User> {
  const user = await request<User>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  client.clear();
  client.setQueryData(sessionOptions.queryKey, user);
  return user;
}

export async function signOut(client: QueryClient): Promise<void> {
  try {
    await request<void>("/auth/logout", { method: "POST" });
  } catch (error: unknown) {
    if (!(error instanceof ApiError && error.status === 401)) throw error;
  }
  await clearSession(client);
}

export async function clearSession(client: QueryClient): Promise<void> {
  await client.cancelQueries();
  client.clear();
  client.setQueryData(sessionOptions.queryKey, null);
}
