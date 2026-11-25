"""Control Center appearance. These colours do not change the lock overlay."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    bg: str
    rail: str
    panel: str
    raised: str
    line: str
    text: str
    muted: str
    accent: str
    ink: str
    selected: str


THEMES = {
    "midnight": Palette("#161818", "#101212", "#202323", "#292D2C", "#3C4140",
                        "#F4F6F1", "#AFB7B1", "#CEEF93", "#17230C", "#313C29"),
    "aurora": Palette("#101E1E", "#0D1718", "#182A2A", "#213737", "#37504F",
                      "#ECF9F6", "#ACC7BF", "#86E2CF", "#09251F", "#22403B"),
    "ember": Palette("#201819", "#191314", "#2D2122", "#3B2A2A", "#564044",
                     "#FCF1E9", "#CEB8B1", "#FFB496", "#35190E", "#483028"),
    "daylight": Palette("#F5F6F1", "#EBEEE7", "#FFFFFF", "#E9EDE6", "#CFD5CB",
                        "#202620", "#535E53", "#305D37", "#FFFFFF", "#DCEBD6"),
}


def theme_for(widget):
    return THEMES.get(widget.window().property("ccTheme"), THEMES["midnight"])


def stylesheet(p):
    font = "'Segoe UI Variable', 'Segoe UI', sans-serif"
    return f"""
    QMainWindow, QScrollArea#pageScroll, QWidget#pageContent {{ background:{p.bg}; }}
    QWidget {{ font-family:{font}; }}
    QLabel {{ background:transparent; color:{p.text}; font-size:13px; }}
    QLabel#eyebrow, QLabel#statLabel {{ color:{p.muted}; font:600 11px {font}; }}
    QLabel#brand {{ font:700 17px {font}; }}
    QLabel#pageTitle {{ font:600 29px {font}; }}
    QLabel#status {{ font:600 25px {font}; }}
    QLabel#value {{ font:600 20px {font}; }}
    QLabel#cardTitle {{ font:600 16px {font}; }}
    QLabel#rowTitle {{ font:600 13px {font}; }}
    QLabel#pageSub, QLabel#detail, QLabel#brandSub, QLabel#rowMeta {{ color:{p.muted}; }}
    QLabel#mono {{ color:{p.muted}; font:12px 'Cascadia Mono', Consolas, monospace; }}
    QLabel#chip, QLabel#pill, QLabel#keycap {{ color:{p.text}; background:{p.raised};
      border:1px solid {p.line}; border-radius:5px; padding:4px 8px; font-size:11px; }}
    QFrame#hair {{ background:{p.line}; min-height:1px; max-height:1px; border:0; }}
    QPushButton, QSpinBox, QComboBox {{ background:{p.raised}; color:{p.text};
      border:1px solid {p.line}; border-radius:6px; padding:7px 12px; font:600 13px {font}; }}
    QPushButton:hover, QComboBox:hover {{ background:{p.selected}; }}
    QPushButton#primary {{ background:{p.accent}; color:{p.ink}; border:1px solid {p.accent};
      padding:10px 18px; }}
    QPushButton#primary:hover {{ border:1px solid {p.text}; }}
    QPushButton#primary:pressed {{ padding:11px 17px 9px 19px; }}
    QPushButton#link {{ color:{p.accent}; background:transparent; border:1px solid transparent; padding:4px 8px; }}
    QPushButton#link:hover {{ background:{p.selected}; }}
    QPushButton#statLink {{ padding:0; color:{p.muted}; background:transparent;
      border:1px solid transparent; font:600 18px {font}; }}
    QPushButton#statLink:hover, QPushButton#statLink:focus {{ color:{p.accent};
      background:{p.selected}; border:1px solid {p.accent}; }}
    QPushButton#secondary[current="true"], QPushButton#themeOption:checked {{ color:{p.accent}; background:{p.selected}; border:1px solid {p.accent}; }}
    QPushButton:focus, QPushButton#link:focus, QSpinBox:focus, QComboBox:focus {{ border:1px solid {p.accent}; }}
    QPushButton#primary:focus {{ border:2px solid {p.text}; }}
    QPushButton:disabled, QPushButton#primary:disabled, QSpinBox:disabled {{ color:{p.muted}; background:{p.panel}; border:1px solid {p.line}; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width:0px; border:none; }}
    QScrollArea#pageScroll {{ border:none; }}
    QScrollBar:vertical {{ background:{p.bg}; width:9px; }}
    QScrollBar::handle:vertical {{ background:{p.line}; border-radius:4px; min-height:32px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0px; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:transparent; }}
    """
