"""
DJI无人机集群控制台
依赖: pip install paho-mqtt requests
"""
import json
import math
import heapq
import random
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
AIRPORTS_FILE = "airports.json"
# ──────────────────────────────────────────────────────


def haversine_distance(lat1, lng1, lat2, lng2):
    """计算两个经纬度点之间的距离（单位：米）"""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def dijkstra_airport_path(airports, src_idx, dst_idx):
    """
    Dijkstra 求机场最短路径。
    连通条件：两机场半径之和 >= 两机场中心距离（覆盖范围能衔接）。
    返回 (路径机场索引列表, 总距离) 或 (None, inf)。
    """
    n = len(airports)
    dist = [math.inf] * n
    prev = [-1] * n
    dist[src_idx] = 0
    heap = [(0, src_idx)]

    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue
        for v in range(n):
            if v == u:
                continue
            a, b = airports[u], airports[v]
            center_dist = haversine_distance(a["lat"], a["lng"], b["lat"], b["lng"])
            if a["radius"] + b["radius"] >= center_dist:   # 两机场覆盖可衔接
                new_d = dist[u] + center_dist
                if new_d < dist[v]:
                    dist[v] = new_d
                    prev[v] = u
                    heapq.heappush(heap, (new_d, v))

    if dist[dst_idx] == math.inf:
        return None, math.inf

    # 还原路径
    path = []
    cur = dst_idx
    while cur != -1:
        path.append(cur)
        cur = prev[cur]
    path.reverse()
    return path, dist[dst_idx]


