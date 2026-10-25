"""Tiny ACP agent used by the protocol test; speaks newline-delimited JSON-RPC."""

import json
import sys


def send(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def config(value):
    return [
        {
            "id": "model",
            "name": "Model",
            "type": "select",
            "currentValue": value,
            "options": [
                {"value": "fake/default", "name": "Default"},
                {"value": "fake/model", "name": "Fake model"},
            ],
        }
    ]


for line in sys.stdin:
    if not line.strip():
        continue
    message = json.loads(line)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": 1,
                    "agentCapabilities": {"sessionCapabilities": {"close": {}}},
                    "agentInfo": {"name": "fake-acp", "version": "0"},
                },
            }
        )
    elif method == "session/new":
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "sessionId": "sess_fake",
                    "configOptions": config("fake/default"),
                },
            }
        )
    elif method == "session/set_config_option":
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"configOptions": config(message["params"]["value"])},
            }
        )
    elif method == "session/prompt":
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "sess_fake",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {
                            "type": "text",
                            "text": '{"queries": ["cache compression"]}',
                        },
                    },
                },
            }
        )
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"stopReason": "end_turn", "usage": {"totalTokens": 42}},
            }
        )
    elif method == "session/close":
        send({"jsonrpc": "2.0", "id": request_id, "result": {}})
    elif request_id is not None:
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "unknown method"},
            }
        )
