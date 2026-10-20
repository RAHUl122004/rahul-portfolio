const contactForm = document.getElementById("contactForm");
const formStatus = document.getElementById("formStatus");

if (contactForm && formStatus) {
    contactForm.addEventListener("submit", async (event) => {
        event.preventDefault();

        const submitButton = contactForm.querySelector('button[type="submit"]');
        submitButton.disabled = true;

        formStatus.textContent = "Sending your message...";
        formStatus.classList.remove("form-success", "form-error");

        try {
            const response = await fetch(contactForm.action, {
                method: "POST",
                body: new FormData(contactForm)
            });

            const result = await response.json();

            formStatus.textContent = result.message || result.error || "Please try again.";
            formStatus.classList.toggle("form-success", response.ok);
            formStatus.classList.toggle("form-error", !response.ok);

            if (response.ok) {
                contactForm.reset();
            }
        } catch (error) {
            formStatus.textContent = "Unable to send right now. Please try again or email me directly.";
            formStatus.classList.add("form-error");
        } finally {
            submitButton.disabled = false;
        }
    });
}
