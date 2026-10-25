import argparse
import os
import threading
import time
from sqlalchemy import update
from .db import init_db, Session, Job
from .tasks import claim, finish, aggregate, Cancelled
from .workflows import archive, search, safe_error
from .config import DATA
from .insights import generate


def single_worker_lock():
    lock = open(DATA / "worker.lock", "a+b")
    lock.seek(0)
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise SystemExit("已有 Worker 正在运行。首版仅允许一个 Worker。")
    return lock


def heartbeat(stop, id_, token):
    while not stop.wait(15):
        (DATA / "worker.heartbeat").touch()
        with Session.begin() as s:
            s.execute(
                update(Job)
                .where(Job.id == id_, Job.lease_token == token, Job.status == "running")
                .values(lease_until=time.time() + 60)
            )


def tick():
    aggregate()
    task = claim()
    if not task:
        return False
    id_, token = task
    stop = threading.Event()
    thread = threading.Thread(target=heartbeat, args=(stop, id_, token), daemon=True)
    thread.start()
    try:
        with Session() as s:
            j = s.get(Job, id_)
            kind = j.kind
            payload = j.payload
        {"archive": archive, "search": search, "report": generate}[kind](
            id_, token, payload
        )
    except Cancelled as exc:
        finish(id_, token, "cancelled", error=str(exc))
    except Exception as exc:
        finish(id_, token, "failed", error=safe_error(exc))
    finally:
        stop.set()
        thread.join(timeout=1)
    aggregate()
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    init_db()
    lock = single_worker_lock()
    while True:
        (DATA / "worker.heartbeat").touch()
        worked = tick()
        if args.once:
            break
        if not worked:
            time.sleep(1)
    lock.close()


if __name__ == "__main__":
    main()
