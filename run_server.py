"""
SY CSC CENTRE - production entry point (Windows friendly)
=========================================================
Uses waitress instead of the Flask development server: no reloader, no debug
page, fewer surprises on a shop computer.

    python run_server.py

Stop it with CTRL+C.
"""
from waitress import serve

from app import create_app, lan_addresses
from config import Config


def main():
    application = create_app()
    port = application.config["PORT"]
    print("=" * 62)
    print("  SY CSC CENTRE - running with waitress")
    print("  this PC          : http://127.0.0.1:%d" % port)
    for address in lan_addresses():
        print("  phone / other PC : http://%s:%d" % (address, port))
    print("  press CTRL+C to stop")
    print("=" * 62)
    serve(application, host=application.config["HOST"], port=port, threads=8)


if __name__ == "__main__":
    main()
