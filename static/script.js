// Global state
let conversationHistory = [];
let isProcessing = false;
let authToken = null;

// DOM Elements
const chatContainer = document.getElementById('chat-container');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const statusText = document.getElementById('status-text');
const statusElement = document.getElementById('status');
const mcpServerElement = document.getElementById('mcp-server');
const capabilitiesGrid = document.getElementById('capabilities-grid');
const loginModal = document.getElementById('login-modal');
const loginForm = document.getElementById('login-form');
const loginError = document.getElementById('login-error');
const logoutBtn = document.getElementById('logout-btn');

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    checkAuthStatus();
    setupEventListeners();
});

// Setup event listeners
function setupEventListeners() {
    sendBtn.addEventListener('click', sendMessage);
    
    userInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    
    // Auto-resize textarea
    userInput.addEventListener('input', () => {
        userInput.style.height = 'auto';
        userInput.style.height = Math.min(userInput.scrollHeight, 120) + 'px';
    });
    
    // Login form
    loginForm.addEventListener('submit', handleLogin);
    
    // Logout button
    logoutBtn.addEventListener('click', handleLogout);
}

// Check authentication status
async function checkAuthStatus() {
    try {
        const response = await fetch('/api/auth/status');
        const data = await response.json();
        
        if (data.success && data.authenticated) {
            authToken = data.user.email; // Store user info
            showApp();
            checkStatus();
            loadCapabilities();
        } else {
            showLogin();
        }
    } catch (error) {
        console.error('Error checking auth status:', error);
        showLogin();
    }
}

// Handle login
async function handleLogin(e) {
    e.preventDefault();
    
    const email = document.getElementById('email').value.trim();
    const password = document.getElementById('password').value;
    
    loginError.style.display = 'none';
    loginError.textContent = '';
    
    try {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ email, password })
        });
        
        const data = await response.json();
        
        if (data.success) {
            authToken = data.token;
            showApp();
            checkStatus();
            loadCapabilities();
        } else {
            loginError.textContent = data.error || 'Login failed';
            loginError.style.display = 'block';
        }
    } catch (error) {
        loginError.textContent = 'Connection error. Please try again.';
        loginError.style.display = 'block';
    }
}

// Handle logout
async function handleLogout() {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
        authToken = null;
        conversationHistory = [];
        chatContainer.innerHTML = `
            <div class="welcome-message">
                <h2>👋 Welcome to Travel Agent AI</h2>
                <p>I can help you search for flights, hotels, and car rentals. Just ask me anything!</p>
            </div>
        `;
        showLogin();
    } catch (error) {
        console.error('Logout error:', error);
    }
}

// Show login modal
function showLogin() {
    loginModal.style.display = 'flex';
    document.querySelector('.container').style.opacity = '0.3';
    document.querySelector('.container').style.pointerEvents = 'none';
    logoutBtn.style.display = 'none';
}

// Show app (hide login)
function showApp() {
    loginModal.style.display = 'none';
    document.querySelector('.container').style.opacity = '1';
    document.querySelector('.container').style.pointerEvents = 'auto';
    logoutBtn.style.display = 'block';
}

// Make authenticated fetch request
async function authenticatedFetch(url, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };
    
    const response = await fetch(url, {
        ...options,
        headers,
        credentials: 'include' // Include session cookies
    });
    
    if (response.status === 401) {
        // Token expired or invalid
        showLogin();
        throw new Error('Authentication required');
    }
    
    return response;
}

// Check server status
async function checkStatus() {
    try {
        const response = await authenticatedFetch('/api/mcp/status');
        const data = await response.json();
        
        if (data.success && data.status === 'connected') {
            statusElement.classList.remove('disconnected');
            statusText.textContent = 'Connected';
            mcpServerElement.textContent = `MCP: ${data.server_url} ✓`;
        } else {
            statusElement.classList.add('disconnected');
            statusText.textContent = data.status === 'not_authenticated' ? 'Please Login' : 'MCP Disconnected';
            mcpServerElement.textContent = `MCP: ${data.server_url || 'N/A'} ✗`;
        }
    } catch (error) {
        statusElement.classList.add('disconnected');
        statusText.textContent = 'Error';
        mcpServerElement.textContent = 'MCP: Connection Failed';
    }
}

// Load capabilities
async function loadCapabilities() {
    try {
        const response = await authenticatedFetch('/api/capabilities');
        const data = await response.json();
        
        if (data.success) {
            displayCapabilities(data.capabilities);
        }
    } catch (error) {
        console.error('Error loading capabilities:', error);
    }
}

