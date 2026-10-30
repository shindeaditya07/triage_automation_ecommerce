// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { applySavedTheme, currentTheme, useTheme } from "./theme";

function prefersDark(dark: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: dark && query.includes("dark"),
  })) as unknown as typeof window.matchMedia;
}

beforeEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("the theme", () => {
  it("follows the device until the owner chooses", () => {
    prefersDark(true);
    expect(currentTheme()).toBe("dark");
    prefersDark(false);
    expect(currentTheme()).toBe("light");
  });

  it("uses the saved choice over the device", () => {
    prefersDark(true);
    window.localStorage.setItem("replydesk-theme", "light");
    expect(currentTheme()).toBe("light");
    applySavedTheme();
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("ignores a saved value it does not know", () => {
    prefersDark(false);
    window.localStorage.setItem("replydesk-theme", "sepia");
    expect(currentTheme()).toBe("light");
  });

  it("switches, applies and remembers the choice", () => {
    prefersDark(false);
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("light");
    act(() => result.current.toggle());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("replydesk-theme")).toBe("dark");
    act(() => result.current.toggle());
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("still switches when the browser will not store the choice", () => {
    prefersDark(false);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const { result } = renderHook(() => useTheme());
    act(() => result.current.toggle());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
