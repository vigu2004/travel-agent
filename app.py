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

async def mcp_initialize(client, token):
    payload = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "math-agent", "version": "1.0.0"}
        },
        "id": 0
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream"
    }

    async with httpx_sse.aconnect_sse(
        client, "POST", f"{MCP_SERVER_URL}/mcp",
        json=payload, headers=headers
    ) as stream:
        async for event in stream.aiter_sse():
            if event.event == "message":
                return stream.response.headers.get("mcp-session-id")

    return None


def call_mcp_tool(tool_name, args, token):
    async def _run():
        async with httpx.AsyncClient() as client:
            session_id = await mcp_initialize(client, token)

            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream",
                "mcp-session-id": session_id
            }

            payload = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": args},
                "id": 2
            }

            async with httpx_sse.aconnect_sse(
                client, "POST", f"{MCP_SERVER_URL}/mcp",
                json=payload, headers=headers
            ) as stream:
                async for event in stream.aiter_sse():
                    if event.event == "message":
                        return json.loads(event.data).get("result")

    return asyncio.run(_run())


# ---------------------------------------------------------
# MATH MCP TOOL WRAPPERS
# ---------------------------------------------------------

def calculate_mcp(expr, token):
    return call_mcp_tool("calculate", {"expression": expr}, token)

def solve_equation_mcp(eq, token):
    return call_mcp_tool("solve_equation", {"equation": eq}, token)

def differentiate_mcp(expr, var, token):
    return call_mcp_tool("differentiate", {"expression": expr, "variable": var}, token)

def integrate_mcp(expr, var, token):
    return call_mcp_tool("integrate", {"expression": expr, "variable": var}, token)


# ---------------------------------------------------------
# OPENAI TOOL SCHEMA
# ---------------------------------------------------------

OPENAI_FUNCTIONS = [
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Evaluate a mathematical expression",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string"}
                },
                "required": ["expression"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "solve_equation",
            "description": "Solve an algebraic equation for x",
            "parameters": {
                "type": "object",
                "properties": {
                    "equation": {"type": "string"}
                },
                "required": ["equation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "differentiate",
            "description": "Differentiate a function",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string"},
                    "variable": {"type": "string"}
                },
                "required": ["expression"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "integrate",
            "description": "Integrate a function",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string"},
                    "variable": {"type": "string"}
                },
                "required": ["expression"]
            }
        }
    }
]


# ---------------------------------------------------------
# FUNCTION EXECUTION HANDLER
# ---------------------------------------------------------

def execute_function(name, args, token):
    if name == "calculate":
        return calculate_mcp(args["expression"], token)

    if name == "solve_equation":
        return solve_equation_mcp(args["equation"], token)

    if name == "differentiate":
        return differentiate_mcp(args["expression"], args.get("variable", "x"), token)

    if name == "integrate":
        return integrate_mcp(args["expression"], args.get("variable", "x"), token)

    return {"error": "Unknown function"}


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

    messages = [
        {"role": "system", "content": "You are a math assistant."},
        {"role": "user", "content": user_message}
    ]

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        tools=OPENAI_FUNCTIONS,
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
                "content": json.dumps(out)
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
    return jsonify({
        "capabilities": [
            {"name": "Calculate", "example": "2+2"},
            {"name": "Solve equation", "example": "x^2 - 5x + 6 = 0"},
            {"name": "Differentiate", "example": "differentiate x^3"},
            {"name": "Integrate", "example": "integrate sin(x)"}
        ]
    })


# ---------------------------------------------------------
# RUN
# ---------------------------------------------------------

if __name__ == "__main__":
    print("Math Agent running at http://localhost:3000")
    app.run(host="0.0.0.0", port=3000, debug=True)
