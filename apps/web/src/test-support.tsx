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
  const seen: { method: string; path: string; search: URLSearchParams; body: string }[] = [];
  vi.stubGlobal("fetch", async (input: Request) => {
    const url = new URL(input.url);
    seen.push({
      method: input.method,
      path: url.pathname,
      search: url.searchParams,
      body: await input.clone().text(),
    });
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

/** The accessibility problems axe finds on the page as rendered (colour contrast needs a real
 * layout engine, so it is checked in the browser instead). Each is "rule: element". */
export async function a11yProblems(): Promise<string[]> {
  const axe = (await import("axe-core")).default;
  const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
  return result.violations.flatMap((v) =>
    v.nodes.map(
      (n) => `${v.id}: ${n.html.slice(0, 120)} (${n.failureSummary?.split("\n")[1]?.trim()})`,
    ),
  );
}
