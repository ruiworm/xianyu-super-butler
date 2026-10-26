"""Build a fixed SDK call, never execute server-supplied JavaScript or URLs."""
import json

try:
    from .protocol import validate_request
except ImportError:
    from protocol import validate_request


SDK_PROBE = """(() => {
    const sdk = (window.lib && window.lib.mtop) || window.mtop;
    return Boolean(sdk && typeof sdk.request === 'function');
})()"""


def query_expression(job):
    validate_request(job.get('api'), job.get('version'), job.get('data'))
    args = json.dumps({'api': job['api'], 'v': job['version'], 'data': job['data']}, ensure_ascii=True)
    return """(async (args) => {
        if (location.protocol !== 'https:' ||
            !['seller.goofish.com', 'www.goofish.com'].includes(location.hostname))
            return {error: 'client_disconnected'};
        if (/punish|validate|captcha/i.test(location.pathname))
            return {error: 'verification_required'};
        const sdk = (window.lib && window.lib.mtop) || window.mtop;
        if (!sdk || typeof sdk.request !== 'function') return {error: 'sdk_unavailable'};
        return await new Promise(resolve => {
            let settled = false;
            const finish = result => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                resolve(result);
            };
            const timer = setTimeout(() => finish({error: 'request_failed'}), 16000);
            const receive = response => {
                try {
                    if (!response || !Array.isArray(response.ret) ||
                        !response.ret.every(x => typeof x === 'string')) {
                        finish({error: 'request_failed'}); return;
                    }
                    const result = {ret: response.ret, data: response.data};
                    if (new TextEncoder().encode(JSON.stringify(result)).length > 800000) {
                        finish({error: 'response_too_large'}); return;
                    }
                    finish({result});
                } catch (_) { finish({error: 'request_failed'}); }
            };
            try {
                // Reuse the SDK already loaded by the official page: it owns signing,
                // credentials and renewal. No digest or Cookie is injected here.
                const request = sdk.request({...args, type: 'POST', dataType: 'json',
                    timeout: 15000, sessionOption: 'AutoLoginOnly'}, receive, receive);
                if (request && typeof request.then === 'function') request.then(receive, receive);
            } catch (_) { finish({error: 'request_failed'}); }
        });
    })(""" + args + ')'
