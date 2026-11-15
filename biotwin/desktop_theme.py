"""Shared midnight-navy monitor palette and native control presentation.

Orange identifies interaction and progress; observation blue is deliberately
separate from caution amber and urgent red. No health state is derived here.
"""
from tkinter import ttk

BG = '#071329'
PANEL = '#102541'
RAISED = '#193653'
HOVER = '#254b70'
SELECTED = '#244565'
INK = '#edf4ff'
MUTED = '#a9bdd5'
DISABLED = '#7188a5'
DISABLED_BG = '#142a43'
PLOT = '#091c33'
GRID = '#294765'
BORDER = '#486789'
TRACE = '#75b7f5'
ACCENT = '#f8945d'
ACCENT_HOVER = '#ffaf80'
ACCENT_PRESSED = '#df7a43'
ACCENT_INK = BG
AMBER = '#f2c46d'
RED = '#ff7d8b'
GREEN = '#85d6b5'
BAND_OUTER = '#132e4c'
BAND_INNER = '#204567'
BASELINE = '#6d91b8'
EXCLUDED = '#303e55'
HUD_SURFACE = '#0b1e38'
HUD_CARD = '#0f2644'
HUD_BORDER = '#274d75'
CYAN_DIM = '#2c5882'
STAGE_ACTIVE = '#27537c'


def primary_button(style, name):
    """Use the same readable primary action in the monitor and assistant."""
    style.configure(name, background=ACCENT, foreground=ACCENT_INK,
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                    focuscolor=ACCENT_INK)
    style.map(name,
              background=[('disabled', DISABLED_BG), ('pressed', ACCENT_PRESSED), ('active', ACCENT_HOVER)],
              foreground=[('disabled', DISABLED), ('!disabled', ACCENT_INK)],
              bordercolor=[('disabled', DISABLED_BG), ('!disabled', ACCENT)],
              lightcolor=[('disabled', DISABLED_BG), ('!disabled', ACCENT)],
              darkcolor=[('disabled', DISABLED_BG), ('!disabled', ACCENT)])


