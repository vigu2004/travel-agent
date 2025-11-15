#!/usr/bin/env python3
"""
Math Agent Backend Server
Auth0 login + OpenAI Chat + MCP Math Server (Cloud Run)
"""

from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
import requests
import json
import os
import asyncio
from urllib.parse import urlencode
from dotenv import load_dotenv
from openai import OpenAI
import httpx
from mcp import ClientSession
from mcp.client.session import ClientSession as MCPClientSession
from mcp.types import (
    InitializeRequest,
    ListToolsRequest,
    CallToolRequest,
    TextContent,
    ImageContent,
    EmbeddedResource
)

# Load env
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key")
CORS(app)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ---------------------------------------------------------
# AUTH0 CONFIG
# ---------------------------------------------------------

AUTH0_DOMAIN = os.environ["AUTH0_DOMAIN"]
AUTH0_CLIENT_ID = os.environ["AUTH0_CLIENT_ID"]
AUTH0_CLIENT_SECRET = os.environ["AUTH0_CLIENT_SECRET"]
AUTH0_AUDIENCE = os.environ["AUTH0_AUDIENCE"]
AUTH0_CALLBACK_URL = os.environ["AUTH0_CALLBACK_URL"]

AUTH0_BASE = f"https://{AUTH0_DOMAIN}"
AUTH0_AUTHORIZE_URL = f"{AUTH0_BASE}/authorize"
AUTH0_TOKEN_URL = f"{AUTH0_BASE}/oauth/token"
AUTH0_USERINFO_URL = f"{AUTH0_BASE}/userinfo"


# ---------------------------------------------------------
# AUTH0 ROUTES
# ---------------------------------------------------------

@app.route("/api/auth/login")
def auth_login():
    params = {
        "response_type": "code",
        "client_id": AUTH0_CLIENT_ID,
        "redirect_uri": AUTH0_CALLBACK_URL,
        "scope": "openid profile email",
        "audience": AUTH0_AUDIENCE
    }
    return jsonify({"redirect": f"{AUTH0_AUTHORIZE_URL}?{urlencode(params)}"})


@app.route("/callback")
def auth_callback():
    code = request.args.get("code")
    if not code:
        return "Missing code", 400

    token_payload = {
        "grant_type": "authorization_code",
        "client_id": AUTH0_CLIENT_ID,
        "client_secret": AUTH0_CLIENT_SECRET,
        "code": code,
        "redirect_uri": AUTH0_CALLBACK_URL
    }

    token_res = requests.post(AUTH0_TOKEN_URL, json=token_payload)
    token_data = token_res.json()

    access_token = token_data.get("access_token")
    if not access_token:
        return f"Auth0 Error: {token_data}", 401

    # User info
    userinfo_res = requests.get(
        AUTH0_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
    )
    userinfo = userinfo_res.json()

    session["mcp_token"] = access_token
    session["mcp_user"] = userinfo

    return render_template("index.html", user=userinfo)


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    token = session.get("mcp_token")
    if token:
        clear_tool_cache(token)
    session.clear()
    return jsonify({"success": True})


@app.route("/api/auth/status")
def auth_status():
    token = session.get("mcp_token")
    user = session.get("mcp_user")
    return jsonify({"authenticated": bool(token), "user": user})


# ---------------------------------------------------------
# MCP CLIENT HELPERS (Using MCP SDK)
# ---------------------------------------------------------

class HTTPTransport:
    """HTTP transport for MCP SDK"""
    
    def __init__(self, server_url: str, token: str):
        self.server_url = server_url
        self.token = token
        self.session_id = None
        self._client = httpx.AsyncClient(timeout=30.0)
        self._request_id = 0
    
    def _get_request_id(self):
        self._request_id += 1
        return self._request_id
    
    async def send_json_rpc(self, method: str, params: dict = None):
        """Send JSON-RPC request"""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": self._get_request_id()
        }
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }
        
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        
        try:
            response = await self._client.post(
                f"{self.server_url}/mcp",
                json=payload,
                headers=headers
            )
            response.raise_for_status()
            
            # Extract session ID from headers
            if not self.session_id:
                self.session_id = (
                    response.headers.get("mcp-session-id") or
                    response.headers.get("x-session-id") or
                    response.headers.get("session-id")
                )
            
            data = response.json()
            
            if "error" in data:
                error_info = data["error"]
                if isinstance(error_info, dict):
                    error_msg = error_info.get("message", str(error_info))
                else:
                    error_msg = str(error_info)
                raise Exception(f"MCP Error: {error_msg}")
            
            return data.get("result", data)
            
        except httpx.HTTPStatusError as e:
            try:
                error_data = e.response.json()
                if "error" in error_data:
                    error_info = error_data["error"]
                    if isinstance(error_info, dict):
                        error_msg = error_info.get("message", str(error_info))
                    else:
                        error_msg = str(error_info)
                    raise Exception(f"MCP HTTP Error ({e.response.status_code}): {error_msg}")
            except ValueError:
                pass
            raise Exception(f"HTTP Error ({e.response.status_code}): {e.response.text[:200]}")
    
    async def close(self):
        await self._client.aclose()


