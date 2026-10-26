import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, unwrap } from "./client";
import type { components } from "./schema";

export type MetricsReport = components["schemas"]["MetricsReport"];

export function useMetrics(start: string, end: string) {
  return useQuery({
    queryKey: ["metrics", start, end],
    queryFn: () => unwrap(api.GET("/api/v1/metrics", { params: { query: { start, end } } })),
    placeholderData: keepPreviousData,
  });
}
