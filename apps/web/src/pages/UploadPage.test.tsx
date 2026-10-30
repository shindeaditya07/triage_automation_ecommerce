// @vitest-environment jsdom
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { a11yProblems, renderAt, stubApi, type Reply } from "../test-support";
import { UploadPage } from "./UploadPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function show(bulk: (n: number, body: { complaints: unknown[] }) => Reply) {
  let calls = 0;
  const seen = stubApi((method, path) => {
    if (path === "/api/v1/auth/me") return { body: { email: "owner@example.com" } };
    if (method === "POST" && path === "/api/v1/tickets/bulk") {
      const body = JSON.parse(seen[seen.length - 1]!.body);
      return bulk(++calls, body);
    }
    return undefined;
  });
  renderAt("/upload", <Route path="/upload" element={<UploadPage />} />);
  return seen;
}

const csv = (rows: number) =>
  "email,order_id,message\n" +
  Array.from({ length: rows }, (_, i) => `c${i}@example.com,RD-1000${i % 9},Complaint ${i}`).join(
    "\n",
  );

async function choose(user: ReturnType<typeof userEvent.setup>, text: string) {
  const file = new File([text], "cases.csv", { type: "text/csv" });
  await user.upload(screen.getByLabelText("CSV file"), file);
}

const accepted = (_n: number, body: { complaints: unknown[] }): Reply => ({
  body: { received: body.complaints.length, duplicates: 0, rejected: [] },
});

describe("the bulk upload page", () => {
  it("previews the file, then sends it in batches of 50 and reports the result", async () => {
    const seen = show(accepted);
    const user = userEvent.setup();
    await choose(user, csv(120));
    expect(await screen.findByText("cases.csv: 120 complaints ready.")).toBeTruthy();
    expect(screen.getAllByRole("row")).toHaveLength(6); // header and the first five
    await user.click(screen.getByRole("button", { name: "Upload 120 complaints" }));
    expect(await screen.findByText(/Done\. 120 new tickets/)).toBeTruthy();

    const posts = seen.filter((r) => r.path === "/api/v1/tickets/bulk");
    expect(posts.map((p) => JSON.parse(p.body).complaints.length)).toEqual([50, 50, 20]);
    expect(new Set(posts.map((p) => JSON.parse(p.body).batch_id)).size).toBe(3);
    expect(JSON.parse(posts[0]!.body).complaints[0]).toEqual({
      email: "c0@example.com",
      order_id: "RD-10000",
      message: "Complaint 0",
    });
    expect(screen.getByRole("link", { name: "Go to the queue" })).toBeTruthy();
  });

  it("lists rows the server did not accept, numbered as in the file", async () => {
    show((n) => ({
      body: {
        received: 49,
        duplicates: 0,
        rejected: n === 2 ? [{ row: 3, reason: "email: value is not a valid email address" }] : [],
      },
    }));
    const user = userEvent.setup();
    await choose(user, csv(80));
    await user.click(screen.getByRole("button", { name: "Upload 80 complaints" }));
    expect(await screen.findByText(/1 not accepted/)).toBeTruthy();
    expect(screen.getByText("Row 53: email: value is not a valid email address")).toBeTruthy();
  });

  it("carries on from where it stopped, with the same batch ids, after an error", async () => {
    let failOnce = true;
    const seen = show((n, body) => {
      if (n === 2 && failOnce) {
        failOnce = false;
        return { status: 500, body: { detail: "down" } };
      }
      return accepted(n, body);
    });
    const user = userEvent.setup();
    await choose(user, csv(120));
    await user.click(screen.getByRole("button", { name: "Upload 120 complaints" }));
    expect(await screen.findByText(/stopped at 50 of 120/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Continue upload" }));
    expect(await screen.findByText(/Done\. 120 new tickets/)).toBeTruthy();

    const posts = seen.filter((r) => r.path === "/api/v1/tickets/bulk");
    const ids = posts.map((p) => JSON.parse(p.body).batch_id);
    expect(ids).toHaveLength(4);
    expect(ids[1]).toBe(ids[2]); // the failed batch is sent again under its own id
  });

  it.each([
    ["email,note\na@example.com,x\n", /Unknown column: note/],
    ["email,message\n", /no complaints/],
  ])("explains a file it cannot use (%j)", async (text, problem) => {
    show(accepted);
    const user = userEvent.setup();
    await choose(user, text);
    expect((await screen.findByRole("alert")).textContent).toMatch(problem);
    expect(screen.queryByRole("button", { name: /Upload/ })).toBeNull();
  });

  it("has no accessibility problems", async () => {
    show(accepted);
    const user = userEvent.setup();
    await choose(user, csv(3));
    await waitFor(() => expect(screen.getByText(/3 complaints ready/)).toBeTruthy());
    expect(await a11yProblems()).toEqual([]);
  });
});
