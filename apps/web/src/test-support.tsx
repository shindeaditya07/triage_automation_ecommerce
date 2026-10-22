import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Routes } from "react-router-dom";
import { vi } from "vitest";

import { createQueryClient } from "./api/queryClient";

export type Reply = { status?: number; body?: unknown };
export type Routing = (method: string, path: string, search: URLSearchParams) => Reply | undefined;

/** Replace `fetch` with a function that answers from `routing`, and record what was asked. */
export function stubApi(routing: Routing) {
  const seen: { method: string; path: string; search: URLSearchParams }[] = [];
  vi.stubGlobal("fetch", async (input: Request) => {
    const url = new URL(input.url);
    seen.push({ method: input.method, path: url.pathname, search: url.searchParams });
    const reply = routing(input.method, url.pathname, url.searchParams) ?? { status: 404 };
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {
      status: reply.status ?? 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  return seen;
}

export function renderAt(path: string, routes: ReactElement) {
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({
    queries: { retry: false, refetchInterval: false },
    mutations: { retry: false },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>{routes}</Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
