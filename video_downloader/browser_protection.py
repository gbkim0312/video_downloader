from __future__ import annotations

from .adapters.browser.protection import (
    AD_POPUP_HINTS,
    install_popup_protection,
    looks_like_ad_popup_url,
)

__all__ = ["AD_POPUP_HINTS", "install_popup_protection", "looks_like_ad_popup_url"]
