from pydantic import BaseModel, Field
from typing import Optional


class CreateOrderRequest(BaseModel):
    buyer_id: str
    seller_id: str
    chatId: Optional[str] = None
    item_id: Optional[str] = None
    amount: float
    currency: Optional[str] = "KRW"


class CreateOrderResponse(BaseModel):
    order_id: str
    amount: float
    currency: str
    payment_reference: Optional[str] = None
    checkout_url: Optional[str] = None
    provider_token: Optional[str] = None
    message_id: Optional[str] = None


class ReconcileTransaction(BaseModel):
    tx_id: str
    amount: float
    description: Optional[str] = None
    payer_name: Optional[str] = None


class ReconcileResult(BaseModel):
    matched_order_id: Optional[str] = None
    matched: bool = False
    reason: Optional[str] = None


class UploadProofResponse(BaseModel):
    ok: bool
    order_id: Optional[str] = None


class DepositReqItem(BaseModel):
    tran_no: str
    bank_tran_id: Optional[str] = None
    fintech_use_num: Optional[str] = None
    account_num: Optional[str] = None
    print_content: Optional[str] = None
    tran_amt: str
    req_client_name: str
    req_client_bank_code: Optional[str] = None
    req_client_account_num: Optional[str] = None
    req_client_fintech_use_num: Optional[str] = None
    req_client_num: str
    transfer_purpose: str = "TR"


class DepositRequest(BaseModel):
    cntr_account_type: str = "N"
    cntr_account_num: str
    wd_pass_phrase: str
    wd_print_content: Optional[str] = "이체"
    name_check_option: Optional[str] = "on"
    tran_dtime: Optional[str] = None
    req_cnt: str = "1"
    req_list: list[DepositReqItem]


class WebhookEvent(BaseModel):
    provider: str
    event_type: str
    provider_payment_id: str
    order_id: Optional[str] = None
    data: Optional[dict] = None
