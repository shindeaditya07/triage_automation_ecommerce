import { useEffect } from "react";

const APP = "ReplyDesk";

/** The browser tab and a screen reader announce the page by its title. */
export function useTitle(title: string | null | undefined): void {
  useEffect(() => {
    document.title = title ? `${title} · ${APP}` : APP;
    return () => {
      document.title = APP;
    };
  }, [title]);
}
