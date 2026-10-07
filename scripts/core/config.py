"""核心配置：scripts/config.json（输出目录 / whisper / 重试 / auth / vision）"""
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

DEFAULT_CONFIG = {
    "out_dir": "",              # 报告输出根目录（硬性：首次使用必须由用户自选；不作默认值）
    "whisper_model": "medium",  # 默认 medium；可降级 tiny/small 或升级 large-v3
    "whisper_vad": "auto",      # auto=长视频默认关、短视频开；true/false 强制
    "vad_long_sec": 1800,       # “长视频”阈值（秒）：≥ 该值时默认禁用 VAD
    "api_retries": 4,           # 接口限流/风控重试次数（指数退避）
    "api_retry_base": 1.5,      # 退避基数（秒）
    "hf_endpoint": "",          # HF 镜像（如 https://hf-mirror.com），空则用官方
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
        "subtitle_check": "auto",  # 字幕归属复检：auto=防线初筛 / always=全量 / off=关闭
        "prompt": ""            # 自定义视觉转写提示词（空则用默认）
    },
}


class ConfigError(Exception):
    """配置缺失（如 out_dir 未设置）"""


def load_config() -> dict:
    """读取配置；文件不存在返回默认。缺失字段用默认值补齐"""
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user = json.load(f)
            cfg.update({k: v for k, v in user.items() if k in DEFAULT_CONFIG})
            # vision 块：缺失字段用默认补齐
            if isinstance(cfg.get("vision"), dict):
                base = dict(DEFAULT_CONFIG["vision"])
                base.update({k: v for k, v in cfg["vision"].items() if k in base})
                cfg["vision"] = base
        except Exception:
            pass  # 配置损坏时静默用默认
    return cfg


def ensure_config() -> str:
    """不存在时写入默认配置，返回路径"""
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
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