// Display capabilities
function displayCapabilities(capabilities) {
    capabilitiesGrid.innerHTML = '';
    
    capabilities.forEach(capability => {
        const card = document.createElement('div');
        card.className = 'capability-card';
        card.title = capability.example;
        card.innerHTML = `
            <div class="capability-icon">${getIconForCapability(capability.name)}</div>
            <div>
                <div class="capability-name">${capability.name}</div>
                <div class="capability-description">${capability.description}</div>
            </div>
        `;
        
        // Click to insert example
        card.addEventListener('click', () => {
            userInput.value = capability.example;
            userInput.focus();
        });
        
        capabilitiesGrid.appendChild(card);
    });
}

// Get icon for capability
function getIconForCapability(name) {
    const icons = {
        'Search Flights': '✈️',
        'Search Hotels': '🏨',
        'Search Car Rentals': '🚗',
        'Book Flights': '🎫'
    };
    return icons[name] || '🔧';
}

// Send message
async function sendMessage() {
    const message = userInput.value.trim();
    
    if (!message || isProcessing) return;
    
    // Clear input
    userInput.value = '';
    userInput.style.height = 'auto';
    
    // Remove welcome message if present
    const welcomeMessage = chatContainer.querySelector('.welcome-message');
    if (welcomeMessage) {
        welcomeMessage.remove();
    }
    
    // Add user message
    addMessage(message, 'user');
    
    // Add to history
    conversationHistory.push({
        role: 'user',
        content: message
    });
    
    // Show typing indicator
    const typingId = showTypingIndicator();
    
    // Disable input
    isProcessing = true;
    sendBtn.disabled = true;
    
    try {
        const response = await authenticatedFetch('/api/chat', {
            method: 'POST',
            body: JSON.stringify({
                message: message,
                history: conversationHistory
            })
        });
        
        const data = await response.json();
        
        // Remove typing indicator
        removeTypingIndicator(typingId);
        
        if (data.success) {
            // Add assistant message
            addMessage(data.message, 'assistant');
            
            // Add to history
            conversationHistory.push({
                role: 'assistant',
                content: data.message
            });
            
            // Show function call info if available
            if (data.function_called) {
                addSystemMessage(`Called: ${data.function_called}`);
            }
        } else {
            addMessage(`Error: ${data.error}`, 'system');
        }
    } catch (error) {
        removeTypingIndicator(typingId);
        if (error.message === 'Authentication required') {
            addMessage('Please log in to continue', 'system');
        } else {
            addMessage(`Connection error: ${error.message}`, 'system');
        }
    } finally {
        // Re-enable input
        isProcessing = false;
        sendBtn.disabled = false;
        userInput.focus();
    }
}

// Add message to chat
function addMessage(text, sender) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    // Format message (handle JSON, etc.)
    if (sender === 'assistant') {
        contentDiv.innerHTML = formatMessage(text);
    } else {
        contentDiv.textContent = text;
    }
    
    messageDiv.appendChild(contentDiv);
    chatContainer.appendChild(messageDiv);
    
    // Scroll to bottom
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

// Add system message
function addSystemMessage(text) {
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message system';
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.textContent = text;
    
    messageDiv.appendChild(contentDiv);
    chatContainer.appendChild(messageDiv);
    
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

// Format message (handle special formatting)
function formatMessage(text) {
    // Convert markdown-style code blocks
    text = text.replace(/```([\s\S]*?)```/g, '<pre>$1</pre>');
    
    // Convert newlines to <br>
    text = text.replace(/\n/g, '<br>');
    
    // Convert bold
    text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    
    return text;
}

// Show typing indicator
function showTypingIndicator() {
    const id = 'typing-' + Date.now();
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message assistant';
    messageDiv.id = id;
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.innerHTML = `
        <div class="typing-indicator">
            <span></span>
            <span></span>
            <span></span>
        </div>
    `;
    
    messageDiv.appendChild(contentDiv);
    chatContainer.appendChild(messageDiv);
    
    chatContainer.scrollTop = chatContainer.scrollHeight;
    
    return id;
}

// Remove typing indicator
function removeTypingIndicator(id) {
    const element = document.getElementById(id);
    if (element) {
        element.remove();
    }
}

// Check status periodically
setInterval(() => {
    if (authToken) {
        checkStatus();
    }
}, 30000); // Every 30 seconds