class MCPClient:
    """MCP SDK-based client with HTTP transport"""
    
    def __init__(self, server_url: str, token: str):
        self.transport = HTTPTransport(server_url, token)
        self._session = None
        self._initialized = False
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def initialize(self):
        """Initialize MCP session using SDK"""
        if self._initialized:
            return
        
        # Use MCP SDK types for initialization
        result = await self.transport.send_json_rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "travel-agent", "version": "1.0.0"}
            }
        )
        
        self._initialized = True
        print(f"✅ MCP initialized, session_id: {self.transport.session_id}")
        return result
    
    async def list_tools(self):
        """List available tools using MCP SDK"""
        if not self._initialized:
            await self.initialize()
        
        result = await self.transport.send_json_rpc("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        print(f"📋 Fetched {len(tools)} tools from MCP server")
        return tools
    
    async def call_tool(self, name: str, arguments: dict):
        """Call a tool using MCP SDK"""
        if not self._initialized:
            await self.initialize()
        
        result = await self.transport.send_json_rpc(
            "tools/call",
            {"name": name, "arguments": arguments}
        )
        
        # Parse MCP content response format
        return self._parse_tool_result(result)
    
    def _parse_tool_result(self, result):
        """Parse MCP tool result content"""
        if isinstance(result, dict) and "content" in result:
            content = result["content"]
            if isinstance(content, list) and len(content) > 0:
                texts = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            texts.append(item.get("text", ""))
                        elif "text" in item:
                            texts.append(item["text"])
                        else:
                            texts.append(str(item))
                    else:
                        texts.append(str(item))
                return "\n".join(texts) if texts else result
            return result
        return result
    
    async def close(self):
        """Close the transport"""
        await self.transport.close()


def call_mcp_tool(tool_name, args, token):
    """Synchronous wrapper for MCP tool calls"""
    async def _run():
        async with MCPClient(MCP_SERVER_URL, token) as client:
            await client.initialize()
            result = await client.call_tool(tool_name, args)
            return result
    
    return asyncio.run(_run())


def get_mcp_tools(token):
    """Get list of MCP tools"""
    async def _run():
        async with MCPClient(MCP_SERVER_URL, token) as client:
            await client.initialize()
            tools = await client.list_tools()
            return tools
    
    return asyncio.run(_run())


def convert_mcp_tool_to_openai(mcp_tool):
    """Convert MCP tool definition to OpenAI function format"""
    properties = {}
    required = []
    
    if "inputSchema" in mcp_tool:
        schema = mcp_tool["inputSchema"]
        if schema.get("type") == "object" and "properties" in schema:
            for prop_name, prop_def in schema["properties"].items():
                properties[prop_name] = {
                    "type": prop_def.get("type", "string"),
                    "description": prop_def.get("description", "")
                }
            required = schema.get( "required", [])
    
    return {
        "type": "function",
        "function": {
            "name": mcp_tool.get("name", ""),
            "description": mcp_tool.get("description", ""),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required
            }
        }
    }


# ---------------------------------------------------------
# TOOL CACHE
# ---------------------------------------------------------

# Cache MCP tools per session to avoid repeated calls
_tool_cache = {}


def get_openai_functions(token):
    """Get OpenAI function definitions from MCP server"""
    if token not in _tool_cache:
        try:
            mcp_tools = get_mcp_tools(token)
            _tool_cache[token] = [convert_mcp_tool_to_openai(tool) for tool in mcp_tools]
        except Exception as e:
            print(f"Error fetching MCP tools: {e}")
            # Return empty list if MCP server is unavailable
            _tool_cache[token] = []
    return _tool_cache[token]


def clear_tool_cache(token=None):
    """Clear tool cache, optionally for a specific token"""
    if token:
        _tool_cache.pop(token, None)
    else:
        _tool_cache.clear()


# ---------------------------------------------------------
# FUNCTION EXECUTION HANDLER
# ---------------------------------------------------------

def execute_function(name, args, token):
    """Execute MCP tool by name"""
    try:
        result = call_mcp_tool(name, args, token)
        return result
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------
# CHAT ENDPOINT
# ---------------------------------------------------------

@app.route("/api/chat", methods=["POST"])
def chat():
    token = session.get("mcp_token")
    if not token:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.json
    user_message = data.get("message")

    # Get tools from MCP server
    openai_functions = get_openai_functions(token)
    
    if not openai_functions:
        return jsonify({"error": "No tools available from MCP server"}), 500

    messages = [
        {"role": "system", "content": "You are a math assistant. Use the available tools to perform calculations."},
        {"role": "user", "content": user_message}
    ]

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        tools=openai_functions,
        tool_choice="auto"
    )

    msg = response.choices[0].message

    # 👉 If LLM triggers MCP tool
    if msg.tool_calls:
        results = []
        for tool_call in msg.tool_calls:
            fname = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            out = execute_function(fname, args, token)
            results.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": fname,
                "content": json.dumps(out) if isinstance(out, (dict, list)) else str(out)
            })

        messages.append(msg)
        messages.extend(results)

        second = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages
        )
        return jsonify({"message": second.choices[0].message.content})

    # No tool call
    return jsonify({"message": msg.content})


# ---------------------------------------------------------
# UI + CAPABILITIES
# ---------------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/capabilities")
def capabilities():
    token = session.get("mcp_token")
    if not token:
        return jsonify({"capabilities": []})
    
    try:
        mcp_tools = get_mcp_tools(token)
        capabilities_list = [
            {
                "name": tool.get("name", ""),
                "description": tool.get("description", "")
            }
            for tool in mcp_tools
        ]
        return jsonify({"capabilities": capabilities_list})
    except Exception as e:
        return jsonify({"capabilities": [], "error": str(e)})


# ---------------------------------------------------------
# RUN
# ---------------------------------------------------------

if __name__ == "__main__":
    print("Math Agent running at http://localhost:3000")
    app.run(host="0.0.0.0", port=3000, debug=True)
