const form = document.getElementById("contactForm");
const statusText = document.getElementById("formStatus");

if (form && statusText) {
    form.addEventListener("submit", async (event) => {
        event.preventDefault();

        const button = form.querySelector('button[type="submit"]');
        button.disabled = true;

        statusText.textContent = "Sending your message...";
        statusText.classList.remove("form-success", "form-error");

        try {
            const response = await fetch(form.action, {
                method: "POST",
                body: new FormData(form)
            });

            const result = await response.json();

            statusText.textContent = result.message || result.error;
            statusText.classList.toggle("form-success", response.ok);
            statusText.classList.toggle("form-error", !response.ok);

            if (response.ok) {
                form.reset();
            }
        } catch (error) {
            statusText.textContent = "Could not send your message. Please try again.";
            statusText.classList.remove("form-success");
            statusText.classList.add("form-error");
        } finally {
            button.disabled = false;
        }
    });
}
