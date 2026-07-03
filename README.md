# BTC 多空信号系统

参照 valuescan.io 的指标体系，只针对 BTC 采集同类数据、按同类逻辑输出多空信号到 Telegram，并提供 Web 仪表盘。

## 与 ValueScan 的对应关系

| ValueScan 模块 | 本系统实现 | 数据来源（全部免费公开） |
|---|---|---|
| 主力行为指标 priceMarketType(1上涨/2下跌) | 六分量投票：现货/合约资金流、大单净流入、大户持仓比、价格vs主力成本、4H动量，≥4票看多 | Binance 现货+合约 K 线、大额成交、Top Trader 持仓比 |
| 主力成本（大额交易加权平均成本） | 单笔 ≥ $500K 的 aggTrades 30 天加权 VWAP（样本不足时用 30 天日线 VWAP 兜底） | Binance aggTrades |
| 交易所资金流（主力净流入） | taker 主动买入 − 主动卖出（现货 + U 本位合约），1H/4H/24H 窗口 | Binance klines takerBuyQuoteVolume |
| 压力/支撑位 denseArea | 7 天 1h 成交额按价格分桶取密集区，现价上方=压力、下方=支撑 | Binance 1h K 线 |
| 社媒情绪 bullish/bearish 比例 | 恐惧贪婪指数 + 合约多空人数比合成（无社媒数据源的替代方案） | alternative.me、Binance globalLongShortAccountRatio |
| 信号：资金异动(108)/Alpha(110)/资金出逃(111)/FOMO加剧(112)/FOMO(113)/下跌风险(100)/主力增持减持/追踪止盈系列 | signal_engine.py 中同名规则 + AI 追踪状态机（+10% 止盈 / 冲高回落 5% / −8% 保护本金 / −15% 止损 / 48h 超时），文案沿用 ValueScan 模板 | 上述指标 |

## 快速开始

```bash
pip install -r requirements.txt
cp config.example.py config.py   # 填入 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
python3 main.py
```

- 未配置 Telegram 时信号只写日志和数据库（DRY-RUN）。
- Web 仪表盘自动在 **8600-8699** 中挑选空闲端口（bind 测试，不会抢占系统已占用端口），启动日志会打印实际地址；也可在 config.py 里固定 `WEB_PORT`。
- 信号历史与大额交易样本存于 `btc_signals.db`（SQLite），重启不丢失。

## 文件结构

```
main.py            主循环：采集→指标→信号→推送→更新仪表盘
collector.py       Binance/恐惧贪婪指数采集
indicators.py      主力行为投票、主力成本、压力支撑、情绪
signal_engine.py   信号规则 + 冷却去重 + AI 追踪状态机
telegram_sender.py Telegram 推送（HTML 格式）
web_server.py      仪表盘 HTTP 服务（自动选端口）
dashboard.html     仪表盘页面（亮/暗自适应）
database.py        SQLite 持久化
settings.py        配置装载（config.py 覆盖 config_defaults.py）
```

## systemd 部署

```ini
[Unit]
Description=BTC Signal Monitor
After=network.target

[Service]
WorkingDirectory=/home/ecs-user/projects/valuescan
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
