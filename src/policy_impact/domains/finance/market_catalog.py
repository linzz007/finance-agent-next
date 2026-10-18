"""Shared instrument identity, not provider-specific daily candle certification."""

from __future__ import annotations

from copy import deepcopy


INSTRUMENTS = {
    "sh000001": {"name": "\u4e0a\u8bc1\u6307\u6570", "aliases": ["\u4e0a\u8bc1", "\u6caa\u6307", "SSE Composite"],
                 "asset_class": "equity_index", "unit": "\u70b9", "timezone": "Asia/Shanghai",
                 "price_basis": "price_index", "calendar": "XSHG"},
    "sh000300": {"name": "\u6caa\u6df1300", "aliases": ["\u6caa\u6df1 300", "CSI 300"],
                 "asset_class": "equity_index", "unit": "\u70b9", "timezone": "Asia/Shanghai",
                 "price_basis": "price_index", "calendar": "XSHG"},
    "sz399006": {"name": "\u521b\u4e1a\u677f\u6307", "aliases": ["\u521b\u4e1a\u677f", "ChiNext"],
                 "asset_class": "equity_index", "unit": "\u70b9", "timezone": "Asia/Shanghai",
                 "price_basis": "price_index", "calendar": "XSHG"},
    "sz399001": {"name": "\u6df1\u8bc1\u6210\u6307", "aliases": ["\u6df1\u6210\u6307", "SZSE Component"],
                 "asset_class": "equity_index", "unit": "\u70b9", "timezone": "Asia/Shanghai",
                 "price_basis": "price_index", "calendar": "XSHG"},
    "sh000016": {"name": "\u4e0a\u8bc150", "aliases": ["\u4e0a\u8bc1 50", "SSE 50"],
                 "asset_class": "equity_index", "unit": "\u70b9", "timezone": "Asia/Shanghai",
                 "price_basis": "price_index", "calendar": "XSHG"},
    "sh000688": {"name": "\u79d1\u521b50", "aliases": ["\u79d1\u521b 50", "STAR 50"],
                 "asset_class": "equity_index", "unit": "\u70b9", "timezone": "Asia/Shanghai",
                 "price_basis": "price_index", "calendar": "XSHG"},
    "spx": {"name": "\u6807\u666e500\u6307\u6570", "aliases": ["\u6807\u666e500", "\u6807\u666e", "S&P 500", "SPX", "^GSPC"],
            "asset_class": "equity_index", "unit": "\u70b9", "timezone": "America/New_York",
            "price_basis": "price_index", "calendar": "XNYS"},
    "ixic": {"name": "\u7eb3\u65af\u8fbe\u514b\u7efc\u5408\u6307\u6570", "aliases": ["\u7eb3\u6307", "\u7eb3\u65af\u8fbe\u514b", "NASDAQ Composite", "IXIC", "^IXIC"],
             "asset_class": "equity_index", "unit": "\u70b9", "timezone": "America/New_York",
             "price_basis": "price_index", "calendar": "XNYS"},
    "dji": {"name": "\u9053\u743c\u65af\u5de5\u4e1a\u5e73\u5747\u6307\u6570", "aliases": ["\u9053\u6307", "\u9053\u743c\u65af", "Dow Jones", "DJI", "^DJI"],
            "asset_class": "equity_index", "unit": "\u70b9", "timezone": "America/New_York",
            "price_basis": "price_index", "calendar": "XNYS"},
    "xauusd": {"name": "\u56fd\u9645\u73b0\u8d27\u9ec4\u91d1", "aliases": ["\u9ec4\u91d1", "\u73b0\u8d27\u9ec4\u91d1", "XAUUSD", "XAU/USD", "spot gold"],
               "asset_class": "spot_metal", "unit": "USD/troy_oz", "timezone": "UTC",
               "price_basis": "spot_price", "calendar": None},
    "xagusd": {"name": "\u56fd\u9645\u73b0\u8d27\u767d\u94f6", "aliases": ["\u767d\u94f6", "\u73b0\u8d27\u767d\u94f6", "XAGUSD", "XAG/USD", "spot silver"],
               "asset_class": "spot_metal", "unit": "USD/troy_oz", "timezone": "UTC",
               "price_basis": "spot_price", "calendar": None},
}

for _symbol, _metadata in INSTRUMENTS.items():
    _metadata["quote_currency"] = "CNY" if _symbol.startswith(("sh", "sz")) else "USD"
    _metadata["enabled"] = True
    _metadata["availability_reason"] = ("verified_public_daily_source" if _metadata["asset_class"] != "spot_metal"
                                        else "public_spot_reference_hlc_requires_websockets_and_complete_utc_samples")


def get_symbol_metadata(symbol: str) -> dict:
    """Return a defensive copy for an exact canonical symbol; never guess an instrument.

    Spot UTC is our complete sampling-day reference, never a provider official close.
    Missing samples fail verification; there is no global exchange close for OTC metals.
    XNYS is the shared regular-session calendar for these US indices.
    """
    if symbol not in INSTRUMENTS:
        raise ValueError(f"unsupported finance symbol: {symbol}")
    return deepcopy(INSTRUMENTS[symbol])
