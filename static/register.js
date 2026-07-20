checkAuth();

const registerForm = document.getElementById("register-form");
const registerError = document.getElementById("error-message");
const registerSuccess = document.getElementById("success-message");
const registerPassword = document.getElementById("password");
const registerButton = document.getElementById("submit-btn");

document.getElementById("toggle-password").addEventListener("click", event => {
    registerPassword.type = registerPassword.type === "password" ? "text" : "password";
    event.currentTarget.querySelector("i").classList.toggle("fa-eye");
    event.currentTarget.querySelector("i").classList.toggle("fa-eye-slash");
});

registerForm.addEventListener("submit", async event => {
    event.preventDefault();
    registerError.classList.add("hidden");
    registerSuccess.classList.add("hidden");
    registerButton.disabled = true;
    try {
        const response = await fetch("/api/auth/register", {
            method: "POST",
            credentials: "same-origin",
            headers: {"Content-Type": "application/json", "Accept": "application/json"},
            body: JSON.stringify({
                full_name: document.getElementById("name").value.trim(),
                email: document.getElementById("email").value.trim(),
                password: registerPassword.value
            })
        });
        const data = await response.json();
        if (!response.ok) {
            const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join("\n") : data.detail;
            throw new Error(detail || "สมัครสมาชิกไม่สำเร็จ");
        }
        registerSuccess.textContent = "ลงทะเบียนสำเร็จ กำลังเปิดหน้าเข้าสู่ระบบ…";
        registerSuccess.classList.remove("hidden");
        setTimeout(() => window.location.replace("/login"), 900);
    } catch (error) {
        registerError.textContent = error.message || "ไม่สามารถเชื่อมต่อระบบได้";
        registerError.classList.remove("hidden");
    } finally {
        registerButton.disabled = false;
    }
});
