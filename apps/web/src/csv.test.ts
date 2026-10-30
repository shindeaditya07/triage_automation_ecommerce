import { describe, expect, it } from "vitest";

import { parseComplaints, parseCsv } from "./csv";

describe("parseCsv", () => {
  it("splits cells and rows, and drops blank lines", () => {
    expect(parseCsv("a,b\n1,2\n\n3,4\n")).toEqual([
      ["a", "b"],
      ["1", "2"],
      ["3", "4"],
    ]);
  });

  it("keeps commas, doubled quotes and line breaks inside quoted cells", () => {
    expect(parseCsv('a,b\n"x, y","she said ""no""\nthen left"\r\n')).toEqual([
      ["a", "b"],
      ["x, y", 'she said "no"\nthen left'],
    ]);
  });

  it("ignores a byte-order mark and reads a last row with no line break", () => {
    expect(parseCsv("﻿a,b\n1,2")).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });
});

describe("parseComplaints", () => {
  it("reads the columns by name, in any order, leaving empty cells out", () => {
    const result = parseComplaints(
      "Message,EMAIL,order_id,name,subject\nWhere is it?,a@example.com,RD-10001,,Late\n",
    );
    expect(result).toEqual({
      ok: true,
      complaints: [
        { message: "Where is it?", email: "a@example.com", order_id: "RD-10001", subject: "Late" },
      ],
    });
  });

  it.each([
    ["", "The file is empty."],
    ["email,message\n", "The file has a header but no complaints."],
    [
      "email,note\na@example.com,hi\n",
      "Unknown column: note. Use name, email, order_id, subject, message.",
    ],
    ["message\nhi\n", 'The file needs an "email" column.'],
    ["email\na@example.com\n", 'The file needs a "message" column.'],
  ])("explains a bad file (%j)", (text, problem) => {
    expect(parseComplaints(text)).toEqual({ ok: false, problem });
  });
});
