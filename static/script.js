document.addEventListener("DOMContentLoaded", () => {

    // =============================
    // AUTH CHECK
    // =============================
    async function checkAuth() {
        const res = await fetch("/api/auth/status");
        const data = await res.json();

        if (data.authenticated) {
            document.getElementById("login-modal").style.display = "none";
            document.getElementById("main-ui").style.display = "block";
            loadCapabilities();
        } else {
            document.getElementById("login-modal").style.display = "flex";
            document.getElementById("main-ui").style.display = "none";
        }
    }

    // =============================
    // AUTH0 LOGIN BUTTON
    // =============================
    const loginBtn = document.getElementById("auth0-login-btn");
    loginBtn.addEventListener("click", async () => {
        const res = await fetch("/api/auth/login");
        const data = await res.json();
        window.location.href = data.redirect;  // redirect to Auth0
    });

    // =============================
    // LOGOUT
    // =============================
    document.getElementById("logout-btn").addEventListener("click", async () => {
        await fetch("/api/auth/logout", { method: "POST" });
        window.location.reload();
    });

    // =============================
    // LOAD CAPABILITIES
    // =============================
    async function loadCapabilities() {
        const res = await fetch("/api/capabilities");
        const data = await res.json();

        const grid = document.getElementById("capabilities-grid");
        grid.innerHTML = "";

        data.capabilities.forEach(cap => {
            const el = document.createElement("div");
            el.classList.add("capability-card");
            el.innerHTML = `
                <div class="capability-name">${cap.name}</div>
                <div class="capability-example">${cap.example}</div>
            `;
            grid.appendChild(el);
        });
    }

    // =============================
    // CHAT
    // =============================
    async function sendMessage() {
        const input = document.getElementById("user-input");
        const message = input.value.trim();
        if (!message) return;

        addMessage("user", message);
        input.value = "";

        const res = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message })
        });

        const data = await res.json();
        addMessage("assistant", data.message || data.error);
    }

    document.getElementById("send-btn").addEventListener("click", sendMessage);

    document.getElementById("user-input").addEventListener("keydown", e => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    function addMessage(role, text) {
        const chat = document.getElementById("chat-container");
        const div = document.createElement("div");
        div.classList.add(role === "user" ? "user-message" : "assistant-message");
        div.textContent = text;
        chat.appendChild(div);
        chat.scrollTop = chat.scrollHeight;
    }

    // =============================
    // INIT
    // =============================
    checkAuth();
});
