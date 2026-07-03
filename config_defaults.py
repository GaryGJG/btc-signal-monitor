"""默认配置（勿改）。自定义请复制 config.example.py 为 config.py 并修改。"""

# ==================== Telegram ====================
TELEGRAM_BOT_TOKEN = ""      # @BotFather 获取
TELEGRAM_CHAT_ID = ""        # @userinfobot 获取；频道用 -100 开头的 ID

# ==================== 数据源 ====================
SYMBOL = "BTCUSDT"
SPOT_BASE = "https://api.binance.com"
FAPI_BASE = "https://fapi.binance.com"
FNG_URL = "https://api.alternative.me/fng/?limit=1"

POLL_INTERVAL = 60           # 主循环周期（秒）
LARGE_TRADE_USD = 200_000    # 大额交易阈值（美元）：aggTrades 按秒内同方向聚合后的订单额
WHALE_COST_WINDOW_DAYS = 30  # 主力成本计算窗口

# ==================== 信号阈值 ====================
FOMO_VOLUME_MULT = 4.0       # 5 分钟成交额 >= N 倍均值
FOMO_PRICE_CHANGE = 0.5      # 且 5 分钟涨跌幅 >= N%
FOMO_ESCALATION_1H = 2.0     # FOMO 加剧：1 小时涨幅 >= N%
NETFLOW_SIGMA = 3.0          # 资金异动：1 小时净流入 z-score 阈值
ALPHA_HOURS = 4              # Alpha：连续 N 个滚动小时净流入为正
ALPHA_MIN_USD = 50_000_000   # 且期间累计净流入 >= N 美元
WHALE_FLOW_USD_4H = 30_000_000  # 主力增持/减持：4 小时大单净流入绝对阈值（美元）

# AI 追踪（与 ValueScan 的止盈/止损提示对应）
TRACK_TAKE_PROFIT = 10.0     # 涨幅 >= N% 上涨止盈
TRACK_DRAWDOWN = 5.0         # 冲高回落 >= N% 移动止盈
TRACK_PROTECT = 8.0          # 跌幅 >= N% 保护本金
TRACK_STOP = 15.0            # 跌幅 >= N% 下跌止盈
TRACK_MAX_HOURS = 48         # 追踪超时自动结束

SIGNAL_COOLDOWN_MIN = {      # 每类信号的冷却时间（分钟）
    "FOMO": 30,
    "FOMO_ESCALATION": 60,
    "FUNDS_MOVEMENT": 120,
    "ALPHA": 240,
    "WHALE_INCREASE": 120,
    "WHALE_REDUCE": 120,
    "WHALE_ACCELERATE": 240,
    "CAPITAL_EXODUS": 240,
    "DOWNSIDE_RISK": 240,
    "TREND_FLIP": 0,
}

# ==================== Web ====================
WEB_PORT = None              # None = 自动在 PORT_RANGE 里找空闲端口，跳过已占用端口
WEB_PORT_RANGE = (8600, 8700)
WEB_BIND = "0.0.0.0"

# ==================== 其他 ====================
DB_FILE = "btc_signals.db"
LOG_FILE = "btc_signals.log"
LOG_LEVEL = "INFO"
