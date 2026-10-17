"""Owned synthetic HTTP protocol responses for installed actual Google SDK probes."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@contextmanager
def provider(mutate=None, before=None):
    captured = []
    counts = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            instruction = body["systemInstruction"]["parts"][0]["text"]
            role = next(
                r
                for r in ("availability", "reservation", "compensation")
                if "You are the " + r + " specialist" in instruction
            )
            step = counts.get(role, 0)
            counts[role] = step + 1
            captured.append(
                {
                    "role": role,
                    "body": body,
                    "headers": dict(self.headers),
                    "path": self.path,
                }
            )
            if before:
                before(role, step, captured)
            responses = [
                p["functionResponse"]
                for c in body["contents"]
                for p in c.get("parts", [])
                if "functionResponse" in p
            ]
            inspections = [
                p["response"] for p in responses if p["name"] == "inspect_request"
            ]
            current = inspections[-1] if inspections else None
            if current is not None and "result" in current:
                current = current["result"]
            name, args = None, {}
            if role == "availability":
                if step == 0:
                    name = "inspect_request"
            elif role == "reservation":
                needs = (
                    current["request"]["needs"]
                    if current
                    else ["ROOM-A", "PROJECTOR-A"]
                )
                count = len(needs)
                if step % 2 == 0 and step <= 2 * count + 2:
                    name = "inspect_request"
                elif step < 2 * count:
                    ident = needs[step // 2]
                    row = next(r for r in current["resources"] if r["id"] == ident)
                    name, args = "hold_resource", {
                        "resource_id": ident,
                        "expected_revision": row["revision"],
                    }
                elif step == 2 * count + 1:
                    name = "commit_allocation"
            else:
                # Whether compensation is needed is read from real tool state.
                if step == 0 or step == 2:
                    name = "inspect_request"
                elif step == 1 and current["request"]["status"] in (
                    "holding",
                    "needs_compensation",
                ):
                    name = "release_holds"
            part = (
                {"functionCall": {"name": name, "args": args}}
                if name
                else {
                    "text": "The synthetic specialist reports only the inspected committed state."
                }
            )
            value = {
                "responseId": "owned-" + str(len(captured)),
                "modelVersion": "gemini-owned-native",
                "candidates": [
                    {
                        "index": 0,
                        "finishReason": "STOP",
                        "content": {"role": "model", "parts": [part]},
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 12,
                },
            }
            raw = None
            status = 200
            if mutate:
                returned = mutate(role, step, value, captured)
                if type(returned) is bytes:
                    raw = returned
                elif type(returned) is int:
                    status = returned
            raw = json.dumps(value).encode() if raw is None else raw
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), captured
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
