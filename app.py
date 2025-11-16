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
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from typing import Any
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
# MCP CLIENT HELPERS (Using Official MCP SDK)
# ---------------------------------------------------------

def call_mcp_tool(tool_name, args, token):
    """Call MCP tool using official SDK"""
    async def _run():
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream"
        }
        
        async with streamablehttp_client(
            url=f"{MCP_SERVER_URL}/mcp",
            headers=headers,
            timeout=30.0,
            sse_read_timeout=300.0
        ) as (read, write, get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                logger.info(f"✅ MCP session initialized")
                
                # Call tool
                result = await session.call_tool(tool_name, args)
                
                # Parse content from result
                if hasattr(result, 'content') and result.content:
                    texts = []
                    for item in result.content:
                        if hasattr(item, 'text'):
                            texts.append(item.text)
                        else:
                            texts.append(str(item))
                    return "\n".join(texts) if texts else str(result)
                
                return str(result)
    
    return asyncio.run(_run())


def get_mcp_tools(token):
    """Get list of MCP tools using official SDK"""
    async def _run():
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream"
        }
        
        async with streamablehttp_client(
            url=f"{MCP_SERVER_URL}/mcp",
            headers=headers,
            timeout=30.0,
            sse_read_timeout=300.0
        ) as (read, write, get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                logger.info(f"✅ MCP session initialized")
                
                # List tools
                tools_result = await session.list_tools()
                tools = tools_result.tools if hasattr(tools_result, 'tools') else []
                logger.info(f"📋 Fetched {len(tools)} tools from MCP server")
                
                # Convert to dict format
                return [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.inputSchema
                    }
                    for tool in tools
                ]
    
    return asyncio.run(_run())


def fix_schema_recursive(schema):
    """Recursively fix schema to be OpenAI-compatible"""
    if not isinstance(schema, dict):
        return schema
    
    fixed = {}
    
    # Copy type (default to string if not specified)
    if "type" in schema:
        fixed["type"] = schema["type"]
    else:
        # If no type specified but has properties, it's an object
        if "properties" in schema:
            fixed["type"] = "object"
        elif "items" in schema:
            fixed["type"] = "array"
        else:
            # Default to string for safety
            return {"type": "string", "description": schema.get("description", "")}
    
    # Handle array types - OpenAI requires 'items'
    if fixed.get("type") == "array":
        if "items" in schema and schema["items"]:
            # Recursively fix nested items
            fixed["items"] = fix_schema_recursive(schema["items"])
        else:
            # Default to number if items not specified or empty
            fixed["items"] = {"type": "number"}
        
        # Ensure items always has a type
        if isinstance(fixed["items"], dict) and "type" not in fixed["items"]:
            fixed["items"]["type"] = "number"
    
    # Handle object types
    if fixed.get("type") == "object":
        if "properties" in schema:
            fixed["properties"] = {}
            for key, value in schema["properties"].items():
                fixed["properties"][key] = fix_schema_recursive(value)
        if "required" in schema:
            fixed["required"] = schema["required"]
        # Add additionalProperties: false for stricter validation
        if "additionalProperties" in schema:
            fixed["additionalProperties"] = schema["additionalProperties"]
    
    # Copy other common schema properties
    for key in ["description", "enum", "default", "minimum", "maximum", 
                "minItems", "maxItems", "minLength", "maxLength",
                "pattern", "format", "title", "examples"]:
        if key in schema:
            fixed[key] = schema[key]
    
    return fixed


def convert_mcp_tool_to_openai(mcp_tool):
    """Convert MCP tool definition to OpenAI function format"""
    properties = {}
    required = []
    
    if "inputSchema" in mcp_tool:
        schema = mcp_tool["inputSchema"]
        if schema.get("type") == "object" and "properties" in schema:
            for prop_name, prop_def in schema["properties"].items():
                # Recursively fix the schema to ensure OpenAI compatibility
                properties[prop_name] = fix_schema_recursive(prop_def)
            
            required = schema.get("required", [])
    
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
        tool_calls_info = []
        results = []
        
        for tool_call in msg.tool_calls:
            fname = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            
            # Track tool call for UI
            tool_calls_info.append({
                "name": fname,
                "arguments": args
            })
            
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
        
        return jsonify({
            "message": second.choices[0].message.content,
            "tool_calls": tool_calls_info  # Include tool call info
        })

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
