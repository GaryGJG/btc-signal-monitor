"""
信号引擎：按 ValueScan 的信号类型与文案模板输出多空信号。
消息类型对应关系（ValueScan messageType / predictType）：
  108 资金异动、110 Alpha、111 资金出逃、112 FOMO加剧、113 FOMO、
  100 下跌风险、主力增持(3)/减持(4)/增持加速(28)/减持加速(29)、
  追踪止盈系列(16/17/19/31)。
"""
import logging
import time

import indicators
import settings as cfg
import database as db

log = logging.getLogger("engine")

UP, DOWN, NEUTRAL = "bullish", "bearish", "neutral"


def _fmt_usd(v):
    a = abs(v)
    if a >= 1e9:
        s = f"{a / 1e9:.2f}B"
    elif a >= 1e6:
        s = f"{a / 1e6:.1f}M"
    else:
        s = f"{a / 1e3:.0f}K"
    return ("-" if v < 0 else "") + "$" + s


class SignalEngine:
    def __init__(self):
        self.cooldowns = db.kv_get("cooldowns", {})
        self.tracking = db.kv_get("tracking", None)   # AI 追踪状态
        self.last_trend = db.kv_get("last_trend", None)

    # ---------- 工具 ----------
    def _cooled(self, key):
        until = self.cooldowns.get(key, 0)
        return time.time() >= until

    def _arm(self, key):
        minutes = cfg.SIGNAL_COOLDOWN_MIN.get(key, 120)
        self.cooldowns[key] = time.time() + minutes * 60
        db.kv_set("cooldowns", self.cooldowns)

    @staticmethod
    def _sig(code, name, direction, title, content):
        return {"ts": int(time.time()), "code": code, "name": name,
                "direction": direction, "title": title, "content": content}

    # ---------- 主入口 ----------
    def evaluate(self, snap):
        signals = []
        price = snap["price"]
        chg24 = snap["price_change_24h"]
        base = f"现报 ${price:,.0f}，24H{'涨' if chg24 >= 0 else '跌'}幅 {abs(chg24):.2f}%"

        signals += self._trend_flip(snap, base)
        signals += self._fomo(snap, base)
        signals += self._funds_movement(snap, base)
        signals += self._alpha(snap, base)
        signals += self._whale_flow(snap, base)
        signals += self._downside_risk(snap, base)
        signals += self._tracking_update(snap)
        return signals

    # ---------- 各信号规则 ----------
    def _trend_flip(self, snap, base):
        """主力行为指标翻转（priceMarketType 1↔2），BTC 大趋势多空判定。"""
        pm = snap["price_market"]
        if self.last_trend == pm["type"]:
            return []
        first = self.last_trend is None
        self.last_trend = pm["type"]
        db.kv_set("last_trend", pm["type"])
        if first:
            return []
        # 附上一周期的有效性回顾
        review = ""
        history = (snap.get("trend_stats") or {}).get("history") or []
        if history:
            p = history[0]
            review = (f" 上一{'多头' if p['trend'] == 1 else '空头'}周期持续 "
                      f"{p['duration_h']:.1f} 小时，期间价格变化 {p['change_pct']:+.2f}%"
                      f"（{'方向正确' if p.get('hit') else '方向错误'}）。")
        if pm["type"] == 1:
            return [self._sig("TREND", "主力行为指标", UP,
                              "BTC 主力行为指标转为【上涨】",
                              f"多维主力行为投票 {pm['score']}/6 看多，{base}。{review}")]
        return [self._sig("TREND", "主力行为指标", DOWN,
                          "BTC 主力行为指标转为【下跌】",
                          f"多维主力行为投票仅 {pm['score']}/6 看多，{base}，注意市场风险。{review}")]

    def _fomo(self, snap, base):
        """113 FOMO：量价同时达到阈值；112 FOMO加剧：过热止盈预警。"""
        vs = snap.get("vol_stats")
        if not vs:
            return []
        out = []
        if (vs["vol_mult"] >= cfg.FOMO_VOLUME_MULT
                and abs(vs["price_change_5m"]) >= cfg.FOMO_PRICE_CHANGE
                and self._cooled("FOMO")):
            self._arm("FOMO")
            out.append(self._sig(
                "113", "FOMO", UP if vs["price_change_5m"] > 0 else DOWN,
                "BTC 交易量激增，市场 FOMO 情绪，请注意关注",
                f"最近 5 分钟涨跌幅 {vs['price_change_5m']:+.2f}%，"
                f"成交额达均值 {vs['vol_mult']:.1f} 倍，{base}，注意风险管控。"))
        if (vs["vol_mult"] >= cfg.FOMO_VOLUME_MULT
                and snap["price_change_1h"] >= cfg.FOMO_ESCALATION_1H
                and self._cooled("FOMO_ESCALATION")):
            self._arm("FOMO_ESCALATION")
            out.append(self._sig(
                "112", "FOMO加剧", DOWN,
                "BTC FOMO 情绪加剧，注意止盈，防范突发风险",
                f"1 小时涨幅 {snap['price_change_1h']:.2f}% 且量能持续放大，{base}，注意止盈防风险。"))
        return out

    def _funds_movement(self, snap, base):
        """108 资金异动：1 小时净流入显著偏离历史分布。"""
        hourly = snap["hourly_netflows"]
        if len(hourly) < 8 or not self._cooled("FUNDS_MOVEMENT"):
            return []
        total_1h = snap["spot_netflow"]["1h"] + snap["futures_netflow"]["1h"]
        z = indicators.zscore(hourly, total_1h)
        if z >= cfg.NETFLOW_SIGMA:
            self._arm("FUNDS_MOVEMENT")
            return [self._sig(
                "108", "资金异动", UP,
                "BTC 24H内 现货+合约资金异动，请重点关注",
                f"1 小时净流入 {_fmt_usd(total_1h)}（z={z:.1f}），出现大量资金异常流入，"
                f"{base}，请注意市场行情变化。")]
        return []

    def _alpha(self, snap, base):
        """110 Alpha：资金连续多小时持续净流入（利多信号）。"""
        hourly = snap["hourly_netflows"]
        n = cfg.ALPHA_HOURS
        if len(hourly) < n or not self._cooled("ALPHA"):
            return []
        if all(v > 0 for v in hourly[-n:]) and sum(hourly[-n:]) >= cfg.ALPHA_MIN_USD:
            self._arm("ALPHA")
            self._start_tracking(snap, reason="Alpha")
            return [self._sig(
                "110", "Alpha", UP,
                "BTC 资金活跃异常，可能是利多信号，请重点跟踪",
                f"现货资金已连续 {n} 小时净流入（近 4 小时合计 "
                f"{_fmt_usd(snap['spot_netflow']['4h'])}），{base}，"
                f"可能出现上涨行情，但需注意风险。")]
        return []

    def _whale_flow(self, snap, base):
        """主力增持/减持/加速/出逃：基于大额交易净流入。"""
        whale = snap["whale"]
        out = []
        nf4 = whale["netflow_4h"]
        if whale["trade_count"] < 50:
            return out  # 大单样本积累期不触发
        if nf4 >= cfg.WHALE_FLOW_USD_4H and self._cooled("WHALE_INCREASE"):
            self._arm("WHALE_INCREASE")
            self._start_tracking(snap, reason="主力增持")
            out.append(self._sig(
                "3", "主力增持", UP,
                "BTC 疑似主力增持，注意市场变化",
                f"4 小时大额成交净流入 {_fmt_usd(nf4)}，疑似主力持仓增加，{base}，"
                f"市场情绪乐观，但需注意高抛风险。"))
            if whale["netflow_1h"] > nf4 * 0.5 and self._cooled("WHALE_ACCELERATE"):
                self._arm("WHALE_ACCELERATE")
                out.append(self._sig(
                    "28", "主力增持加速", UP,
                    "BTC 疑似主力增持加速，可能有上涨行情",
                    f"近 1 小时大单净流入 {_fmt_usd(whale['netflow_1h'])}，买入力量明显增强，{base}。"))
        elif nf4 <= -cfg.WHALE_FLOW_USD_4H and self._cooled("WHALE_REDUCE"):
            self._arm("WHALE_REDUCE")
            out.append(self._sig(
                "4", "主力减持", DOWN,
                "BTC 疑似主力减持，注意市场风险",
                f"4 小时大额成交净流出 {_fmt_usd(nf4)}，疑似主力持仓减少，{base}，注意市场风险。"))
            if whale["netflow_24h"] < 0 and snap["price_change_4h"] < -1 and self._cooled("CAPITAL_EXODUS"):
                self._arm("CAPITAL_EXODUS")
                out.append(self._sig(
                    "111", "资金出逃", DOWN,
                    "BTC 主力资金疑似出逃",
                    f"24 小时大单净流出 {_fmt_usd(whale['netflow_24h'])} 且价格走弱，注意市场风险。"))
        return out

    def _downside_risk(self, snap, base):
        """100 下跌风险：趋势看空 + 大单流出 + 跌破主力成本。"""
        whale = snap["whale"]
        if not self._cooled("DOWNSIDE_RISK") or not whale["cost"]:
            return []
        if (snap["price_market"]["type"] == 2
                and whale["netflow_24h"] < 0
                and snap["price"] < whale["cost"]):
            self._arm("DOWNSIDE_RISK")
            dev = (snap["price"] - whale["cost"]) / whale["cost"] * 100
            return [self._sig(
                "100", "下跌风险", DOWN,
                "BTC 上榜（下跌风险），下行压力增大",
                f"主力行为指标看空，24H 大单净流出 {_fmt_usd(whale['netflow_24h'])}，"
                f"价格已低于主力成本 ${whale['cost']:,.0f}（偏离 {dev:.1f}%），{base}。")]
        return []

    # ---------- AI 追踪（止盈/止损提示） ----------
    def _start_tracking(self, snap, reason):
        if self.tracking:
            return
        self.tracking = {"entry": snap["price"], "peak": snap["price"],
                         "start_ts": int(time.time()), "reason": reason}
        db.kv_set("tracking", self.tracking)
        log.info("开始 AI 追踪：%s @ %s", reason, snap["price"])

    def _end_tracking(self):
        self.tracking = None
        db.kv_set("tracking", None)

    def _tracking_update(self, snap):
        t = self.tracking
        if not t:
            return []
        price = snap["price"]
        t["peak"] = max(t["peak"], price)
        db.kv_set("tracking", t)
        gains = (price - t["entry"]) / t["entry"] * 100
        drawdown = (t["peak"] - price) / t["peak"] * 100
        hours = (time.time() - t["start_ts"]) / 3600

        if gains >= cfg.TRACK_TAKE_PROFIT:
            self._end_tracking()
            return [self._sig("16", "上涨止盈", NEUTRAL,
                              f"BTC 追踪后的涨幅超过 {gains:.1f}%，移动止盈以保护利润",
                              f"AI 追踪（{t['reason']}）后涨幅 {gains:.1f}%，现报 ${price:,.0f}，"
                              f"注意移动止盈以保护利润。")]
        if gains > 3 and drawdown >= cfg.TRACK_DRAWDOWN:
            self._end_tracking()
            return [self._sig("17", "回调止盈", NEUTRAL,
                              f"BTC 达到最大涨幅后回落超过 {drawdown:.1f}%，移动止盈",
                              f"AI 追踪后冲高回落，最高 ${t['peak']:,.0f}，现报 ${price:,.0f}，"
                              f"注意移动止盈以保护利润。")]
        if gains <= -cfg.TRACK_STOP:
            self._end_tracking()
            return [self._sig("19", "下跌止盈", DOWN,
                              f"BTC 追踪后的跌幅超过 {abs(gains):.1f}%，注意市场风险",
                              f"AI 追踪后下跌 {abs(gains):.1f}%，现报 ${price:,.0f}，移动止盈以保护利润。")]
        if gains <= -cfg.TRACK_PROTECT:
            # 保护本金只提示一次：转入更深的止损档后再提示 19
            if not t.get("protected"):
                t["protected"] = True
                db.kv_set("tracking", t)
                return [self._sig("31", "保护本金", DOWN,
                                  f"BTC 追踪后的跌幅超过 {abs(gains):.1f}%，注意保护本金",
                                  f"AI 追踪后下跌，现报 ${price:,.0f}，注意保护本金。")]
        if hours >= cfg.TRACK_MAX_HOURS:
            self._end_tracking()
            return [self._sig("6", "追踪结束", NEUTRAL,
                              "AI 实时追踪 BTC 结束，注意市场风险",
                              f"追踪 {hours:.0f} 小时结束，期间最大涨幅 "
                              f"{(t['peak'] - t['entry']) / t['entry'] * 100:.1f}%，"
                              f"现报 ${price:,.0f}。")]
        return []
