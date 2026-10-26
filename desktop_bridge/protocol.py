"""Fixed, read-only operations understood by both ends of the connector."""
import json


READ_APIS = frozenset({
    'mtop.taobao.idle.trade.merchant.sold.get',
    'mtop.taobao.idle.merchant.order.count',
    'mtop.idle.web.trade.order.detail',
    'mtop.idle.web.xyh.item.list',
    'mtop.taobao.idle.pc.detail',
})
MAX_REQUEST_BYTES = 65536
MAX_RESULT_BYTES = 1024 * 1024
ERRORS = frozenset({'sdk_unavailable', 'verification_required', 'account_changed',
                    'client_disconnected', 'request_failed', 'response_too_large'})


def validate_request(api, version, data):
    if api not in READ_APIS or version != '1.0' or not isinstance(data, dict):
        raise ValueError('Unsupported desktop query')
    raw = json.dumps(data, ensure_ascii=True, allow_nan=False)
    if len(raw.encode('utf-8')) > MAX_REQUEST_BYTES:
        raise ValueError('Desktop query is too large')
