# Things MCP Integration

**Date**: 2026-02-10
**Status**: ✅ Fully Connected
**MCP**: things (hald/things-mcp via uvx)

## Setup Summary

Successfully configured Things MCP for task management integration with Claude Code.

## Installation Steps

1. **Installed uv package manager**:
   ```bash
   brew install uv
   ```

2. **Added Things MCP**:
   ```bash
   claude mcp add things -- uvx things-mcp
   ```

3. **Verified connection**:
   ```bash
   claude mcp list
   ```
   Result: ✓ Connected

## Configuration

**Command**: `uvx things-mcp`
**Type**: stdio MCP server
**Implementation**: hald/things-mcp (Python-based, uses uv package runner)

## Prerequisites

- macOS with Things 3 installed
- "Enable Things URLs" enabled in Things preferences (Preferences > General > Things URLs)
- uv package manager installed

## Available Tools

The Things MCP provides these capabilities:
- `get-inbox` - Get tasks in Things inbox
- `get-today` - Get today's tasks
- `get-todos` - Get all to-do items
- `add-todo` - Create new tasks
- `search-todos` - Search for specific tasks

## Natural Language Commands

You can now use natural language to interact with Things:
- "What's in my Things inbox?"
- "Show me my tasks for today"
- "Add a task to Things: [task description]"
- "Search my Things for [keyword]"
- "What todos do I have?"

## Integration with PM OS

The Things MCP is automatically integrated with these skills:
- `/daily-plan` - Pulls tasks from Things inbox and today list
- `/weekly-plan` - References Things todos for planning
- `/meeting-notes` - Can create action items in Things
- `/prioritize` - Uses LNO framework with Things tasks

## Troubleshooting

If connection fails:
1. Ensure Things 3 is installed and has been opened at least once
2. Check that "Enable Things URLs" is toggled ON in Things preferences
3. Verify uv is installed: `which uvx` (should show `/usr/local/bin/uvx` or similar)
4. Try running manually: `uvx things-mcp` to see any error messages

## Alternative Implementations

Other Things MCP implementations exist but were not used:
- `drjforrest/mcp-things3` - AppleScript-based
- `excelsier/things-fastmcp` - Enterprise-focused

We chose `hald/things-mcp` as it's the most popular and stable community implementation.

## Next Steps

- ✅ Things MCP connected and working
- Test with: "What's in my Things inbox?"
- Can now pull tasks directly into daily planning workflows

---

**Setup completed**: 2026-02-10 01:37 EST
**Last verified**: Connection successful ✓

## Sources
- [GitHub - hald/things-mcp](https://github.com/hald/things-mcp)
- [Things 3 MCP Server Guide](https://skywork.ai/skypage/en/things-3-mcp-server-ai-engineer-productivity/1978646363076415488)
- [PulseMCP - Things3 Server](https://www.pulsemcp.com/servers/drjforrest-things3)
