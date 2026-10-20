from typing import Literal
from pydantic import BaseModel, Field, field_validator


class StockRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9.^=\-]+$")
    buy_or_sell: Literal["buy", "sell"]
    target_price: float = Field(gt=0, allow_inf_nan=False)
    entry_price: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("ticker", "buy_or_sell", mode="before")
    @classmethod
    def normalize_text(cls, value, info):
        if not isinstance(value, str):
            return value
        value = value.strip()
        return value.upper() if info.field_name == "ticker" else value.lower()
