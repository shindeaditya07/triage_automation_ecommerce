import { useMutation } from "@tanstack/react-query";

import { ApiError } from "./client";

export const MESSAGE_MAX = 5000;

/** What the public form endpoint accepts (docs/intake.md). Attachments are not sent. */
export interface ContactRequest {
  name?: string;
  email: string;
  subject?: string;
  message: string;
  order_id?: string;
  submission_id: string;
  /** The honeypot. The page never fills it. */
  website: string;
}

/**
 * Plain `fetch`, not the owner client: a customer has no session, so there is no CSRF token to
 * attach and no refresh to attempt. The endpoint answers 202 whatever it did with the message.
 */
export async function sendContact(body: ContactRequest): Promise<void> {
  const request = new Request(new URL("/api/v1/tickets/form", location.origin), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const response = await fetch(request);
  if (response.status === 202) return;
  let detail: unknown = null;
  try {
    detail = await response.json();
  } catch {
    // An error page that is not JSON: the status alone is what we use.
  }
  throw new ApiError(response.status, detail);
}

export function useSendContact() {
  return useMutation({ mutationFn: sendContact });
}
