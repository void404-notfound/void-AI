"""Run with:  python -m assist5   [--port N] [--no-browser]"""
import argparse, getpass, sys, threading, time, webbrowser
from . import config, scheduler, voice
from .server import app


def reset_password_cli():
    from .server import reset_password
    print(f"Reset the {config.APP_NAME} login password (stored only on this computer).")
    try:
        pw = getpass.getpass("New password (6+ characters): ")
        if pw != getpass.getpass("Repeat it: "):
            print("Those didn't match. Nothing was changed."); return 1
        reset_password(pw)
    except ValueError as e:
        print(f"{e} Nothing was changed."); return 1
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled. Nothing was changed."); return 1
    print("Done. Start the assistant as usual and log in with the new password.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=f"{config.APP_NAME} assistant")
    ap.add_argument("--port", type=int, default=config.get("port"))
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--reset-password", action="store_true", help="set a new login password (forgot it?) and exit")
    a = ap.parse_args()
    if a.reset_password:
        return reset_password_cli()
    host = "0.0.0.0" if config.get("allow_lan") else "127.0.0.1"
    scheduler.start()
    voice.prewarm()          # load the local voice in the background so the first reply isn't slow
    url = f"http://127.0.0.1:{a.port}"
    print(f"\n  {config.APP_NAME} is running at {url}\n  (Ctrl+C to stop)\n")
    if not a.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)), daemon=True).start()
    app.run(host=host, port=a.port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    sys.exit(main() or 0)
