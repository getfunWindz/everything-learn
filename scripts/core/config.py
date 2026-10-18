"""核心配置：scripts/config.json（输出目录 / whisper / 重试 / auth / vision）"""
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

DEFAULT_CONFIG = {
    "out_dir": "",              # 报告输出根目录（硬性：首次使用必须由用户自选；不作默认值）
    "onboarded": False,        # 首次引导门：True 后不再触发引导流程（el.py onboard --complete 置位）
    "kb": {"enabled": False, "path": ""},  # 内建知识库（el.py setup --kb / kb build）
    "whisper_model": "small",  # 出厂默认 fast 档（small）；引导建议升 medium（实机最佳）；可升级 large-v3
    "whisper_vad": "auto",      # auto=长视频默认关、短视频开；true/false 强制
    "vad_long_sec": 1800,       # “长视频”阈值（秒）：≥ 该值时默认禁用 VAD
    "api_retries": 4,           # 接口限流/风控重试次数（指数退避）
    "api_retry_base": 1.5,      # 退避基数（秒）
    "rate_limits": {            # 反爬缓速：每平台请求最小间隔（秒）+ 抖动；0=关闭；风险高的调大
        "zhihu": {"min_interval": 4.0, "jitter": 1.5},
        "douyin": {"min_interval": 4.0, "jitter": 1.5},
        "weibo": {"min_interval": 6.0, "jitter": 1.5},
        "wechat_mp": {"min_interval": 6.0, "jitter": 1.5},
        "x_twitter": {"min_interval": 10.0, "jitter": 2.0},
        "xiaohongshu": {"min_interval": 12.0, "jitter": 2.0},
        "medium": {"min_interval": 10.0, "jitter": 2.0},
        "bilibili": {"min_interval": 1.5, "jitter": 0.5},
        "youtube": {"min_interval": 1.5, "jitter": 0.5}
    },
    "hf_endpoint": "",          # HF 镜像（如 https://hf-mirror.com），空则用官方
    "browser_proxy": "",       # 浏览器代理（如 http://127.0.0.1:7897；X 等平台必需）；也可用环境变量 EL_BROWSER_PROXY
    "hf_disable_xet": True,     # 禁用 HF xet 协议（镜像站必需）
    "auth": {},                 # 各平台凭据：{"bilibili": {...}, "zhihu": {...}}（按需）
    "vision": {                 # 多模态大模型 API（OpenAI 兼容）
        "enabled": False,       # 是否启用（字幕归属复检 / 内容完整性复检）
        "base_url": "",         # OpenAI 兼容端点，如 https://api.openai.com/v1
        "api_key": "",          # API 密钥（config.json 已被 gitignore，不会泄露）
        "model": "gpt-4o-mini", # 多模态模型名（如 qwen-vl-max / gemini-2.0-flash）
        "frame_interval": 10,   # 抽帧间隔（秒）
        "max_frames": 30,       # 最多帧数
        "max_rounds": 2,        # 复检最大轮数（一轮效果不佳可继续多轮）
        "subtitle_check": "auto",  # 兼容旧键（新策略见 recheck.tier_policy）
        "prompt": "",
        "pricing": {"currency": "USD", "input_per_1m": 0.3,
                    "output_per_1m": 1.2, "tokens_per_frame": 600},
    },
    "recheck": {                    # M13：保真复检（档位/区域/预算/同源）
        "tier_policy": "auto",     # auto | off(=档3) | tier1 | tier2
        "tier2_score": 0.45,
        "region_top_n": 5,
        "region_window_sec": 60,
        "gap_sec": 20,
        "frame_fps": 3,
        "window_sec": 30,
        "max_check_frames": 20000,
        "dedup": True,
        "auto_approve_amount": 0.3,
        "confirm_over_sec": 3600,
        "same_source_threshold": 0.45,
        "same_source_model": "small",
        "cross_check": True,
        "cross_window_sec": 15,    # T14.5 双 ASR 交叉比对窗宽（秒）
        "cross_threshold": 0.65,   # T14.5 逐窗归一化相似度 >= 此值记 agree
        "tier3": {"allow": False, "max_density": 1.5, "max_gap_sec": 15},
    },
}


class ConfigError(Exception):
    """配置缺失（如 out_dir 未设置）"""


def _deep_update(base: dict, user: dict) -> dict:
    """用户配置覆盖默认：dict 递归合并，其余类型直接覆盖"""
    out = dict(base)
    for k, v in (user or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_update(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    """读取配置；文件不存在返回默认。缺失字段用默认值补齐"""
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user = json.load(f)
            cfg = _deep_update(dict(DEFAULT_CONFIG), user)
        except Exception:
            pass  # 配置损坏时静默用默认
    return cfg


def ensure_config() -> str:
    """不存在时写入默认配置，返回路径"""
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    return CONFIG_PATH


def write_user_config(updates: dict) -> str:
    """原子合并写 config.json（只改指定键；损坏时以空基底重建）"""
    raw = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                raw = json.load(f) or {}
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw.update(updates or {})
    from core import fsutil
    fsutil.atomic_write_text(CONFIG_PATH, json.dumps(raw, ensure_ascii=False, indent=2))
    return CONFIG_PATH


def resolve_out_dir(cli_out: str = None) -> str:
    """CLI 参数 > config.out_dir > 报错（硬性要求用户自选报告根路径）"""
    if cli_out:
        return cli_out
    out = load_config().get("out_dir") or ""
    if not out:
        raise ConfigError(
            "未配置报告输出目录。请先选择存放学习报告的根文件夹（建议新建「学习笔记」总文件夹），"
            "写入 scripts/config.json 的 out_dir，或运行时用 --out 指定。")
    return out


def apply_hf_env(cfg: dict) -> None:
    """按配置设置 HF 环境变量（setdefault：不覆盖用户已有环境变量）"""
    if cfg.get("hf_endpoint"):
        os.environ.setdefault("HF_ENDPOINT", cfg["hf_endpoint"])
    if cfg.get("hf_disable_xet"):
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
