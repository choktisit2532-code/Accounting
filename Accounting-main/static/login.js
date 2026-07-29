checkAuth();

const loginForm = document.getElementById("login-form");
const loginError = document.getElementById("error-message");
const loginPassword = document.getElementById("password");
const loginButton = document.getElementById("submit-btn");

document.getElementById("toggle-password").addEventListener("click", event => {
    loginPassword.type = loginPassword.type === "password" ? "text" : "password";
    event.currentTarget.querySelector("i").classList.toggle("fa-eye");
    event.currentTarget.querySelector("i").classList.toggle("fa-eye-slash");
});

loginForm.addEventListener("submit", async event => {
    event.preventDefault();
    loginError.classList.add("hidden");
    loginButton.disabled = true;
    try {
        const response = await fetch("/api/auth/login", {
            method: "POST",
            credentials: "same-origin",
            headers: {"Content-Type": "application/json", "Accept": "application/json"},
            body: JSON.stringify({
                email: document.getElementById("email").value.trim(),
                password: loginPassword.value
            })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "เข้าสู่ระบบไม่สำเร็จ");
        setUser(data.user);
        window.location.replace("/dashboard");
    } catch (error) {
        loginError.textContent = error.message || "ไม่สามารถเชื่อมต่อระบบได้";
        loginError.classList.remove("hidden");
    } finally {
        loginButton.disabled = false;
    }
});
