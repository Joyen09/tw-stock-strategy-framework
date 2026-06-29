"""名人交易策略集合。"""
from .base import Strategy, StrategyContext
from .buffett import BuffettStrategy
from .graham import GrahamStrategy
from .lynch import LynchStrategy
from .oneil import ONeilStrategy
from .livermore import LivermoreStrategy
from .us_overnight import USOvernightStrategy

# 名稱 -> 策略類別，供 CLI / 設定檔以字串選用。
REGISTRY = {
    "buffett": BuffettStrategy,
    "graham": GrahamStrategy,
    "lynch": LynchStrategy,
    "oneil": ONeilStrategy,
    "livermore": LivermoreStrategy,
    "us_overnight": USOvernightStrategy,
}


def build(name: str, **kwargs) -> Strategy:
    """依名稱建立策略實例。"""
    key = name.lower()
    if key not in REGISTRY:
        raise KeyError(f"未知策略 '{name}'，可選: {', '.join(REGISTRY)}")
    return REGISTRY[key](**kwargs)


__all__ = [
    "Strategy",
    "StrategyContext",
    "BuffettStrategy",
    "GrahamStrategy",
    "LynchStrategy",
    "ONeilStrategy",
    "LivermoreStrategy",
    "USOvernightStrategy",
    "REGISTRY",
    "build",
]
