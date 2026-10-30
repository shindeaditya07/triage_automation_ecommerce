import { useQueryClient } from "@tanstack/react-query";

import { ME_KEY } from "../api/auth";
import { ContactForm } from "../components/ContactForm";
import { TopBar } from "../components/TopBar";
import { useTitle } from "../hooks/useTitle";

/**
 * The one page for taking in a complaint. A customer opens it from a link; the owner opens it
 * from the queue to record a complaint for a customer. The page never asks the server who is
 * looking (a customer's visit must not wait on, or depend on, sign-in); it only recognises an
 * owner whose session this browser tab already loaded.
 */
export function ContactPage() {
  const queryClient = useQueryClient();
  const owner = queryClient.getQueryData(ME_KEY) !== undefined;
  useTitle(owner ? "New complaint" : "Contact us");
  return (
    <div className={owner ? "app" : undefined}>
      {owner && <TopBar />}
      <main id="main" tabIndex={-1} className="contact">
        <h1>{owner ? "New complaint" : "Contact us"}</h1>
        <ContactForm
          mode={owner ? "owner" : "customer"}
          onSent={
            owner ? () => void queryClient.invalidateQueries({ queryKey: ["tickets"] }) : undefined
          }
        />
      </main>
    </div>
  );
}
