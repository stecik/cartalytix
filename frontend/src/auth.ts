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
  remember = false,
): Promise<User> {
  const user = await request<User>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password, remember }),
  });
  client.clear();
  client.setQueryData(sessionOptions.queryKey, user);
  return user;
}

export interface CredentialsChange {
  /** Omitted for the forced change right after signing in with an issued password. */
  current_password?: string;
  /** Omitted when only the e-mail changes. */
  new_password?: string;
  email?: string;
}

/** Mirrors the API policy: 8+ characters with uppercase, lowercase and a special character. */
export function isStrongPassword(value: string): boolean {
  return value.length >= 8 && value.length <= 1024
    && /\p{Lu}/u.test(value) && /\p{Ll}/u.test(value) && /[^\p{L}\p{N}]/u.test(value);
}

export async function changeCredentials(
  client: QueryClient,
  change: CredentialsChange,
): Promise<User> {
  const user = await request<User>("/auth/credentials", {
    method: "POST",
    body: JSON.stringify(change),
  });
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

/** In-app path to return to after sign-in; anything else falls back to the overview. */
export function returnPath(value: unknown): string {
  return typeof value === "string" && /^\/(?![/\\])/.test(value) ? value : "/";
}

export async function clearSession(client: QueryClient): Promise<void> {
  await client.cancelQueries();
  client.clear();
  client.setQueryData(sessionOptions.queryKey, null);
}
