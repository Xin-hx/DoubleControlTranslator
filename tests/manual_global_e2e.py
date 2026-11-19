"""交互式原生 E2E：真实物理双 Ctrl 才能通过（注入事件按安全要求会被忽略）。"""
import queue, sys, tkinter as tk
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from double_ctrl_translator.hook import KeyboardHook

root=tk.Tk(); root.title("DoubleCtrlTranslator 原生全局键 E2E")
text=tk.Text(root,width=55,height=8); text.pack(padx=20,pady=12)
text.insert("1.0","Select this English sentence, then press and release Ctrl twice quickly.")
text.tag_add("sel","1.0","1.end"); text.focus_force()
label=tk.Label(root,text="等待真实物理双 Ctrl（自动注入不会通过）"); label.pack(pady=8)
events=queue.Queue(maxsize=1); hook=KeyboardHook(350,events); hook.start()
def poll():
    try: x,y,target=events.get_nowait(); label.config(text=f"PASS：捕获触发位置 ({x}, {y})，窗口 {target.hwnd}")
    except queue.Empty: root.after(50,poll)
root.after(50,poll)
try: root.mainloop()
finally: hook.stop()
