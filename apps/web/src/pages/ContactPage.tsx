import { ContactForm } from "../components/ContactForm";
import { useTitle } from "../hooks/useTitle";

export function ContactPage() {
  useTitle("Contact us");
  return (
    <main id="main" tabIndex={-1} className="contact">
      <h1>Contact us</h1>
      <ContactForm mode="customer" />
    </main>
  );
}
