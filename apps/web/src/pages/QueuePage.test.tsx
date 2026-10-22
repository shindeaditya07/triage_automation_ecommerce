// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RequireOwner } from "../components/RequireOwner";
import { renderAt, stubApi, type Routing } from "../test-support";
import { LoginPage } from "./LoginPage";
import { QueuePage } from "./QueuePage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const ticket = (id: string, over: Record<string, unknown> = {}) => ({
  ticket_id: id,
  received_at: "2026-10-22T10:00:00Z",
  channel: "form",
  customer_email: "asha@example.com",
  customer_name: "Asha",
  subject: `Subject ${id}`,
  status: "drafted",
  intent: "order_status",
  urgency: "high",
  escalation_reason: null,
  flags: [],
  has_draft: true,
  suggestion_only: false,
  used_fallback_template: false,
  ...over,
});

const page = (items: unknown[], total = items.length) => ({
  body: { items, total, limit: 25, offset: 0 },
});

function app(routing: Routing, path = "/") {
  const seen = stubApi((method, p, search) => {
    if (p === "/api/v1/auth/me") return { body: { email: "owner@example.com" } };
    return routing(method, p, search);
  });
  renderAt(
    path,
    <>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <RequireOwner>
            <QueuePage />
          </RequireOwner>
        }
      />
    </>,
  );
  return seen;
}

describe("the queue", () => {
  it("links each ticket to its screen", async () => {
    app(() => page([ticket("a")]));
    const link = await screen.findByRole("link", { name: "Subject a" });
    expect(link.getAttribute("href")).toBe("/tickets/a");
  });

  it("lists tickets with their badges and the signed-in owner", async () => {
    app(() =>
      page([
        ticket("a", { urgency: "critical", flags: ["prompt_injection_suspected"] }),
        ticket("b", { status: "drafted", suggestion_only: true, used_fallback_template: true }),
      ]),
    );
    expect(await screen.findByText("Subject a")).toBeTruthy();
    expect(screen.getByText("owner@example.com")).toBeTruthy();
    expect(screen.getByText("Critical")).toBeTruthy();
    expect(screen.getByText(/Suspected injection/)).toBeTruthy();
    expect(screen.getByText("Suggestion only")).toBeTruthy();
    expect(screen.getByText("Fallback reply")).toBeTruthy();
  });

  it("asks for the tab's statuses and changes them when the tab changes", async () => {
    const seen = app(() => page([]));
    expect(await screen.findByText(/Nothing in “In progress”/)).toBeTruthy();
    const first = seen.find((r) => r.path === "/api/v1/tickets")!;
    expect(first.search.getAll("status")).toEqual(["classified", "drafted"]);
    await userEvent.click(screen.getByRole("tab", { name: "Escalated" }));
    await waitFor(() =>
      expect(
        seen
          .filter((r) => r.path === "/api/v1/tickets")
          .some((r) => r.search.getAll("status").join() === "escalated"),
      ).toBe(true),
    );
    expect(await screen.findByText(/Nothing in “Escalated”/)).toBeTruthy();
  });

  it("shows an escalation reason in plain words", async () => {
    app(() => page([ticket("e", { status: "escalated", escalation_reason: "legal_threat" })]));
    expect(await screen.findByText("Legal threat")).toBeTruthy();
  });

  it("offers a retry when the list cannot be loaded", async () => {
    let fail = true;
    app(() => (fail ? { status: 500, body: { detail: "down" } } : page([ticket("r")])));
    expect(await screen.findByRole("alert")).toBeTruthy();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Subject r")).toBeTruthy();
  });

  it("pages through a long list", async () => {
    const seen = app((_m, _p, search) => {
      const offset = Number(search.get("offset") ?? 0);
      return { body: { items: [ticket(`p${offset}`)], total: 30, limit: 25, offset } };
    });
    expect(await screen.findByText("Subject p0")).toBeTruthy();
    expect(screen.getByText("1–25 of 30")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Subject p25")).toBeTruthy();
    expect(seen.some((r) => r.search.get("offset") === "25")).toBe(true);
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("moves the selection with J and K", async () => {
    app(() => page([ticket("one"), ticket("two"), ticket("three")]));
    await screen.findByText("Subject one");
    const current = () =>
      screen.getByRole("list", { name: /tickets/ }).querySelector("[aria-current]");
    expect(current()?.textContent).toContain("Subject one");
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "j" }); // stops at the last
    await waitFor(() => expect(current()?.textContent).toContain("Subject three"));
    fireEvent.keyDown(window, { key: "k" });
    await waitFor(() => expect(current()?.textContent).toContain("Subject two"));
  });

  it("says the server is waking up when the first answer is slow", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stubApi(() => undefined);
    vi.stubGlobal("fetch", (input: Request) => {
      const path = new URL(input.url).pathname;
      if (path === "/api/v1/auth/me") {
        return Promise.resolve(
          new Response(JSON.stringify({ email: "o@example.com" }), { status: 200 }),
        );
      }
      return new Promise<Response>(() => {}); // never answers
    });
    renderAt(
      "/",
      <Route
        path="/"
        element={
          <RequireOwner>
            <QueuePage />
          </RequireOwner>
        }
      />,
    );
    expect(await screen.findByText("Loading…")).toBeTruthy();
    await vi.advanceTimersByTimeAsync(8_100);
    expect(await screen.findByText(/waking up/)).toBeTruthy();
  });
});

