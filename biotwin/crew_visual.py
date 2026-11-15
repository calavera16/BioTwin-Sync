"""Neutral navigation artwork and truthful communication geometry; no evidence interpretation."""
from __future__ import annotations

import hashlib
import json
import tkinter as tk

from .desktop_assets import asset_directory
from .desktop_theme import ACCENT, GRID, MUTED, TRACE


class CrewImages:
    """A bounded cache owned by one Tk interpreter, released on its UI thread."""
    def __init__(self, master, directory=None):
        self.master = master
        self.images = {}
        self.encoded = {}
        self.failed = set()
        self.error = None
        try:
            path = (directory or asset_directory()) / 'crew-visuals-v1.json'
            if path.is_symlink() or path.stat().st_size > 1_000_000:
                raise ValueError('Invalid navigation artwork file.')
            document = json.loads(path.read_text(encoding='ascii'))
            images = document['images']
            expected = {str(h) for h in range(64, 261, 4)}
            if (document.get('format') != 'biotwin-crew-visuals' or document.get('version') != 1
                    or type(images) is not dict or set(images) != expected
                    or any(type(v) is not str or len(v) > 30_000 for v in images.values())):
                raise ValueError('Unsupported navigation artwork.')
            payload = json.dumps(images, sort_keys=True, separators=(',', ':'))
            if hashlib.sha256(payload.encode('ascii')).hexdigest() != document['sha256']:
                raise ValueError('Navigation artwork integrity mismatch.')
            self.encoded = images
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.error = str(exc)

    def get(self, height):
        height = min(260, max(64, round(height / 4) * 4))
        if not self.encoded or height in self.failed:
            return None, height
        if height not in self.images:
            try:
                image = tk.PhotoImage(master=self.master, data=self.encoded[str(height)], format='png')
                if image.height() != height or image.width() != round(150 * height / 340):
                    raise ValueError('Navigation artwork dimensions mismatch.')
                self.images[height] = image
            except (tk.TclError, ValueError) as exc:
                self.error = str(exc)
                self.failed.add(height)
                return None, height
        return self.images[height], height

    def close(self):
        self.images.clear()
        self.encoded.clear()
        self.failed.clear()
        self.master = None


def blue_light(canvas, cx, cy, rx, ry):
    """Compatibility hook: the neutral figure needs no decorative scanner field."""
    return None


def draw_crew(canvas, cx, top, height, *, active=None, interactive=True, images=None):
    """Draw one antialiased neutral human; targets indicate navigation only."""
    image, height = images.get(height) if images is not None else (None, height)
    s = height / 340
    if image is not None:
        canvas.create_image(cx, top, image=image, anchor='n', tags=('crew-silhouette',))
    else:
        canvas.create_text(cx, top + height*.65, text='Figure unavailable', fill=MUTED,
                           font=('Segoe UI', 8), tags=('crew-unavailable',))
    targets = {'head': (cx, top+24*s), 'heart': (cx, top+94*s), 'vital': (cx, top+157*s)}
    if interactive:
        for name, (x,y) in targets.items():
            r = (25 if name == 'head' else 23)*s
            canvas.create_oval(x-r,y-r,x+r,y+r,fill='',
                               outline=ACCENT if active == name else '',width=1.5,
                               tags=('crew-target',name))
            # A small quiet locator makes the category discoverable without filling the body.
            canvas.create_oval(x-2,y-2,x+2,y+2,fill=TRACE,outline='',tags=('crew-locator',))
    return targets


def target_at(canvas, x, y):
    """Route the full target interior without opaque fills or Tk stippling."""
    for name in ('head','heart','vital'):
        bounds = canvas.coords(name)
        if len(bounds) != 4:
            continue
        x0,y0,x1,y1 = bounds
        rx,ry = (x1-x0)/2,(y1-y0)/2
        if rx and ry and ((x-(x0+x1)/2)/rx)**2+((y-(y0+y1)/2)/ry)**2 <= 1:
            return name
    return None


def path_point(fraction, width, height, *, reverse=False, lane=0, offset_y=0):
    """Straight aligned directional lane; progress belongs to the real timeline."""
    fraction = min(1, max(0, fraction))
    t = 1-fraction if reverse else fraction
    return 22+(width-46)*t, offset_y+height*.32+lane*16


def draw_path(canvas, width, height, *, reverse=False, lane=0, fraction=None, color=ACCENT, offset_y=0):
    start = path_point(0,width,height,reverse=reverse,lane=lane,offset_y=offset_y)
    end = path_point(1,width,height,reverse=reverse,lane=lane,offset_y=offset_y)
    canvas.create_line(*start,*end,fill=GRID,width=2,arrow='last',arrowshape=(6,7,3),tags=('packet-path',))
    if fraction is not None:
        x,y = path_point(fraction,width,height,reverse=reverse,lane=lane,offset_y=offset_y)
        if fraction >= 1:
            canvas.create_oval(x-2,y-2,x+2,y+2,fill=MUTED,outline='',tags=('packet-delivered',))
        else:
            canvas.create_oval(x-3,y-3,x+3,y+3,fill=color,outline='',tags=('packet',))
        return x,y
    return None
