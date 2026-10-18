"""Small JSON-lines plugin host. Process isolation is fault isolation, not an OS sandbox."""
import importlib.util
import json
import sys
import traceback


def main():
    path = sys.argv[1]
    spec = importlib.util.spec_from_file_location("yukino_user_plugin", path)
    module = importlib.util.module_from_spec(spec)
    # Plugin prints cannot corrupt the JSON protocol on stdout.
    protocol = sys.stdout
    sys.stdout = sys.stderr
    try:
        spec.loader.exec_module(module)
        protocol.write(json.dumps({"ready": True}) + "\n"); protocol.flush()
        for line in sys.stdin:
            try:
                req = json.loads(line)
                result = module.handle(req["text"], req.get("context", {}))
                if result is not None and not isinstance(result, str): raise TypeError("Plugin must return str or None")
                protocol.write(json.dumps({"text": result}, ensure_ascii=False) + "\n")
            except Exception:
                protocol.write(json.dumps({"error": "plugin_failed"}) + "\n")
            protocol.flush()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__": main()
