# -*- coding: utf-8 -*-
"""DocFlow — точка входа: нативное окно (pywebview + WebView2), без браузера."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webview  # noqa: E402

from api import Api  # noqa: E402
from config import APP_VERSION, UI_DIR, ensure_dirs  # noqa: E402
import db  # noqa: E402


def main():
    ensure_dirs()
    db.init_db()
    api = Api()
    url = str(UI_DIR / 'index.html')
    window = webview.create_window(
        f'DocFlow — документооборот  v{APP_VERSION}',
        url,
        js_api=api,
        width=1440,
        height=900,
        min_size=(1100, 700),
        background_color='#f5f7fb',
    )
    api.set_window(window)
    webview.start()


if __name__ == '__main__':
    main()
