#!/usr/bin/env python3
"""
Math Agent Backend Server
Auth0 login + OpenAI Chat + MCP Math Server (Cloud Run)
"""

from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
import requests
import httpx
import httpx_sse
import json
import os
import asyncio
from urllib.parse import urlencode
from dotenv import load_dotenv
from openai import OpenAI

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
# MCP CLIENT HELPERS
# ---------------------------------------------------------

_request_id = 0

def _get_request_id():
    global _request_id
    _request_id += 1
    return _request_id


async def _mcp_request(client, method, params, token, session_id=None):
    """Make MCP JSON-RPC request, supports both JSON and SSE responses"""
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": _get_request_id()
    }
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"
    }
    
    if session_id:
        headers["mcp-session-id"] = session_id
    
    try:
        # Make request expecting JSON response
        response = await client.post(
            f"{MCP_SERVER_URL}/mcp",
            json=payload,
            headers=headers,
            timeout=30.0
        )
        response.raise_for_status()
        
        # Check content type
        content_type = response.headers.get("content-type", "").lower()
        
        # Handle JSON response (most common)
        if "application/json" in content_type or not content_type:
            data = response.json()
            if "result" in data:
                return data["result"]
            elif "error" in data:
                error_info = data["error"]
                if isinstance(error_info, dict):
                    error_msg = error_info.get("message", str(error_info))
                else:
                    error_msg = str(error_info)
                raise Exception(f"MCP Error: {error_msg}")
            return data
        
        # Handle SSE response (less common)
        elif "text/event-stream" in content_type:
            # For SSE, we need to make a new request with SSE handler
            # Close the current response
            await response.aclose()
            
            async with httpx_sse.aconnect_sse(
                client, "POST", f"{MCP_SERVER_URL}/mcp",
                json=payload, headers=headers
            ) as stream:
                async for event in stream.aiter_sse():
                    if event.event == "message":
                        data = json.loads(event.data)
                        if "result" in data:
                            return data["result"]
                        elif "error" in data:
                            error_info = data["error"]
                            if isinstance(error_info, dict):
                                error_msg = error_info.get("message", str(error_info))
                            else:
                                error_msg = str(error_info)
                            raise Exception(f"MCP Error: {error_msg}")
                        return data
        else:
            raise Exception(f"Unexpected content type: {content_type}")
            
    except httpx.HTTPStatusError as http_err:
        # If HTTP error, try to parse JSON error response
        try:
            error_data = http_err.response.json()
            if "error" in error_data:
                error_info = error_data["error"]
                if isinstance(error_info, dict):
                    error_msg = error_info.get("message", str(error_info))
                else:
                    error_msg = str(error_info)
                raise Exception(f"MCP HTTP Error ({http_err.response.status_code}): {error_msg}")
        except ValueError:
            # Not JSON
            pass
        raise Exception(f"HTTP Error ({http_err.response.status_code}): {http_err.response.text[:200]}")
    except httpx_sse.SSEError as sse_err:
        raise Exception(f"SSE Error: {sse_err}")
    except Exception as e:
        # Re-raise if it's already our custom exception
        if "Error:" in str(e):
            raise
        raise Exception(f"Request failed: {str(e)}")


async def mcp_initialize(client, token):
    """Initialize MCP session"""
    params = {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "travel-agent", "version": "1.0.0"}
    }
    try:
        result = await _mcp_request(client, "initialize", params, token)
        # MCP initialize returns serverInfo, but we might also get session info
        # Return the full result to allow access to any session identifiers
        return result if isinstance(result, dict) else {"serverInfo": result}
    except Exception as e:
        print(f"Initialize error: {e}")
        raise


async def mcp_list_tools(client, token, session_id=None):
    """Get list of available tools from MCP server"""
    result = await _mcp_request(client, "tools/list", {}, token, session_id)
    return result.get("tools", [])


async def mcp_call_tool(client, tool_name, args, token, session_id=None):
    """Call an MCP tool"""
    params = {
        "name": tool_name,
        "arguments": args
    }
    result = await _mcp_request(client, "tools/call", params, token, session_id)
    
    # MCP tools/call can return content in different formats
    # Handle { "content": [...] } format
    if isinstance(result, dict) and "content" in result:
        content = result["content"]
        if isinstance(content, list) and len(content) > 0:
            # Extract text from content items
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
    
    # Return result as-is if it's already in a usable format
    return result


def call_mcp_tool(tool_name, args, token):
    """Synchronous wrapper for MCP tool calls"""
    async def _run():
        async with httpx.AsyncClient() as client:
            # Initialize session (some servers require this first)
            session_id = None
            try:
                server_info = await mcp_initialize(client, token)
                # Extract session ID if present (could be in different places)
                session_id = (
                    server_info.get("sessionId") or 
                    server_info.get("serverInfo", {}).get("sessionId") or
                    None
                )
                print(f"Initialized MCP session, session_id: {session_id}")
            except Exception as e:
                # Some servers don't require explicit initialize
                print(f"Initialize warning: {e}")
                # Don't fail here, try calling the tool anyway
            
            # Call tool
            try:
                result = await mcp_call_tool(client, tool_name, args, token, session_id)
                return result
            except Exception as e:
                print(f"Tool call error for {tool_name}: {e}")
                raise
    
    return asyncio.run(_run())


def get_mcp_tools(token):
    """Get list of MCP tools and convert to OpenAI format"""
    async def _run():
        async with httpx.AsyncClient() as client:
            # Initialize session (some servers require this first)
            session_id = None
            try:
                server_info = await mcp_initialize(client, token)
                # Extract session ID if present (could be in different places)
                session_id = (
                    server_info.get("sessionId") or 
                    server_info.get("serverInfo", {}).get("sessionId") or
                    None
                )
                print(f"Initialized MCP session for tools/list, session_id: {session_id}")
            except Exception as e:
                # Some servers don't require explicit initialize
                print(f"Initialize warning: {e}")
                # Don't fail here, try getting tools anyway
            
            try:
                tools = await mcp_list_tools(client, token, session_id)
                print(f"Fetched {len(tools)} tools from MCP server")
                return tools
            except Exception as e:
                print(f"Error fetching tools: {e}")
                raise
    
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
