import { useEffect, useRef, useState } from "react";

/**
 * J and K move a selection down and up a list of `count` rows, and Enter opens the selected one
 * (`onOpen`). Ignored while typing in a field or when a control has the focus.
 */
export function useListKeys(count: number, onOpen?: (index: number) => void): number {
  const [selected, setSelected] = useState(0);
  const open = useRef(onOpen);
  open.current = onOpen;
  const current = useRef(0);
  current.current = selected;
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
      if (event.key === "Enter" && target === document.body && count > 0) {
        open.current?.(current.current);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [count]);
  return selected;
}
