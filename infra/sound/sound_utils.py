#!/usr/bin/env python3
"""
Sound resolution helper for tools/ scripts.

Usage:
    from sound_utils import resolve_sound, play_sound, get_sound

    path = get_sound("heartbeat_sound", project_root)  # 自动按 profile 选
    play_sound(path)

Sound resolution order:
  1. Starts with "/" → absolute path
  2. Exists in tools/sounds/ → custom sound
  3. Matches /System/Library/Sounds/<name>.aiff → system sound
  4. Fallback → Glass
"""
import os
import subprocess
from pathlib import Path

SYSTEM_SOUNDS_DIR = Path("/System/Library/Sounds")
FALLBACK_SOUND = "Glass"


def resolve_sound(name: str, project_root: str = "") -> str:
    """
    解析声音名称为文件路径。

    Args:
        name: 声音名称（"Glass"、"done.mp3"、"/abs/path"）
        project_root: workyb 项目根路径，用于定位 tools/sounds/

    Returns:
        解析后的绝对路径字符串
    """
    if not name:
        name = FALLBACK_SOUND

    # 1. 绝对路径
    if name.startswith("/"):
        if os.path.isfile(name):
            return name
        return _fallback()

    # 2. 自定义声音目录
    if project_root:
        custom = Path(project_root) / "tools" / "sounds" / name
        if custom.is_file():
            return str(custom)

    # 3. 系统声音（尝试多种扩展名）
    for ext in [".aiff", ".wav", ".mp3", ".m4a", ".aac"]:
        sys_path = SYSTEM_SOUNDS_DIR / f"{name}{ext}"
        if sys_path.is_file():
            return str(sys_path)

    # 4. Fallback: Glass
    return _fallback()


def _fallback() -> str:
    """返回默认系统声音"""
    return str(SYSTEM_SOUNDS_DIR / f"{FALLBACK_SOUND}.aiff")


def play_sound(sound_path: str) -> bool:
    """
    播放声音文件。afplay 支持 AIFF / WAV / MP3 / AAC / M4A 等格式。

    Returns:
        True 如果播放成功，False 否则
    """
    try:
        subprocess.run(["afplay", sound_path], capture_output=True)
        return True
    except Exception:
        return False


def load_config(project_root: str) -> dict:
    """
    加载 tools/tools_config.yaml。

    Returns:
        解析后的配置字典（失败返回空 dict）
    """
    import yaml
    config_path = Path(project_root) / "tools" / "tools_config.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (FileNotFoundError, yaml.YAMLError):
        return {}


def get_sound(key: str, project_root: str, config=None):  # -> str
    """
    按 profile 自动选择声音。

    流程：
      1. 如果 tools_config.yaml 中的字段显式指定了声音（非空），直接用
      2. 否则读取 profile 字段，从 profiles.<profile>.<key> 取

    Args:
        key: 配置中的声音键名，如 "heartbeat_sound", "auto_mode_sound"
        project_root: workyb 项目根路径
        config: 已加载的配置（可选，避免重复加载）

    Returns:
        解析后的声音文件路径
    """
    if config is None:
        config = load_config(project_root)

    # 1. 先看对应工具段是否有显式指定
    #    heartbeat_monitor.sound 或 dialog_watcher.auto_mode_sound
    for section in ["heartbeat_monitor", "dialog_watcher"]:
        sec = config.get(section, {})
        for k, v in sec.items():
            if k in ("sound", "auto_mode_sound", "crash_sound") and v:
                return resolve_sound(str(v), project_root)

    # 2. 没有显式指定，按 profile 选
    profile_name = config.get("profile", "public")
    profiles = config.get("profiles", {})
    profile = profiles.get(profile_name, {})
    sound_name = str(profile.get(key, FALLBACK_SOUND))

    return resolve_sound(sound_name, project_root)