# ══════════════════════════════════════════════════════
#  机场管理弹窗
# ══════════════════════════════════════════════════════
class AirportManagerDialog:
    """机场管理独立弹窗：增删改机场、绑定飞机"""

    def __init__(self, parent, airports_ref, on_save, app=None):
        """
        airports_ref : list，机场数据的引用（直接修改）
        on_save      : 保存后回调（用于刷新主界面等）
        app          : DroneControlApp 实例（用于获取已连接飞机列表）
        """
        self.airports = airports_ref
        self.on_save = on_save
        self.app = app

        self.win = tk.Toplevel(parent)
        self.win.title("✈ 机场管理")
        self.win.geometry("780x560")
        self.win.configure(bg="#1e1e2e")
        self.win.grab_set()

        self._build_ui()
        self._refresh_list()
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI ──────────────────────────────────────────
    def _build_ui(self):
        BG = "#1e1e2e"
        FG = "#cdd6f4"
        ENTRY_BG = "#313244"

        # 机场列表
        list_frame = tk.Frame(self.win, bg=BG)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(10, 4))

        cols = ("名称", "纬度", "经度", "半径(m)", "绑定飞机")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=10)
        widths = [100, 110, 110, 80, 260]
        for col, w in zip(cols, widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor=tk.CENTER)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=sb.set)

        # 编辑表单
        form = tk.LabelFrame(self.win, text=" 机场信息 ", bg=BG, fg=FG, font=("微软雅黑", 10))
        form.pack(fill=tk.X, padx=12, pady=4)

        row1 = tk.Frame(form, bg=BG)
        row1.pack(fill=tk.X, padx=8, pady=4)

        def lbl(parent, text):
            tk.Label(parent, text=text, bg=BG, fg=FG, font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=(8, 2))

        def entry(parent, width=13):
            e = tk.Entry(parent, bg=ENTRY_BG, fg=FG, insertbackground=FG,
                         font=("Consolas", 10), width=width, relief=tk.FLAT)
            e.pack(side=tk.LEFT, padx=(0, 8))
            return e

        lbl(row1, "名称"); self.e_name   = entry(row1, 10)
        lbl(row1, "纬度"); self.e_lat    = entry(row1, 14)
        lbl(row1, "经度"); self.e_lng    = entry(row1, 14)
        lbl(row1, "半径(m)"); self.e_radius = entry(row1, 7)

        row2 = tk.Frame(form, bg=BG)
        row2.pack(fill=tk.X, padx=8, pady=4)
        lbl(row2, "绑定飞机（已连接）")

        # 多选 Listbox，显示已连接飞机
        lb_frame = tk.Frame(row2, bg=BG)
        lb_frame.pack(side=tk.LEFT, padx=(0, 8))

        lb_scroll = ttk.Scrollbar(lb_frame, orient=tk.VERTICAL)
        self.lb_drones = tk.Listbox(
            lb_frame, selectmode=tk.MULTIPLE,
            bg=ENTRY_BG, fg=FG, selectbackground="#89b4fa", selectforeground="#1e1e2e",
            font=("Consolas", 10), width=36, height=4,
            relief=tk.FLAT, yscrollcommand=lb_scroll.set,
            exportselection=False
        )
        lb_scroll.config(command=self.lb_drones.yview)
        self.lb_drones.pack(side=tk.LEFT)
        lb_scroll.pack(side=tk.LEFT, fill=tk.Y)

        # 填充已连接飞机列表
        connected_ids = list(self.app.drones.keys()) if self.app else []
        for did in connected_ids:
            self.lb_drones.insert(tk.END, did)

        tk.Label(row2, text="（Ctrl/Shift多选）",
                 bg=BG, fg="#6c7086", font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=4)

        row3 = tk.Frame(form, bg=BG)
        row3.pack(fill=tk.X, padx=8, pady=(4, 8))

        def btn(parent, text, color, cmd):
            tk.Button(parent, text=text, bg=color, fg="#1e1e2e",
                      font=("微软雅黑", 10, "bold"), relief=tk.FLAT, padx=14,
                      command=cmd).pack(side=tk.LEFT, padx=4)

        btn(row3, "➕ 新增机场", "#a6e3a1", self._add_airport)
        btn(row3, "✏ 更新选中", "#f9e2af", self._update_airport)
        btn(row3, "🗑 删除选中", "#f38ba8", self._delete_airport)
        btn(row3, "💾 保存到文件", "#89b4fa", self._save_airports)

    def _refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for ap in self.airports:
            self.tree.insert("", tk.END, values=(
                ap.get("name", ""),
                f"{ap['lat']:.6f}",
                f"{ap['lng']:.6f}",
                ap.get("radius", 50),
                ", ".join(ap.get("drone_ids", [])),
            ))

    def _on_select(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        ap = self.airports[idx]
        for e, key, default in [
            (self.e_name,   "name",   ""),
            (self.e_lat,    "lat",    ""),
            (self.e_lng,    "lng",    ""),
            (self.e_radius, "radius", "50"),
        ]:
            e.delete(0, tk.END)
            e.insert(0, str(ap.get(key, default)))

        # 在 Listbox 中高亮已绑定的飞机
        bound = ap.get("drone_ids", [])
        self.lb_drones.selection_clear(0, tk.END)
        for i in range(self.lb_drones.size()):
            if self.lb_drones.get(i) in bound:
                self.lb_drones.selection_set(i)

    def _form_data(self):
        name   = self.e_name.get().strip()
        lat    = self.e_lat.get().strip()
        lng    = self.e_lng.get().strip()
        radius = self.e_radius.get().strip()
        # 从 Listbox 读取选中的飞机 ID
        drones = [self.lb_drones.get(i) for i in self.lb_drones.curselection()]
        if not (name and lat and lng and radius):
            messagebox.showwarning("提示", "名称、纬度、经度、半径均不能为空", parent=self.win)
            return None
        try:
            return {
                "name": name,
                "lat": float(lat),
                "lng": float(lng),
                "radius": float(radius),
                "drone_ids": drones,
            }
        except ValueError:
            messagebox.showerror("错误", "纬度/经度/半径必须是数字", parent=self.win)
            return None

    def _add_airport(self):
        data = self._form_data()
        if data is None:
            return
        self.airports.append(data)
        self._refresh_list()

    def _update_airport(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在列表中选择一个机场", parent=self.win)
            return
        data = self._form_data()
        if data is None:
            return
        idx = self.tree.index(sel[0])
        self.airports[idx] = data
        self._refresh_list()

    def _delete_airport(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择要删除的机场", parent=self.win)
            return
        idx = self.tree.index(sel[0])
        name = self.airports[idx].get("name", "")
        if not messagebox.askyesno("确认", f"确定删除机场「{name}」？", parent=self.win):
            return
        self.airports.pop(idx)
        self._refresh_list()

    def _save_airports(self):
        try:
            with open(AIRPORTS_FILE, "w", encoding="utf-8") as f:
                json.dump({"airports": self.airports}, f, indent=2, ensure_ascii=False)
            messagebox.showinfo("成功", f"机场数据已保存到 {AIRPORTS_FILE}", parent=self.win)
            if self.on_save:
                self.on_save()
        except Exception as e:
            messagebox.showerror("错误", f"保存失败：{e}", parent=self.win)

    def _on_close(self):
        if messagebox.askyesno("保存确认", "是否将机场数据保存到文件？\n（不保存则本次修改重启后丢失）", parent=self.win):
            self._save_airports()
        self.win.destroy()


# ══════════════════════════════════════════════════════
#  配送任务规划弹窗
# ══════════════════════════════════════════════════════
class DeliveryPlannerDialog:
    """
    配送任务规划弹窗。
    流程：
      1. 输入商家坐标、用户坐标、飞行参数
      2. Dijkstra 规划最短机场路径
      3. 展示规划结果，确认后写入主程序 missions + dependencies
      4. 单独派出取餐飞机（飞机场→商家→回机场），
         取餐完成后自动触发现有接力逻辑
    """

    def __init__(self, parent, airports, drones_ref, app):
        """
        airports   : 当前机场列表
        drones_ref : app.drones dict 引用（用于查看在线飞机）
        app        : DroneControlApp 实例（写入 missions/dependencies 并调用编排）
        """
        self.airports = airports
        self.drones_ref = drones_ref
        self.app = app
        self._planned_path = []       # 规划出的机场索引链
        self._pickup_drone_id = None  # 负责取餐的飞机

        self.win = tk.Toplevel(parent)
        self.win.title("🛵 配送任务规划")
        self.win.geometry("700x620")
        self.win.configure(bg="#1e1e2e")
        self.win.grab_set()

        self._build_ui()

    # ── UI ──────────────────────────────────────────
    def _build_ui(self):
        BG, FG, ENTRY_BG = "#1e1e2e", "#cdd6f4", "#313244"

        def section(text):
            f = tk.LabelFrame(self.win, text=f" {text} ", bg=BG, fg=FG, font=("微软雅黑", 10))
            f.pack(fill=tk.X, padx=12, pady=4)
            return f

        def row(parent):
            r = tk.Frame(parent, bg=BG)
            r.pack(fill=tk.X, padx=8, pady=4)
            return r

        def lbl(parent, text):
            tk.Label(parent, text=text, bg=BG, fg=FG, font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=(8, 2))

        def entry(parent, width=16, default=""):
            e = tk.Entry(parent, bg=ENTRY_BG, fg=FG, insertbackground=FG,
                         font=("Consolas", 10), width=width, relief=tk.FLAT)
            if default:
                e.insert(0, default)
            e.pack(side=tk.LEFT, padx=(0, 8))
            return e

        # ── 坐标输入 ──
        coord_frame = section("商家 & 用户坐标")
        r1 = row(coord_frame)
        lbl(r1, "商家纬度"); self.e_m_lat = entry(r1)
        lbl(r1, "商家经度"); self.e_m_lng = entry(r1)
        r2 = row(coord_frame)
        lbl(r2, "用户纬度"); self.e_u_lat = entry(r2)
        lbl(r2, "用户经度"); self.e_u_lng = entry(r2)

        # 快速加载 delivery_coords.json
        def load_coords():
            try:
                with open("delivery_coords.json", "r", encoding="utf-8") as f:
                    c = json.load(f)
                for e, key in [(self.e_m_lat, "merchant_lat"), (self.e_m_lng, "merchant_lng"),
                               (self.e_u_lat, "user_lat"),     (self.e_u_lng, "user_lng")]:
                    e.delete(0, tk.END)
                    e.insert(0, str(c.get(key, "")))
            except Exception as ex:
                messagebox.showwarning("提示", f"加载失败：{ex}", parent=self.win)

        def save_coords():
            try:
                c = {
                    "merchant_lat": float(self.e_m_lat.get()),
                    "merchant_lng": float(self.e_m_lng.get()),
                    "user_lat":     float(self.e_u_lat.get()),
                    "user_lng":     float(self.e_u_lng.get()),
                }
                with open("delivery_coords.json", "w", encoding="utf-8") as f:
                    json.dump(c, f, indent=2, ensure_ascii=False)
                messagebox.showinfo("成功", "坐标已保存到 delivery_coords.json", parent=self.win)
            except ValueError:
                messagebox.showerror("错误", "请先填写有效的坐标数值", parent=self.win)
            except Exception as ex:
                messagebox.showerror("错误", f"保存失败：{ex}", parent=self.win)

        r3 = row(coord_frame)
        tk.Button(r3, text="📂 加载 delivery_coords.json", bg="#fab387", fg="#1e1e2e",
                  font=("微软雅黑", 10, "bold"), relief=tk.FLAT, padx=12,
                  command=load_coords).pack(side=tk.LEFT, padx=4)
        tk.Button(r3, text="💾 保存坐标", bg="#94e2d5", fg="#1e1e2e",
                  font=("微软雅黑", 10, "bold"), relief=tk.FLAT, padx=12,
                  command=save_coords).pack(side=tk.LEFT, padx=4)

        # ── 飞行参数 ──
        param_frame = section("飞行参数（所有段共用）")
        rp = row(param_frame)
        lbl(rp, "高度(m)");  self.e_alt   = entry(rp, 7, "10")
        lbl(rp, "速度(m/s)"); self.e_spd  = entry(rp, 7, "2")
        lbl(rp, "停留(s)");  self.e_stay  = entry(rp, 7, "5")

        # ── 操作按钮 ──
        btn_frame = tk.Frame(self.win, bg=BG)
        btn_frame.pack(fill=tk.X, padx=12, pady=4)

        def btn(text, color, cmd):
            tk.Button(btn_frame, text=text, bg=color, fg="#1e1e2e",
                      font=("微软雅黑", 10, "bold"), relief=tk.FLAT, padx=16,
                      command=cmd).pack(side=tk.LEFT, padx=6, pady=4)

        btn("🔍 规划路径", "#89b4fa", self._plan_route)
        btn("🚀 确认执行", "#a6e3a1", self._confirm_execute)

        # ── 规划结果 ──
        result_frame = section("规划结果")
        self.result_text = tk.Text(
            result_frame, bg="#181825", fg=FG, font=("Consolas", 9),
            height=16, wrap=tk.WORD, relief=tk.FLAT, state=tk.DISABLED
        )
        self.result_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    # ── 规划逻辑 ────────────────────────────────────
    def _get_floats(self):
        try:
            m_lat  = float(self.e_m_lat.get())
            m_lng  = float(self.e_m_lng.get())
            u_lat  = float(self.e_u_lat.get())
            u_lng  = float(self.e_u_lng.get())
            alt    = float(self.e_alt.get())
            spd    = float(self.e_spd.get())
            stay   = int(self.e_stay.get())
            return m_lat, m_lng, u_lat, u_lng, alt, spd, stay
        except ValueError:
            messagebox.showerror("错误", "请检查所有坐标和参数格式", parent=self.win)
            return None

    def _find_covering_airport(self, lat, lng):
        """找到覆盖该点（在半径内）且距离最近的机场，返回索引或 -1"""
        best_idx, best_dist = -1, math.inf
        for i, ap in enumerate(self.airports):
            d = haversine_distance(lat, lng, ap["lat"], ap["lng"])
            if d <= ap["radius"] and d < best_dist:
                best_dist = d
                best_idx = i
        return best_idx

    def _plan_route(self):
        vals = self._get_floats()
        if vals is None:
            return
        m_lat, m_lng, u_lat, u_lng, alt, spd, stay = vals

        if not self.airports:
            messagebox.showerror("错误", "当前没有任何机场，请先在「机场管理」中添加", parent=self.win)
            return

        # 找商家最近机场
        src_idx = self._find_covering_airport(m_lat, m_lng)
        if src_idx == -1:
            self._show_result("❌ 规划失败\n\n商家坐标不在任何机场的覆盖半径内。\n请增大附近机场半径或新增机场。")
            return

        # 找用户最近机场
        dst_idx = self._find_covering_airport(u_lat, u_lng)
        if dst_idx == -1:
            self._show_result("❌ 规划失败\n\n用户坐标不在任何机场的覆盖半径内。\n请增大附近机场半径或新增机场。")
            return

        # 如果商家和用户在同一机场覆盖内
        if src_idx == dst_idx:
            self._planned_path = [src_idx]
            ap = self.airports[src_idx]
            self._show_result(
                f"✅ 规划成功（同机场直送）\n\n"
                f"商家 → {ap['name']} → 用户\n"
                f"（商家和用户均在 {ap['name']} 覆盖范围内，同一飞机完成取送）\n\n"
                f"机场信息：{ap['name']}  ({ap['lat']:.6f}, {ap['lng']:.6f})  半径{ap['radius']}m\n"
                f"绑定飞机：{', '.join(ap['drone_ids']) or '（无绑定飞机）'}"
            )
            return

        # Dijkstra 求最短路径
        path, total_dist = dijkstra_airport_path(self.airports, src_idx, dst_idx)
        if path is None:
            self._show_result(
                "❌ 规划失败：机场半径不够，无法覆盖\n\n"
                f"起点机场「{self.airports[src_idx]['name']}」到终点机场「{self.airports[dst_idx]['name']}」\n"
                "之间的机场链无法形成连续覆盖，无法规划路径。\n\n"
                "建议：\n  • 增大沿途机场的覆盖半径\n  • 在中间地带新增机场"
            )
            return

        self._planned_path = path

        # 构造结果文本
        lines = [f"✅ 规划成功！总飞行距离约 {total_dist:.0f} 米\n"]
        lines.append(f"路径：商家 → {'→'.join(self.airports[i]['name'] for i in path)} → 用户\n")
        lines.append("─" * 50)
        lines.append("\n途经机场详情：")
        for i, idx in enumerate(path):
            ap = self.airports[idx]
            role = ""
            if i == 0:
                role = "【起点机场 - 取餐】"
            elif i == len(path) - 1:
                role = "【终点机场 - 送达】"
            else:
                role = f"【中转 #{i}】"
            lines.append(
                f"  {role} {ap['name']}  "
                f"({ap['lat']:.6f}, {ap['lng']:.6f})  半径{ap['radius']}m\n"
                f"    绑定飞机：{', '.join(ap['drone_ids']) or '（无绑定飞机）'}"
            )
        lines.append("\n─" * 25)
        lines.append("\n执行计划：")
        lines.append(f"  Step0: 取餐飞机从 {self.airports[path[0]]['name']} 出发 → 飞到商家 → 返回机场")
        for i in range(len(path) - 1):
            src_name = self.airports[path[i]]['name']
            dst_name = self.airports[path[i+1]]['name']
            lines.append(f"  Step{i+1}: {src_name} 的飞机 → 飞往 {dst_name}")
        lines.append(f"\n最终在 {self.airports[path[-1]]['name']} 完成送达用户")
        lines.append("\n点击「确认执行」开始任务")

        self._show_result("\n".join(lines))

    def _show_result(self, text):
        self.result_text.config(state=tk.NORMAL)
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", text)
        self.result_text.config(state=tk.DISABLED)

    # ── 确认执行 ────────────────────────────────────
    def _confirm_execute(self):
        if not self._planned_path:
            messagebox.showwarning("提示", "请先点击「规划路径」", parent=self.win)
            return

        vals = self._get_floats()
        if vals is None:
            return
        m_lat, m_lng, u_lat, u_lng, alt, spd, stay = vals

        path = self._planned_path
        airports = self.airports
        app = self.app

        # ── 同机场直送特殊处理 ──
        if len(path) == 1:
            ap = airports[path[0]]
            if not ap["drone_ids"]:
                messagebox.showerror("错误", f"机场「{ap['name']}」没有绑定飞机，无法执行", parent=self.win)
                return
            drone_id = random.choice(ap["drone_ids"])
            # 直接安排取餐+送达（飞往商家→飞往用户），这里视为取餐任务
            self._launch_pickup_then_delivery(
                drone_id, ap, m_lat, m_lng, u_lat, u_lng, alt, spd, stay,
                relay_path=[], relay_airports=[]
            )
            messagebox.showinfo("成功", f"已启动同机场直送任务\n飞机：{drone_id}", parent=self.win)
            return

        # ── 多机场接力 ──
        # 检查每个机场是否有飞机
        for idx in path:
            ap = airports[idx]
            if not ap["drone_ids"]:
                messagebox.showerror(
                    "错误",
                    f"机场「{ap['name']}」没有绑定飞机，无法规划接力任务。\n请在机场管理中绑定飞机。",
                    parent=self.win
                )
                return

        # 为每段接力随机选一架飞机（跳过第一个机场，它的飞机负责取餐）
        # path[0] 的飞机负责取餐，之后 path[0]→path[1]→...→path[-1]
        pickup_drone = random.choice(airports[path[0]]["drone_ids"])
        self._pickup_drone_id = pickup_drone

        # 构建接力任务：path[0]→path[1], path[1]→path[2], ...
        # 第 i 段：airports[path[i]] 的随机飞机 飞往 airports[path[i+1]]
        relay_missions = []   # list of (drone_id, start_airport, end_airport)
        for i in range(len(path) - 1):
            src_ap = airports[path[i]]
            dst_ap = airports[path[i + 1]]
            relay_drone = random.choice(src_ap["drone_ids"])
            relay_missions.append((relay_drone, src_ap, dst_ap))

        # 写入主程序 missions 和 dependencies
        # - 取餐飞机：起终点 = 机场0→商家→回机场0（特殊处理，单独发送）
        # - 接力飞机：起点=本机场，终点=下一机场，依赖前一段完成
        #
        # 接力段写入
        app.missions.clear()
        app.dependencies.clear()
        app.completed.clear()

        prev_drone = None
        for i, (relay_drone, src_ap, dst_ap) in enumerate(relay_missions):
            app.missions[relay_drone] = {
                "start_lat":    src_ap["lat"],
                "start_lng":    src_ap["lng"],
                "end_lat":      dst_ap["lat"],
                "end_lng":      dst_ap["lng"],
                "altitude":     alt,
                "speed":        spd,
                "stay_duration": stay,
            }
            if prev_drone is not None:
                app.dependencies[relay_drone] = [prev_drone]
            prev_drone = relay_drone

        # 最终送达：终点机场的飞机 → 用户位置（往返）
        final_ap = airports[path[-1]]
        final_drone = random.choice(final_ap["drone_ids"])
        app.missions[final_drone] = {
            "start_lat":    final_ap["lat"],
            "start_lng":    final_ap["lng"],
            "end_lat":      u_lat,
            "end_lng":      u_lng,
            "altitude":     alt,
            "speed":        spd,
            "stay_duration": stay,
        }
        if prev_drone is not None:
            app.dependencies[final_drone] = [prev_drone]
        prev_drone = final_drone

        # 刷新主界面表格
        app.root.after(0, app._refresh_table)

        # ── 启动取餐任务 ──
        self._launch_pickup(pickup_drone, airports[path[0]], m_lat, m_lng, alt, spd, stay)

        summary = (
            f"✅ 配送任务已启动！\n\n"
            f"取餐飞机：{pickup_drone}\n"
            f"  {airports[path[0]]['name']} → 商家({m_lat:.5f},{m_lng:.5f}) → 回机场\n\n"
            f"接力链：\n"
        )
        for relay_drone, src_ap, dst_ap in relay_missions:
            summary += f"  {relay_drone}：{src_ap['name']} → {dst_ap['name']}\n"
        messagebox.showinfo("任务已启动", summary, parent=self.win)

    def _launch_pickup(self, drone_id, home_airport, m_lat, m_lng, alt, spd, stay):
        """
        取餐任务：
          发送 action=pickup_mission（去商家再回机场）
          取餐任务完成后（收到 "电机停止，降落完成"），自动触发接力链的第一段
        """
        app = self.app
        if not app.connected:
            messagebox.showerror("错误", "MQTT未连接，无法启动取餐任务", parent=self.win)
            return

        payload = {
            "action":      "pickup_mission",     # Android端识别为往返取餐
            "pickup_lat":  m_lat,
            "pickup_lng":  m_lng,
            "home_lat":    home_airport["lat"],
            "home_lng":    home_airport["lng"],
            "altitude":    alt,
            "speed":       spd,
            "stay_duration": stay,
            "mode":        "auto",
        }
        result = app.mqtt_client.publish(
            f"dji/drone/{drone_id}/command", json.dumps(payload), qos=1
        )
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            app._log(f"[取餐] ✓ 已发送取餐任务 → {drone_id}  商家({m_lat:.5f}, {m_lng:.5f})")
            # 注册一个"取餐完成监听器"：取餐飞机降落后自动触发接力链
            app._register_pickup_completion(drone_id)
        else:
            app._log(f"[取餐] ✗ 取餐任务发送失败 → {drone_id} (rc={result.rc})")

    def _launch_pickup_then_delivery(self, drone_id, home_airport,
                                     m_lat, m_lng, u_lat, u_lng,
                                     alt, spd, stay,
                                     relay_path, relay_airports):
        """同机场直送：取餐后直接飞到用户"""
        app = self.app
        if not app.connected:
            messagebox.showerror("错误", "MQTT未连接", parent=self.win)
            return
        # 先取餐
        payload = {
            "action":        "pickup_mission",
            "pickup_lat":    m_lat,
            "pickup_lng":    m_lng,
            "home_lat":      home_airport["lat"],
            "home_lng":      home_airport["lng"],
            "altitude":      alt,
            "speed":         spd,
            "stay_duration": stay,
            "mode":          "auto",
        }
        app.mqtt_client.publish(f"dji/drone/{drone_id}/command", json.dumps(payload), qos=1)
        app._log(f"[直送] ✓ 取餐任务 → {drone_id}")

        # 取餐完成后送到用户
        final_mission = {
            "start_lat":    home_airport["lat"],
            "start_lng":    home_airport["lng"],
            "end_lat":      u_lat,
            "end_lng":      u_lng,
            "altitude":     alt,
            "speed":        spd,
            "stay_duration": stay,
        }
        app.missions[drone_id] = final_mission
        # 取餐完成后自动触发接力（这里只有一架飞机）
        app._register_pickup_completion(drone_id)


# ══════════════════════════════════════════════════════
#  主控制台
# ══════════════════════════════════════════════════════
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

        # 机场数据
        self.airports = []
        self._load_airports_file()

        # 取餐监听：pickup_drone_id -> 是否已触发接力
        self._pickup_watchers = set()

        self._build_ui()
        self._connect_mqtt()
        self._start_retry_monitor()

    # ── 机场文件 ──────────────────────────────────────
    def _load_airports_file(self):
        try:
            with open(AIRPORTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.airports = data.get("airports", [])
        except FileNotFoundError:
            self.airports = []
        except Exception as e:
            self.airports = []
            print(f"加载机场文件失败: {e}")

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

        # 操作面板（精简版）
        cmd_frame = tk.LabelFrame(self.root, text=" 任务控制 ", bg="#1e1e2e",
                                   fg="#cdd6f4", font=("微软雅黑", 10))
        cmd_frame.pack(fill=tk.X, padx=12, pady=4)

        ctrl_row = ttk.Frame(cmd_frame)
        ctrl_row.pack(fill=tk.X, padx=8, pady=6)

        self.selected_drone = tk.StringVar(value="(未选择)")
        ttk.Label(ctrl_row, text="目标飞机:").pack(side=tk.LEFT, padx=(4, 2))
        self.lbl_selected = ttk.Label(ctrl_row, textvariable=self.selected_drone, foreground="#a6e3a1")
        self.lbl_selected.pack(side=tk.LEFT, padx=(0, 20))

        def mkbtn(parent, text, color, cmd):
            tk.Button(parent, text=text, bg=color, fg="#1e1e2e",
                      font=("微软雅黑", 10, "bold"), relief=tk.FLAT, padx=16,
                      command=cmd).pack(side=tk.LEFT, padx=4)

        mkbtn(ctrl_row, "✈ 机场管理",  "#cba6f7", self._open_airport_manager)
        mkbtn(ctrl_row, "🛵 规划配送",  "#f38ba8", self._open_delivery_planner)
        mkbtn(ctrl_row, "⬛ 停止任务",  "#f38ba8", self._stop_mission)
        mkbtn(ctrl_row, "📂 加载配置",  "#fab387", self._load_config)
        mkbtn(ctrl_row, "💾 保存配置",  "#94e2d5", self._save_config)

        # 日志
        log_frame = ttk.Frame(self.root)
        log_frame.pack(fill=tk.X, padx=12, pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=5, bg="#313244", fg="#cdd6f4",
                                font=("Consolas", 9), state=tk.DISABLED)
        self.log_text.pack(fill=tk.X)

    # ── 机场/配送入口 ─────────────────────────────────
    def _open_airport_manager(self):
        AirportManagerDialog(
            self.root, self.airports,
            on_save=self._load_airports_file,   # 保存后重新加载
            app=self
        )

    def _open_delivery_planner(self):
        if not self.airports:
            messagebox.showwarning("提示", "当前没有机场数据，请先打开「机场管理」添加机场")
            return
        DeliveryPlannerDialog(self.root, self.airports, self.drones, self)

    # ── 取餐完成监听 ─────────────────────────────────
    def _register_pickup_completion(self, drone_id):
        """注册取餐完成监听：该飞机降落后自动触发接力链（自动编排）"""
        self._pickup_watchers.add(drone_id)
        self._log(f"[取餐] 监听 {drone_id} 取餐返航完成...")

    def _on_pickup_completed(self, drone_id):
        """取餐飞机降落完成，启动接力链"""
        if drone_id not in self._pickup_watchers:
            return
        self._pickup_watchers.discard(drone_id)
        self._log(f"[取餐] ✅ {drone_id} 取餐完成并返回机场，启动接力链...")
        # 接力链第一段飞机没有依赖（已在 DeliveryPlannerDialog 中写入 missions/dependencies）
        # 直接调用现有自动编排逻辑
        self.root.after(0, self._auto_execute)

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
                "start_lat": data.get("start_lat", 0),
                "start_lng": data.get("start_lng", 0),
                "end_lat": data.get("end_lat", 0),
                "end_lng": data.get("end_lng", 0),
                "last_seen": time.strftime("%H:%M:%S"),
            }
            self.root.after(0, self._refresh_table)

            # 如果是当前选中的飞机，更新详情面板
            if drone_id == self.selected_drone.get():
                self.root.after(0, lambda d=drone_id: self._update_detail_panel(d))
            self._save_to_bmob(drone_id, data)

            # 检查待确认指令：如果状态变化了，说明指令已送达
            if drone_id in self.pending_commands:
                last_status = self.pending_commands[drone_id]["last_status"]
                if status != last_status and status not in ["待命", "电机停止"]:
                    self._log(f"[确认] {drone_id} 指令已送达，状态: {status}")
                    del self.pending_commands[drone_id]

            # 检测降落完成
            if "电机停止，降落完成" in status and drone_id not in self.completed:
                # 指令刚发出还在待确认期间，跳过（防止旧状态上报误触发）
                if drone_id in self.pending_commands:
                    self._log(f"[忽略] {drone_id} 指令待确认中，跳过旧落地状态")
                else:
                    self.completed.add(drone_id)

                # 取餐任务完成检测（优先于接力链）
                if drone_id in self._pickup_watchers:
                    stay = self.missions.get(drone_id, {}).get("stay_duration", 5)
                    delay = stay * 2
                    self._log(f"[取餐] {drone_id} 取餐返回，{delay}秒后启动接力链")
                    threading.Timer(
                        delay,
                        lambda d=drone_id: self.root.after(0, lambda: self._on_pickup_completed(d))
                    ).start()
                else:
                    # 正常接力链触发
                    stay = self.missions.get(drone_id, {}).get("stay_duration", 5)
                    delay = stay * 2
                    self._log(f"[编排] {drone_id} 中途降落，{delay}秒后触发依赖飞机")
                    threading.Timer(
                        delay,
                        lambda d=drone_id: self.root.after(0, lambda: self._check_and_trigger_dependents(d))
                    ).start()
        except Exception as e:
            self._log(f"解析状态失败: {e}")

    # ── 指令发送 ───────────────────────────────────────
    def _stop_mission(self):
        if not self.drones:
            messagebox.showwarning("提示", "当前没有已连接的飞机")
            return
        payload = json.dumps({"action": "stop_mission"})
        for drone_id in self.drones:
            self.mqtt_client.publish(f"dji/drone/{drone_id}/command", payload, qos=1)
            self._log(f"[紧急停止] 已发送停止指令 → {drone_id}")
        self.missions.clear()
        self.dependencies.clear()
        self.completed.clear()
        self.pending_commands.clear()
        self._pickup_watchers.clear()
        self._refresh_table()
        self._log(f"[紧急停止] 已广播停止指令至全部 {len(self.drones)} 架飞机，任务状态已清空")

    def _auto_execute(self):
        """自动编排执行 - 按依赖关系顺序执行"""
        if not self.missions:
            messagebox.showwarning("提示", "没有飞机设置了任务")
            return

        self._log("开始自动编排执行...")

        # 只清除将要立即起飞的飞机的完成状态，不能全清
        # 全清会导致旧的"电机停止，降落完成"状态上报重复触发依赖链
        for drone_id in self.missions:
            if drone_id not in self.dependencies or not self.dependencies[drone_id]:
                self.completed.discard(drone_id)
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

    def _publish_mission(self, drone_id, mission):
        # 检查MQTT连接状态
        if not self.connected:
            messagebox.showerror("错误", "MQTT未连接，无法发送任务\n请检查网络连接")
            self._log("发送失败：MQTT未连接")
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
            "mode": mission.get("mode", "auto"),
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
