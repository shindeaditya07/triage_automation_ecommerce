import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

const KEY = "replydesk-theme";

function stored(): Theme | null {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null; // private window or blocked storage: the choice just is not remembered
  }
}

function system(): Theme {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** The owner's saved choice, else what the device prefers. */
export function currentTheme(): Theme {
  return stored() ?? system();
}

function apply(theme: Theme) {
  document.documentElement.dataset.theme = theme;
}

/** Run once before the first render so the page does not flash the wrong colours. */
export function applySavedTheme() {
  apply(currentTheme());
}

/** The theme in use and a way to switch it. The choice is saved in this browser. */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  useEffect(() => apply(theme), [theme]);
  const toggle = useCallback(() => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    try {
      window.localStorage.setItem(KEY, next);
    } catch {
      // not remembered, still switches for this visit
    }
    setTheme(next);
  }, [theme]);
  return { theme, toggle };
}
