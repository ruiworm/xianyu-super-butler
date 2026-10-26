"""Windows desktop connector. Credentials stay in memory for this process only."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from .bridge import BridgeError, BridgeWorker, CDPClient, SessionUploader, launch_client
except ImportError:
    from bridge import BridgeError, BridgeWorker, CDPClient, SessionUploader, launch_client


class ConnectorWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.SimpleQueue = queue.SimpleQueue()
        self.stop_event: threading.Event | None = None
        self.running = False
        self.closed = False
        self.run_id = 0
        self.server = tk.StringVar()
        self.token = tk.StringVar()
        self.executable = tk.StringVar()
        self.port = tk.StringVar(value="9222")
        self.status = tk.StringVar(value="请在管家账号页面生成连接令牌，然后填写下面的连接信息。")

        root.title("闲鱼管家 · 桌面客户端连接")
        root.minsize(690, 540)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        panel = ttk.Frame(root, padding=24)
        panel.grid(sticky="nsew")
        panel.columnconfigure(1, weight=1)
        ttk.Label(panel, text="桌面客户端连接", font=("Microsoft YaHei UI", 18, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        ttk.Label(
            panel,
            text="在这台电脑上登录闲鱼卖家客服，手动完成滑块后，会话会自动同步到管家服务器。",
            wraplength=620,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 20))

        self.inputs: list[ttk.Widget] = []
        self._field(panel, 2, "管家服务器地址", self.server)
        ttk.Label(panel, text="使用 HTTPS；通过 SSH 隧道连接时可填 http://127.0.0.1:38888。", wraplength=540).grid(
            row=3, column=1, columnspan=2, sticky="w", pady=(0, 12)
        )
        self._field(panel, 4, "账号专属连接令牌", self.token, show="●")
        ttk.Label(panel, text="从“账号管理 → 桌面客户端接入”复制。令牌仅在当前窗口内存中保存。", wraplength=540).grid(
            row=5, column=1, columnspan=2, sticky="w", pady=(0, 12)
        )
        self._field(panel, 6, "闲鱼卖家客服程序", self.executable, span=1)
        browse = ttk.Button(panel, text="选择 .exe", command=self._choose_executable)
        browse.grid(row=6, column=2, padx=(8, 0), pady=(0, 8))
        self.inputs.append(browse)
        self._field(panel, 7, "本机连接端口", self.port)

        actions = ttk.Frame(panel)
        actions.grid(row=8, column=0, columnspan=3, sticky="w", pady=(12, 16))
        self.launch_button = ttk.Button(actions, text="启动客户端并连接", command=lambda: self.start(True))
        self.launch_button.pack(side="left", padx=(0, 8))
        self.attach_button = ttk.Button(actions, text="连接已启动客户端", command=lambda: self.start(False))
        self.attach_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(actions, text="停止同步", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")

        status_box = ttk.LabelFrame(panel, text="连接状态", padding=12)
        status_box.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        ttk.Label(status_box, textvariable=self.status, wraplength=600, justify="left").pack(anchor="w")
        ttk.Label(
            panel,
            text=("需要客户端支持本机 Chromium 调试连接，连接工具会检查是否可用。"
                  "若客户端已经运行，请先正常完全退出，再点击“启动客户端并连接”。"
                  "可在管家启用“商品和订单通过电脑客户端查询”。"
                  "需要客户端页面提供查询接口；消息收发和发货仍由服务器处理。"),
            wraplength=620,
            justify="left",
        ).grid(row=10, column=0, columnspan=3, sticky="w")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(150, self._drain_events)

    def _field(self, panel: ttk.Frame, row: int, label: str, value: tk.StringVar,
               span: int = 2, show: str = "") -> None:
        ttk.Label(panel, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=(0, 8))
        entry = ttk.Entry(panel, textvariable=value, show=show)
        entry.grid(row=row, column=1, columnspan=span, sticky="ew", pady=(0, 8))
        self.inputs.append(entry)

    def _choose_executable(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root, title="选择闲鱼卖家客服主程序",
            filetypes=[("Windows 程序", "*.exe")],
        )
        if selected:
            self.executable.set(selected)

    def start(self, launch: bool) -> None:
        if self.running:
            return
        # Read every Tk variable on the UI thread before creating the worker.
        server = self.server.get().strip()
        token = self.token.get().strip()
        executable = self.executable.get().strip()
        try:
            port = int(self.port.get().strip())
        except ValueError:
            messagebox.showerror("连接信息不完整", "本机连接端口必须是 1 到 65535 的整数。", parent=self.root)
            return
        if not 1 <= port <= 65535:
            messagebox.showerror("连接信息不完整", "本机连接端口必须是 1 到 65535 的整数。", parent=self.root)
            return
        if launch and not executable:
            messagebox.showerror("请选择程序", "请先选择闲鱼卖家客服的 .exe 主程序。", parent=self.root)
            return
        try:
            cdp = CDPClient(port)
            uploader = SessionUploader(server, token)
        except BridgeError as error:
            messagebox.showerror("连接信息有误", str(error), parent=self.root)
            return
        self.run_id += 1
        run_id = self.run_id
        stop_event = threading.Event()
        self.stop_event = stop_event
        self.running = True
        self._set_running(True)
        self.status.set("正在启动客户端…" if launch else "正在连接本机客户端…")

        def work() -> None:
            try:
                if launch:
                    launch_client(executable, port)
                    self.events.put((run_id, "status", "客户端启动中，请在客户端登录并手动完成验证。"))
                if not stop_event.is_set():
                    worker = BridgeWorker(cdp, uploader, lambda message: self.events.put((run_id, "status", message)))
                    worker.run(stop_event)
            except BridgeError as error:
                self.events.put((run_id, "status", str(error)))
            except Exception:
                # Raw exceptions may include request data; keep them out of the UI.
                self.events.put((run_id, "status", "连接工具遇到异常，请停止后重新连接。"))
            finally:
                self.events.put((run_id, "done", None))

        threading.Thread(target=work, name="desktop-session-bridge", daemon=True).start()

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for widget in self.inputs:
            widget.configure(state=state)
        self.launch_button.configure(state=state)
        self.attach_button.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")

    def stop(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
            self.stop_button.configure(state="disabled")
            self.status.set("正在停止同步，等待当前连接结束…")

    def _drain_events(self) -> None:
        if self.closed:
            return
        while True:
            try:
                run_id, kind, message = self.events.get_nowait()
            except queue.Empty:
                break
            if run_id != self.run_id:
                continue
            if kind == "status" and self.stop_event is not None and not self.stop_event.is_set():
                self.status.set(message)
            elif kind == "done":
                if self.stop_event is not None and self.stop_event.is_set():
                    self.status.set("已停止同步。闲鱼卖家客服仍可正常使用。")
                self.running = False
                self.stop_event = None
                self._set_running(False)
        self.root.after(150, self._drain_events)

    def close(self) -> None:
        self.closed = True
        if self.stop_event is not None:
            self.stop_event.set()
        self.token.set("")
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    ConnectorWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
