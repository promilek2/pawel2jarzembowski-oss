// Skrypt stron statycznych (dokumenty prawne): zmiana decyzji o zapisie wyników offline.
const button = document.getElementById("consent-reset");
if (button) {
  const status = document.createElement("span");
  status.setAttribute("role", "status");
  button.after(" ", status);
  button.addEventListener("click", () => {
    try {
      localStorage.removeItem("termin:consent");
      localStorage.removeItem("termin:last");
    } catch { /* tryb prywatny */ }
    status.textContent = "Zgoda wycofana, zapisane wyniki usunięte. Przy następnej wizycie zapytamy ponownie.";
  });
}
