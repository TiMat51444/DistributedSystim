import os
import socket
import struct
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

PRIMARY_HOST = "127.0.0.1"
PRIMARY_PORT = 8000
BACKUP_HOST = "127.0.0.1"
BACKUP_PORT = 8001


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


class NotesDriveApp:

  def __init__(self, root):
    self.root = root
    self.root.title("Distributed Student Notes Drive")
    self.root.geometry("850x600")

    self.current_user = None
    self.current_role = None

    self.active_host = PRIMARY_HOST
    self.active_port = PRIMARY_PORT
    self.status_label = None

    self.show_login_screen()

  def clear_window(self):
    for widget in self.root.winfo_children():
      widget.destroy()

  def get_server_label(self, host, port):
    if (host, port) == (PRIMARY_HOST, PRIMARY_PORT):
      return f"Primary ({host}:{port})"
    if (host, port) == (BACKUP_HOST, BACKUP_PORT):
      return f"Backup ({host}:{port})"
    return f"{host}:{port}"

  def update_server_indicator(self):
    if self.status_label and self.status_label.winfo_exists():
      label = self.get_server_label(self.active_host, self.active_port)
      is_primary = (self.active_host, self.active_port) == (PRIMARY_HOST, PRIMARY_PORT)
      self.status_label.config(
          text=f"Server: {label}",
          foreground="green" if is_primary else "darkorange",
      )

  def send_request(self, payload: bytes):
    primary = (PRIMARY_HOST, PRIMARY_PORT)
    backup = (BACKUP_HOST, BACKUP_PORT)
    current = (self.active_host, self.active_port)
    fallback = backup if current == primary else primary

    def _try_endpoint(host, port):
      s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      s.settimeout(4.0)
      try:
        s.connect((host, port))
        send_msg(s, payload)
        return recv_msg(s)
      finally:
        s.close()

    try:
      res = _try_endpoint(*current)
      self.update_server_indicator()
      return res
    except Exception:
      pass

    current_name = "Primary" if current == primary else "Backup"
    fallback_name = "Backup" if fallback == backup else "Primary"
    messagebox.showwarning(
        "Server Crash Detected",
        f"The {current_name} server ({current[0]}:{current[1]}) has crashed or is unreachable.\n\n"
        f"Click OK to switch to the {fallback_name} server.",
    )

    try:
      response = _try_endpoint(*fallback)
      self.active_host, self.active_port = fallback
      self.update_server_indicator()
      print(f"[*] Switched connection to {fallback_name} at {fallback[0]}:{fallback[1]}")
      return response
    except Exception:
      messagebox.showerror(
          "Connection Error",
          "Both Primary and Backup servers are unreachable. Please try again later.",
      )
      return None

  def show_login_screen(self):
    self.clear_window()
    frame = ttk.Frame(self.root, padding=20)
    frame.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

    ttk.Label(frame, text="📚 Student Notes Drive - Login", font=("Arial", 16, "bold")).pack(pady=10)
    ttk.Label(frame, text="Username:").pack(anchor=tk.W)
    self.user_entry = ttk.Entry(frame, width=30)
    self.user_entry.pack(pady=5)

    ttk.Label(frame, text="Password:").pack(anchor=tk.W)
    self.pass_entry = ttk.Entry(frame, width=30, show="*")
    self.pass_entry.pack(pady=5)

    ttk.Button(frame, text="Log In", command=self.handle_login, width=25).pack(pady=10)
    ttk.Button(frame, text="Create New Account", command=self.show_register_screen, width=25).pack(pady=5)
    ttk.Label(
        frame,
        text=f"Active Endpoint: {self.get_server_label(self.active_host, self.active_port)}",
        font=("Arial", 9),
    ).pack(pady=(10, 0))

  def show_register_screen(self):
    self.clear_window()
    frame = ttk.Frame(self.root, padding=20)
    frame.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

    ttk.Label(frame, text="📝 Create Account", font=("Arial", 16, "bold")).pack(pady=10)
    ttk.Label(frame, text="Choose Username:").pack(anchor=tk.W)
    self.reg_user_entry = ttk.Entry(frame, width=30)
    self.reg_user_entry.pack(pady=5)

    ttk.Label(frame, text="Choose Password:").pack(anchor=tk.W)
    self.reg_pass_entry = ttk.Entry(frame, width=30, show="*")
    self.reg_pass_entry.pack(pady=5)

    ttk.Button(frame, text="Register", command=self.handle_register, width=25).pack(pady=10)
    ttk.Button(frame, text="Back to Login", command=self.show_login_screen, width=25).pack(pady=5)

  def handle_login(self):
    username = self.user_entry.get().strip()
    password = self.pass_entry.get().strip()
    if not username or not password:
      messagebox.showwarning("Warning", "Please fill in all fields.")
      return

    res = self.send_request(f"LOGIN:{username}:{password}".encode("utf-8"))
    if res and res.startswith(b"SUCCESS"):
      _, role = res.decode("utf-8").split(":", 1)
      self.current_user = username
      self.current_role = role
      self.show_main_dashboard()
    else:
      err_msg = res.decode("utf-8") if res else "Login failed"
      messagebox.showerror("Error", err_msg.replace("ERROR: ", ""))

  def handle_register(self):
    username = self.reg_user_entry.get().strip()
    password = self.reg_pass_entry.get().strip()
    if not username or not password:
      messagebox.showwarning("Warning", "Please fill in all fields.")
      return

    res = self.send_request(f"REGISTER:{username}:{password}".encode("utf-8"))
    if res and b"SUCCESS" in res:
      messagebox.showinfo("Success", "Account created successfully! Please log in.")
      self.show_login_screen()
    else:
      err_msg = res.decode("utf-8") if res else "Registration failed"
      messagebox.showerror("Error", err_msg.replace("ERROR: ", ""))

  def show_main_dashboard(self):
    self.clear_window()

    top_bar = ttk.Frame(self.root, padding=10)
    top_bar.pack(fill=tk.X, side=tk.TOP)

    status_text = f"Logged in as: {self.current_user} (Role: {self.current_role.upper()})"
    ttk.Label(top_bar, text=status_text, font=("Arial", 11, "bold"), foreground="blue").pack(side=tk.LEFT)

    self.status_label = ttk.Label(top_bar, text="", font=("Arial", 10, "bold"))
    self.status_label.pack(side=tk.LEFT, padx=20)
    self.update_server_indicator()

    if self.current_role == "admin":
      ttk.Button(top_bar, text="Manage Users", command=self.open_user_manager).pack(side=tk.RIGHT, padx=5)

    ttk.Button(top_bar, text="Log Out", command=self.handle_logout).pack(side=tk.RIGHT)

    content_frame = ttk.Frame(self.root, padding=10)
    content_frame.pack(fill=tk.BOTH, expand=True)

    left_pane = ttk.Frame(content_frame)
    left_pane.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

    ttk.Label(left_pane, text="Sorted Course Folders:").pack(anchor=tk.W, pady=(0, 5))

    self.tree = ttk.Treeview(left_pane, selectmode="browse")
    self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    self.tree.tag_configure("owned", foreground="blue", font=("Arial", 10, "bold"))
    self.tree.tag_configure("other", foreground="black", font=("Arial", 10))

    scrollbar = ttk.Scrollbar(left_pane, orient="vertical", command=self.tree.yview)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    self.tree.config(yscrollcommand=scrollbar.set)
    self.tree.bind("<<TreeviewSelect>>", self.preview_file_in_app)

    right_pane = ttk.Frame(content_frame)
    right_pane.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

    ttk.Label(right_pane, text="In-App File Viewer:").pack(anchor=tk.W, pady=(0, 5))
    self.viewer_text = scrolledtext.ScrolledText(right_pane, wrap=tk.WORD, width=45, height=20)
    self.viewer_text.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

    btn_frame = ttk.Frame(self.root, padding=10)
    btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

    ttk.Button(btn_frame, text="Refresh List", command=self.fetch_file_list).pack(side=tk.LEFT, padx=3)
    ttk.Button(btn_frame, text="Upload Note (<50MB)", command=self.upload_file).pack(side=tk.LEFT, padx=3)
    ttk.Button(btn_frame, text="Rename File", command=self.rename_file).pack(side=tk.LEFT, padx=3)
    ttk.Button(btn_frame, text="Download Option", command=self.download_file).pack(side=tk.LEFT, padx=3)
    ttk.Button(btn_frame, text="Delete File", command=self.delete_file).pack(side=tk.LEFT, padx=3)

    self.fetch_file_list()

  def open_user_manager(self):
    top = tk.Toplevel(self.root)
    top.title("User Account Manager")
    top.geometry("350x350")

    ttk.Label(top, text="Registered Users:", font=("Arial", 11, "bold")).pack(pady=10)
    user_listbox = tk.Listbox(top, width=40, height=12)
    user_listbox.pack(pady=5)

    def load_user_list():
      res = self.send_request(f"GET_USERS:{self.current_user}".encode("utf-8"))
      if res and not res.startswith(b"ERROR"):
        user_listbox.delete(0, tk.END)
        for u in res.decode("utf-8").splitlines():
          if u.strip():
            user_listbox.insert(tk.END, u.strip())

    load_user_list()

    def delete_selected_user():
      selected = user_listbox.curselection()
      if not selected:
        messagebox.showwarning("Warning", "Please select a user to delete.")
        return
      target_user = user_listbox.get(selected[0])
      if target_user == "admin":
        messagebox.showerror("Error", "Cannot delete the primary admin account!")
        return
      if not messagebox.askyesno("Confirm", f"Are you sure you want to delete user account '{target_user}'?"):
        return

      res = self.send_request(f"DELETE_USER:{target_user}:{self.current_user}".encode("utf-8"))
      if res and b"SUCCESS" in res:
        messagebox.showinfo("Success", f"User account '{target_user}' deleted successfully.")
        load_user_list()
      else:
        err_msg = res.decode("utf-8") if res else "Failed to delete user."
        messagebox.showerror("Error", err_msg.replace("ERROR: ", ""))

    ttk.Button(top, text="Delete Selected User Account", command=delete_selected_user).pack(pady=10)

  def handle_logout(self):
    self.current_user = None
    self.current_role = None
    self.status_label = None
    self.show_login_screen()

  def fetch_file_list(self):
    res = self.send_request(b"LIST")
    if res is not None:
      for item in self.tree.get_children():
        self.tree.delete(item)

      folders = {
          "Text Files (.txt)": self.tree.insert("", "end", text="📄 Text Files (.txt)"),
          "PDF Files (.pdf)": self.tree.insert("", "end", text="📕 PDF Files (.pdf)"),
          "Document Files (.doc, .docx)": self.tree.insert("", "end", text="📘 Document Files (.doc, .docx)"),
          "Presentation Files (.ppt, .pptx)": self.tree.insert("", "end", text="📊 Presentation Files (.ppt, .pptx)"),
          "Image Files (.png, .jpg, .jpeg)": self.tree.insert("", "end", text="🖼️ Image Files (.png, .jpg, .jpeg)"),
          "Excel Files (.xls, .xlsx)": self.tree.insert("", "end", text="📈 Excel Files (.xls, .xlsx)"),
      }

      for line in res.decode("utf-8").splitlines():
        if not line.strip():
          continue
        parts = line.split(" | uploaded by: ")
        filename = parts[0].strip()
        uploader = parts[1].strip() if len(parts) > 1 else "unknown"
        ext = os.path.splitext(filename)[1].lower()

        target_folder = None
        if ext == ".txt":
          target_folder = folders["Text Files (.txt)"]
        elif ext == ".pdf":
          target_folder = folders["PDF Files (.pdf)"]
        elif ext in [".doc", ".docx"]:
          target_folder = folders["Document Files (.doc, .docx)"]
        elif ext in [".ppt", ".pptx"]:
          target_folder = folders["Presentation Files (.ppt, .pptx)"]
        elif ext in [".png", ".jpg", ".jpeg"]:
          target_folder = folders["Image Files (.png, .jpg, .jpeg)"]
        elif ext in [".xls", ".xlsx"]:
          target_folder = folders["Excel Files (.xls, .xlsx)"]

        if target_folder:
          tag = "owned" if uploader == self.current_user or self.current_role == "admin" else "other"
          self.tree.insert(target_folder, "end", text=f"{filename} (by: {uploader})", values=(filename,), tags=(tag,))

      self.viewer_text.delete("1.0", tk.END)

  def preview_file_in_app(self, event):
    selected_items = self.tree.selection()
    if not selected_items:
      return
    item = self.tree.item(selected_items[0])
    values = item.get("values")
    if not values:
      return

    filename = values[0]
    res = self.send_request(f"READ:{filename}".encode("utf-8"))
    if not res or res.startswith(b"ERROR"):
      return

    self.viewer_text.delete("1.0", tk.END)
    if filename.lower().endswith(".txt"):
      try:
        self.viewer_text.insert(tk.END, res.decode("utf-8"))
      except UnicodeDecodeError:
        self.viewer_text.insert(tk.END, "[Could not decode text file. Use 'Download Option'.]")
    else:
      self.viewer_text.insert(
          tk.END,
          f"[{filename}]\n\nIn-app preview is available for .txt files.\nFor PDFs, Word documents, images, and spreadsheets, please use the 'Download Option' button below to view.",
      )

  def upload_file(self):
    path = filedialog.askopenfilename(
        title="Select Note File (Max 50MB)",
        filetypes=[("Allowed Files", "*.txt *.pdf *.doc *.docx *.ppt *.pptx *.png *.jpg *.jpeg *.xls *.xlsx")],
    )
    if not path:
      return

    filename = os.path.basename(path)
    if os.path.getsize(path) > 50 * 1024 * 1024:
      messagebox.showerror("Error", "File exceeds the 50MB limit. Please choose a smaller file.")
      return

    try:
      with open(path, "rb") as f:
        file_data = f.read()
      payload = b"WRITE:" + filename.encode("utf-8") + b":" + self.current_user.encode("utf-8") + b":" + file_data
      res = self.send_request(payload)
      if res and b"SUCCESS" in res:
        messagebox.showinfo("Success", f"'{filename}' uploaded successfully!")
        self.fetch_file_list()
      else:
        err_msg = res.decode("utf-8") if res else "Upload failed"
        messagebox.showerror("Error", err_msg.replace("ERROR: ", ""))
    except Exception as e:
      messagebox.showerror("Error", f"Failed reading file: {e}")

  def rename_file(self):
    selected_items = self.tree.selection()
    if not selected_items:
      messagebox.showwarning("Warning", "Please select a file to rename.")
      return
    item = self.tree.item(selected_items[0])
    values = item.get("values")
    if not values:
      messagebox.showwarning("Warning", "Please select an actual file, not a folder category.")
      return

    old_name = values[0]
    top = tk.Toplevel(self.root)
    top.title("Rename File")
    top.geometry("300x150")

    ttk.Label(top, text=f"Rename '{old_name}' to:").pack(pady=10)
    new_name_entry = ttk.Entry(top, width=30)
    new_name_entry.pack(pady=5)
    new_name_entry.insert(0, old_name)

    def submit_rename():
      new_name = new_name_entry.get().strip()
      if not new_name:
        return
      res = self.send_request(f"RENAME:{old_name}:{new_name}:{self.current_user}".encode("utf-8"))
      if res and b"SUCCESS" in res:
        messagebox.showinfo("Success", "File renamed successfully!")
        top.destroy()
        self.fetch_file_list()
      else:
        err_msg = res.decode("utf-8") if res else "Rename failed"
        messagebox.showerror("Permission Error", err_msg.replace("ERROR: ", ""))

    ttk.Button(top, text="Confirm Rename", command=submit_rename).pack(pady=10)

  def download_file(self):
    selected_items = self.tree.selection()
    if not selected_items:
      messagebox.showwarning("Warning", "Please select a file to download.")
      return
    item = self.tree.item(selected_items[0])
    values = item.get("values")
    if not values:
      return

    filename = values[0]
    save_path = filedialog.asksaveasfilename(initialfile=filename, title="Save File As")
    if not save_path:
      return

    res = self.send_request(f"READ:{filename}".encode("utf-8"))
    if res and not res.startswith(b"ERROR"):
      try:
        with open(save_path, "wb") as f:
          f.write(res)
        messagebox.showinfo("Success", "File downloaded successfully!")
      except Exception as e:
        messagebox.showerror("Error", f"Could not save file: {e}")
    else:
      messagebox.showerror("Error", "File not found on server.")

  def delete_file(self):
    selected_items = self.tree.selection()
    if not selected_items:
      messagebox.showwarning("Warning", "Please select a file to delete.")
      return
    item = self.tree.item(selected_items[0])
    values = item.get("values")
    if not values:
      return

    filename = values[0]
    if not messagebox.askyesno("Confirm", f"Delete '{filename}'?"):
      return

    res = self.send_request(f"DELETE:{filename}:{self.current_user}".encode("utf-8"))
    if res and b"SUCCESS" in res:
      messagebox.showinfo("Success", "File deleted successfully!")
      self.fetch_file_list()
    else:
      err_msg = res.decode("utf-8") if res else "Failed to delete file."
      messagebox.showerror("Permission Error", err_msg.replace("ERROR: ", ""))


if __name__ == "__main__":
  root = tk.Tk()
  app = NotesDriveApp(root)
  root.mainloop()