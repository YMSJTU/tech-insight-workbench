"""Minimal Agent Client Protocol (ACP) client used by the worker.

The worker no longer talks to a model HTTP endpoint. It starts an ACP agent
(for example ``opencode acp``) once per process and asks that agent to run
each extraction turn in a fresh session. Model choice and provider
credentials stay inside the agent configuration.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT, agent_config

PROTOCOL_VERSION = 1


class AcpError(RuntimeError):
    pass


class AcpCancelled(AcpError):
    pass


class AcpTimeout(AcpError):
    pass


@dataclass
class AcpResult:
    text: str
    usage: int
    stop_reason: str
    tool_calls: int
    session_id: str


def _command():
    raw = agent_config()["command"]
    if not raw:
        raise AcpError("未配置 ACP_COMMAND（例如 opencode acp）")
    parts = shlex.split(raw, posix=False)
    executable = parts[0].strip('"')
    resolved = shutil.which(executable) or (
        executable if Path(executable).exists() else ""
    )
    if not resolved:
        raise AcpError(f"找不到 ACP agent 可执行文件：{executable}")
    parts[0] = resolved
    # CreateProcess cannot run .cmd/.bat shims directly on Windows.
    if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        parts = ["cmd", "/c"] + parts
    return parts


def _child_env():
    env = dict(os.environ)
    home = agent_config()["home"]
    if home:
        base = Path(home)
        if not base.is_absolute():
            base = ROOT / base
        base = base.resolve()
        for suffix in (".config", ".local/share", ".cache", ".local/state", "tmp"):
            (base / suffix).mkdir(parents=True, exist_ok=True)
        env.update(
            HOME=str(base),
            USERPROFILE=str(base),
            XDG_CONFIG_HOME=str(base / ".config"),
            XDG_DATA_HOME=str(base / ".local/share"),
            XDG_CACHE_HOME=str(base / ".cache"),
            XDG_STATE_HOME=str(base / ".local/state"),
            TMP=str(base / "tmp"),
            TEMP=str(base / "tmp"),
            TMPDIR=str(base / "tmp"),
        )
    return env


class AcpProcess:
    def __init__(self):
        cfg = agent_config()
        self.timeout = cfg["timeout"]
        self.command = _command()
        self.cwd = str(ROOT)
        self._stderr = deque(maxlen=40)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._next_id = 0
        self._capture: dict | None = None
        self._session_id = ""
        self._closing = False
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        command = list(self.command)
        if "acp" not in command[1:]:
            command.append("acp")
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=self.cwd,
            env=_child_env(),
            creationflags=flags,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        init = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {},
                "clientInfo": {
                    "name": "technical-insight-workbench",
                    "title": "技术洞察工作台",
                    "version": "0.1.0",
                },
            },
            timeout=min(60, self.timeout),
        )
        version = init.get("protocolVersion")
        if version != PROTOCOL_VERSION:
            self.close()
            raise AcpError(f"ACP 协议版本不兼容：{version}")
        self.capabilities = init.get("agentCapabilities") or {}
        self.agent = init.get("agentInfo") or {}

    def _read_stdout(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if line:
                    self._queue.put(line)
        finally:
            self._queue.put(None)

    def _read_stderr(self):
        for line in self.proc.stderr:
            line = line.rstrip()
            if line:
                self._stderr.append(line[:400])

    def _send(self, message):
        if self.proc.poll() is not None:
            raise AcpError("ACP agent 已退出：" + self._tail())
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise AcpError(f"ACP agent 写入失败：{exc}") from None

    def _tail(self):
        return " | ".join(list(self._stderr)[-3:])[:600]

    def _respond(self, request_id, result=None, error=None):
        message = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            message["error"] = {"code": -32601, "message": error}
        else:
            message["result"] = result
        self._send(message)

    def _handle_agent_request(self, message):
        method = message["method"]
        if method == "session/request_permission":
            # The extraction prompt forbids tools; deny anything unexpected.
            self._respond(
                message["id"], result={"outcome": {"outcome": "cancelled"}}
            )
        else:
            self._respond(message["id"], error=f"client does not support {method}")

    def _handle_notification(self, message):
        if message.get("method") != "session/update":
            return
        params = message.get("params") or {}
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")
        if self._capture is None:
            return
        if kind == "agent_message_chunk":
            content = update.get("content") or {}
            if content.get("type") == "text":
                self._capture["parts"].append(content.get("text", ""))
        elif kind == "tool_call":
            self._capture["tool_calls"] += 1
        elif kind == "usage_update":
            cost = update.get("cost") or {}
            self._capture["cost"] = cost

    def request(self, method, params, timeout=None, cancel_check=None):
        self._next_id += 1
        request_id = self._next_id
        self._send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        deadline = time.time() + (timeout or self.timeout)
        cancel_sent = False
        while True:
            if not cancel_sent and cancel_check and cancel_check():
                try:
                    self._send(
                        {
                            "jsonrpc": "2.0",
                            "method": "session/cancel",
                            "params": {"sessionId": self._session_id},
                        }
                    )
                except AcpError:
                    raise AcpCancelled("任务已取消") from None
                cancel_sent = True
            if time.time() > deadline:
                self._kill()
                raise AcpTimeout(f"ACP 请求超时：{method}")
            try:
                line = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            if line is None:
                raise AcpError("ACP agent 提前关闭输出：" + self._tail())
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id and "method" not in message:
                if cancel_sent:
                    raise AcpCancelled("任务已取消")
                if "error" in message:
                    raise AcpError(
                        f"ACP {method} 失败：{(message['error'] or {}).get('message', '')}"
                    )
                return message.get("result") or {}
            if "method" in message and "id" in message:
                self._handle_agent_request(message)
            elif "method" in message:
                self._handle_notification(message)

    def new_session(self, model):
        result = self.request(
            "session/new", {"cwd": self.cwd, "mcpServers": []}, timeout=120
        )
        session_id = result.get("sessionId")
        if not session_id:
            raise AcpError("ACP session/new 未返回 sessionId")
        self._session_id = session_id
        if model:
            options = {
                option.get("id"): option
                for option in result.get("configOptions") or []
            }
            current = options.get("model")
            if current and current.get("currentValue") != model:
                values = {
                    item.get("value")
                    for item in current.get("options") or []
                    if isinstance(item, dict)
                }
                if model not in values:
                    raise AcpError(
                        "ACP 模型不可用："
                        + model
                        + "；可选："
                        + ", ".join(sorted(v for v in values if v))[:400]
                    )
                updated = self.request(
                    "session/set_config_option",
                    {"sessionId": session_id, "configId": "model", "value": model},
                    timeout=60,
                )
                chosen = next(
                    (
                        option.get("currentValue")
                        for option in updated.get("configOptions") or []
                        if option.get("id") == "model"
                    ),
                    None,
                )
                if chosen != model:
                    raise AcpError(f"ACP 切换模型失败：当前为 {chosen}")
        return session_id

    def prompt(self, text, model, cancel_check=None):
        session_id = self.new_session(model)
        self._capture = {"parts": [], "tool_calls": 0, "cost": {}}
        try:
            result = self.request(
                "session/prompt",
                {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": text}],
                },
                timeout=self.timeout,
                cancel_check=cancel_check,
            )
        finally:
            capture = self._capture
            self._capture = None
        # Sessions stay open for the lifetime of the agent process. Closing one
        # makes opencode terminate its ACP server, which would break later turns.
        stop_reason = result.get("stopReason", "")
        if stop_reason == "cancelled":
            raise AcpCancelled("任务已取消")
        if stop_reason not in {"end_turn", "max_tokens"}:
            raise AcpError(f"ACP 会话停止原因：{stop_reason}")
        usage = result.get("usage") or {}
        return AcpResult(
            text="".join(capture["parts"]).strip(),
            usage=int(usage.get("totalTokens") or 0),
            stop_reason=stop_reason,
            tool_calls=capture["tool_calls"],
            session_id=session_id,
        )

    def _kill(self):
        try:
            if self.proc.poll() is None:
                self.proc.kill()
        except OSError:
            pass

    def close(self):
        if self._closing:
            return
        self._closing = True
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except OSError:
            pass


_LOCK = threading.Lock()
_PROCESS: AcpProcess | None = None


def _process():
    global _PROCESS
    if _PROCESS is None or _PROCESS.proc.poll() is not None:
        if _PROCESS is not None:
            _PROCESS.close()
        _PROCESS = AcpProcess()
    return _PROCESS


def prompt(text, model="", cancel_check=None):
    with _LOCK:
        try:
            return _process().prompt(text, model, cancel_check)
        except AcpError:
            global _PROCESS
            if _PROCESS is not None:
                _PROCESS.close()
                _PROCESS = None
            raise


def shutdown():
    global _PROCESS
    with _LOCK:
        if _PROCESS is not None:
            _PROCESS.close()
            _PROCESS = None


atexit.register(shutdown)