describe("signing in", () => {
  it("sends a signed-out visitor to the login page", async () => {
    stubApi((_m, p) =>
      p === "/api/v1/auth/me" ? { status: 401, body: { detail: "no" } } : undefined,
    );
    renderAt(
      "/",
      <>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <RequireOwner>
              <QueuePage />
            </RequireOwner>
          }
        />
      </>,
    );
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
  });

  it("signs out and returns to the login page", async () => {
    let signedIn = true;
    stubApi((method, p) => {
      if (p === "/api/v1/auth/me") {
        return signedIn
          ? { body: { email: "owner@example.com" } }
          : { status: 401, body: { detail: "no" } };
      }
      if (method === "POST" && p === "/api/v1/auth/logout") {
        signedIn = false;
        return { status: 204 };
      }
      return page([ticket("x")]);
    });
    renderAt(
      "/",
      <>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <RequireOwner>
              <QueuePage />
            </RequireOwner>
          }
        />
      </>,
    );
    await screen.findByText("Subject x");
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
  });

  it("signs in and opens the queue", async () => {
    let signedIn = false;
    stubApi((method, p) => {
      if (p === "/api/v1/auth/me") {
        return signedIn
          ? { body: { email: "owner@example.com" } }
          : { status: 401, body: { detail: "no" } };
      }
      if (method === "POST" && p === "/api/v1/auth/login") {
        signedIn = true;
        return { body: { csrf_token: "t", access_expires_in: 900 } };
      }
      return page([ticket("x")]);
    });
    renderAt(
      "/login",
      <>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <RequireOwner>
              <QueuePage />
            </RequireOwner>
          }
        />
      </>,
    );
    await userEvent.type(await screen.findByLabelText("Email"), "owner@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "a password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Subject x")).toBeTruthy();
  });

  it.each([
    [401, "That email or password is not right."],
    [429, "Too many attempts. Wait a little, then try again."],
  ])("explains a %s", async (status, message) => {
    stubApi((method, p) => {
      if (p === "/api/v1/auth/me") return { status: 401, body: { detail: "no" } };
      if (method === "POST") return { status, body: { detail: "x" } };
      return undefined;
    });
    renderAt("/login", <Route path="/login" element={<LoginPage />} />);
    await userEvent.type(await screen.findByLabelText("Email"), "o@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect((await screen.findByRole("alert")).textContent).toBe(message);
  });
});
