"""Keep this agent running to emit status and accept admin scrape requests."""
import logging
import os
import threading

from mrk_signal import ACTIVE, Signal, local_lock, run_pipeline

LOG = logging.getLogger("maraki.client")


def recover(signal):
    if signal.read()["phase"] in ACTIVE:
        try:
            with local_lock(signal.directory / "scrape.lock"):
                signal.emit("failed", "Previous scrape was interrupted. Request a new scrape to retry.")
        except RuntimeError:
            pass  # A separately launched client scrape still owns the lock.


def tick(signal, worker=None):
    state = signal.read()
    response = signal.heartbeat()
    if state.get("package") and state["phase"] in ("awaiting_import", "failed"):
        if response.get("import_status") == "imported":
            signal.emit("completed", "Reports received and imported into host Maraki database")
            signal.heartbeat()
            return worker
        elif response.get("import_status") == "failed" and state["phase"] != "failed":
            signal.emit("failed", "Host database import failed; inspect package watcher errors on the host")
            signal.heartbeat()
            return worker
        if state["phase"] == "awaiting_import":
            return worker
    if state["phase"] in ACTIVE or (worker and worker.is_alive()):
        return worker
    command = signal.next_command()
    if not command:
        return worker
    if state.get("command_id") == command["id"] and state["phase"] in ("completed", "failed"):
        signal.heartbeat()  # Terminal acknowledgement may have been lost; never rerun it.
        return worker
    def scrape():
        try:
            run_pipeline(command["start_date"], command["end_date"], command["id"], signal.root)
        except Exception:
            LOG.error("Scrape failed; progress signal contains the result")
    worker = threading.Thread(target=scrape, name="maraki-scrape", daemon=True)
    worker.start()
    return worker


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    signal = Signal()
    try:
        with local_lock(signal.directory / "agent.lock"):
            recover(signal)
            interval = max(float(os.environ.get("MRK_AGENT_POLL_SECONDS", "10")), 1)
            LOG.info("Client %s (%s); heartbeat every %s seconds", signal.name, signal.client_id, interval)
            stop = threading.Event()
            worker = None
            connected = None
            if not signal.headers["X-MRK-Token"]:
                LOG.warning("Set MRK_CONTROL_CLIENT_TOKEN in client .env to enable status delivery")
            while not stop.is_set():
                try:
                    worker = tick(signal, worker)
                    if not connected:
                        LOG.info("ONLINE: host is receiving client signals")
                    connected = True
                except Exception as exc:
                    if connected is not False:
                        LOG.warning("OFFLINE: status delivery failed (%s); retrying", type(exc).__name__)
                    connected = False
                stop.wait(interval)
    except RuntimeError:
        LOG.info("Scraping agent already running")
    except KeyboardInterrupt:
        LOG.info("Agent stopped")


if __name__ == "__main__":
    main()
