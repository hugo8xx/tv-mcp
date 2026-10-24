"""ข้อมูลผู้ใช้ของโหมด cloud (SQLite บน volume) — โหมด self-host ไม่ใช้ไฟล์นี้

เก็บเท่าที่จำเป็น: Google sub + อีเมล, รายการทีวี (id, เวลาจับคู่, เห็นล่าสุด)
ไม่เก็บคำสั่งหรือลิงก์ที่เปิด
"""
import os
import sqlite3
import threading
import time


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT NOT NULL, created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created INTEGER NOT NULL, last_seen INTEGER NOT NULL DEFAULT 0,
                revoked INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS devices_user ON devices(user_id);
        """)
        self.lock = threading.Lock()
        self._seen: dict[str, float] = {}

    def _q(self, sql, *args):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    # ----- users -----

    def upsert_user(self, uid: str, email: str) -> int:
        """คืน created — ใส่ไว้ใน token ทุกตัว ลบบัญชีแล้วสมัครใหม่ token เก่าจะใช้ไม่ได้"""
        rows = self._q("SELECT created FROM users WHERE id=?", uid)
        if rows:
            self._q("UPDATE users SET email=? WHERE id=?", email, uid)
            return rows[0][0]
        now = int(time.time() * 1000)
        self._q("INSERT INTO users(id,email,created) VALUES(?,?,?)", uid, email, now)
        return now

    def user(self, uid: str):
        rows = self._q("SELECT email, created FROM users WHERE id=?", uid)
        return {"email": rows[0][0], "created": rows[0][1]} if rows else None

    def delete_user(self, uid: str):
        self._q("DELETE FROM users WHERE id=?", uid)

    def count_users(self) -> int:
        return self._q("SELECT COUNT(*) FROM users")[0][0]

    # ----- devices -----

    def add_device(self, did: str, uid: str):
        """beta: 1 บัญชี = ทีวี 1 เครื่อง — จับคู่ใหม่ถอนเครื่องเก่า"""
        self._q("UPDATE devices SET revoked=1 WHERE user_id=?", uid)
        self._q("INSERT INTO devices(id,user_id,created) VALUES(?,?,?)", did, uid, int(time.time()))

    def device_ok(self, did: str, uid: str) -> bool:
        rows = self._q("SELECT revoked FROM devices WHERE id=? AND user_id=?", did, uid)
        if not rows or rows[0][0]:
            return False
        now = time.time()
        if now - self._seen.get(did, 0) > 300:
            self._seen[did] = now
            self._q("UPDATE devices SET last_seen=? WHERE id=?", int(now), did)
        return True

    def devices(self, uid: str):
        return [{"id": r[0], "created": r[1], "last_seen": r[2]} for r in self._q(
            "SELECT id, created, last_seen FROM devices WHERE user_id=? AND revoked=0 ORDER BY created DESC", uid)]

    def revoke_device(self, did: str, uid: str):
        self._q("UPDATE devices SET revoked=1 WHERE id=? AND user_id=?", did, uid)
