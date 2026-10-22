import { useEffect, useState } from "react";

export const WAKING_UP_AFTER_MS = 8_000;

/** True once a request has been pending for 8 seconds: a sleeping free-tier server. */
export function useWakingUp(pending: boolean, afterMs: number = WAKING_UP_AFTER_MS): boolean {
  const [waking, setWaking] = useState(false);
  useEffect(() => {
    if (!pending) {
      setWaking(false);
      return;
    }
    const timer = setTimeout(() => setWaking(true), afterMs);
    return () => clearTimeout(timer);
  }, [pending, afterMs]);
  return waking;
}
