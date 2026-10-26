"""卡券/发货内容的分段发送。

把一段较长的发货内容按 IM 消息的安全长度切分成多段依次发送，
返回分段数供调用方记录日志。段与段之间的节奏由本模块控制，
调用方只需关心"一条卡券"级别的间隔。
"""

from __future__ import annotations

import asyncio
import json
import re


def render_delivery_template(template: str, content: str) -> str:
    """一条库存记录是一个整体：支持 ---- 分列或 JSON 命名参数。"""
    values = {'参数': content, 'DELIVERY_CONTENT': content}
    values.update({f'参数{i}': value.strip()
                   for i, value in enumerate(content.split('----'), 1)})
    if content.lstrip().startswith('{'):
        try:
            record = json.loads(content)
        except json.JSONDecodeError:
            raise ValueError('库存行的 JSON 格式无效') from None
        if not isinstance(record, dict):
            raise ValueError('命名参数必须是 JSON 对象')
        for key, value in record.items():
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                values[str(key)] = str(value)

    def replace(match):
        name = match.group(1)
        if name not in values or not values[name].strip():
            raise ValueError(f'发货内容缺少参数：{name}')
        return values[name]

    if not template.strip():
        raise ValueError('请填写发货模板')
    return re.sub(r'\{([^{}\n]+)\}', replace, template)

# 单段消息的最大字符数。闲鱼 IM 对长文本的容忍度有限，
# 超长内容一次性发送容易被截断或吞掉，500 字符是保守值。
MAX_SEGMENT_CHARS = 500

# 相邻两段之间的发送间隔（秒），模拟人工输入节奏，降低风控概率。
SEGMENT_INTERVAL_SECONDS = 0.8


def split_delivery_content(content: str, max_chars: int = MAX_SEGMENT_CHARS) -> list[str]:
    """按最大长度切分文本，优先在换行处断开，避免把一行卡密从中间切断。"""
    text = (content or "").strip()
    if not text:
        return []

    segments: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        cut = remaining.rfind("\n", 0, max_chars)
        if cut <= 0:
            # 没有可用的换行边界（单行超长），退化为硬切
            cut = max_chars
        segments.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        segments.append(remaining)
    return segments


async def send_payload(live_instance, ws, chat_id, buyer_id, content) -> int:
    """把发货内容分段发送给买家，返回实际发送的段数。

    任何一段发送失败都会抛出异常，由调用方决定重试或记录失败；
    已发出的段不回滚（消息无法撤回）。
    """
    segments = split_delivery_content(content)
    if not segments:
        return 0

    for index, segment in enumerate(segments):
        if index > 0:
            await asyncio.sleep(SEGMENT_INTERVAL_SECONDS)
        await live_instance.send_msg(ws, chat_id, buyer_id, segment)

    return len(segments)
