#!/usr/bin/env python3
"""
Travel Agent Backend Server
Connects OpenAI to a hosted MCP server for travel bookings
Now uses Auth0 user authentication instead of email/password.
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
from datetime import datetime
from openai import OpenAI

# Load environment variables
load_dotenv()

# Flask setup
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key")  # CHANGE IN PRODUCTION
CORS(app)

# Config
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ============================================================
# AUTH0 CONFIG
# ============================================================

AUTH0_DOMAIN = os.environ["AUTH0_DOMAIN"]
AUTH0_CLIENT_ID = os.environ["AUTH0_CLIENT_ID"]
AUTH0_CLIENT_SECRET = os.environ["AUTH0_CLIENT_SECRET"]
AUTH0_AUDIENCE = os.environ["AUTH0_AUDIENCE"]
AUTH0_CALLBACK_URL = os.environ["AUTH0_CALLBACK_URL"]  # /callback URL

AUTH0_BASE = f"https://{AUTH0_DOMAIN}"
AUTH0_AUTHORIZE_URL = f"{AUTH0_BASE}/authorize"
AUTH0_TOKEN_URL = f"{AUTH0_BASE}/oauth/token"
AUTH0_USERINFO_URL = f"{AUTH0_BASE}/userinfo"

# ============================================================
# AUTH ROUTES (Auth0 Login → Callback → Logout)
# ============================================================

@app.route("/api/auth/login")
def auth_login():
    """Redirect user to Auth0 login page."""
    params = {
        "response_type": "code",
        "client_id": AUTH0_CLIENT_ID,
        "redirect_uri": AUTH0_CALLBACK_URL,
        "scope": "openid profile email",
        "audience": AUTH0_AUDIENCE
    }
    return jsonify({
        "redirect": f"{AUTH0_AUTHORIZE_URL}?{urlencode(params)}"
    })


@app.route("/callback")
def auth_callback():
    """Auth0 redirects here after user logs in."""
    code = request.args.get("code")
    if not code:
        return "Missing ?code from Auth0", 400

    # Exchange code for tokens
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
        return f"Auth0 error: {token_data}", 401

    # Fetch user profile
    userinfo_res = requests.get(
        AUTH0_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"}
    )
    userinfo = userinfo_res.json()

    # Save in session
    session["mcp_token"] = access_token
    session["mcp_user"] = userinfo

    # Redirect back to UI
    return render_template("index.html", user=userinfo)


@app.route("/api/auth/logout")
def auth_logout():
    session.clear()
    return jsonify({"success": True})


@app.route("/api/auth/status")
def auth_status():
    token = session.get("mcp_token")
    user = session.get("mcp_user")

    return jsonify({
        "success": True,
        "authenticated": bool(token and user),
        "user": user if user else None
    })


# ============================================================
# MCP CLIENT
# ============================================================

async def mcp_initialize(client, token):
    payload = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "travel-agent", "version": "1.0.0"}
        },
        "id": 0
    }

    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}

    async with httpx_sse.aconnect_sse(
        client, "POST", f"{MCP_SERVER_URL}/mcp",
        json=payload, headers=headers
    ) as stream:
        async for event in stream.aiter_sse():
            if event.event == "message":
                return stream.response.headers.get("mcp-session-id")
    return None


def get_mcp_tools(token):
    async def _run():
        async with httpx.AsyncClient() as client:
            session_id = await mcp_initialize(client, token)
            if not session_id:
                return []

            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream",
                "mcp-session-id": session_id
            }

            payload = {
                "jsonrpc": "2.0",
                "method": "tools/list",
                "params": {},
                "id": 1
            }

            async with httpx_sse.aconnect_sse(
                client, "POST", f"{MCP_SERVER_URL}/mcp",
                json=payload, headers=headers
            ) as stream:
                async for event in stream.aiter_sse():
                    if event.event == "message":
                        data = json.loads(event.data)
                        return data.get("result", {}).get("tools", [])
    return asyncio.run(_run())


def call_mcp_tool(tool_name, arguments, token):
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
                "params": {"name": tool_name, "arguments": arguments},
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


# ============================================================
# TOOL WRAPPERS
# ============================================================

def search_flights_mcp(origin, destination, travel_class=None, token=None):
    args = {"origin": origin, "destination": destination}
    if travel_class:
        args["cabin_class"] = travel_class
    return call_mcp_tool("search_flights", args, token)


def search_hotels_mcp(location, min_rating=None, token=None):
    args = {"location": location}
    if min_rating:
        args["min_rating"] = min_rating
    return call_mcp_tool("search_hotels", args, token)


def search_car_rentals_mcp(location, car_type=None, token=None):
    args = {"location": location}
    if car_type:
        args["car_type"] = car_type
    return call_mcp_tool("search_car_rentals", args, token)


def book_flight_mcp(flight_id, passenger_name, num_seats=1, token=None):
    args = {"flight_id": flight_id, "passenger_name": passenger_name, "num_seats": num_seats}
    return call_mcp_tool("book_flight", args, token)


# ============================================================
# OPENAI CHAT + FUNCTION CALLING
# ============================================================

OPENAI_FUNCTIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_flights",
            "description": "Search for available flights",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string"},
                    "destination": {"type": "string"},
                    "travel_class": {"type": "string"}
                },
                "required": ["origin", "destination"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_hotels",
            "description": "Search hotels",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "min_rating": {"type": "integer"}
                },
                "required": ["location"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_car_rentals",
            "description": "Search car rentals",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "car_type": {"type": "string"}
                },
                "required": ["location"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "book_flight",
            "description": "Book a flight",
            "parameters": {
                "type": "object",
                "properties": {
                    "flight_id": {"type": "string"},
                    "passenger_name": {"type": "string"},
                    "num_seats": {"type": "integer"}
                },
                "required": ["flight_id", "passenger_name"]
            }
        }
    }
]


def execute_function(fname, args, token):
    if fname == "search_flights":
        return search_flights_mcp(args["origin"], args["destination"], args.get("travel_class"), token)
    if fname == "search_hotels":
        return search_hotels_mcp(args["location"], args.get("min_rating"), token)
    if fname == "search_car_rentals":
        return search_car_rentals_mcp(args["location"], args.get("car_type"), token)
    if fname == "book_flight":
        return book_flight_mcp(args["flight_id"], args["passenger_name"], args.get("num_seats", 1), token)
    return {"error": "Unknown function"}


# ============================================================
# CHAT ROUTE
# ============================================================

@app.route("/api/chat", methods=["POST"])
def chat():
    token = session.get("mcp_token")
    if not token:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    data = request.json
    user_message = data.get("message")
    history = data.get("history", [])

    messages = [
        {
            "role": "system",
            "content": "You are a helpful travel agent assistant."
        },
        *history,
        {"role": "user", "content": user_message}
    ]

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        tools=OPENAI_FUNCTIONS,
        tool_choice="auto"
    )

    msg = response.choices[0].message

    # Tool call handling
    if msg.tool_calls:
        results = []
        for tool_call in msg.tool_calls:
            fname = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            result = execute_function(fname, args, token)
            results.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": fname,
                "content": json.dumps(result)
            })

        messages.append(msg)
        messages.extend(results)

        second = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages
        )

        return jsonify({
            "success": True,
            "message": second.choices[0].message.content
        })

    return jsonify({"success": True, "message": msg.content})


# ============================================================
# MCP STATUS
# ============================================================

@app.route("/api/mcp/status")
def mcp_status():
    token = session.get("mcp_token")
    if not token:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    tools = get_mcp_tools(token)
    return jsonify({
        "success": True,
        "tools_available": len(tools),
        "tools": [t["name"] for t in tools]
    })
@app.route("/")
def home():
    user = session.get("mcp_user")
    return render_template("index.html", user=user)

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("Travel Agent running at http://localhost:3000")
    app.run(host="0.0.0.0", port=3000, debug=True)
