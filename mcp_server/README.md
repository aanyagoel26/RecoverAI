# RecoverAI MCP server

Lets any MCP client (Claude Desktop, Cursor, ChatGPT connectors…) work with a merchant's PayPal recovery data.
Read and draft tools are safe; nothing customer-facing happens without the merchant approving in the dashboard.

```bash
pip install -r requirements.txt
RECOVERAI_API_URL=http://localhost:8000 python server.py          # stdio
RECOVERAI_API_URL=https://your-api.onrender.com python server.py --http 8765   # streamable HTTP at /mcp
```

Claude Desktop (`claude_desktop_config.json`):
```json
{ "mcpServers": { "recoverai": { "command": "python", "args": ["/path/to/mcp_server/server.py"],
  "env": { "RECOVERAI_API_URL": "http://localhost:8000", "RECOVERAI_APP_URL": "http://localhost:5173" } } } }
```
Omit `RECOVERAI_EMAIL`/`RECOVERAI_PASSWORD` to use the demo workspace.

Tools: `get_revenue_at_risk`, `list_priority_cases`, `explain_payment`, `get_agent_trace`, `draft_recovery_message`,
`request_recovery_approval`, `revenue_forecast`, `run_failure_lab` (sandbox only).

Try: *"Which failed PayPal payments should I chase today, and why?"* → *"Explain TXN-2048 and show me the guardrail trace."*
