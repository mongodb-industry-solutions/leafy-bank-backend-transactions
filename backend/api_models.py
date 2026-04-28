"""Pydantic request models for the BIAN PaymentOrderProcedure service domain.

Field names are BIAN canonical (PascalCase). The runtime registry handles translation
to camelCase Mongo storage keys — these models exist purely for boundary validation,
IDE autocomplete, and OpenAPI request schemas.

Drift between these models and `bian-alias-map.json` is a real risk. Verify periodically.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# Inner record types use a `Body` suffix to avoid shadowing the BIAN field names
# when the parent model declares `PaymentDebtorRecord: PaymentDebtorRecord`. With
# Optional wrappers this name collision causes Pydantic to collapse the field type
# to None — see the 2026-04-28 debug session in BIAN_MIGRATION.md.
class PaymentDebtorRecordBody(BaseModel):
    DebtorAccountReference: str
    model_config = ConfigDict(extra="forbid")


class PaymentCreditorRecordBody(BaseModel):
    CreditorAccountReference: str
    model_config = ConfigDict(extra="forbid")


class PaymentRemittanceRecordBody(BaseModel):
    RemittanceUnstructuredInformationText: Optional[str] = None
    model_config = ConfigDict(extra="forbid")


class PaymentOrderInitiateRequest(BaseModel):
    CustomerReference: str = Field(min_length=1)
    PaymentType: Literal[
        "CREDIT_TRANSFER", "DIRECT_DEBIT", "CARD_PAYMENT", "CHEQUE", "INTRABANK_TRANSFER"
    ]
    PaymentRailType: Literal["INTERNAL"]  # Phase 1 supports INTERNAL only
    PaymentDebtorRecord: PaymentDebtorRecordBody
    PaymentCreditorRecord: PaymentCreditorRecordBody
    PaymentInstructedAmount: float = Field(gt=0)
    PaymentInstructedCurrencyCode: str = Field(min_length=3, max_length=3)
    PaymentRemittanceRecord: Optional[PaymentRemittanceRecordBody] = None
    model_config = ConfigDict(extra="forbid")


class PaymentOrderRetrieveRequest(BaseModel):
    PaymentOrderReference: str = Field(min_length=1)
    model_config = ConfigDict(extra="forbid")
