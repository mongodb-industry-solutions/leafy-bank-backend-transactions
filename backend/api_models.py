"""Pydantic request models for the BIAN PaymentOrderProcedure service domain.

Field names use camelCase alias names (matching Mongo storage). The registry
handles BIAN documentation mapping; no wire translation is done at request time.

Inner record types use a `Body` suffix to avoid Pydantic forward-ref shadow bugs
when the parent model declares an Optional field with the same name as the class.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class PaymentDebtorBody(BaseModel):
    accountId: str
    model_config = ConfigDict(extra="forbid")


class PaymentCreditorBody(BaseModel):
    accountId: str
    model_config = ConfigDict(extra="forbid")


class PaymentRemittanceBody(BaseModel):
    unstructured: Optional[str] = None
    model_config = ConfigDict(extra="forbid")


class PaymentOrderInitiateRequest(BaseModel):
    customerId: str = Field(min_length=1)
    type: Literal[
        "CREDIT_TRANSFER", "DIRECT_DEBIT", "CARD_PAYMENT", "CHEQUE", "INTRABANK_TRANSFER"
    ]
    rail: Literal["INTERNAL"]  # Phase 1 supports INTERNAL only
    debtor: PaymentDebtorBody
    creditor: PaymentCreditorBody
    instructedAmount: float = Field(gt=0)
    instructedCurrency: str = Field(min_length=3, max_length=3)
    remittance: Optional[PaymentRemittanceBody] = None
    model_config = ConfigDict(extra="forbid")


class PaymentOrderRetrieveRequest(BaseModel):
    paymentId: str = Field(min_length=1)
    model_config = ConfigDict(extra="forbid")
