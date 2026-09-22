from typing import Dict, Type
from .base import HistoricalDataProvider
from .breeze_data_provider import BreezeHistoricalDataProvider
from .angel_data_provider import AngelHistoricalDataProvider
from .yfinance_data_provider import YFinanceDataProvider

class ProviderFactory:
    """Factory to retrieve the appropriate data provider."""
    
    _providers: Dict[str, Type[HistoricalDataProvider]] = {
        "breeze": BreezeHistoricalDataProvider,
        "angel": AngelHistoricalDataProvider,
        "yfinance": YFinanceDataProvider,
    }

    @classmethod
    def get(cls, provider_name: str, **kwargs) -> HistoricalDataProvider:
        provider_cls = cls._providers.get(provider_name.lower())
        if not provider_cls:
            raise ValueError(f"Unsupported data provider: {provider_name}. Supported: {list(cls._providers.keys())}")
        
        return provider_cls(**kwargs)
