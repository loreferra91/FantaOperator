"""Local macOS launcher. Reuses only a verified process from this project."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent


def healthy(port: int) -> bool:
    try:
        # Local health requests must never go through a configured external proxy.
        with build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as response:
            return response.status == 200 and response.read(32).strip() == b"ok"
    except Exception:
        return False


def available_port() -> int:
    for port in range(8501, 8521):
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    raise RuntimeError("Le porte 8501–8520 sono occupate. Chiudi un'istanza precedente e riprova.")


def running_port(state: Path) -> int | None:
    try:
        data = json.loads(state.read_text())
        port, pid = int(data["port"]), int(data["pid"])
        if not 8501 <= port <= 8520 or pid <= 0:
            return None
        command = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=2)
        if command.returncode or str(ROOT / "app.py") not in command.stdout or "-m streamlit run" not in command.stdout:
            return None
        return port if healthy(port) else None
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return None


def show(port: int, no_browser: bool) -> None:
    url = f"http://localhost:{port}"
    print(f"\nFantaOperator pronto: {url}", flush=True)
    if not no_browser:
        result = subprocess.run(["/usr/bin/open", url], check=False)
        if result.returncode:
            print("Apri l'indirizzo qui sopra nel browser.", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Avvia FantaOperator in locale")
    parser.add_argument("--no-browser", action="store_true", help="Avvia senza aprire il browser")
    args = parser.parse_args(argv)
    runtime = ROOT / ".runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    state = runtime / "streamlit.json"
    with (runtime / "launcher.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("FantaOperator è già in avvio o in esecuzione…", flush=True)
            for _ in range(60):
                port = running_port(state)
                if port:
                    show(port, args.no_browser)
                    return 0
                time.sleep(0.5)
            print("L'istanza precedente non risponde. Controlla la sua finestra Terminale.", flush=True)
            return 1
        port = available_port()
        child = None
        def stop(_signum, _frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGHUP, stop)
        try:
            child = subprocess.Popen([
                sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
                "--server.address", "localhost", "--server.port", str(port),
                "--server.headless", "true", "--browser.gatherUsageStats", "false",
            ], cwd=ROOT, env={**os.environ, "FANTAOPERATOR_DB": os.environ.get("FANTAOPERATOR_DB", str(ROOT / "data" / "fantaoperator.db"))})
            state.write_text(json.dumps({"pid": child.pid, "port": port}))
            for _ in range(120):
                if child.poll() is not None:
                    print("Streamlit non è partito: controlla l'errore sopra.", flush=True)
                    return child.returncode or 1
                if healthy(port):
                    show(port, args.no_browser)
                    print("Lascia aperto questo Terminale. Per arrestare l'app premi Ctrl+C.", flush=True)
                    return child.wait()
                time.sleep(0.5)
            print("Avvio troppo lento: riprova dopo aver controllato le dipendenze.", flush=True)
            return 1
        except KeyboardInterrupt:
            print("\nArresto di FantaOperator…", flush=True)
            return 0
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            state.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        print(f"Avvio non riuscito: {exc}", file=sys.stderr)
        raise SystemExit(1)
