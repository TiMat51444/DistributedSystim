import json
import os
import socket
import struct
import sys
import threading

HOST = "127.0.0.1"

ALLOWED_EXTENSIONS = {
    ".txt",
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".png",
    ".jpg",
    ".jpeg",
    ".xls",
    ".xlsx",
}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB limit


def send_msg(sock, data: bytes):
  sock.sendall(struct.pack(">I", len(data)) + data)


def recv_exact(sock, n: int) -> bytes:
  buf = bytearray()
  while len(buf) < n:
    chunk = sock.recv(n - len(buf))
    if not chunk:
      raise ConnectionError("Socket closed before full payload arrived")
    buf.extend(chunk)
  return bytes(buf)


def recv_msg(sock) -> bytes:
  raw_len = recv_exact(sock, 4)
  msg_len = struct.unpack(">I", raw_len)[0]
  if msg_len == 0:
    return b""
  return recv_exact(sock, msg_len)


class FileServer:

  def __init__(
      self,
      host="127.0.0.1",
      port=8000,
      peer_host=None,
      peer_port=None,
      data_dir=None,
  ):
    self.host = host
    self.port = port
    self.peer_host = peer_host
    self.peer_port = peer_port

    base_dir = data_dir or f"./server_data_{port}"
    self.storage_dir = os.path.join(base_dir, "storage")
    self.users_file = os.path.join(base_dir, "users.json")
    self.metadata_file = os.path.join(base_dir, "metadata.json")
    self.lock = threading.Lock()
    self._init_storage()

  def _init_storage(self):
    os.makedirs(self.storage_dir, exist_ok=True)
    if not os.path.exists(self.users_file):
      with open(self.users_file, "w") as f:
        json.dump({"admin": {"password": "adminpassword", "role": "admin"}}, f, indent=4)
    if not os.path.exists(self.metadata_file):
      with open(self.metadata_file, "w") as f:
        json.dump({}, f)

  def load_users(self):
    with open(self.users_file, "r") as f:
      return json.load(f)

  def save_users(self, users):
    with open(self.users_file, "w") as f:
      json.dump(users, f, indent=4)

  def load_metadata(self):
    with open(self.metadata_file, "r") as f:
      return json.load(f)

  def save_metadata(self, meta):
    with open(self.metadata_file, "w") as f:
      json.dump(meta, f, indent=4)

  def start(self):
    self.sync_from_peer()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((self.host, self.port))
    s.listen(10)
    print(f"[*] Server listening on {self.host}:{self.port} (Peer: {self.peer_host}:{self.peer_port})")

    try:
      while True:
        conn, addr = s.accept()
        threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
      print("\n[*] Server shutting down...")
    finally:
      s.close()

  def _call_peer(self, payload: bytes, timeout=3.0):
    if not self.peer_host or not self.peer_port:
      return None
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
      s.connect((self.peer_host, self.peer_port))
      send_msg(s, payload)
      return recv_msg(s)
    except Exception:
      return None
    finally:
      s.close()

  def replicate_to_peer(self, payload: bytes):
    res = self._call_peer(payload, timeout=3.0)
    if res is None:
      print(f"[!] Peer ({self.peer_host}:{self.peer_port}) unreachable; will sync on peer startup.")

  def sync_from_peer(self):
    if not self.peer_host or not self.peer_port:
      return
    raw_users = self._call_peer(b"EXPORT_USERS", timeout=2.0)
    raw_meta = self._call_peer(b"EXPORT_META", timeout=2.0)
    if raw_users is None or raw_meta is None:
      print(f"[*] Peer {self.peer_host}:{self.peer_port} not available for startup catch-up.")
      return

    try:
      peer_users = json.loads(raw_users.decode("utf-8"))
      peer_meta = json.loads(raw_meta.decode("utf-8"))
    except Exception as e:
      print(f"[!] Catch-up sync parse error: {e}")
      return

    with self.lock:
      users = self.load_users()
      users.update(peer_users)
      self.save_users(users)
      self.save_metadata(peer_meta)

      for fname in os.listdir(self.storage_dir):
        fpath = os.path.join(self.storage_dir, fname)
        if os.path.isfile(fpath) and fname not in peer_meta:
          os.remove(fpath)

    synced = 0
    for fname in peer_meta:
      safe_name = os.path.basename(fname)
      content = self._call_peer(f"READ:{safe_name}".encode("utf-8"), timeout=5.0)
      if content is not None and not content.startswith(b"ERROR"):
        with self.lock:
          with open(os.path.join(self.storage_dir, safe_name), "wb") as f:
            f.write(content)
        synced += 1

    print(f"[*] Synced from peer ({synced} file(s), {len(peer_users)} user(s)).")

  def handle_client(self, conn, addr):
    conn.settimeout(10.0)
    try:
      data = recv_msg(conn)
      if not data:
        return

      if data.startswith(b"EXPORT_USERS"):
        with self.lock:
          send_msg(conn, json.dumps(self.load_users()).encode("utf-8"))

      elif data.startswith(b"EXPORT_META"):
        with self.lock:
          send_msg(conn, json.dumps(self.load_metadata()).encode("utf-8"))

      elif data.startswith(b"REGISTER:"):
        _, username, password = data.decode("utf-8").split(":", 2)
        with self.lock:
          users = self.load_users()
          if username in users:
            send_msg(conn, b"ERROR: Username already exists")
          else:
            users[username] = {"password": password, "role": "user"}
            self.save_users(users)
            self.replicate_to_peer(f"SYNC_USER:{username}:{password}".encode("utf-8"))
            send_msg(conn, b"SUCCESS")

      elif data.startswith(b"SYNC_USER:"):
        _, username, password = data.decode("utf-8").split(":", 2)
        with self.lock:
          users = self.load_users()
          users[username] = {"password": password, "role": "user"}
          self.save_users(users)
        send_msg(conn, b"SYNC_SUCCESS")

      elif data.startswith(b"LOGIN:"):
        _, username, password = data.decode("utf-8").split(":", 2)
        users = self.load_users()
        if username in users and users[username]["password"] == password:
          send_msg(conn, f"SUCCESS:{users[username]['role']}".encode("utf-8"))
        else:
          send_msg(conn, b"ERROR: Invalid username or password")

      elif data.startswith(b"GET_USERS:"):
        requester = data.decode("utf-8").split(":", 1)[1]
        users = self.load_users()
        if users.get(requester, {}).get("role") == "admin":
          send_msg(conn, "\n".join(users.keys()).encode("utf-8"))
        else:
          send_msg(conn, b"ERROR: Permission denied")

      elif data.startswith(b"DELETE_USER:"):
        _, target_user, requester = data.decode("utf-8").split(":", 2)
        with self.lock:
          users = self.load_users()
          if users.get(requester, {}).get("role") != "admin":
            send_msg(conn, b"ERROR: Permission denied.")
          elif target_user == "admin":
            send_msg(conn, b"ERROR: Cannot delete primary admin.")
          elif target_user in users:
            del users[target_user]
            self.save_users(users)
            self.replicate_to_peer(f"SYNC_DEL_USER:{target_user}".encode("utf-8"))
            send_msg(conn, b"SUCCESS")
          else:
            send_msg(conn, b"ERROR: User not found.")

      elif data.startswith(b"SYNC_DEL_USER:"):
        target_user = data.split(b":", 1)[1].decode("utf-8")
        with self.lock:
          users = self.load_users()
          if target_user in users and target_user != "admin":
            del users[target_user]
            self.save_users(users)
        send_msg(conn, b"SYNC_SUCCESS")

      elif data.startswith(b"LIST"):
        metadata = self.load_metadata()
        lines = []
        for f in os.listdir(self.storage_dir):
          if os.path.isfile(os.path.join(self.storage_dir, f)):
            lines.append(f"{f} | uploaded by: {metadata.get(f, 'unknown')}")
        send_msg(conn, "\n".join(lines).encode("utf-8"))

      elif data.startswith(b"READ:"):
        filename = os.path.basename(data.split(b":", 1)[1].decode("utf-8"))
        path = os.path.join(self.storage_dir, filename)
        if os.path.exists(path):
          with open(path, "rb") as f:
            send_msg(conn, f.read())
        else:
          send_msg(conn, b"ERROR: File not found")

      elif data.startswith(b"WRITE:"):
        parts = data.split(b":", 3)
        filename = os.path.basename(parts[1].decode("utf-8"))
        username = parts[2].decode("utf-8")
        content = parts[3]
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
          send_msg(conn, b"ERROR: File type not allowed.")
          return
        if len(content) > MAX_FILE_SIZE:
          send_msg(conn, b"ERROR: File exceeds 50MB limit.")
          return
        self.execute_write(filename, username, content, is_sync=False)
        send_msg(conn, b"SUCCESS")

      elif data.startswith(b"SYNC_WRITE:"):
        parts = data.split(b":", 3)
        filename = os.path.basename(parts[1].decode("utf-8"))
        username = parts[2].decode("utf-8")
        content = parts[3]
        self.execute_write(filename, username, content, is_sync=True)
        send_msg(conn, b"SYNC_SUCCESS")

      elif data.startswith(b"RENAME:"):
        _, old_raw, new_raw, requester = data.decode("utf-8").split(":", 3)
        old_name = os.path.basename(old_raw)
        new_name = os.path.basename(new_raw)
        if os.path.splitext(new_name)[1].lower() not in ALLOWED_EXTENSIONS:
          send_msg(conn, b"ERROR: New file extension not allowed.")
          return
        with self.lock:
          users = self.load_users()
          metadata = self.load_metadata()
          if users.get(requester, {}).get("role") == "admin" or requester == metadata.get(old_name, ""):
            old_path = os.path.join(self.storage_dir, old_name)
            new_path = os.path.join(self.storage_dir, new_name)
            if os.path.exists(old_path):
              os.rename(old_path, new_path)
              metadata[new_name] = metadata.pop(old_name, requester)
              self.save_metadata(metadata)
              self.replicate_to_peer(
                  f"SYNC_RENAME:{old_name}:{new_name}:{metadata[new_name]}".encode("utf-8")
              )
              send_msg(conn, b"SUCCESS")
            else:
              send_msg(conn, b"ERROR: File not found")
          else:
            send_msg(conn, b"ERROR: Permission denied.")

      elif data.startswith(b"SYNC_RENAME:"):
        _, old_name, new_name, owner = data.decode("utf-8").split(":", 3)
        with self.lock:
          old_path = os.path.join(self.storage_dir, old_name)
          new_path = os.path.join(self.storage_dir, new_name)
          metadata = self.load_metadata()
          if os.path.exists(old_path):
            os.rename(old_path, new_path)
          metadata.pop(old_name, None)
          metadata[new_name] = owner
          self.save_metadata(metadata)
        send_msg(conn, b"SYNC_SUCCESS")

      elif data.startswith(b"DELETE:"):
        _, raw_name, requester = data.decode("utf-8").split(":", 2)
        filename = os.path.basename(raw_name)
        users = self.load_users()
        metadata = self.load_metadata()
        if users.get(requester, {}).get("role") == "admin" or requester == metadata.get(filename, ""):
          self.execute_delete(filename, is_sync=False)
          send_msg(conn, b"SUCCESS")
        else:
          send_msg(conn, b"ERROR: Permission denied. You can only delete your own files.")

      elif data.startswith(b"SYNC_DELETE:"):
        filename = os.path.basename(data.split(b":", 1)[1].decode("utf-8"))
        self.execute_delete(filename, is_sync=True)
        send_msg(conn, b"SYNC_SUCCESS")

    except Exception as e:
      print(f"Error: {e}")
    finally:
      conn.close()

  def execute_write(self, filename, username, content, is_sync=False):
    with self.lock:
      with open(os.path.join(self.storage_dir, filename), "wb") as f:
        f.write(content)
      metadata = self.load_metadata()
      metadata[filename] = username
      self.save_metadata(metadata)
      print(f"[WRITE] Saved '{filename}' (Owner: {username}) {'[REPLICATED]' if is_sync else ''}")
    if not is_sync:
      self.replicate_to_peer(
          b"SYNC_WRITE:" + filename.encode("utf-8") + b":" + username.encode("utf-8") + b":" + content
      )

  def execute_delete(self, filename, is_sync=False):
    with self.lock:
      path = os.path.join(self.storage_dir, filename)
      if os.path.exists(path):
        os.remove(path)
      metadata = self.load_metadata()
      metadata.pop(filename, None)
      self.save_metadata(metadata)
      print(f"[DELETE] Removed '{filename}' {'[REPLICATED]' if is_sync else ''}")
    if not is_sync:
      self.replicate_to_peer(b"SYNC_DELETE:" + filename.encode("utf-8"))


if __name__ == "__main__":
  mode = sys.argv[1] if len(sys.argv) > 1 else "primary"
  if mode == "primary":
    FileServer("127.0.0.1", 8000, "127.0.0.1", 8001).start()
  elif mode == "backup":
    FileServer("127.0.0.1", 8001, "127.0.0.1", 8000).start()