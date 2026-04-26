import sqlite3
import urllib.request
import uvicorn
import os
import uuid
import shutil
import sys
import threading
import time
import webview
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

# --- 1. 环境与绝对路径初始化 ---
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

DB_FILE = os.path.join(BASE_DIR, "todo_calendar.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT,
                all_day BOOLEAN DEFAULT 1,
                is_completed BOOLEAN DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS eval_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module TEXT NOT NULL,
                sub_module TEXT NOT NULL,
                title TEXT NOT NULL,
                score REAL NOT NULL,
                record_date TEXT,          
                proof_path TEXT
            )
        ''')

        cursor.execute("PRAGMA table_info(eval_records)")
        eval_columns = [info[1] for info in cursor.fetchall()]
        if "record_date" not in eval_columns:
            cursor.execute("ALTER TABLE eval_records ADD COLUMN record_date TEXT")

        cursor.execute("PRAGMA table_info(tasks)")
        task_columns = [info[1] for info in cursor.fetchall()]
        if "color_hex" not in task_columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN color_hex TEXT DEFAULT '#4F46E5'")

        conn.commit()


init_db()

app = FastAPI(title="SDU 软院效率中枢 桌面版")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# --- 2. 日历 API ---
class TaskCreate(BaseModel):
    title: str
    start_date: str
    end_date: Optional[str] = None
    all_day: bool = True
    color_hex: str = "#4F46E5"


class TaskTimeUpdate(BaseModel):
    start_date: str
    end_date: Optional[str] = None
    all_day: bool = True


class TaskEditUpdate(BaseModel):
    title: str
    color_hex: str


@app.get("/api/tasks")
def get_tasks():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks")
        rows = cursor.fetchall()
        events = []
        for row in rows:
            color = "#10B981" if row["is_completed"] else (row["color_hex"] or "#4F46E5")
            events.append({
                "id": str(row["id"]),
                "title": row["title"],
                "start": row["start_date"],
                "end": row["end_date"],
                "allDay": bool(row["all_day"]),
                "backgroundColor": color,
                "borderColor": color,
                "extendedProps": {
                    "is_completed": bool(row["is_completed"]),
                    "raw_color": row["color_hex"] or "#4F46E5"
                }
            })
        return events


@app.post("/api/tasks")
def create_task(task: TaskCreate):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO tasks (title, start_date, end_date, all_day, color_hex) VALUES (?, ?, ?, ?, ?)",
                       (task.title, task.start_date, task.end_date, task.all_day, task.color_hex))
        conn.commit()
        return {"id": cursor.lastrowid}


@app.put("/api/tasks/{task_id}")
def update_task_time(task_id: int, task: TaskTimeUpdate):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET start_date=?, end_date=?, all_day=? WHERE id=?",
                       (task.start_date, task.end_date, task.all_day, task_id))
        conn.commit()
        return {"status": "success"}


@app.patch("/api/tasks/{task_id}/edit")
def update_task_details(task_id: int, payload: TaskEditUpdate):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET title=?, color_hex=? WHERE id=?", (payload.title, payload.color_hex, task_id))
        conn.commit()
        return {"status": "success"}


@app.patch("/api/tasks/{task_id}/toggle")
def toggle_task(task_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET is_completed = NOT is_completed WHERE id=?", (task_id,))
        conn.commit()
        return {"status": "success"}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        conn.commit()
        return {"status": "success"}


# --- 3. 综测 API ---
@app.post("/api/eval")
async def create_eval_record(
        module: str = Form(...),
        sub_module: str = Form(...),
        title: str = Form(...),
        score: float = Form(...),
        record_date: str = Form(...),
        file: Optional[UploadFile] = File(None)
):
    proof_path = ""
    if file and file.filename:
        ext = os.path.splitext(file.filename)[1]
        unique_filename = f"{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        proof_path = f"/uploads/{unique_filename}"

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO eval_records (module, sub_module, title, score, record_date, proof_path) VALUES (?, ?, ?, ?, ?, ?)",
            (module, sub_module, title, score, record_date, proof_path)
        )
        conn.commit()
    return {"status": "success"}


@app.put("/api/eval/{record_id}")
async def update_eval_record(
        record_id: int,
        module: str = Form(...),
        sub_module: str = Form(...),
        title: str = Form(...),
        score: float = Form(...),
        record_date: str = Form(...),
        file: Optional[UploadFile] = File(None)
):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        if file and file.filename:
            cursor.execute("SELECT proof_path FROM eval_records WHERE id=?", (record_id,))
            old = cursor.fetchone()
            if old and old[0] and os.path.exists(os.path.join(BASE_DIR, old[0].lstrip("/uploads/"))):
                try:
                    os.remove(os.path.join(BASE_DIR, old[0].lstrip("/uploads/")))
                except:
                    pass

            ext = os.path.splitext(file.filename)[1]
            unique_filename = f"{uuid.uuid4().hex}{ext}"
            file_path = os.path.join(UPLOAD_DIR, unique_filename)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            proof_path = f"/uploads/{unique_filename}"
            cursor.execute(
                "UPDATE eval_records SET module=?, sub_module=?, title=?, score=?, record_date=?, proof_path=? WHERE id=?",
                (module, sub_module, title, score, record_date, proof_path, record_id))
        else:
            cursor.execute("UPDATE eval_records SET module=?, sub_module=?, title=?, score=?, record_date=? WHERE id=?",
                           (module, sub_module, title, score, record_date, record_id))
        conn.commit()
    return {"status": "success"}


@app.get("/api/eval")
def get_eval_records():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM eval_records ORDER BY record_date DESC, id DESC")
        return [dict(row) for row in cursor.fetchall()]


@app.delete("/api/eval/{record_id}")
def delete_eval_record(record_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT proof_path FROM eval_records WHERE id=?", (record_id,))
        row = cursor.fetchone()
        if row and row[0]:
            target_file = os.path.join(BASE_DIR, row[0].lstrip("/uploads/"))
            if os.path.exists(target_file):
                try:
                    os.remove(target_file)
                except:
                    pass
        cursor.execute("DELETE FROM eval_records WHERE id=?", (record_id,))
        conn.commit()
    return {"status": "success"}


# --- 4. 前端界面 HTML ---
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>SDU 软院效率中枢</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.11/index.global.min.js"></script>
    <style>
        .fc-theme-standard .fc-scrollgrid { border-color: #e5e7eb; }
        .fc-col-header-cell { background-color: #f9fafb; padding: 8px 0; font-weight: 600; text-align: center; }
        .fc-event { cursor: pointer; border-radius: 4px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); height: auto !important; margin-bottom: 2px !important;}
        .fc-event-title { white-space: normal !important; word-break: break-word !important; line-height: 1.5 !important; padding: 4px 6px !important; font-size: 0.9rem;}
        .tab-active { border-bottom: 3px solid #1E40AF; color: #1E3A8A; font-weight: 800; }
        .tab-inactive { color: #6B7280; }
        .tag-jichu { background-color: #DBEAFE; color: #1E40AF; border: 1px solid #BFDBFE; }
        .tag-chengguo { background-color: #FEF3C7; color: #92400E; border: 1px solid #FDE68A; }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 10px; }
        body { user-select: none; }
        input, textarea { user-select: text; }
        #task-modal, #custom-confirm-modal { transition: opacity 0.2s ease-in-out; }

        #startup-loader { position: fixed; inset: 0; background: #f8fafc; z-index: 9999; display: flex; flex-direction: column; align-items: center; justify-content: center; transition: opacity 0.4s ease-out; }
        .progress-container { width: 60%; max-width: 280px; height: 6px; background: #e2e8f0; border-radius: 8px; margin-top: 24px; overflow: hidden; box-shadow: inset 0 1px 2px rgba(0,0,0,0.05); }
        .progress-bar { height: 100%; width: 0%; background: linear-gradient(90deg, #4F46E5, #8B5CF6); transition: width 0.4s ease-out; border-radius: 8px; }
        #loader-text { margin-top: 12px; font-size: 0.8rem; color: #64748b; font-weight: 600; letter-spacing: 0.05em; transition: opacity 0.2s; }
    </style>
</head>
<body class="bg-slate-50 text-slate-900 h-screen flex flex-col font-sans">

    <div id="startup-loader">
        <div class="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-indigo-100 mb-4 shadow-inner">
            <svg class="w-8 h-8 text-indigo-600 animate-pulse" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
        </div>
        <h2 class="text-xl font-black text-slate-800 tracking-tight">SDU 效率中枢</h2>
        <div class="progress-container">
            <div id="progress-bar" class="progress-bar"></div>
        </div>
        <span id="loader-text">准备启动引擎...</span>
    </div>

    <header class="bg-white border-b shadow-sm sticky top-0 z-40" style="-webkit-app-region: drag;">
        <div class="max-w-7xl mx-auto px-6 py-4 flex flex-col items-center">
            <h1 class="text-3xl font-black text-slate-800 tracking-tighter mb-4">SDU 软院效率平台</h1>
            <div class="flex space-x-12 text-lg" style="-webkit-app-region: no-drag;">
                <button id="tab-cal" class="pb-2 px-4 tab-active transition-all" onclick="switchTab('cal')">📅 日程对齐</button>
                <button id="tab-eval" class="pb-2 px-4 tab-inactive transition-all" onclick="switchTab('eval')">🏆 综测备忘</button>
            </div>
        </div>
    </header>

    <main class="flex-1 overflow-hidden relative max-w-7xl mx-auto w-full">
        <div id="view-cal" class="h-full w-full p-6 absolute inset-0">
            <div class="bg-white rounded-2xl shadow-sm border p-6 h-full" id="calendar"></div>
        </div>

        <div id="view-eval" class="h-full w-full p-6 absolute inset-0 hidden overflow-y-auto">
            <div class="bg-indigo-900 text-white rounded-2xl p-8 mb-8 flex flex-col items-center shadow-xl relative overflow-hidden">
                <div class="absolute inset-0 bg-gradient-to-r from-blue-600/20 to-purple-600/20 z-0"></div>
                <span class="text-indigo-200 text-sm font-bold uppercase tracking-widest mb-2 z-10">本学年素质能力总分</span>
                <div class="flex items-baseline space-x-2 z-10">
                    <div class="text-7xl font-black" id="total-score">0.000</div>
                    <div class="text-3xl font-bold text-indigo-300">/ 100</div>
                </div>
                <div class="mt-6 flex space-x-4 z-10">
                    <button onclick="openEvalModal()" class="bg-white text-indigo-900 px-8 py-2.5 rounded-full font-bold hover:bg-indigo-50 transition shadow-lg">+ 添加全局条目</button>
                </div>
            </div>
            <div id="modules-grid" class="space-y-8 pb-12"></div>
        </div>
    </main>

    <div id="task-modal" class="fixed inset-0 bg-slate-900/60 backdrop-blur-sm hidden flex justify-center items-center z-40">
        <div class="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-6">
            <h3 id="task-modal-title" class="text-xl font-bold mb-4 text-slate-800 border-b pb-2">日程信息</h3>
            <input type="hidden" id="t-id"><input type="hidden" id="t-start"><input type="hidden" id="t-end"><input type="hidden" id="t-allday">

            <div class="mb-4">
                <label class="block text-xs font-bold text-slate-500 uppercase mb-1">待办内容</label>
                <textarea id="t-title" rows="3" class="w-full border-2 rounded-xl p-3 bg-slate-50 focus:border-indigo-500 outline-none resize-none"></textarea>
            </div>

            <div class="mb-6 bg-slate-50 p-4 rounded-xl border border-slate-200 shadow-inner">
                <div class="flex justify-between items-center mb-4">
                    <label class="text-sm font-bold text-slate-700">颜色标记</label>
                    <div class="flex items-center space-x-2">
                        <div id="color-preview" class="w-7 h-7 rounded border border-slate-300 shadow-sm transition-colors duration-200"></div>
                        <input type="text" id="t-color-hex" value="#4F46E5" class="w-24 text-sm font-mono border-2 border-slate-200 rounded-lg px-2 py-1 outline-none focus:border-indigo-500 uppercase transition-colors" maxlength="7" placeholder="#HEX">
                    </div>
                </div>

                <div class="mb-3">
                    <label class="block text-[11px] font-bold text-slate-400 uppercase mb-2">预设主题色</label>
                    <div id="preset-colors-container" class="flex flex-wrap gap-2"></div>
                </div>

                <div>
                    <label class="block text-[11px] font-bold text-slate-400 uppercase mb-2">最近使用</label>
                    <div id="recent-colors-container" class="flex flex-wrap gap-2"></div>
                </div>
            </div>

            <div class="flex flex-col space-y-2">
                <button onclick="saveTask()" class="w-full bg-indigo-600 text-white py-3 rounded-xl font-bold shadow-md hover:bg-indigo-700">保存日程</button>
                <div id="task-actions" class="hidden grid-cols-2 gap-2 mt-2">
                    <button onclick="toggleTask()" id="btn-toggle" class="bg-emerald-100 text-emerald-800 py-2 rounded-xl font-bold">标记完成</button>
                    <button onclick="deleteTask()" class="bg-rose-100 text-rose-800 py-2 rounded-xl font-bold">删除</button>
                </div>
                <button onclick="closeTaskModal()" class="w-full py-2 font-bold text-slate-400">取消</button>
            </div>
        </div>
    </div>

    <div id="custom-confirm-modal" class="fixed inset-0 bg-slate-900/70 backdrop-blur-md hidden flex justify-center items-center z-[60] opacity-0">
        <div class="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-8 text-center transform scale-95 transition-transform duration-200" id="confirm-modal-content">
            <div class="inline-flex items-center justify-center w-16 h-16 rounded-full bg-rose-100 mb-4">
                <svg class="w-8 h-8 text-rose-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path></svg>
            </div>
            <h3 class="text-2xl font-black text-slate-800 mb-2">确认彻底删除？</h3>
            <p class="text-sm text-slate-500 mb-8">此操作无法恢复，相关数据将从数据库中永久移除。</p>
            <div class="flex space-x-4">
                <button onclick="closeConfirmModal()" class="flex-1 py-3 bg-slate-100 text-slate-700 rounded-xl font-bold hover:bg-slate-200 transition">取消</button>
                <button id="btn-do-delete" class="flex-1 py-3 bg-rose-600 text-white rounded-xl font-bold shadow-lg hover:bg-rose-700 transition hover:-translate-y-0.5">确认删除</button>
            </div>
        </div>
    </div>

    <div id="eval-modal" class="fixed inset-0 bg-slate-900/60 backdrop-blur-sm hidden flex justify-center items-center z-50">
        <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md p-6">
            <h3 id="eval-modal-title" class="text-xl font-bold mb-4 text-slate-800 border-b pb-2">新增加分项</h3>
            <form id="eval-form" onsubmit="handleEvalSubmit(event)">
                <input type="hidden" id="f-id">
                <div class="grid grid-cols-2 gap-4 mb-4">
                    <div>
                        <label class="block text-xs font-bold text-slate-500 mb-1">所属模块</label>
                        <select id="f-module" class="w-full border-2 rounded-xl p-2 bg-slate-50 focus:border-indigo-500 outline-none" required>
                            <option value="身心">身心素养</option>
                            <option value="文艺">文艺素养</option>
                            <option value="劳动">劳动素养</option>
                            <option value="创新">创新素养</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-500 mb-1">评价类型</label>
                        <select id="f-sub" class="w-full border-2 rounded-xl p-2 bg-slate-50 focus:border-indigo-500 outline-none" required>
                            <option value="基础">基础性评价</option>
                            <option value="成果">成果性评价</option>
                            <option value="突破">突破性评价(仅创新)</option>
                        </select>
                    </div>
                </div>
                <div class="mb-4">
                    <label class="block text-xs font-bold text-slate-500 mb-1">项目名称</label>
                    <input type="text" id="f-title" class="w-full border-2 rounded-xl p-2 bg-slate-50 focus:border-indigo-500 outline-none" required>
                </div>
                <div class="grid grid-cols-2 gap-4 mb-4">
                    <div>
                        <label class="block text-xs font-bold text-slate-500 mb-1">加分分值</label>
                        <input type="number" id="f-score" step="0.001" class="w-full border-2 rounded-xl p-2 bg-slate-50 focus:border-indigo-500 outline-none" required>
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-500 mb-1">记录日期</label>
                        <input type="date" id="f-date" class="w-full border-2 rounded-xl p-2 bg-slate-50 focus:border-indigo-500 outline-none" required>
                    </div>
                </div>
                <div class="mb-6">
                    <label class="block text-xs font-bold text-slate-500 mb-1">证明材料 (可选图片)</label>
                    <input type="file" id="f-file" accept="image/*" class="w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-bold file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100">
                </div>
                <div class="flex flex-col space-y-2">
                    <button type="submit" class="w-full bg-indigo-600 text-white py-3 rounded-xl font-bold shadow-md hover:bg-indigo-700">保存记录</button>
                    <button type="button" onclick="closeEvalModal()" class="w-full py-2 font-bold text-slate-400">取消</button>
                </div>
            </form>
        </div>
    </div>

    <div id="img-preview" class="fixed inset-0 bg-black/90 hidden z-[70] flex items-center justify-center p-8" onclick="this.classList.add('hidden')">
        <img id="preview-src" src="" class="max-w-full max-h-full rounded-lg shadow-2xl">
    </div>

    <script>
        function updateProgress(percent, text) {
            const bar = document.getElementById('progress-bar');
            const label = document.getElementById('loader-text');
            if (bar) bar.style.width = percent + '%';
            if (label && text) label.innerText = text;
        }

        updateProgress(10, "正在连接前端框架...");

        let calendar;
        const MODS = {
            "身心": { max: 15, limits: {"基础":9, "成果":6} }, "文艺": { max: 15, limits: {"基础":9, "成果":6} },
            "劳动": { max: 25, limits: {"基础":15, "成果":10} }, "创新": { max: 45, limits: {"基础":5, "突破":40} }
        };

        const MAX_RECENT_COLORS = 10;
        const DEFAULT_COLORS = ['#4F46E5', '#E11D48', '#059669', '#D97706', '#7C3AED'];
        const PRESET_COLORS = ['#EF4444', '#F97316', '#F59E0B', '#10B981', '#06B6D4', '#3B82F6', '#6366F1', '#8B5CF6', '#D946EF', '#F43F5E'];

        // === 全新自定义色彩同步逻辑 ===
        function syncColorUI(hex) {
            document.getElementById('color-preview').style.backgroundColor = hex;
            document.getElementById('t-color-hex').value = hex.toUpperCase();
        }

        document.getElementById('t-color-hex').addEventListener('input', function(e) {
            let val = e.target.value.trim();
            if (/^#[0-9A-F]{6}$/i.test(val) || /^#[0-9A-F]{3}$/i.test(val)) {
                document.getElementById('color-preview').style.backgroundColor = val;
            }
        });

        function renderPresetColors() {
            const container = document.getElementById('preset-colors-container');
            container.innerHTML = '';
            PRESET_COLORS.forEach(color => {
                const btn = document.createElement('div');
                btn.className = 'w-5 h-5 rounded-full cursor-pointer hover:scale-125 transition-transform shadow-sm border border-slate-200/50';
                btn.style.backgroundColor = color;
                btn.onclick = () => syncColorUI(color);
                container.appendChild(btn);
            });
        }

        function loadRecentColors() {
            renderPresetColors();
            let colors = JSON.parse(localStorage.getItem('SDU_recent_colors'));
            if (!colors || colors.length === 0) {
                colors = DEFAULT_COLORS;
                localStorage.setItem('SDU_recent_colors', JSON.stringify(colors));
            }
            renderRecentColors(colors);
        }

        function renderRecentColors(colors) {
            const container = document.getElementById('recent-colors-container');
            container.innerHTML = '';
            colors.forEach(color => {
                const btn = document.createElement('div');
                btn.className = 'w-5 h-5 rounded-full cursor-pointer hover:scale-125 transition-transform shadow-sm border border-slate-200/50';
                btn.style.backgroundColor = color;
                btn.onclick = () => syncColorUI(color);
                container.appendChild(btn);
            });
        }

        function saveRecentColor(newColor) {
            let colors = JSON.parse(localStorage.getItem('SDU_recent_colors')) || DEFAULT_COLORS;
            colors = colors.filter(c => c.toLowerCase() !== newColor.toLowerCase());
            colors.unshift(newColor);
            if (colors.length > MAX_RECENT_COLORS) colors = colors.slice(0, MAX_RECENT_COLORS);
            localStorage.setItem('SDU_recent_colors', JSON.stringify(colors));
            renderRecentColors(colors);
        }

        let pendingDeleteAction = null;
        function openConfirmModal(actionCallback) {
            pendingDeleteAction = actionCallback;
            const modal = document.getElementById('custom-confirm-modal');
            const content = document.getElementById('confirm-modal-content');
            modal.classList.remove('hidden');
            void modal.offsetWidth; 
            modal.classList.remove('opacity-0');
            content.classList.remove('scale-95');
        }
        function closeConfirmModal() {
            const modal = document.getElementById('custom-confirm-modal');
            const content = document.getElementById('confirm-modal-content');
            modal.classList.add('opacity-0');
            content.classList.add('scale-95');
            setTimeout(() => { modal.classList.add('hidden'); pendingDeleteAction = null; }, 200); 
        }
        document.getElementById('btn-do-delete').addEventListener('click', async () => {
            if (pendingDeleteAction) { await pendingDeleteAction(); closeConfirmModal(); }
        });

        function switchTab(t) {
            const isCal = t === 'cal';
            document.getElementById('view-cal').classList.toggle('hidden', !isCal);
            document.getElementById('view-eval').classList.toggle('hidden', isCal);
            document.getElementById('tab-cal').className = isCal ? "pb-2 px-4 tab-active transition-all" : "pb-2 px-4 tab-inactive transition-all";
            document.getElementById('tab-eval').className = !isCal ? "pb-2 px-4 tab-active transition-all" : "pb-2 px-4 tab-inactive transition-all";
            if(isCal) { if(calendar) calendar.render(); } else { loadEval(); }
        }

        function closeTaskModal() { document.getElementById('task-modal').classList.add('hidden'); }

        async function saveTask() {
            const id = document.getElementById('t-id').value;
            const title = document.getElementById('t-title').value.trim();
            const color = document.getElementById('t-color-hex').value; 
            if(!title) return alert("待办内容不能为空！");

            saveRecentColor(color);

            if(id) {
                // 编辑逻辑：直接更新对象的属性，不引发重绘
                await fetch(`/api/tasks/${id}/edit`, {
                    method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title: title, color_hex: color})
                });
                let ev = calendar.getEventById(id);
                if (ev) {
                    ev.setProp('title', title);
                    ev.setExtendedProp('raw_color', color);
                    let isCompleted = ev.extendedProps.is_completed;
                    ev.setProp('backgroundColor', isCompleted ? "#10B981" : color);
                    ev.setProp('borderColor', isCompleted ? "#10B981" : color);
                }
            } else {
                // 新增逻辑
                const start = document.getElementById('t-start').value;
                const end = document.getElementById('t-end').value;
                const allday = document.getElementById('t-allday').value === 'true';
                
                let res = await fetch('/api/tasks', { 
                    method: 'POST', headers: {'Content-Type':'application/json'}, 
                    body: JSON.stringify({title: title, start_date: start, end_date: end || null, all_day: allday, color_hex: color})
                });
                let data = await res.json();

                // 🔥 终极解法：获取当前的后端数据源，并将新事件挂载到该源上
                const currentEventSource = calendar.getEventSources()[0];
                
                calendar.addEvent({
                    id: String(data.id), // ID 转为字符串，保证与后端接口拉取的数据类型绝对一致
                    title: title,
                    start: start,
                    end: end ? end : null,
                    allDay: allday,
                    backgroundColor: color,
                    borderColor: color,
                    extendedProps: {
                        is_completed: false,
                        raw_color: color
                    }
                }, currentEventSource); // 关键动作：将事件归属给这个源
            }
            closeTaskModal();
        }

        async function toggleTask() {
            const id = document.getElementById('t-id').value;
            await fetch(`/api/tasks/${id}/toggle`, {method:'PATCH'});
            let ev = calendar.getEventById(id);
            if (ev) {
                let isComp = !ev.extendedProps.is_completed;
                ev.setExtendedProp('is_completed', isComp);
                let rawColor = ev.extendedProps.raw_color;
                ev.setProp('backgroundColor', isComp ? "#10B981" : rawColor);
                ev.setProp('borderColor', isComp ? "#10B981" : rawColor);
            }
            closeTaskModal();
        }

        function deleteTask() {
            const id = document.getElementById('t-id').value;
            openConfirmModal(async () => {
                await fetch(`/api/tasks/${id}`, {method:'DELETE'});
                let ev = calendar.getEventById(id);
                if (ev) ev.remove();
                closeTaskModal();
            });
        }

        document.addEventListener('DOMContentLoaded', function() {
            updateProgress(35, "加载本地配置数据...");
            loadRecentColors();
            let isInitialLoad = true;

            const safetyTimer = setTimeout(() => {
                const loader = document.getElementById('startup-loader');
                if (loader && !loader.classList.contains('hidden')) {
                    updateProgress(100, "加载超时，跳过检查强行进入工作区...");
                    loader.style.opacity = '0';
                    setTimeout(() => loader.remove(), 400);
                }
            }, 7000);

            updateProgress(60, "初始化日历视图组件...");

            calendar = new FullCalendar.Calendar(document.getElementById('calendar'), {
                initialView: 'dayGridMonth',
                eventOrder: "backgroundColor,title",
                headerToolbar: { left: 'prev,next today', center: 'title', right: 'dayGridMonth,timeGridWeek' },
                events: '/api/tasks', editable: true, selectable: true,

                loading: function(isLoading) {
                    if (isLoading) {
                        updateProgress(85, "正在同步本地数据库记录...");
                    } else if (isInitialLoad) {
                        isInitialLoad = false;
                        updateProgress(100, "环境就绪！");
                        clearTimeout(safetyTimer); 
                        setTimeout(() => {
                            const loader = document.getElementById('startup-loader');
                            if (loader) {
                                loader.style.opacity = '0';
                                setTimeout(() => loader.remove(), 400);
                            }
                        }, 250);
                    }
                },
                select: function(info) {
                    document.getElementById('t-id').value = ''; document.getElementById('t-title').value = '';
                    syncColorUI('#4F46E5'); // 重置颜色UI
                    document.getElementById('t-start').value = info.startStr; document.getElementById('t-end').value = info.endStr;
                    document.getElementById('t-allday').value = info.allDay;
                    document.getElementById('task-modal-title').innerText = '新建日程';
                    document.getElementById('task-actions').classList.add('hidden'); document.getElementById('task-actions').classList.remove('grid');
                    document.getElementById('task-modal').classList.remove('hidden');
                },
                eventDrop: async (info) => {
                    await fetch(`/api/tasks/${info.event.id}`, {
                        method: 'PUT', headers: {'Content-Type':'application/json'},
                        body: JSON.stringify({start_date:info.event.startStr, end_date:info.event.endStr||null, all_day:info.event.allDay})
                    });
                },
                eventResize: async (info) => {
                    await fetch(`/api/tasks/${info.event.id}`, {
                        method: 'PUT', headers: {'Content-Type':'application/json'},
                        body: JSON.stringify({start_date:info.event.startStr, end_date:info.event.endStr||null, all_day:info.event.allDay})
                    });
                },
                eventClick: function(info) {
                    document.getElementById('t-id').value = info.event.id;
                    document.getElementById('t-title').value = info.event.title;
                    syncColorUI(info.event.extendedProps.raw_color); // 同步颜色UI
                    document.getElementById('task-modal-title').innerText = '编辑日程';
                    const btnToggle = document.getElementById('btn-toggle');
                    if(info.event.extendedProps.is_completed) {
                        btnToggle.innerText = "恢复为未完成"; btnToggle.className = "bg-amber-100 text-amber-800 py-2 rounded-xl font-bold";
                    } else {
                        btnToggle.innerText = "标记为完成"; btnToggle.className = "bg-emerald-100 text-emerald-800 py-2 rounded-xl font-bold";
                    }
                    document.getElementById('task-actions').classList.remove('hidden'); document.getElementById('task-actions').classList.add('grid');
                    document.getElementById('task-modal').classList.remove('hidden');
                }
            });
            calendar.render();
        });

        // 综测逻辑部分
        function closeEvalModal() { 
            const viewContainer = document.getElementById('view-eval');
            const st = viewContainer.scrollTop;
            document.getElementById('eval-modal').classList.add('hidden'); 
            viewContainer.scrollTop = st;
        }

        async function loadEval() {
            const viewContainer = document.getElementById('view-eval');
            const currentScrollTop = viewContainer.scrollTop; 

            const res = await fetch('/api/eval'); 
            const records = await res.json();
            const grid = document.getElementById('modules-grid'); 

            let htmlBuffer = '';
            let total = 0;

            Object.entries(MODS).forEach(([name, cfg]) => {
                const recs = records.filter(r => r.module === name);
                let leftSum = recs.filter(r => r.sub_module === '基础').reduce((s,r)=>s+r.score, 0);
                let rightVal = 0;
                if(name === '创新') rightVal = recs.filter(r => r.sub_module === '突破').reduce((s,r)=>s+r.score, 0);
                else rightVal = recs.filter(r => r.sub_module === '成果').reduce((s,r)=>s+r.score, 0);

                const leftFinal = Math.min(leftSum, cfg.limits["基础"]);
                const rightFinal = Math.min(rightVal, cfg.limits[name==='创新'?'突破':'成果']);
                const modScore = leftFinal + rightFinal; total += modScore;

                htmlBuffer += `
                <div class="bg-white rounded-3xl border shadow-sm overflow-hidden">
                    <div class="bg-slate-50 px-8 py-5 flex justify-between items-center border-b">
                        <div class="flex items-center space-x-4">
                            <h4 class="text-xl font-bold text-slate-800">${name}素养</h4>
                            <button onclick="openEvalModal('${name}')" class="text-xs bg-indigo-100 text-indigo-700 px-3 py-1.5 rounded-full font-bold">+ 添加活动</button>
                        </div>
                        <div class="flex items-baseline space-x-1">
                            <div class="text-3xl font-black text-indigo-600 tracking-tight">${modScore.toFixed(3)}</div>
                            <div class="text-lg font-bold text-slate-400">/ ${cfg.max}</div>
                        </div>
                    </div>
                    <div class="grid grid-cols-2 divide-x">
                        <div class="p-6">
                            <div class="flex justify-between items-center mb-4">
                                <span class="px-3 py-1 rounded-full text-xs font-bold tag-jichu">基础性评价</span>
                                <span class="text-xs font-bold text-slate-400">小计: ${leftFinal.toFixed(3)} / ${cfg.limits["基础"]}</span>
                            </div>
                            <div class="space-y-3">${renderEvalItems(recs.filter(r=>r.sub_module==='基础'))}</div>
                        </div>
                        <div class="p-6">
                            <div class="flex justify-between items-center mb-4">
                                <span class="px-3 py-1 rounded-full text-xs font-bold tag-chengguo">${name==='创新'?'突破性评价':'成果性评价'}</span>
                                <span class="text-xs font-bold text-slate-400">小计: ${rightFinal.toFixed(3)} / ${cfg.limits[name==='创新'?'突破':'成果']}</span>
                            </div>
                            <div class="space-y-3">${renderEvalItems(recs.filter(r=>r.sub_module!=='基础'))}</div>
                        </div>
                    </div>
                </div>`;
            });

            grid.innerHTML = htmlBuffer;
            document.getElementById('total-score').innerText = Math.min(total, 100).toFixed(3);

            let count = 0;
            const scrollLock = setInterval(() => {
                viewContainer.scrollTop = currentScrollTop;
                count++;
                if (count > 5) clearInterval(scrollLock); 
            }, 20);
        }

        function renderEvalItems(items) {
            if(!items.length) return `<div class="text-center py-8 text-slate-300 text-sm italic">暂无记录</div>`;
            return items.map(i => `
                <div class="group bg-slate-50 p-3 rounded-xl border border-transparent hover:border-indigo-200 transition relative">
                    <div class="flex justify-between items-start mb-1">
                        <div class="font-bold text-sm text-slate-800">${i.title}</div>
                        <div class="text-indigo-600 font-mono text-xs font-bold">+${i.score.toFixed(3)}</div>
                    </div>
                    <div class="flex justify-between items-center mt-2">
                        <span class="text-[11px] text-slate-400 font-medium">${i.record_date || '未记录'}</span>
                        <div class="flex space-x-3 opacity-0 group-hover:opacity-100 transition">
                            ${i.proof_path ? `<button onclick="viewImg('${i.proof_path}')" class="text-indigo-500 hover:text-indigo-700 font-bold text-[11px]">证明</button>` : ''}
                            <button onclick='editEvalRecord(${JSON.stringify(i)})' class="text-slate-500 hover:text-slate-800 font-bold text-[11px]">编辑</button>
                            <button onclick="delEvalRecord(${i.id})" class="text-rose-500 hover:text-rose-700 font-bold text-[11px]">删除</button>
                        </div>
                    </div>
                </div>`).join('');
        }

        function openEvalModal(defaultModule = null) {
            document.getElementById('eval-form').reset(); document.getElementById('f-id').value = '';
            document.getElementById('f-date').value = new Date().toISOString().split('T')[0];
            if(defaultModule) document.getElementById('f-module').value = defaultModule;
            document.getElementById('eval-modal-title').innerText = '新增加分项';
            document.getElementById('eval-modal').classList.remove('hidden');
        }

        function editEvalRecord(item) {
            document.getElementById('f-id').value = item.id; document.getElementById('f-module').value = item.module;
            document.getElementById('f-sub').value = item.sub_module; document.getElementById('f-title').value = item.title;
            document.getElementById('f-score').value = item.score; document.getElementById('f-date').value = item.record_date || '';
            document.getElementById('eval-modal-title').innerText = '编辑加分项'; document.getElementById('eval-modal').classList.remove('hidden');
        }

        async function handleEvalSubmit(e) {
            e.preventDefault(); const id = document.getElementById('f-id').value;
            const formData = new FormData();
            formData.append('module', document.getElementById('f-module').value); formData.append('sub_module', document.getElementById('f-sub').value);
            formData.append('title', document.getElementById('f-title').value); formData.append('score', document.getElementById('f-score').value);
            formData.append('record_date', document.getElementById('f-date').value);
            if(document.getElementById('f-file').files[0]) formData.append('file', document.getElementById('f-file').files[0]);
            await fetch(id ? `/api/eval/${id}` : '/api/eval', { method: id ? 'PUT' : 'POST', body: formData });
            closeEvalModal(); loadEval();
        }

        function delEvalRecord(id) {
            openConfirmModal(async () => { await fetch(`/api/eval/${id}`, { method: 'DELETE' }); loadEval(); });
        }

        function viewImg(src) { document.getElementById('preview-src').src = src; document.getElementById('img-preview').classList.remove('hidden'); }
    </script>
</body>
</html>
"""


@app.get("/")
def read_root():
    return HTMLResponse(content=HTML_CONTENT)


def start_server():
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="error")


def wait_for_server(url='http://127.0.0.1:8000', timeout=5.0):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            urllib.request.urlopen(url, timeout=0.2)
            return True
        except Exception:
            time.sleep(0.1)
    return False


if __name__ == "__main__":
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    if wait_for_server('http://127.0.0.1:8000'):
        webview.create_window(
            title='SDU 软院效率中枢',
            url='http://127.0.0.1:8000',
            width=1280,
            height=850,
            min_size=(1024, 700)
        )
        webview.start()
    else:
        print("错误: 无法在 5 秒内启动本地服务。")
