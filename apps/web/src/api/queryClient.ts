import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";

const MAX_QUERY_RETRIES = 2;

/** Retry a read twice for a network failure or a 5xx; never for a 4xx, which will not change. */
export function shouldRetryQuery(failures: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status < 500) return false;
  return failures < MAX_QUERY_RETRIES;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: shouldRetryQuery, staleTime: 10_000, refetchOnWindowFocus: false },
      // A write is never repeated on its own: only the owner decides to try again.
      mutations: { retry: false },
    },
  });
}
