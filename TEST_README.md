# MCP Connection Test Script

## Setup

1. Make sure your `.env` file has the following variables:

```bash
MCP_SERVER_URL=https://your-mcp-server.run.app
TEST_AUTH_TOKEN=your_auth0_token_here
```

To get a test token, you can either:
- Use the Auth0 dashboard to generate a test token
- Login to your app and copy the token from the session
- Use Auth0's API to get a token programmatically

## Usage

### Basic test (list all tools):

```bash
python test_mcp.py
```

This will:
- Connect to your MCP server
- Initialize the session
- List all available tools with their schemas
- Show the full JSON definitions

### Test a specific tool:

Uncomment and modify the example at the bottom of `test_mcp.py`:

```python
# Example for math server:
await test_tool_call("calculate", {"expression": "2 + 2"})
await test_tool_call("solve_equation", {"equation": "x^2 - 5x + 6 = 0"})
```

## Example Output

```
🔗 Connecting to MCP server: https://math-mcp-969432608491.us-central1.run.app
🔑 Using token: eyJhbGciOi...
------------------------------------------------------------
🔄 Initializing MCP session...
✅ Session initialized!
   Server: math-server
   Version: 1.0.0
   Protocol: 2024-11-05
------------------------------------------------------------

📋 Fetching available tools...
✅ Found 4 tools:

1. calculate
   Description: Evaluate a mathematical expression
   Input Schema:
      - expression: string (required)
        Mathematical expression to evaluate

2. solve_equation
   Description: Solve an algebraic equation
   Input Schema:
      - equation: string (required)
        Equation to solve
...
```

## Troubleshooting

### Connection errors:
- Check your `MCP_SERVER_URL` is correct
- Verify the server is running
- Check your Auth0 token is valid

### Authentication errors:
- Make sure `TEST_AUTH_TOKEN` is set
- Verify the token hasn't expired
- Check the token has the right audience/scope

### Empty tools list:
- Check the MCP server is properly exposing tools
- Verify the server logs for any errors

