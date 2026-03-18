"""
DJI无人机集群控制台
依赖: pip install paho-mqtt requests
"""
import json
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import paho.mqtt.client as mqtt
import requests

# ── 配置 ──────────────────────────────────────────────
MQTT_BROKER = "broker.emqx.io"
MQTT_PORT = 1883
BMOB_APP_ID = "b71e89ce2fe4a62c2ee1306de444f1af"
BMOB_REST_KEY = "89de77adbf404b494347023f18ac952a"
BMOB_BASE_URL = "https://api2.bmob.cn/1/classes"
# ──────────────────────────────────────────────────────

class DroneControlApp:
    def __init__(self, root):
        self.root = root
        self.root.title("DJI无人机集群控制台")
        self.root.geometry("900x650")
        self.root.configure(bg="#1e1e2e")

        self.drones = {}       # drone_id -> 状态dict
        self.missions = {}     # drone_id -> 任务参数dict
        self.dependencies = {} # drone_id -> [依赖的drone_id列表]
        self.completed = set() # 已完成任务的drone_id集合
        self.pending_commands = {} # drone_id -> {"time": 时间戳, "retry": 重试次数, "last_status": 上次状态}
        self.mqtt_client = None
        self.connected = False

        self._build_ui()
        self._connect_mqtt()
        self._start_retry_monitor()

    # ── UI ────────────────────────────────────────────
    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#1e1e2e")
        style.configure("TLabel", background="#1e1e2e", foreground="#cdd6f4", font=("微软雅黑", 10))
        style.configure("TButton", font=("微软雅黑", 10), padding=6)
        style.configure("Treeview", background="#313244", foreground="#cdd6f4",
                        fieldbackground="#313244", rowheight=28, font=("微软雅黑", 10))
        style.configure("Treeview.Heading", background="#45475a", foreground="#cdd6f4",
                        font=("微软雅黑", 10, "bold"))

        # 顶部状态栏
        top = ttk.Frame(self.root)
        top.pack(fill=tk.X, padx=12, pady=(10, 0))
        ttk.Label(top, text="DJI 无人机集群控制台", font=("微软雅黑", 14, "bold")).pack(side=tk.LEFT)
        self.lbl_mqtt = ttk.Label(top, text="● MQTT 连接中...", foreground="#f38ba8")
        self.lbl_mqtt.pack(side=tk.RIGHT)

        # 飞机列表
        list_frame = ttk.Frame(self.root)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        cols = ("drone_id", "状态", "纬度", "经度", "高度(m)", "任务状态", "预设任务", "依赖", "最后更新")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=12)
        widths = [120, 70, 90, 90, 60, 130, 80, 100, 90]
        for col, w in zip(cols, widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor=tk.CENTER)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # 飞机详情面板
        detail_frame = tk.LabelFrame(list_frame, text=" 飞机详情 ", bg="#1e1e2e",
                                      fg="#cdd6f4", font=("微软雅黑", 10), width=280)
        detail_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        detail_frame.pack_propagate(False)

        self.detail_text = tk.Text(detail_frame, bg="#181825", fg="#cdd6f4",
                                    font=("Consolas", 9), wrap=tk.WORD, relief=tk.FLAT)
        self.detail_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.detail_text.insert("1.0", "点击左侧飞机查看详情")
        self.detail_text.config(state=tk.DISABLED)

        # 指令面板
        cmd_frame = tk.LabelFrame(self.root, text=" 发送任务指令 ", bg="#1e1e2e",
                                   fg="#cdd6f4", font=("微软雅黑", 10))
        cmd_frame.pack(fill=tk.X, padx=12, pady=4)

        row1 = ttk.Frame(cmd_frame)
        row1.pack(fill=tk.X, padx=8, pady=4)

        fields = [
            ("起点纬度", "start_lat"), ("起点经度", "start_lng"),
            ("终点纬度", "end_lat"),   ("终点经度", "end_lng"),
        ]
        self.entries = {}
        for label, key in fields:
            ttk.Label(row1, text=label).pack(side=tk.LEFT, padx=(8, 2))
            e = ttk.Entry(row1, width=13)
            e.pack(side=tk.LEFT, padx=(0, 8))
            self.entries[key] = e

        row2 = ttk.Frame(cmd_frame)
        row2.pack(fill=tk.X, padx=8, pady=4)

        ttk.Label(row2, text="高度(m)").pack(side=tk.LEFT, padx=(8, 2))
        self.entries["altitude"] = ttk.Entry(row2, width=7)
        self.entries["altitude"].insert(0, "10")
        self.entries["altitude"].pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(row2, text="速度(m/s)").pack(side=tk.LEFT, padx=(0, 2))
        self.entries["speed"] = ttk.Entry(row2, width=7)
        self.entries["speed"].insert(0, "2")
        self.entries["speed"].pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(row2, text="停留(s)").pack(side=tk.LEFT, padx=(0, 2))
        self.entries["stay"] = ttk.Entry(row2, width=7)
        self.entries["stay"].insert(0, "5")
        self.entries["stay"].pack(side=tk.LEFT, padx=(0, 8))

        self.selected_drone = tk.StringVar(value="(未选择)")
        ttk.Label(row2, text="目标飞机:").pack(side=tk.LEFT, padx=(16, 2))
        self.lbl_selected = ttk.Label(row2, textvariable=self.selected_drone, foreground="#a6e3a1")
        self.lbl_selected.pack(side=tk.LEFT)

        row3 = ttk.Frame(cmd_frame)
        row3.pack(fill=tk.X, padx=8, pady=(4, 4))
        tk.Button(row3, text="💾 设置任务", bg="#f9e2af", fg="#1e1e2e",
                  font=("微软雅黑", 10, "bold"), relief=tk.FLAT, padx=16,
                  command=self._save_mission).pack(side=tk.LEFT, padx=4)
        tk.Button(row3, text="🔗 设置依赖", bg="#cba6f7", fg="#1e1e2e",
                  font=("微软雅黑", 10, "bold"), relief=tk.FLAT, padx=16,
                  command=self._set_dependency).pack(side=tk.LEFT, padx=4)
        tk.Button(row3, text="▶ 执行任务", bg="#a6e3a1", fg="#1e1e2e",
                  font=("微软雅黑", 10, "bold"), relief=tk.FLAT, padx=16,
                  command=self._send_mission).pack(side=tk.LEFT, padx=4)
        tk.Button(row3, text="⬛ 停止任务", bg="#f38ba8", fg="#1e1e2e",
                  font=("微软雅黑", 10, "bold"), relief=tk.FLAT, padx=16,
                  command=self._stop_mission).pack(side=tk.LEFT, padx=4)
        tk.Button(row3, text="🚀 自动编排", bg="#89b4fa", fg="#1e1e2e",
                  font=("微软雅黑", 10, "bold"), relief=tk.FLAT, padx=16,
                  command=self._auto_execute).pack(side=tk.LEFT, padx=4)

        row4 = ttk.Frame(cmd_frame)
        row4.pack(fill=tk.X, padx=8, pady=(4, 8))
        tk.Button(row4, text="📂 加载配置", bg="#fab387", fg="#1e1e2e",
                  font=("微软雅黑", 10, "bold"), relief=tk.FLAT, padx=16,
                  command=self._load_config).pack(side=tk.LEFT, padx=4)
        tk.Button(row4, text="💾 保存配置", bg="#94e2d5", fg="#1e1e2e",
                  font=("微软雅黑", 10, "bold"), relief=tk.FLAT, padx=16,
                  command=self._save_config).pack(side=tk.LEFT, padx=4)

        # 日志
        log_frame = ttk.Frame(self.root)
        log_frame.pack(fill=tk.X, padx=12, pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=5, bg="#313244", fg="#cdd6f4",
                                font=("Consolas", 9), state=tk.DISABLED)
        self.log_text.pack(fill=tk.X)

    # ── MQTT ──────────────────────────────────────────
    def _connect_mqtt(self):
        self.mqtt_client = mqtt.Client(client_id=f"control_pc_{int(time.time())}")
        self.mqtt_client.on_connect = self._on_mqtt_connect
        self.mqtt_client.on_message = self._on_mqtt_message
        self.mqtt_client.on_disconnect = self._on_mqtt_disconnect

        def _connect():
            try:
                self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
                self.mqtt_client.loop_forever()
            except Exception as e:
                self._log(f"MQTT连接失败: {e}")

        threading.Thread(target=_connect, daemon=True).start()

    def _start_retry_monitor(self):
        """启动指令重试监控线程"""
        def monitor():
            while True:
                time.sleep(8)  # 每8秒检查一次
                current_time = time.time()
                retry_list = []

                for drone_id, info in list(self.pending_commands.items()):
                    elapsed = current_time - info["time"]
                    if elapsed > 8:  # 8秒内没反应
                        retry_list.append(drone_id)

                for drone_id in retry_list:
                    info = self.pending_commands[drone_id]
                    if info["retry"] < 3:  # 最多重试3次
                        info["retry"] += 1
                        info["time"] = current_time
                        self._log(f"[重试] {drone_id} 无响应，第{info['retry']}次重发指令")
                        if drone_id in self.missions:
                            self._publish_mission(drone_id, self.missions[drone_id])
                    else:
                        self._log(f"[失败] {drone_id} 重试3次仍无响应，放弃")
                        del self.pending_commands[drone_id]

        threading.Thread(target=monitor, daemon=True).start()

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            client.subscribe("dji/drone/+/status")
            self.root.after(0, lambda: self.lbl_mqtt.config(
                text="● MQTT 已连接", foreground="#a6e3a1"))
            self._log("MQTT连接成功，监听所有飞机状态...")
        else:
            self._log(f"MQTT连接失败 rc={rc}")

    def _on_mqtt_disconnect(self, client, userdata, rc):
        self.connected = False
        self.root.after(0, lambda: self.lbl_mqtt.config(
            text="● MQTT 已断开", foreground="#f38ba8"))

    def _on_mqtt_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
            drone_id = data.get("drone_id", "unknown")
            status = data.get("mission_status", "")

            self.drones[drone_id] = {
                "lat": data.get("lat", 0),
                "lng": data.get("lng", 0),
                "altitude": data.get("altitude", 0),
                "mission_status": status,
                "is_flying": data.get("is_flying", False),
                "last_seen": time.strftime("%H:%M:%S"),
            }
            self.root.after(0, self._refresh_table)
            self._save_to_bmob(drone_id, data)

            # 检查待确认指令：如果状态变化了，说明指令已送达
            if drone_id in self.pending_commands:
                last_status = self.pending_commands[drone_id]["last_status"]
                if status != last_status and status not in ["待命", "电机停止"]:
                    self._log(f"[确认] {drone_id} 指令已送达，状态: {status}")
                    del self.pending_commands[drone_id]

            # 检测任务完成，触发依赖的飞机
            if ("任务完成" in status or "降落完成" in status) and drone_id not in self.completed:
                self.completed.add(drone_id)
                self._log(f"[编排] ✓ {drone_id} 任务完成")
                self._log(f"[编排] 当前已完成: {list(self.completed)}")
                self._check_and_trigger_dependents(drone_id)
        except Exception as e:
            self._log(f"解析状态失败: {e}")

    # ── 指令发送 ───────────────────────────────────────
    def _save_mission(self):
        """保存任务参数到选中飞机"""
        drone_id = self.selected_drone.get()
        if drone_id == "(未选择)":
            messagebox.showwarning("提示", "请先在列表中选择一台飞机")
            return

        try:
            mission = {
                "start_lat": float(self.entries["start_lat"].get()),
                "start_lng": float(self.entries["start_lng"].get()),
                "end_lat": float(self.entries["end_lat"].get()),
                "end_lng": float(self.entries["end_lng"].get()),
                "altitude": float(self.entries["altitude"].get()),
                "speed": float(self.entries["speed"].get()),
                "stay_duration": int(self.entries["stay"].get()),
            }
            self.missions[drone_id] = mission
            self._log(f"已保存任务参数 → {drone_id}")
            self._refresh_table()
            messagebox.showinfo("成功", f"任务已保存到 {drone_id}\n点击'执行任务'开始飞行")
        except ValueError:
            messagebox.showerror("错误", "请检查坐标和参数格式是否正确")

    def _set_dependency(self):
        """设置飞机执行依赖"""
        drone_id = self.selected_drone.get()
        if drone_id == "(未选择)":
            messagebox.showwarning("提示", "请先在列表中选择一台飞机")
            return

        if drone_id not in self.missions:
            messagebox.showwarning("提示", "请先为该飞机设置任务")
            return

        # 创建依赖选择对话框
        dialog = tk.Toplevel(self.root)
        dialog.title(f"设置 {drone_id} 的执行依赖")
        dialog.geometry("400x300")
        dialog.configure(bg="#1e1e2e")

        tk.Label(dialog, text=f"选择 {drone_id} 需要等待哪些飞机完成任务后再执行：",
                 bg="#1e1e2e", fg="#cdd6f4", font=("微软雅黑", 10)).pack(pady=10)

        # 可选飞机列表（排除自己）
        available = [d for d in self.missions.keys() if d != drone_id]

        if not available:
            tk.Label(dialog, text="没有其他飞机可选", bg="#1e1e2e", fg="#f38ba8").pack()
            return

        # 复选框列表
        vars_dict = {}
        frame = tk.Frame(dialog, bg="#1e1e2e")
        frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        for d in available:
            var = tk.BooleanVar(value=(d in self.dependencies.get(drone_id, [])))
            cb = tk.Checkbutton(frame, text=d, variable=var, bg="#1e1e2e", fg="#cdd6f4",
                                selectcolor="#313244", font=("微软雅黑", 10))
            cb.pack(anchor=tk.W, pady=2)
            vars_dict[d] = var

        def save():
            selected = [d for d, var in vars_dict.items() if var.get()]
            if selected:
                self.dependencies[drone_id] = selected
                self._log(f"{drone_id} 依赖: {', '.join(selected)}")
            else:
                if drone_id in self.dependencies:
                    del self.dependencies[drone_id]
                self._log(f"{drone_id} 无依赖，可立即执行")
            self._refresh_table()
            dialog.destroy()

        tk.Button(dialog, text="保存", bg="#a6e3a1", fg="#1e1e2e", command=save).pack(pady=10)

    def _send_mission(self):
        """执行选中飞机的预设任务"""
        drone_id = self.selected_drone.get()
        if drone_id == "(未选择)":
            messagebox.showwarning("提示", "请先在列表中选择一台飞机")
            return

        if drone_id not in self.missions:
            messagebox.showwarning("提示", "该飞机尚未设置任务，请先点击'设置任务'")
            return

        self._publish_mission(drone_id, self.missions[drone_id])

    def _stop_mission(self):
        drone_id = self.selected_drone.get()
        if drone_id == "(未选择)":
            messagebox.showwarning("提示", "请先在列表中选择一台飞机")
            return
        payload = json.dumps({"action": "stop_mission"})
        self.mqtt_client.publish(f"dji/drone/{drone_id}/command", payload)
        self._log(f"已发送停止指令 → {drone_id}")

    def _broadcast_mission(self):
        """广播执行所有已设置任务的飞机"""
        if not self.missions:
            messagebox.showwarning("提示", "没有飞机设置了任务")
            return
        for drone_id in self.missions:
            if drone_id in self.drones:
                self._publish_mission(drone_id, self.missions[drone_id])
        self._log(f"已广播执行 {len(self.missions)} 架飞机的任务")

    def _auto_execute(self):
        """自动编排执行 - 按依赖关系顺序执行"""
        if not self.missions:
            messagebox.showwarning("提示", "没有飞机设置了任务")
            return

        self.completed.clear()
        self._log("开始自动编排执行...")

        # 找出无依赖的飞机，立即执行
        for drone_id in self.missions:
            if drone_id not in self.dependencies or not self.dependencies[drone_id]:
                self._publish_mission(drone_id, self.missions[drone_id])
                self._log(f"[编排] {drone_id} 无依赖，立即执行")

        self._log("自动编排已启动，将根据任务完成情况自动触发后续飞机")

    def _load_config(self):
        """从missions.json加载配置"""
        try:
            with open("missions.json", "r", encoding="utf-8") as f:
                config = json.load(f)

            # 加载任务
            for m in config.get("missions", []):
                drone_id = m["drone_id"]
                self.missions[drone_id] = {
                    "start_lat": m["start_lat"],
                    "start_lng": m["start_lng"],
                    "end_lat": m["end_lat"],
                    "end_lng": m["end_lng"],
                    "altitude": m["altitude"],
                    "speed": m["speed"],
                    "stay_duration": m["stay_duration"],
                }

            # 加载依赖关系
            self.dependencies = config.get("dependencies", {})

            self._refresh_table()
            self._log(f"已加载 {len(self.missions)} 架飞机的任务配置")
            messagebox.showinfo("成功", f"已加载 {len(self.missions)} 架飞机的任务\n可直接点击'自动编排'执行")
        except FileNotFoundError:
            messagebox.showerror("错误", "未找到 missions.json 配置文件")
        except Exception as e:
            messagebox.showerror("错误", f"加载配置失败：{e}")

    def _save_config(self):
        """保存当前配置到missions.json"""
        try:
            config = {
                "missions": [
                    {
                        "drone_id": drone_id,
                        **mission
                    }
                    for drone_id, mission in self.missions.items()
                ],
                "dependencies": self.dependencies
            }

            with open("missions.json", "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

            self._log("配置已保存到 missions.json")
            messagebox.showinfo("成功", "配置已保存到 missions.json")
        except Exception as e:
            messagebox.showerror("错误", f"保存配置失败：{e}")

    def _publish_mission(self, drone_id, mission=None):
        # 检查MQTT连接状态
        if not self.connected:
            messagebox.showerror("错误", "MQTT未连接，无法发送任务\n请检查网络连接")
            self._log("发送失败：MQTT未连接")
            return

        if mission is None:
            try:
                mission = {
                    "start_lat": float(self.entries["start_lat"].get()),
                    "start_lng": float(self.entries["start_lng"].get()),
                    "end_lat": float(self.entries["end_lat"].get()),
                    "end_lng": float(self.entries["end_lng"].get()),
                    "altitude": float(self.entries["altitude"].get()),
                    "speed": float(self.entries["speed"].get()),
                    "stay_duration": int(self.entries["stay"].get()),
                }
            except ValueError:
                messagebox.showerror("错误", "请检查坐标和参数格式是否正确")
                return

        payload = {
            "action": "start_mission",
            "start_lat": mission["start_lat"],
            "start_lng": mission["start_lng"],
            "end_lat": mission["end_lat"],
            "end_lng": mission["end_lng"],
            "altitude": mission["altitude"],
            "speed": mission["speed"],
            "stay_duration": mission["stay_duration"],
            "mode": "auto",
        }
        result = self.mqtt_client.publish(f"dji/drone/{drone_id}/command", json.dumps(payload), qos=1)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            self._log(f"✓ 已发送任务 → {drone_id}  终点({payload['end_lat']}, {payload['end_lng']})")
            # 记录待确认的指令
            current_status = self.drones.get(drone_id, {}).get("mission_status", "待命")
            self.pending_commands[drone_id] = {
                "time": time.time(),
                "retry": 0,
                "last_status": current_status
            }
        else:
            self._log(f"✗ 发送失败 → {drone_id} (错误码: {result.rc})")
            messagebox.showerror("发送失败", f"MQTT发送失败，错误码: {result.rc}")

    # ── 表格刷新 ───────────────────────────────────────
    def _refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        for drone_id, d in self.drones.items():
            status_icon = "🟢 飞行中" if d["is_flying"] else "⚪ 待命"
            mission_preset = "✅ 已设置" if drone_id in self.missions else "❌ 未设置"

            # 显示依赖关系
            if drone_id in self.dependencies and self.dependencies[drone_id]:
                dep_text = ", ".join(self.dependencies[drone_id])
            else:
                dep_text = "无"

            self.tree.insert("", tk.END, iid=drone_id, values=(
                drone_id,
                status_icon,
                f"{d['lat']:.6f}",
                f"{d['lng']:.6f}",
                f"{d['altitude']:.1f}",
                d["mission_status"],
                mission_preset,
                dep_text,
                d["last_seen"],
            ))

    def _check_and_trigger_dependents(self, completed_drone_id):
        """检查并触发依赖已完成飞机的其他飞机"""
        self._log(f"[编排] 检查依赖 {completed_drone_id} 的飞机...")

        for drone_id, deps in self.dependencies.items():
            if completed_drone_id in deps:
                self._log(f"[编排] {drone_id} 依赖 {completed_drone_id}")
                # 检查该飞机的所有依赖是否都已完成
                all_completed = all(d in self.completed for d in deps)
                self._log(f"[编排] {drone_id} 的依赖: {deps}, 已完成: {[d for d in deps if d in self.completed]}")

                if all_completed and drone_id not in self.completed:
                    self._log(f"[编排] ✓ {drone_id} 的依赖已全部完成，开始执行")
                    if drone_id in self.missions:
                        self._publish_mission(drone_id, self.missions[drone_id])
                    else:
                        self._log(f"[编排] ✗ {drone_id} 没有预设任务")
                elif drone_id in self.completed:
                    self._log(f"[编排] {drone_id} 已完成，跳过")
                else:
                    self._log(f"[编排] {drone_id} 的依赖未全部完成，等待中")

    def _on_select(self, event):
        sel = self.tree.selection()
        if sel:
            drone_id = sel[0]
            self.selected_drone.set(drone_id)
            self._update_detail_panel(drone_id)

    def _update_detail_panel(self, drone_id):
        """更新飞机详情面板"""
        self.detail_text.config(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)

        if drone_id not in self.drones:
            self.detail_text.insert("1.0", f"飞机 {drone_id} 离线")
            self.detail_text.config(state=tk.DISABLED)
            return

        d = self.drones[drone_id]
        mission = self.missions.get(drone_id)
        deps = self.dependencies.get(drone_id, [])

        info = f"""╔═══════════════════════════╗
║  飞机ID: {drone_id}
╚═══════════════════════════╝

【实时状态】
  状态: {'🟢 飞行中' if d['is_flying'] else '⚪ 待命'}
  纬度: {d['lat']:.6f}
  经度: {d['lng']:.6f}
  高度: {d['altitude']:.1f} m
  任务: {d['mission_status']}
  更新: {d['last_seen']}

【预设任务】"""

        if mission:
            info += f"""
  起点: ({mission['start_lat']:.6f}, {mission['start_lng']:.6f})
  终点: ({mission['end_lat']:.6f}, {mission['end_lng']:.6f})
  高度: {mission['altitude']} m
  速度: {mission['speed']} m/s
  停留: {mission['stay_duration']} 秒"""
        else:
            info += "\n  ❌ 未设置"

        info += "\n\n【执行依赖】\n"
        if deps:
            info += "  等待: " + ", ".join(deps)
        else:
            info += "  无依赖，可立即执行"

        self.detail_text.insert("1.0", info)
        self.detail_text.config(state=tk.DISABLED)

    # ── Bmob存储 ───────────────────────────────────────
    def _save_to_bmob(self, drone_id, data):
        def _post():
            try:
                headers = {
                    "X-Bmob-Application-Id": BMOB_APP_ID,
                    "X-Bmob-REST-API-Key": BMOB_REST_KEY,
                    "Content-Type": "application/json",
                }
                body = {
                    "droneId": drone_id,
                    "lat": data.get("lat"),
                    "lng": data.get("lng"),
                    "altitude": data.get("altitude"),
                    "missionStatus": data.get("mission_status"),
                    "isFlying": data.get("is_flying"),
                }
                requests.post(f"{BMOB_BASE_URL}/DroneStatus", headers=headers,
                              json=body, timeout=5)
            except Exception:
                pass
        threading.Thread(target=_post, daemon=True).start()

    # ── 日志 ───────────────────────────────────────────
    def _log(self, msg):
        def _append():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self.root.after(0, _append)


if __name__ == "__main__":
    root = tk.Tk()
    app = DroneControlApp(root)
    root.mainloop()
