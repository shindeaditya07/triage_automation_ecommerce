import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "./client";
import type { components } from "./schema";

export type LoginRequest = components["schemas"]["LoginRequest"];

export const ME_KEY = ["me"] as const;

/** The signed-in owner, or an `ApiError` with status 401 when nobody is signed in. */
export function useMe() {
  return useQuery({ queryKey: ME_KEY, queryFn: () => unwrap(api.GET("/api/v1/auth/me")) });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: LoginRequest) => unwrap(api.POST("/api/v1/auth/login", { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ME_KEY }),
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/auth/logout")),
    onSettled: () => queryClient.clear(),
  });
}
