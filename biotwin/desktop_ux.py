"""Small native UI helpers. No medical state, network or persisted preferences."""
from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import ttk

from .desktop_theme import (
    ACCENT, AMBER, BAND_INNER, BAND_OUTER, BASELINE, BG, GREEN, GRID,
    HUD_BORDER, HUD_CARD, INK, MUTED, PANEL, PLOT, RAISED, TRACE,
)


def gradient(canvas, *, start=BG, end='#163d62', tag='gradient'):
    """A real native vector field, redrawn only when its surface changes.

    Bounded stripe count keeps resize and finite feedback cheap at any size.
    Call before drawing foreground content; this function owns only its tag.
    """
    canvas.delete(tag)
    width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
    a, b = [tuple(int(color[i:i + 2], 16) for i in (1, 3, 5)) for color in (start, end)]
    for index in range(80):
        fraction = index / 79
        color = '#' + ''.join(f'{round(x + (y - x) * fraction):02x}' for x, y in zip(a, b))
        canvas.create_rectangle(
            index * width / 80, 0, (index + 1) * width / 80 + 1, height,
            fill=color, outline='', tags=(tag,),
        )
    canvas.tag_lower(tag)


class GradientText(tk.Canvas):
    """Wrapping task text on a quiet flat surface, without an inset card.

    A tiny label-compatible interface keeps question/urgency updates synchronous.
    This surface has no animation or recurring timer.
    """

    def __init__(self, parent, **options):
        self.presentation = dict(text='', foreground=INK, font=('Segoe UI', 12), wraplength=690)
        for key in tuple(self.presentation):
            if key in options:
                self.presentation[key] = options.pop(key)
        super().__init__(parent, bg=BG, highlightthickness=0, height=40, **options)
        self.bind('<Configure>', lambda _: self.render())
        self.render()

    def configure(self, cnf=None, **options):
        if cnf is not None:
            options.update(cnf)
        for key in tuple(options):
            if key in self.presentation:
                self.presentation[key] = options.pop(key)
        if options:
            super().configure(**options)
        self.render()

    config = configure

    def cget(self, key):
        return self.presentation[key] if key in self.presentation else super().cget(key)

    def render(self):
        self.delete('all')
        available = self.winfo_width() - 16 if self.winfo_width() > 1 else self.presentation['wraplength']
        width = min(self.presentation['wraplength'], max(260, available))
        item = self.create_text(
            8, 4, text=self.presentation['text'], anchor='nw', width=width,
            fill=self.presentation['foreground'], font=self.presentation['font'],
        )
        bounds = self.bbox(item)
        super().configure(height=max(32, bounds[3] + 4 if bounds else 32))


class FiniteMotion:
    """One finite presentation transition; no evidence or state is deferred."""

    def __init__(self, root):
        self.root = root
        self.job = None
        self.draw = None

    def start(self, draw, *, reduced_motion=False):
        self.cancel()
        if reduced_motion:
            draw(1.0)
            return
        self.draw = draw
        self.started = time.monotonic()
        self.tick()

    def tick(self):
        self.job = None
        fraction = min(1.0, (time.monotonic() - self.started) / 0.24)
        self.draw(1 - (1 - fraction) ** 3)
        if fraction < 1:
            self.job = self.root.after(16, self.tick)
        else:
            self.draw = None

    def cancel(self):
        if self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None
        self.draw = None

    def close(self):
        self.cancel()
        self.root = None


class FocusHelp:
    """Identical inline help for mouse hover and keyboard focus; no timers."""

    def __init__(self, widget, variable, message):
        self.variable = variable
        self.message = message
        widget.bind('<Enter>', self.show, add='+')
        widget.bind('<FocusIn>', self.show, add='+')

    def show(self, _=None):
        self.variable.set(self.message)


def form_text_navigation(widget):
    """Text in action forms follows the same Tab order as adjacent controls."""
    def move(backwards=False):
        target=widget.tk_focusPrev() if backwards else widget.tk_focusNext()
        if target is not None: target.focus_set()
        return 'break'
    widget.bind('<Tab>',lambda _:move())
    widget.bind('<Shift-Tab>',lambda _:move(True))


