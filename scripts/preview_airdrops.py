"""Serve the interactive demo without constructing or opening a real data store."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import Flask, redirect, request
from binance_alpha.web import register_routes


class PreviewStore:
    read_only = False
    workbook_path = Path('体验页（不读取真实账目）.xlsx')

    def get_quick_scores(self):
        return []


def create_preview_app():
    app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.secret_key = 'airdrop-demo-only'

    @app.before_request
    def preview_only():
        if request.path != '/airdrops' and not request.path.startswith(('/assets/', '/static/', '/api/airdrops/')):
            return redirect('/airdrops')

    register_routes(app, PreviewStore())
    return app


if __name__ == '__main__':
    create_preview_app().run(host='127.0.0.1', port=5057, debug=False)
