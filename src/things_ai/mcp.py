from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_COMMAND = ("uvx", "things-mcp")
DEFAULT_PROTOCOL_VERSION = "2025-03-26"


class McpError(RuntimeError):
    """Raised when the Things MCP server returns an error or is unavailable."""


class StdioMcpClient:
    def __init__(
        self,
        command: list[str] | tuple[str, ...] | None = None,
        cwd: str | Path | None = None,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
    ) -> None:
        self.command = list(command or DEFAULT_COMMAND)
        self.cwd = None if cwd is None else str(cwd)
        self.protocol_version = protocol_version
        self._proc: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._initialized = False
        self.server_info: dict[str, Any] = {}

    @classmethod
    def from_environment(
        cls,
        *,
        command_text: str | None = None,
        cwd: str | Path | None = None,
    ) -> "StdioMcpClient":
        raw = command_text or os.environ.get("THINGS_MCP_COMMAND", "uvx things-mcp")
        return cls(command=shlex.split(raw), cwd=cwd)

    def __enter__(self) -> "StdioMcpClient":
        self.initialize()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def initialize(self) -> dict[str, Any]:
        if self._initialized:
            return self.server_info

        self._ensure_process()
        response = self._request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "things-ai-lab", "version": "0.1.0"},
            },
        )
        self.server_info = response
        self._notify("notifications/initialized", {})
        self._initialized = True
        return response

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list", {})
        tools = result.get("tools", [])
        if not isinstance(tools, list):
            raise McpError(f"Unexpected tools/list response: {result!r}")
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        return {
            "tool": name,
            "arguments": arguments or {},
            "result": result,
            "payload": extract_tool_payload(result),
        }

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        finally:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)
        self._proc = None
        self._initialized = False

    def _ensure_process(self) -> None:
        if self._proc is not None:
            return
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
        )

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})

        while True:
            message = self._read_message()
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise McpError(f"MCP error for {method}: {message['error']}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise McpError(f"Unexpected MCP result for {method}: {message!r}")
            return result

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, message: dict[str, Any]) -> None:
        self._ensure_process()
        assert self._proc is not None and self._proc.stdin is not None
        body = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        self._proc.stdin.write(body)
        self._proc.stdin.flush()

    def _read_message(self) -> dict[str, Any]:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise McpError("Things MCP closed the connection unexpectedly")
            stripped = line.strip()
            if not stripped:
                continue

            if stripped.lower().startswith(b"content-length:"):
                headers: dict[str, str] = {}
                current = line
                while True:
                    key, value = current.decode("utf-8").split(":", 1)
                    headers[key.strip().lower()] = value.strip()
                    current = self._proc.stdout.readline()
                    if not current:
                        raise McpError("Things MCP closed the connection unexpectedly")
                    if current in (b"\r\n", b"\n"):
                        break

                length = int(headers["content-length"])
                body = self._proc.stdout.read(length)
                return json.loads(body.decode("utf-8"))

            try:
                return json.loads(stripped.decode("utf-8"))
            except json.JSONDecodeError:
                continue


def extract_tool_payload(result: dict[str, Any]) -> Any:
    if "structuredContent" in result:
        return result["structuredContent"]

    content = result.get("content")
    if not isinstance(content, list):
        return result

    text_values: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "json" and "json" in item:
            return item["json"]
        if item.get("type") == "text":
            text = item.get("text", "")
            if not isinstance(text, str):
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                text_values.append(text)

    if len(text_values) == 1:
        return text_values[0]
    if text_values:
        return text_values
    return result