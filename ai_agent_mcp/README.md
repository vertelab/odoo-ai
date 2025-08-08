# AI Agent MCP Integration

Dynamic MCP (Model Context Protocol) integration for AI Quests in Odoo.

## Overview

This module implements dynamic MCP tools for `ai.quest` records, allowing each quest marked as `available_to_mcp=True` to become a discoverable MCP tool. The implementation uses **dynamic MCP tools** rather than dynamic FastAPI endpoints, which provides better scalability and cleaner architecture.

## Architecture

### Key Components

1. **Dynamic Tool Discovery**: Each `ai.quest` record becomes an MCP tool.
2. **FastAPI Integration**: Uses Odoo's `fastapi` module for HTTP endpoints.
3. **MCP Server**: Built with `fastapi-mcp` library.
4. **Authentication**: Uses Odoo API keys for secure access.

### File Structure

```
ai_agent_mcp/
├── __init__.py                 # Module initialization
├── __manifest__.py            # Odoo module manifest
├── controllers/
│   └── main.py               # FastAPI routes (Health, Quests list)
├── models/
│   ├── __init__.py
│   └── fastapi_endpoint.py   # FastAPI app setup and MCP server mounting
├── services/
│   └── mcp_tools.py          # Core MCP tool logic (Quest execution, dynamic router creation)
├── security/
│   └── auth.py               # API Key authentication logic
├── data/
│   └── fastapi_data.xml      # FastAPI endpoint configuration
└── test_mcp_integration.py   # Test suite
```

## Features

### ✅ Dynamic MCP Tools
- **Automatic Discovery**: Quests marked `available_to_mcp=True` become tools.
- **Real-time Updates**: Tools refresh automatically when quests change.
- **Safe Naming**: Quest names converted to safe tool identifiers.
- **Rich Descriptions**: Includes quest descriptions in tool metadata.

### ✅ Security
- **API Key Authentication**: Uses Odoo's API key system for `Bearer` token validation.
- **User Context**: Tools execute with authenticated user's permissions.
- **Database Isolation**: Proper database connection handling per request.

### ✅ Flexible Input
- **Prompt Parameter**: Main input for quest execution.
- **Context Object**: Optional additional context (record IDs, model, etc.).
- **Error Handling**: Comprehensive error reporting.

## Installation

### Prerequisites

- Odoo 18.0+
- `fastapi` module for Odoo (ensure it's installed in your Odoo environment)
- `fastapi-mcp` Python package

### Installation Steps

1. **Install Python dependencies**:
   ```bash
   pip install fastapi-mcp
   ```

2. **Install the Odoo module**:
   - Copy the `ai_agent_mcp` folder to your Odoo addons directory.
   - Update the addons list in Odoo.
   - Install the "AI Agent MCP" module.

3. **Configure API Access**:
   - In Odoo, navigate to **Settings -> Users & Companies -> Users**.
   - Select the user who will interact with the MCP endpoint.
   - Go to the **API Keys** tab and click **Generate API Key**.
   - Copy the generated key. This will be your `API_KEY`.
   - Ensure quests have `available_to_mcp=True`.

## Usage

### 1. Enable Quests for MCP

In Odoo, navigate to **AI → Quests** and:
1. Select the quests you want to expose as MCP tools.
2. Check the "Available to MCP" checkbox.
3. Save the changes.

### 2. Access MCP Endpoint

The MCP server is available at:
- **Base URL**: `http://localhost:7069/api` (assuming Odoo is running on port 7069)
- **MCP Endpoint**: `/mcp`
- **Full MCP URL**: `http://localhost:7069/api/mcp`

### 3. Authentication

All protected endpoints require an API Key in the `Authorization` header using the `Bearer` scheme.

Example Header:
`Authorization: Bearer <YOUR_GENERATED_API_KEY>`

### 4. MCP Client Configuration

Example `mcp_config.json` for your MCP client:

```json
{
  "mcpServers": {
    "odoo-ai-quest-mcp": {
      "serverUrl": "http://localhost:7069/api/mcp",
      "headers": {
        "Authorization": "Bearer <YOUR_GENERATED_API_KEY>"
      }
    }
  }
}
```

**Remember to replace `<YOUR_GENERATED_API_KEY>` with the actual API key generated in Odoo.**

## API Endpoints

- **GET** `/api/health`
  - **Description**: Health check endpoint. Does NOT require authentication.

- **GET** `/api/quests`
  - **Description**: Lists all available AI quests for MCP. Requires API Key authentication.

- **POST** `/api/mcp`
  - **Description**: The main MCP protocol endpoint for tool listing and execution. Requires API Key authentication.

## Dynamic Tool Examples

### Quest: "Customer Analysis"
- **Tool Name**: `customer_analysis`
- **Description**: "Customer Analysis: Analyze customer behavior and patterns"
- **Input**: `{ "prompt": "Analyze customer purchase patterns for last month" }`

### Quest: "Sales Forecast"
- **Tool Name**: `sales_forecast`
- **Description**: "Sales Forecast: Predict future sales based on historical data"
- **Input**: `{ "prompt": "Forecast Q4 sales", "context": { "model": "sale.order" } }`

## Development

### Adding New Features

1. **New Quest Parameters**: Extend the `inputSchema` in `mcp_tools.py`.
2. **Custom Authentication**: Modify the authentication logic in `security/auth.py`.
3. **Response Formatting**: Update the response processing in `execute_quest_by_id`.


## Troubleshooting

### Common Issues

1. **No tools available**
   - Check that quests have `available_to_mcp=True`.
   - Verify database connection.
   - Check Odoo logs for errors.

2. **Authentication failures**
   - Verify API key is valid and correctly generated in Odoo.
   - Ensure the `Authorization: Bearer <API_KEY>` header is correctly formatted.
   - Check user has proper permissions.
   - Ensure API key scope includes 'rpc'.

3. **MCP client connection issues**
   - Verify endpoint URL is correct (`http://localhost:7069/api/mcp`).
   - Check firewall settings.

### Debug Commands

```bash

# Test Health Check (no auth required)
curl -X GET http://localhost:7069/api/health

# Test Quests List (auth required)
curl -X GET \
  http://localhost:7069/api/quests \
  -H "Authorization: Bearer <YOUR_GENERATED_API_KEY>"

# Test MCP endpoint (auth required)
curl -X POST \
  http://localhost:7069/api/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <YOUR_GENERATED_API_KEY>" \
  -d '{"type": "list_tools"}'
```

## Performance Considerations

- **Tool Discovery**: Tools are cached and refreshed on demand.
- **Database Connections**: Uses connection pooling via Odoo's registry.
- **Memory Usage**: Minimal overhead as tools are generated from database records.

s

