#!/usr/bin/env python3
"""
Test script to verify MCP connection and list available tools
"""

import asyncio
import os
import json
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# Load environment variables
load_dotenv()

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL")
AUTH_TOKEN = os.getenv("TEST_AUTH_TOKEN")  # Add your test token to .env


async def test_mcp_connection():
    """Test MCP connection and list tools"""
    
    print(f"🔗 Connecting to MCP server: {MCP_SERVER_URL}")
    print(f"🔑 Using token: {AUTH_TOKEN[:10]}..." if AUTH_TOKEN else "❌ No token provided")
    print("-" * 60)
    
    headers = {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Accept": "application/json, text/event-stream"
    }
    
    try:
        async with streamablehttp_client(
            url=f"{MCP_SERVER_URL}/mcp",
            headers=headers,
            timeout=30.0,
            sse_read_timeout=300.0
        ) as (read, write, get_session_id):
            async with ClientSession(read, write) as session:
                # Initialize session
                print("🔄 Initializing MCP session...")
                init_result = await session.initialize()
                print(f"✅ Session initialized!")
                print(f"   Server: {init_result.serverInfo.name if hasattr(init_result, 'serverInfo') else 'Unknown'}")
                print(f"   Version: {init_result.serverInfo.version if hasattr(init_result, 'serverInfo') else 'Unknown'}")
                print(f"   Protocol: {init_result.protocolVersion if hasattr(init_result, 'protocolVersion') else 'Unknown'}")
                print("-" * 60)
                
                # List tools
                print("\n📋 Fetching available tools...")
                tools_result = await session.list_tools()
                tools = tools_result.tools if hasattr(tools_result, 'tools') else []
                
                print(f"✅ Found {len(tools)} tools:\n")
                
                for i, tool in enumerate(tools, 1):
                    print(f"{i}. {tool.name}")
                    print(f"   Description: {tool.description}")
                    
                    # Print input schema
                    if hasattr(tool, 'inputSchema') and tool.inputSchema:
                        schema = tool.inputSchema
                        if isinstance(schema, dict):
                            print(f"   Input Schema:")
                            if 'properties' in schema:
                                for prop_name, prop_def in schema['properties'].items():
                                    prop_type = prop_def.get('type', 'any')
                                    prop_desc = prop_def.get('description', '')
                                    required = prop_name in schema.get('required', [])
                                    req_marker = " (required)" if required else ""
                                    print(f"      - {prop_name}: {prop_type}{req_marker}")
                                    if prop_desc:
                                        print(f"        {prop_desc}")
                    print()
                
                print("-" * 60)
                print("\n📄 Full tool definitions (JSON):\n")
                tools_json = [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.inputSchema if hasattr(tool, 'inputSchema') else None
                    }
                    for tool in tools
                ]
                print(json.dumps(tools_json, indent=2))
                
                print("\n" + "=" * 60)
                print("✅ MCP connection test successful!")
                print("=" * 60)
                
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


async def test_tool_call(tool_name: str, arguments: dict):
    """Test calling a specific tool"""
    
    print(f"\n🧪 Testing tool call: {tool_name}")
    print(f"   Arguments: {json.dumps(arguments)}")
    print("-" * 60)
    
    headers = {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Accept": "application/json, text/event-stream"
    }
    
    try:
        async with streamablehttp_client(
            url=f"{MCP_SERVER_URL}/mcp",
            headers=headers,
            timeout=30.0,
            sse_read_timeout=300.0
        ) as (read, write, get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                # Call tool
                result = await session.call_tool(tool_name, arguments)
                
                print(f"✅ Tool call successful!")
                print(f"   Result type: {type(result)}")
                
                # Parse content from result
                if hasattr(result, 'content') and result.content:
                    print(f"   Content items: {len(result.content)}")
                    for item in result.content:
                        if hasattr(item, 'text'):
                            print(f"   Text: {item.text}")
                        else:
                            print(f"   Item: {item}")
                else:
                    print(f"   Result: {result}")
                
                print("-" * 60)
                
    except Exception as e:
        print(f"❌ Tool call failed: {str(e)}")
        import traceback
        traceback.print_exc()


async def main():
    """Run all tests"""
    
    if not MCP_SERVER_URL:
        print("❌ MCP_SERVER_URL not set in .env file")
        return
    
    if not AUTH_TOKEN:
        print("⚠️  TEST_AUTH_TOKEN not set in .env file")
        print("   Continuing without authentication (may fail)...")
        print()
    
    # Test connection and list tools
    success = await test_mcp_connection()
    
    if success:
        # Example tool call test (uncomment and modify as needed)
        # await test_tool_call("calculate", {"expression": "2 + 2"})
        pass


if __name__ == "__main__":
    asyncio.run(main())

