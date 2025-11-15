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
            document.getElementById("logout-btn").style.display = "block";
            loadCapabilities();
        } else {
            document.getElementById("login-modal").style.display = "flex";
            document.getElementById("main-ui").style.display = "none";
            document.getElementById("logout-btn").style.display = "none";
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
        try {
            const res = await fetch("/api/capabilities");
            const data = await res.json();

            if (data.error) {
                document.getElementById("mcp-server").textContent = "MCP: ❌ Error";
                document.getElementById("mcp-server").style.color = "#ff4444";
                return;
            }

            const grid = document.getElementById("capabilities-grid");
            grid.innerHTML = "";

            // Update MCP status
            const toolCount = data.capabilities?.length || 0;
            document.getElementById("mcp-server").textContent = `MCP: ✅ Connected (${toolCount} tools)`;
            document.getElementById("mcp-server").style.color = "#4caf50";

            const icons = {
                "calculate": "🔢",
                "solve_equation": "📐",
                "differentiate": "📊",
                "integrate": "∫",
                "simplify": "✨",
                "expand": "📏",
                "factorize": "🔍",
                "limit": "∞",
                "series": "📈",
                "matrix": "⬛"
            };

            data.capabilities.forEach(cap => {
                const el = document.createElement("div");
                el.classList.add("capability-card");
                const icon = icons[cap.name] || icons[cap.name?.toLowerCase()] || "🔧";
                const description = cap.description || cap.example || "Math tool";
                // Truncate long descriptions
                const shortDesc = description.length > 50 
                    ? description.substring(0, 50) + "..." 
                    : description;
                
                el.innerHTML = `
                    <div class="capability-icon">${icon}</div>
                    <div class="capability-info">
                        <div class="capability-name">${cap.name}</div>
                        <div class="capability-description" title="${description}">${shortDesc}</div>
                    </div>
                `;
                grid.appendChild(el);
            });
        } catch (error) {
            console.error("Failed to load capabilities:", error);
            document.getElementById("mcp-server").textContent = "MCP: ❌ Failed";
            document.getElementById("mcp-server").style.color = "#ff4444";
        }
    }

    // =============================
    // CHAT
    // =============================
    async function sendMessage() {
        const input = document.getElementById("user-input");
        const sendBtn = document.getElementById("send-btn");
        const message = input.value.trim();
        if (!message) return;

        addMessage("user", message);
        input.value = "";
        sendBtn.disabled = true;

        // Show typing indicator
        const chat = document.getElementById("chat-container");
        const typingDiv = document.createElement("div");
        typingDiv.classList.add("message", "assistant");
        typingDiv.id = "typing-indicator";
        typingDiv.innerHTML = '<div class="message-content"><div class="typing-indicator"><span></span><span></span><span></span></div></div>';
        chat.appendChild(typingDiv);
        chat.scrollTop = chat.scrollHeight;

        try {
            const res = await fetch("/api/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message })
            });

            const data = await res.json();
            typingDiv.remove();
            addMessage("assistant", data.message || data.error || "An error occurred");
        } catch (error) {
            typingDiv.remove();
            addMessage("assistant", "Error: " + error.message);
        } finally {
            sendBtn.disabled = false;
        }
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
        
        // Remove welcome message if it exists
        const welcomeMsg = chat.querySelector(".welcome-message");
        if (welcomeMsg) {
            welcomeMsg.remove();
        }
        
        const messageDiv = document.createElement("div");
        messageDiv.classList.add("message", role);
        
        const contentDiv = document.createElement("div");
        contentDiv.classList.add("message-content");
        contentDiv.textContent = text;
        
        messageDiv.appendChild(contentDiv);
        chat.appendChild(messageDiv);
        chat.scrollTop = chat.scrollHeight;
    }

    // =============================
    // TOGGLE CAPABILITIES PANEL
    // =============================
    document.getElementById("capabilities-toggle").addEventListener("click", () => {
        const panel = document.getElementById("capabilities-panel");
        panel.classList.toggle("collapsed");
    });

    // =============================
    // INIT
    // =============================
    checkAuth();
});
