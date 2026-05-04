import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DEFAULT_MODEL = "claude-sonnet-4-20250514"


def load_env():
    env = {}
    if not ENV_PATH.exists():
        return env

    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def _send_json(self, status_code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/health":
            env = load_env()
            self._send_json(
                200,
                {
                    "ok": True,
                    "configured": bool(env.get("ANTHROPIC_API_KEY")),
                    "model": env.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
                },
            )
            return

        super().do_GET()

    def do_POST(self):
        if self.path != "/api/anthropic/messages":
            self._send_json(404, {"error": "Маршрут не найден."})
            return

        env = load_env()
        api_key = env.get("ANTHROPIC_API_KEY")
        model = env.get("ANTHROPIC_MODEL", DEFAULT_MODEL)

        if not api_key:
            self._send_json(
                500,
                {
                    "error": "В файле .env не найден ANTHROPIC_API_KEY.",
                },
            )
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)

        try:
            payload = json.loads(raw_body or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Некорректный JSON в запросе."})
            return

        prompt = payload.get("prompt", "").strip()
        max_tokens = int(payload.get("max_tokens", 1024))

        if not prompt:
            self._send_json(400, {"error": "Пустой prompt."})
            return

        anthropic_payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }

        request = Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(anthropic_payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
                self._send_json(200, data)
        except HTTPError as exc:
            try:
                details = json.loads(exc.read().decode("utf-8"))
                message = details.get("error", {}).get("message") or details
            except Exception:
                message = f"Anthropic error: {exc.code}"
            self._send_json(exc.code, {"error": str(message)})
        except URLError as exc:
            self._send_json(502, {"error": f"Ошибка сети: {exc.reason}"})
        except Exception as exc:
            self._send_json(500, {"error": f"Внутренняя ошибка сервера: {exc}"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), AppHandler)
    print(f"Server running at http://0.0.0.0:{port}")
    server.serve_forever()
