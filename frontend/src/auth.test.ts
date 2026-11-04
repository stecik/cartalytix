import { describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/vue-query";
import { authenticate, signOut } from "./auth";

describe("account session", () => {
  it("sends credentials only in a same-origin POST and stores the authenticated user", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ id: "1", email: "owner@example.com" })),
      );
    vi.stubGlobal("fetch", fetcher);
    const client = new QueryClient();
    await authenticate(client, "owner@example.com", "secret", true);
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/auth/login",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        body: JSON.stringify({
          email: "owner@example.com",
          password: "secret",
          remember: true,
        }),
      }),
    );
    expect(client.getQueryData(["session"])).toEqual({
      id: "1",
      email: "owner@example.com",
    });
  });

  it("clears private query data after logout", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(new Response(null, { status: 204 })),
    );
    const client = new QueryClient();
    client.setQueryData(["receipts"], { items: [{ id: "private" }] });
    await signOut(client);
    expect(client.getQueryData(["receipts"])).toBeUndefined();
  });

  it("clears private data when logging out an already expired session", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 401 })));
    const client = new QueryClient();
    client.setQueryData(["receipts"], { items: [{ id: "private" }] });
    await signOut(client);
    expect(client.getQueryData(["receipts"])).toBeUndefined();
  });
});
