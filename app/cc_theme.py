"""Control Center appearance. These colours do not change the lock overlay."""
from dataclasses import dataclass
import os


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
    "midnight": Palette("#141617", "#0E1011", "#202324", "#292C2D", "#3D4243",
                        "#F5F6F3", "#AFB8B8", "#D3ECE3", "#142922", "#263932"),
    "aurora": Palette("#101E1E", "#0D1718", "#182A2A", "#213737", "#37504F",
                      "#ECF9F6", "#ACC7BF", "#86E2CF", "#09251F", "#22403B"),
    "ember": Palette("#201819", "#191314", "#2D2122", "#3B2A2A", "#564044",
                     "#FCF1E9", "#CEB8B1", "#FFB496", "#35190E", "#483028"),
    "daylight": Palette("#F5F6F7", "#E8EBED", "#FFFFFF", "#E9ECEF", "#CCD1D5",
                        "#172029", "#50606B", "#204F80", "#FFFFFF", "#DEE9F5"),
}


def theme_for(widget):
    return THEMES.get(widget.window().property("ccTheme"), THEMES["midnight"])


def reduced_motion(widget):
    return os.environ.get("MG_UI_REDUCE_MOTION") == "1" or bool(widget.window().property("ccReduceMotion"))


def stylesheet(p):
    font = "'Segoe UI Variable', 'Segoe UI', sans-serif"
    return f"""
    QMainWindow, QDialog#aboutDialog, QScrollArea#pageScroll, QWidget#pageContent {{ background:{p.bg}; }}
    QFrame#quickControls {{ background:{p.panel}; border:1px solid {p.line}; }}
    QWidget {{ font-family:{font}; }}
    QWidget#tabUnderline {{ background:{p.accent}; border-radius:1px; }}
    QFrame#masthead, QFrame#aperturePlate {{ background:#182026; }}
    QFrame#aperturePlate {{ border-radius:8px; }}
    QLabel#mastBrand {{ color:#F2F5F7; font:700 20px {font}; }}
    QLabel#mastSub, QLabel#plateDetail {{ color:#C0CDD5; font:14px {font}; }}
    QLabel#engraving {{ color:#C0CDD5; font:600 12px 'Cascadia Mono', Consolas, monospace; }}
    QLabel#plateTitle {{ color:#F9FAFB; font:600 30px {font}; }}
    QLabel#journalTitle {{ color:{p.text}; font:600 25px {font}; }}
    QLabel#ledgerValue {{ font:600 16px {font}; }}
    QLabel {{ background:transparent; color:{p.text}; font-size:13px; }}
    QLabel#eyebrow, QLabel#statLabel {{ color:{p.muted}; font:600 11px {font}; }}
    QLabel#railSection {{ color:{p.muted}; font:600 11px {font}; }}
    QLabel#quickStatus {{ font:600 19px {font}; }}
    QLabel#quickPlateTitle {{ color:#F9FAFB; font:600 18px {font}; }}
    QLabel#quickArrow {{ color:{p.muted}; font:16px {font}; }}
    QLabel#brand {{ font:700 17px {font}; }}
    QLabel#pageTitle {{ font:600 31px {font}; }}
    QLabel#status {{ font:600 27px {font}; }}
    QLabel#value {{ font:600 20px {font}; }}
    QLabel#cardTitle {{ font:600 16px {font}; }}
    QLabel#rowTitle {{ font:600 13px {font}; }}
    QLabel#pageSub, QLabel#detail, QLabel#brandSub, QLabel#rowMeta {{ color:{p.muted}; }}
    QLabel#mono {{ color:{p.muted}; font:12px 'Cascadia Mono', Consolas, monospace; }}
    QLineEdit#folderPath {{ background:{p.bg}; color:{p.text}; border:1px solid {p.line};
      border-radius:4px; padding:6px; font:12px 'Cascadia Mono', Consolas, monospace; }}
    QLineEdit#folderPath:focus {{ border:1px solid {p.accent}; }}
    QLineEdit#activitySearch {{ background:{p.bg}; color:{p.text}; border:1px solid {p.line};
      border-radius:5px; padding:7px 10px; font:13px {font}; }}
    QLineEdit#activitySearch:focus {{ border:1px solid {p.accent}; }}
    QLabel#chip, QLabel#pill, QLabel#keycap {{ color:{p.text}; background:{p.raised};
      border:1px solid {p.line}; border-radius:5px; padding:4px 8px; font-size:11px; }}
    QFrame#hair {{ background:{p.line}; min-height:1px; max-height:1px; border:0; }}
    QPushButton, QSpinBox, QComboBox {{ background:{p.raised}; color:{p.text};
      border:1px solid {p.line}; border-radius:6px; padding:7px 12px; font:600 13px {font}; }}
    QPushButton:hover, QComboBox:hover {{ background:{p.selected}; }}
    QPushButton#mastButton, QPushButton#mastInfo {{ background:#29353D; color:#F2F6F8;
      border:1px solid #53616B; border-radius:5px; }}
    QPushButton#mastInfo {{ padding:0; }}
    QPushButton#mastButton:hover, QPushButton#mastInfo:hover, QPushButton#mastButton:focus,
    QPushButton#mastInfo:focus {{ background:#394852; border-color:#D0E1EC; }}
    QPushButton#plateAction {{ background:#E6F2FD; color:#122838; border:1px solid #C1D2E0;
      border-radius:5px; padding:9px 18px; }}
    QPushButton#plateAction:hover, QPushButton#plateAction:focus {{ background:white; border-color:#689ACA; }}
    QPushButton#plateAction:disabled {{ background:#33414A; color:#BBCCD6; border-color:#586773; }}
    QPushButton#ledgerRow {{ background:transparent; padding:0; border:0;
      border-bottom:1px solid {p.line}; border-radius:0; }}
    QPushButton#ledgerRow:hover, QPushButton#ledgerRow:focus {{ background:{p.selected}; border-bottom:1px solid {p.accent}; }}
    QPushButton#readout {{ background:{p.panel}; }}
    QPushButton#quickRoute {{ background:transparent; border:1px solid transparent; padding:0; }}
    QPushButton#quickRoute:hover, QPushButton#readout:hover, QPushButton#quickRoute:focus,
    QPushButton#readout:focus {{ background:{p.selected}; border:1px solid {p.accent}; }}
    QPushButton#filter:checked {{ color:{p.accent}; background:{p.selected}; border:1px solid {p.accent}; }}
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
    QPushButton#infoButton {{ padding:0; border-radius:8px; color:{p.muted}; font:600 16px {font}; }}
    QPushButton#infoButton:hover, QPushButton#infoButton:focus {{ color:{p.accent}; }}
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