def apply_theme(root):
    """Configure shared ttk states, including standalone assistant windows."""
    style = ttk.Style(root)
    if style.theme_use() != 'clam':
        style.theme_use('clam')
    style.configure('.', font=('Segoe UI', 10), foreground=INK, background=BG,
                    bordercolor=GRID, lightcolor=GRID, darkcolor=GRID,
                    focuscolor=ACCENT, troughcolor=PLOT, selectbackground=SELECTED,
                    selectforeground=INK)
    style.configure('TFrame', background=BG)
    # Flat controls share the open midnight field. Canvas surfaces carry the
    # blue gradient; nested frames never introduce another filled card.
    style.configure('Card.TFrame', background=BG)
    style.configure('TLabel', background=BG)
    style.configure('Card.TLabel', background=BG)
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('CardMuted.TLabel', foreground=MUTED, background=BG)
    style.configure('TButton', padding=(10, 5), background=RAISED, foreground=INK,
                    borderwidth=0, bordercolor=RAISED, lightcolor=RAISED, darkcolor=RAISED)
    style.map('TButton',
              background=[('disabled', DISABLED_BG), ('pressed', SELECTED), ('active', HOVER)],
              foreground=[('disabled', DISABLED)],
              bordercolor=[('focus', ACCENT), ('active', HOVER)],
              lightcolor=[('focus', ACCENT), ('active', HOVER)],
              darkcolor=[('focus', ACCENT), ('active', HOVER)])
    primary_button(style, 'Accent.TButton')
    style.configure('Small.TButton', padding=(7, 3), font=('Segoe UI', 9))
    style.configure('Stage.TButton', padding=(7, 3), font=('Segoe UI', 8, 'bold'),
                    background=PANEL, foreground=MUTED, bordercolor=HUD_BORDER,
                    lightcolor=HUD_BORDER, darkcolor=HUD_BORDER)
    style.map('Stage.TButton',
              background=[('disabled', DISABLED_BG), ('pressed', SELECTED), ('active', HOVER)],
              foreground=[('disabled', DISABLED), ('active', INK)],
              bordercolor=[('focus', ACCENT), ('active', TRACE)])
    style.configure('StageActive.TButton', padding=(7, 3), font=('Segoe UI', 8, 'bold'),
                    background=ACCENT, foreground=ACCENT_INK, bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT)
    style.map('StageActive.TButton',
              background=[('disabled', DISABLED_BG), ('pressed', ACCENT_PRESSED), ('active', ACCENT_HOVER)],
              foreground=[('disabled', DISABLED), ('!disabled', ACCENT_INK)])
    style.configure('Hero.TButton', padding=(12, 6), font=('Segoe UI', 9, 'bold'))
    primary_button(style, 'Hero.TButton')
    style.configure('TMenubutton', background=RAISED, foreground=INK,
                    arrowcolor=MUTED, bordercolor=RAISED, lightcolor=RAISED, darkcolor=RAISED)
    style.map('TMenubutton', background=[('disabled', DISABLED_BG), ('active', HOVER)],
              foreground=[('disabled', DISABLED)], bordercolor=[('focus', ACCENT)])
    style.configure('TNotebook', background=PANEL, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure('TNotebook.Tab', background=RAISED, foreground=MUTED,
                    padding=(12, 6), borderwidth=0, bordercolor=RAISED,
                    lightcolor=RAISED, darkcolor=RAISED, focuscolor=ACCENT)
    style.map('TNotebook.Tab',
              background=[('disabled', DISABLED_BG), ('selected', SELECTED), ('active', HOVER)],
              foreground=[('disabled', DISABLED), ('selected', INK), ('active', INK)],
              lightcolor=[('selected', ACCENT), ('active', HOVER)],
              darkcolor=[('selected', SELECTED), ('active', HOVER)],
              bordercolor=[('selected', SELECTED), ('active', HOVER)])
    for name in ('TCombobox', 'TEntry', 'TSpinbox'):
        style.configure(name, fieldbackground=RAISED, background=RAISED,
                        foreground=INK, arrowcolor=MUTED, insertcolor=INK,
                        bordercolor=BORDER, lightcolor=RAISED, darkcolor=RAISED)
        style.map(name,
                  fieldbackground=[('disabled', DISABLED_BG), ('readonly', RAISED)],
                  foreground=[('disabled', DISABLED), ('readonly', INK)],
                  background=[('active', HOVER)],
                  bordercolor=[('focus', ACCENT), ('active', TRACE)],
                  arrowcolor=[('disabled', DISABLED), ('active', INK)],
                  selectbackground=[('!disabled', SELECTED)],
                  selectforeground=[('!disabled', INK)])
    style.configure('Horizontal.TScale', background=RAISED, troughcolor=PLOT,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
    style.map('Horizontal.TScale', background=[('disabled', DISABLED_BG), ('active', ACCENT)],
              bordercolor=[('focus', ACCENT)])
    style.configure('TSeparator', background=GRID)
    for name in ('TRadiobutton', 'TCheckbutton'):
        style.configure(name, background=BG, foreground=INK, indicatorbackground=RAISED,
                        indicatorforeground=ACCENT_INK, upperbordercolor=BORDER, lowerbordercolor=BORDER)
        style.map(name, background=[('active', BG)], foreground=[('disabled', DISABLED)],
                  indicatorbackground=[('disabled', DISABLED_BG), ('selected', ACCENT), ('active', HOVER)],
                  upperbordercolor=[('selected', ACCENT), ('focus', ACCENT)],
                  lowerbordercolor=[('selected', ACCENT), ('focus', ACCENT)])
    for name in ('Vertical.TScrollbar', 'Horizontal.TScrollbar'):
        style.configure(name, background=RAISED, troughcolor=PLOT, arrowcolor=MUTED,
                        bordercolor=GRID, lightcolor=RAISED, darkcolor=RAISED)
        style.map(name, background=[('active', HOVER), ('pressed', SELECTED)],
                  arrowcolor=[('disabled', DISABLED), ('active', INK)])
    # Native Tk widgets do not inherit ttk colours.
    for option, value in {
        '*TCombobox*Listbox.background': RAISED,
        '*TCombobox*Listbox.foreground': INK,
        '*TCombobox*Listbox.selectBackground': SELECTED,
        '*TCombobox*Listbox.selectForeground': INK,
        '*Menu.background': PANEL, '*Menu.foreground': INK,
        '*Menu.activeBackground': SELECTED, '*Menu.activeForeground': INK,
        '*Menu.disabledForeground': DISABLED,
        '*Text.background': PLOT, '*Text.foreground': INK,
        '*Text.insertBackground': INK, '*Text.selectBackground': SELECTED,
        '*Text.selectForeground': INK, '*Text.highlightBackground': GRID,
        '*Text.highlightColor': ACCENT,
        '*Scrollbar.background': RAISED, '*Scrollbar.troughColor': PLOT,
        '*Scrollbar.activeBackground': HOVER, '*Scrollbar.highlightBackground': GRID,
        '*Scrollbar.highlightColor': ACCENT,
    }.items():
        root.option_add(option, value)
    return style
