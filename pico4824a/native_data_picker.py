"""Native host folder selection. Never accepts a folder grant from an HTTP path."""
from pathlib import Path
import json
import os
import subprocess
import sys
import threading
from .data_locations import remember_selected_directory

_PICKER_LOCK=threading.Lock()


def choose_data_directory(project):
    if not _PICKER_LOCK.acquire(blocking=False):
        raise RuntimeError('已有文件夹选择窗口打开，请先完成或取消选择')
    try:
        default=project/'data' if (project/'data').is_dir() else project
        try:
            result=subprocess.run([sys.executable,'-B','-m','pico4824a.native_data_picker',str(default)],
                cwd=project,capture_output=True,text=True,encoding='utf-8',timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except subprocess.TimeoutExpired:
            raise RuntimeError('文件夹选择已超时，请重新打开选择窗口') from None
        if result.returncode:
            raise RuntimeError('无法打开系统文件夹选择窗口。请在运行 WaveGuard 的电脑上选择；无桌面的 Linux 可在 configs/data-locations.json 添加数据目录。')
        selected=json.loads(result.stdout).get('path','')
        if not selected:return {'cancelled':True}
        folder=remember_selected_directory(selected,project)
        return {'cancelled':False,'path':str(folder)}
    finally:
        _PICKER_LOCK.release()


if __name__=='__main__':
    import tkinter as tk
    from tkinter import filedialog
    root=tk.Tk();root.withdraw();root.attributes('-topmost',True)
    try:
        selected=filedialog.askdirectory(parent=root,title='选择要读取的数据文件夹（可选择 U 盘）',
            initialdir=sys.argv[1],mustexist=True)
        sys.stdout.reconfigure(encoding='utf-8')
        print(json.dumps({'path':selected},ensure_ascii=False))
    finally:root.destroy()
