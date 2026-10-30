#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quench —— 一个不需要数据库的喝水提醒

原理：用「日期」当随机种子，算出当天 8 个提醒时间点和这 8 条消息，
所以同一个日期永远算出同一套结果。定时触发器每 5 分钟叫醒它一次，
它只看「现在是不是落在某个时间点的 5 分钟内」，是就推一条，不是就退出。

不需要数据库、不需要服务器常驻、不需要状态文件。

用法
    BARK_KEY=你的key python3 water.py --plan       # 看今天/明天的时间表与文案
    BARK_KEY=你的key python3 water.py --plan 2026-11-01
    BARK_KEY=你的key python3 water.py --test       # 立刻推一条，验证通道
    BARK_KEY=你的key python3 water.py              # 到点就推（给定时任务调）

环境变量
    BARK_KEY    Bark 的推送 key（必填，https://github.com/Finb/Bark）
    BARK_GROUP  通知分组名，默认「喝水」
    BARK_SOUND  通知铃声，默认空
    QUENCH_LIB  词库路径，默认同目录 water_lib.json
"""

import json
import os
import random
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------- 可调参数 ----------------
WINDOW_START = (9, 0)     # 推送窗口开始
WINDOW_END = (21, 30)     # 推送窗口结束（留出睡前时间）
SLOTS = 8                 # 一天几次 × 每次 300ml ≈ 2.4L
MIN_GAP = 55              # 相邻两次最短间隔（分钟）
TICK = 5                  # 定时触发周期（分钟），必须等于触发器周期

CST = timezone(timedelta(hours=8))

BARK_KEY = os.environ.get("BARK_KEY", "")
BARK_GROUP = os.environ.get("BARK_GROUP", "喝水")
BARK_SOUND = os.environ.get("BARK_SOUND", "")

# 由 build_fc.py 生成单文件版本时会写入这里；直接跑仓库里这份时为空
EMBEDDED_LIB = {}


# ---------------- 词库 ----------------
def load_lib() -> dict:
    if EMBEDDED_LIB:
        return EMBEDDED_LIB
    path = Path(os.environ.get("QUENCH_LIB", Path(__file__).with_name("water_lib.json")))
    with path.open(encoding="utf-8") as f:
        return json.load(f)


LIB = load_lib()


def _rng(seed: str) -> random.Random:
    return random.Random(seed)


# ---------------- 排期 ----------------
def slots_for(date_str: str) -> list:
    """当天 8 个推送时间点，单位是「从 00:00 起的分钟数」，确定性可复现。"""
    rng = _rng("slots|" + date_str)
    start = WINDOW_START[0] * 60 + WINDOW_START[1]
    end = WINDOW_END[0] * 60 + WINDOW_END[1]
    span = end - start
    margin = 12          # 首末各留 12 分钟，避免天天 09:00 整
    usable = span - 2 * margin
    base = span / SLOTS

    gaps = [max(MIN_GAP, base + rng.uniform(-25, 25)) for _ in range(SLOTS - 1)]
    total = sum(gaps)
    if total > usable:                       # 挤一挤，别越过窗口
        gaps = [g * usable / total for g in gaps]
        total = usable
    lead = rng.uniform(0, max(min(45.0, usable - total), 0))

    pos, cur = [], margin + lead
    for g in [0] + gaps:
        cur += g
        pos.append(cur)
    return [int(start + t) for t in pos]


# ---------------- 选句 ----------------
_PLAN_CACHE = {}


def _plan(date_str: str) -> list:
    """当天 8 条，确定性、互不重复；第 3/5/7 条带进度。"""
    if date_str in _PLAN_CACHE:
        return _PLAN_CACHE[date_str]
    rng = _rng("msg|" + date_str)
    pool = [(c, t) for c, items in LIB.items() if c != "progress" for t in items]
    rng.shuffle(pool)
    progress = list(LIB.get("progress", []))
    plan, k = [], 0
    for i in range(SLOTS):
        if progress and i in (2, 4, 6):
            text = progress[i % len(progress)].format(n=i + 1, total=SLOTS, left=SLOTS - i - 1)
        else:
            text = pool[k][1]
            k += 1
        plan.append(text)
    _PLAN_CACHE[date_str] = plan
    return plan


def pick_message(date_str: str, idx: int) -> str:
    return _plan(date_str)[idx]


# ---------------- 推送 ----------------
def bark(title: str, body: str) -> bool:
    if not BARK_KEY:
        raise SystemExit("缺少 BARK_KEY。先在 Bark App 里拿到 key，再 export BARK_KEY=xxx")
    url = "{}/{}/{}/{}".format(
        "https://api.day.app", BARK_KEY,
        urllib.parse.quote(title, safe=""), urllib.parse.quote(body, safe=""))
    params = {"group": BARK_GROUP}
    if BARK_SOUND:
        params["sound"] = BARK_SOUND
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": "quench"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return b'"code":200' in r.read().replace(b" ", b"")
    except Exception as e:
        print("push failed:", e)
        return False


# ---------------- 主逻辑 ----------------
def check(now=None) -> dict:
    now = now or datetime.now(CST)
    date_str = now.strftime("%Y-%m-%d")
    minute_now = now.hour * 60 + now.minute
    slots = slots_for(date_str)
    for idx, s in enumerate(slots):
        if s <= minute_now < s + TICK:
            n = idx + 1
            msg = pick_message(date_str, idx)
            title = "喝水 · 第 %d/%d 杯" % (n, SLOTS) if n >= 3 else "喝水"
            ok = bark(title, msg)
            return {"fired": True, "slot": s, "idx": idx, "ok": ok, "msg": msg}
    return {"fired": False, "now": minute_now, "slots": slots}


# 云函数入口（函数计算 FC / SCF 通用）
def handler(event, context):
    res = check()
    print(json.dumps(res, ensure_ascii=False))
    return res


main_handler = handler


def main():
    import sys
    args = sys.argv[1:]
    if "--plan" in args:
        i = args.index("--plan")
        d = args[i + 1] if len(args) > i + 1 else datetime.now(CST).strftime("%Y-%m-%d")
        mins = slots_for(d)
        print(f"{d} 排期：")
        for n, (m, text) in enumerate(zip(mins, _plan(d)), 1):
            print("  #%d %02d:%02d  %s" % (n, m // 60, m % 60, text))
        return
    if "--test" in args:
        print(bark("喝水 · 测试", "收到这条说明通道是通的。"))
        return
    print(json.dumps(check(), ensure_ascii=False))


if __name__ == "__main__":
    main()
