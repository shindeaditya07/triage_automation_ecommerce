import { useEffect, useState } from "react";

/** J and K move a selection down and up a list of `count` rows. Ignored while typing in a field. */
export function useListKeys(count: number): number {
  const [selected, setSelected] = useState(0);
  useEffect(() => {
    setSelected((current) => Math.min(current, Math.max(count - 1, 0)));
  }, [count]);
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target;
      if (
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        (target instanceof HTMLElement &&
          (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)))
      ) {
        return;
      }
      if (event.key === "j") setSelected((s) => Math.min(s + 1, Math.max(count - 1, 0)));
      if (event.key === "k") setSelected((s) => Math.max(s - 1, 0));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [count]);
  return selected;
}
