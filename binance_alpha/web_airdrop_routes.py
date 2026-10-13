from flask import jsonify, request

from .services.airdrop_market_service import AirdropMarketService


def register_airdrop_routes(app, ledger=None):
    service = AirdropMarketService()

    @app.get('/api/airdrops/members')
    def airdrop_members():
        if ledger is None:
            return jsonify(error='此页面不能读取本机账户。'), 403
        response = jsonify(members=ledger.active_members())
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/api/airdrops/save')
    def airdrop_save():
        if ledger is None:
            return jsonify(ok=False, error='此页面不能保存正式记录。'), 403
        if request.content_length and request.content_length > 5_000_000:
            return jsonify(ok=False, error='记录内容过大，未保存。'), 413
        try:
            revision = ledger.save(request.get_json(silent=True))
            return jsonify(ok=True, revision=revision)
        except RuntimeError as exc:
            return jsonify(ok=False, conflict=True, error=str(exc))
        except (ValueError, TypeError, KeyError, AttributeError):
            return jsonify(ok=False, error='记录未保存，请检查账户、领取数量、整份卖出金额和时间。')
        except Exception:
            app.logger.exception('Unable to save local airdrop ledger')
            return jsonify(ok=False, error='本机暂时无法保存，当前填写已保留为草稿。')

    @app.post('/api/airdrops/delete')
    def airdrop_delete():
        if ledger is None:
            return jsonify(ok=False, error='此页面不能删除正式记录。'), 403
        try:
            return jsonify(ok=True, **ledger.delete(request.get_json(silent=True)))
        except RuntimeError as exc:
            return jsonify(ok=False, conflict=True, error=str(exc))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            return jsonify(ok=False, error=str(exc) or '请重新选择要删除的空投。')
        except Exception:
            app.logger.exception('Unable to delete local airdrop event')
            return jsonify(ok=False, error='本机暂时无法删除，请稍后重试。')

    @app.get('/api/airdrops/catalog')
    def airdrop_catalog():
        try:
            return jsonify(service.catalog())
        except Exception:
            app.logger.warning('Unable to retrieve public airdrop history', exc_info=True)
            return jsonify(error='暂时无法读取 Alpha123 历史，请稍后重试；已有记录不受影响。'), 503

    @app.get('/api/airdrops/quote')
    def airdrop_quote():
        event_id = request.args.get('id', '').strip()
        if len(event_id) != 24 or any(c not in '0123456789abcdef' for c in event_id):
            return jsonify(error='请先从空投历史选择一条记录。'), 400
        try:
            return jsonify(service.quote(event_id))
        except LookupError as exc:
            return jsonify(error=str(exc)), 404
        except Exception:
            app.logger.warning('Unable to retrieve public airdrop price', exc_info=True)
            return jsonify(error='行情暂时无法更新，已保留原参考价。'), 503
