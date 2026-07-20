function setUser(user) {
    sessionStorage.setItem("pf_user", JSON.stringify(user));
}

function getUser() {
    try {
        return JSON.parse(sessionStorage.getItem("pf_user") || "null");
    } catch {
        return null;
    }
}

function clearUser() {
    sessionStorage.removeItem("pf_user");
}

function isPublicPath(path) {
    return ["/", "/login", "/register", ""].includes(path);
}

async function checkAuth() {
    const path = window.location.pathname;
    try {
        const response = await fetch("/api/auth/me", {
            credentials: "same-origin",
            headers: { "Accept": "application/json" }
        });
        if (response.ok) {
            const user = await response.json();
            setUser(user);
            if (isPublicPath(path)) window.location.replace("/dashboard");
            return true;
        }
    } catch {
        // The page below will show the connection state when appropriate.
    }
    clearUser();
    if (!isPublicPath(path)) window.location.replace("/login");
    return false;
}

async function authenticatedFetch(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        credentials: "same-origin",
        headers: {
            "Accept": "application/json",
            ...(options.headers || {})
        }
    });
    if (response.status === 401) {
        clearUser();
        window.location.replace("/login");
        return null;
    }
    return response;
}

async function apiError(response, fallback) {
    if (!response) return fallback;
    try {
        const data = await response.json();
        if (Array.isArray(data.detail)) {
            return data.detail.map(item => item.msg).join("\n");
        }
        return data.detail || fallback;
    } catch {
        return fallback;
    }
}
