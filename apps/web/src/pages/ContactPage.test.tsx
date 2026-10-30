// @vitest-environment jsdom
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { a11yProblems, renderAt, stubApi, type Reply } from "../test-support";
import { ContactPage } from "./ContactPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function show(reply: Reply | (() => Reply) = { status: 202, body: { status: "received" } }) {
  const seen = stubApi((method, path) =>
    method === "POST" && path === "/api/v1/tickets/form"
      ? typeof reply === "function"
        ? reply()
        : reply
      : undefined,
  );
  renderAt("/contact", <Route path="/contact" element={<ContactPage />} />);
  return seen;
}

async function fill(user: ReturnType<typeof userEvent.setup>, order = "RD-10001") {
  await user.type(screen.getByLabelText("Your name (optional)"), "Asha");
  await user.type(screen.getByLabelText("Email address"), "asha@example.com");
  if (order) await user.type(screen.getByLabelText("Order number (optional)"), order);
  await user.type(screen.getByLabelText("Subject (optional)"), "Where is it?");
  await user.type(screen.getByLabelText("Message"), "My order has not arrived.");
}

describe("the customer contact page", () => {
  it("sends the details and the order number to the public form endpoint", async () => {
    const seen = show();
    const user = userEvent.setup();
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByText(/We have your message/)).toBeTruthy();
    expect(seen).toHaveLength(1);
    const sent = JSON.parse(seen[0]!.body);
    expect(sent).toMatchObject({
      name: "Asha",
      email: "asha@example.com",
      order_id: "RD-10001",
      subject: "Where is it?",
      message: "My order has not arrived.",
      website: "",
    });
    expect(sent.submission_id).toMatch(UUID);
  });

  it("works without the optional fields and leaves them out", async () => {
    const seen = show();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email address"), "asha@example.com");
    await user.type(screen.getByLabelText("Message"), "Hello");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText(/We have your message/);
    expect(Object.keys(JSON.parse(seen[0]!.body)).sort()).toEqual([
      "email",
      "message",
      "submission_id",
      "website",
    ]);
  });

  it("does not send until there is an email and a message", async () => {
    show();
    const user = userEvent.setup();
    const send = screen.getByRole("button", { name: "Send message" });
    expect(send.hasAttribute("disabled")).toBe(true);
    await user.type(screen.getByLabelText("Email address"), "asha@example.com");
    expect(send.hasAttribute("disabled")).toBe(true);
    await user.type(screen.getByLabelText("Message"), "Hello");
    expect(send.hasAttribute("disabled")).toBe(false);
  });

  it("refuses an order number the server would not accept", async () => {
    show();
    const user = userEvent.setup();
    await fill(user, "bad order!");
    expect(screen.getByText(/Order numbers use letters/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send message" }).hasAttribute("disabled")).toBe(
      true,
    );
  });

  it("keeps the same submission id when a failed send is retried", async () => {
    let calls = 0;
    const seen = show(() => (++calls === 1 ? { status: 500 } : { status: 202, body: {} }));
    const user = userEvent.setup();
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/could not send/);
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText(/We have your message/);
    const ids = seen.map((r) => JSON.parse(r.body).submission_id);
    expect(ids).toHaveLength(2);
    expect(ids[0]).toBe(ids[1]);
  });

  it.each([
    [429, /several messages/],
    [422, /check your email/],
    [413, /too long/],
    [503, /cannot take messages/],
  ])("explains a %i answer in plain words", async (status, text) => {
    show({ status, body: { detail: "x" } });
    const user = userEvent.setup();
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(text);
    expect(screen.getByLabelText("Message")).toBeTruthy(); // the form stays, nothing is lost
  });

  it("starts a fresh submission for another message", async () => {
    const seen = show();
    const user = userEvent.setup();
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await user.click(await screen.findByRole("button", { name: "Send another message" }));
    await user.type(screen.getByLabelText("Message"), "One more thing");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(seen).toHaveLength(2));
    const [first, second] = seen.map((r) => JSON.parse(r.body).submission_id);
    expect(first).not.toBe(second);
  });

  it("hides the honeypot from people and assistive technology", () => {
    show();
    const trap = screen.getByLabelText("Leave this empty");
    expect(trap.closest("[aria-hidden='true']")).not.toBeNull();
    expect(trap.getAttribute("tabindex")).toBe("-1");
  });

  it("has no accessibility problems", async () => {
    show();
    expect(await a11yProblems()).toEqual([]);
  });

  it("is reachable without signing in", async () => {
    const seen = show();
    await screen.findByRole("heading", { name: "Contact us" });
    expect(seen.some((r) => r.path.includes("/auth/"))).toBe(false);
  });
});