class ScrollableForm(ttk.Frame):
    """Resize-safe form body; the caller keeps action buttons outside this frame.

    Focus reveals only obscured fields, so tabbing does not continually jump the
    page. Text fields keep their own wheel behavior; labels and gaps scroll the
    form. Bindings belong to this window and are removed with the form.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, takefocus=True)
        scrollbar = ttk.Scrollbar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.body = ttk.Frame(self.canvas)
        self.item = self.canvas.create_window(0, 0, window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.item, width=e.width))
        for key, amount in (('<Next>', 1), ('<Prior>', -1)):
            self.canvas.bind(key, lambda _, n=amount: (self.canvas.yview_scroll(n, 'pages'), 'break')[1])
        self.owner = self.winfo_toplevel()
        self.bindings = [(event, self.owner.bind(event, callback, add='+')) for event, callback in
                         (('<MouseWheel>', self.scroll), ('<FocusIn>', self.reveal))]
        self.bind('<Destroy>', self.cleanup, add='+')

    def contains(self, widget):
        return str(widget).startswith(str(self.body) + '.')

    def scroll(self, event):
        if (event.widget is self.canvas or self.contains(event.widget)) and not isinstance(event.widget, (tk.Text, ttk.Combobox)):
            if event.delta:
                self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
                return 'break'

    def reveal(self, event):
        if not self.contains(event.widget):
            return
        widget = event.widget
        top = widget.winfo_rooty() - self.body.winfo_rooty()
        bottom = top + widget.winfo_height()
        visible = self.canvas.canvasy(0)
        height = self.canvas.winfo_height()
        target = top if top < visible else bottom - height if bottom > visible + height else None
        if target is not None:
            self.canvas.yview_moveto(max(0, target) / max(1, self.body.winfo_height()))

    def cleanup(self, event):
        if event.widget is self:
            for sequence, binding in self.bindings:
                self.owner.unbind(sequence, binding)
            self.bindings.clear()


class Introduction:
    """Nonmodal, finite introduction using the actual projected demo facts."""

    STEPS = (
        ('Choose crew', 'Open Crew 001 or Crew 002. Each has separate observations and received messages.'),
        ('Understand the change', 'Compare a recorded measurement with its earlier comparable history. The spread is descriptive, not a health range.'),
        ('Take the next step', 'Read the received checklist, select a step and explicitly record what you completed. A checklist does not perform an action for you.'),
        ('Read Earth’s reply', 'Earth sees delivered snapshots only. With 20 simulated minutes each way, its reply can refer to an older observation.'),
    )

    def __init__(self, root, *, animate=False, reduced_motion=False, on_close=None, on_launch_demo=None, facts=None):
        self.root=root; self.on_close=on_close; self.on_launch_demo=on_launch_demo
        self.previous_focus=root.focus_get(); self.job=None; self.closed=False; self.index=0
        self.reduced_motion=reduced_motion; self.animating=False; self.facts=facts or {}
        self.window=tk.Toplevel(root); self.window.title('BioTwin Sync — quick guide')
        self.window.geometry('860x520'); self.window.minsize(560,480); self.window.configure(bg=BG)
        self.window.transient(root); self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.window.bind('<Destroy>',self.destroyed,add='+'); self.window.bind('<Escape>',lambda _:self.close())
        body=ttk.Frame(self.window,padding=24); body.pack(fill='both',expand=True)
        body.columnconfigure(0,weight=1); body.rowconfigure(1,weight=1)
        controls=ttk.Frame(body); controls.grid(row=4,column=0,sticky='ew',pady=(12,0))
        self.progress=ttk.Label(body,foreground=ACCENT,font=('Segoe UI',11,'bold')); self.progress.grid(row=0,column=0,sticky='w')
        self.canvas=tk.Canvas(body,height=160,bg=BG,highlightthickness=1,highlightbackground=HUD_BORDER)
        self.canvas.grid(row=1,column=0,sticky='nsew',pady=12); self.canvas.bind('<Configure>',lambda _:self.redraw())
        self.heading=ttk.Label(body,font=('Segoe UI',22,'bold')); self.heading.grid(row=2,column=0,sticky='ew')
        self.explanation=ttk.Label(body,wraplength=790,font=('Segoe UI',11)); self.explanation.grid(row=3,column=0,sticky='ew',pady=(6,10))
        self.window.bind('<Configure>',self.resize,add='+')
        self.skip=ttk.Button(controls,text='Close guide',command=self.close); self.skip.pack(side='left')
        if self.on_launch_demo is not None:
            self.launch_btn=ttk.Button(controls,text='Open guided demo',command=self.launch_demo)
            self.launch_btn.pack(side='left',padx=(10,0))
        self.next=ttk.Button(controls,text='Next',style='Accent.TButton',command=self.forward); self.next.pack(side='right')
        self.back=ttk.Button(controls,text='Back',command=self.backward); self.back.pack(side='right',padx=8)
        self.started=time.monotonic()
        if animate and not reduced_motion:
            self.animating=True; self.render(); self.job=self.root.after(240,self.finish_animation)
        else: self.render()
        self.next.focus_set()

    def launch_demo(self):
        callback=self.on_launch_demo; self.close()
        if callback: callback()

    def resize(self,event):
        if event.widget is self.window:
            self.explanation.configure(wraplength=max(280,event.width-60))
            self.heading.configure(wraplength=max(280,event.width-60))

    def redraw(self):
        if not self.closed: self.render()

    def render(self):
        self.progress.configure(text=f'QUICK GUIDE / SYNTHETIC EXAMPLE / {self.index+1} OF 4')
        title,explanation=self.STEPS[self.index]
        if self.index==1 and self.facts:
            explanation=self.facts['reference']
        self.heading.configure(text=title); self.explanation.configure(text=explanation)
        self.back.configure(state='normal' if self.index else 'disabled')
        self.next.configure(text='Finish' if self.index==3 else 'Next')
        c=self.canvas; c.delete('all'); w=max(300,c.winfo_width()); h=max(120,c.winfo_height())
        gradient(c,start='#050f22',end='#14375a')
        if self.index==0:
            labels=('CREW 001','CREW 002')
            for i,label in enumerate(labels):
                x=w*(.25+.5*i)
                c.create_text(x,h*.33,text=label,fill=INK,font=('Segoe UI',20,'bold'))
                c.create_text(x,h*.64,text='Separate observations',fill=MUTED,font=('Segoe UI',11))
        elif self.index==1:
            c.create_text(24,16,text='Reaction time',anchor='nw',fill=MUTED,font=('Segoe UI',11,'bold'))
            c.create_text(24,43,text=self.facts.get('changed','Recorded measurement'),anchor='nw',fill=INK,font=('Segoe UI',30,'bold'))
            c.create_text(24,100,text='Observed '+self.facts.get('observed','time unavailable'),anchor='nw',fill=TRACE,font=('Segoe UI',11))
        elif self.index==2:
            c.create_text(24,24,text='Read → Complete → Follow up',anchor='nw',fill=ACCENT,font=('Segoe UI',19,'bold'),width=w-48)
            c.create_text(24,80,text='Full received steps open in a scrolling workspace. Completion notes stay explicit.',anchor='nw',width=w-48,fill=INK,font=('Segoe UI',12))
        else:
            c.create_line(45,h*.60,w*.50,h*.12,w-45,h*.60,smooth=True,fill=ACCENT,width=3)
            c.create_text(24,h*.68,text='Crew now\n'+self.facts.get('changed','Current observation'),anchor='nw',fill=INK,font=('Segoe UI',14,'bold'))
            c.create_text(w-24,h*.68,text='Earth last received\n'+self.facts.get('initial','Delivered observation'),anchor='ne',fill=TRACE,font=('Segoe UI',14,'bold'))
            c.create_text(w/2,h*.12,text='20 min each way · simulated',fill=MUTED,font=('Segoe UI',11))

    def animate(self):
        # Hold readable content; do not auto-advance text-heavy guide screens.
        self.render()

    def finish_animation(self):
        self.animating=False; self.cancel_timer(); self.index=0; self.render()

    def forward(self):
        self.animating=False; self.cancel_timer()
        if self.index==3: self.close()
        else: self.index+=1; self.render()

    def backward(self):
        self.animating=False; self.cancel_timer(); self.index=max(0,self.index-1); self.render()

    def cancel_timer(self):
        if self.job is not None:
            self.root.after_cancel(self.job); self.job=None

    def destroyed(self,event):
        if event.widget is self.window: self.close()

    def close(self):
        if self.closed: return
        self.closed=True; self.animating=False; self.cancel_timer()
        callback=self.on_close; self.on_close=None; self.on_launch_demo=None
        try:
            self.window.destroy()
            if self.previous_focus is not None and self.previous_focus.winfo_exists(): self.previous_focus.focus_set()
        except tk.TclError: pass
        self.previous_focus=None
        if callback: callback(self)
        for name,value in tuple(vars(self).items()):
            if isinstance(value,(tk.Misc,tk.Variable)): setattr(self,name,None)
