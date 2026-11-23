document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll('input[type="password"]').forEach((input) => {
    const control = document.createElement("span");
    control.className = "password-control";
    input.parentNode.insertBefore(control, input);
    control.appendChild(input);

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "password-toggle";
    toggle.textContent = "Show";
    toggle.setAttribute("aria-label", "Show password");
    toggle.setAttribute("aria-controls", input.id);
    toggle.setAttribute("aria-pressed", "false");
    toggle.addEventListener("click", () => {
      const visible = input.type === "password";
      input.type = visible ? "text" : "password";
      toggle.textContent = visible ? "Hide" : "Show";
      toggle.setAttribute("aria-label", `${visible ? "Hide" : "Show"} password`);
      toggle.setAttribute("aria-pressed", String(visible));
    });
    control.appendChild(toggle);
  });
});
