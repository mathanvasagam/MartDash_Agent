from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import Bill
from app.db.session import session_scope
from app.documents.deck import generate_analysis_deck
from app.documents.invoice import generate_invoice_pdf
from app.domain.analytics import AnalyticsService
from app.domain.billing import BillingService
from app.domain.errors import AmbiguousProductError, DomainError
from app.domain.inventory import InventoryService
from app.domain.khata import KhataService
from app.domain.preferences import PreferenceService


@dataclass
class ToolContext:
    owner_id: str
    chat_id: str
    update_id: int
    pending_files: list[Path] = field(default_factory=list)


def _product(product) -> dict[str, Any]:
    return {
        "id": product.id,
        "sku": product.sku,
        "name": product.name,
        "brand": product.brand,
        "type": product.product_type,
        "unit": product.base_unit,
        "pack_size": str(product.pack_size) if product.pack_size is not None else None,
        "pack_unit": product.pack_unit,
        "cost_price": str(product.cost_price),
        "sell_price": str(product.sell_price),
        "mrp": str(product.mrp) if product.mrp is not None else None,
        "gst_rate": str(product.gst_rate),
        "hsn_code": product.hsn_code,
        "stock_quantity": str(product.stock_quantity),
        "reorder_level": str(product.reorder_level),
    }


def _bill(bill) -> dict[str, Any]:
    return {
        "id": bill.id,
        "status": bill.status,
        "invoice_number": bill.invoice_number,
        "payment_mode": bill.payment_mode,
        "payment_reference": bill.payment_reference,
        "taxable_total": str(bill.taxable_total),
        "tax_total": str(bill.tax_total),
        "grand_total": str(bill.grand_total),
        "items": [
            {
                "product": item.product_name,
                "quantity": str(item.quantity),
                "unit_price": str(item.unit_price),
                "gst_rate": str(item.gst_rate),
                "cgst": str(item.cgst_amount),
                "sgst": str(item.sgst_amount),
                "line_total": str(item.line_total),
            }
            for item in bill.items
        ],
    }


def _active_or_error(service: BillingService, context: ToolContext):
    bill = service.active_draft(context.owner_id, context.chat_id)
    if not bill:
        raise DomainError("No active bill draft. Start a bill first.")
    return bill


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "retrieve_store_context",
            "description": "Retrieve grounded store context for a factual question. Use this for product, price, GST, HSN, availability, inventory, or reorder questions before answering.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 12},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Search the store database for products. Use this before assuming a product, price, GST rate, HSN, or stock quantity.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_product",
            "description": "Add a new SKU after all mandatory commercial and GST fields are known. Never invent missing values.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sku": {"type": "string"},
                    "name": {"type": "string"},
                    "cost_price": {"type": "number"},
                    "sell_price": {"type": "number"},
                    "gst_rate": {"type": "number"},
                    "hsn_code": {"type": "string"},
                    "brand": {"type": "string"},
                    "product_type": {"type": "string", "enum": ["PACKAGED", "LOOSE"]},
                    "base_unit": {"type": "string"},
                    "pack_size": {"type": "number"},
                    "pack_unit": {"type": "string"},
                    "mrp": {"type": "number"},
                    "reorder_level": {"type": "number"},
                },
                "required": ["sku", "name", "cost_price", "sell_price", "gst_rate", "hsn_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "receive_stock",
            "description": "Receive positive stock for an existing SKU and optionally update its cost/MRP from the receipt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string"},
                    "quantity": {"type": "number"},
                    "cost_price": {"type": "number"},
                    "mrp": {"type": "number"},
                    "reference": {"type": "string"},
                },
                "required": ["product", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock",
            "description": "Get grounded stock and price information for one product.",
            "parameters": {
                "type": "object",
                "properties": {"product": {"type": "string"}},
                "required": ["product"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_low_stock",
            "description": "List products at or below their reorder level.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_bill_draft",
            "description": "Create or retrieve the active multi-turn bill draft for this chat. Does not decrement stock.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_bill",
            "description": "Inspect the active bill draft and its current items/totals.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_bill_item",
            "description": "Add an item to the active bill draft. Stock is checked but not decremented until finalization.",
            "parameters": {
                "type": "object",
                "properties": {"product": {"type": "string"}, "quantity": {"type": "number"}},
                "required": ["product", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_bill_item",
            "description": "Set the exact quantity of an existing item in the active bill draft.",
            "parameters": {
                "type": "object",
                "properties": {"product": {"type": "string"}, "quantity": {"type": "number"}},
                "required": ["product", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_bill_item",
            "description": "Remove an item from the active bill draft.",
            "parameters": {
                "type": "object",
                "properties": {"product": {"type": "string"}},
                "required": ["product"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_bill_payment",
            "description": "Set payment mode for the active bill. Modes: CASH, UPI, CARD, KHATA.",
            "parameters": {
                "type": "object",
                "properties": {
                    "payment_mode": {"type": "string", "enum": ["CASH", "UPI", "CARD", "KHATA"]},
                    "payment_reference": {"type": "string"},
                },
                "required": ["payment_mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_bill",
            "description": "Recalculate and return the current draft totals and GST breakup.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_bill",
            "description": "Atomically finalize the active bill and decrement stock. For KHATA, customer_name is mandatory. Call only when the owner explicitly confirms finalization/cutting the bill.",
            "parameters": {
                "type": "object",
                "properties": {"customer_name": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "khata_add_credit",
            "description": "Add an explicit amount to a customer's credit ledger.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string"},
                    "amount": {"type": "number"},
                    "note": {"type": "string"},
                },
                "required": ["customer_name", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "khata_payment",
            "description": "Record a payment against an existing customer khata. Refuses nonexistent or over-settled khata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string"},
                    "amount": {"type": "number"},
                    "note": {"type": "string"},
                },
                "required": ["customer_name", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "khata_balance",
            "description": "Get the outstanding credit balance for an existing customer khata.",
            "parameters": {
                "type": "object",
                "properties": {"customer_name": {"type": "string"}},
                "required": ["customer_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sales_summary",
            "description": "Return finalized sales, GST, payment mix, and top items for the requested recent period.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 365}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "daily_close",
            "description": "Return today's Indian calendar-day close: sales, GST, payment mix, bill count, and top items.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_preference",
            "description": "Persist an owner preference such as default_payment, default_atta, shop_name, shop_address, gstin, or state across /new chats.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_preferences",
            "description": "Read all persistent preferences for this owner.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_invoice_pdf",
            "description": "Generate and attach a clean GST invoice PDF for the latest finalized bill or a specified invoice number.",
            "parameters": {
                "type": "object",
                "properties": {"invoice_number": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_analysis_deck",
            "description": "Generate and attach a PPTX analysis deck with sales, payment mix, top items, GST, and stock health.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 365}},
            },
        },
    },
]


def dispatch_tool(name: str, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    try:
        with session_scope() as session:
            inventory = InventoryService(session)
            billing = BillingService(session)
            khata = KhataService(session)
            prefs = PreferenceService(session)

            if name == "retrieve_store_context":
                context_data = inventory.retrieve_context(
                    args["query"], limit=int(args.get("limit", 8))
                )
                return {
                    "ok": True,
                    "query": args["query"],
                    "products": [_product(p) for p in context_data["products"]],
                    "low_stock": [_product(p) for p in context_data["low_stock"]],
                }
            if name == "search_products":
                return {
                    "ok": True,
                    "products": [_product(p) for p in inventory.search_products(args["query"])],
                }
            if name == "add_product":
                product = inventory.add_product(**args)
                return {"ok": True, "product": _product(product)}
            if name == "receive_stock":
                product = inventory.receive_stock(
                    args["product"],
                    args["quantity"],
                    cost_price=args.get("cost_price"),
                    mrp=args.get("mrp"),
                    reference=args.get("reference"),
                )
                return {"ok": True, "product": _product(product)}
            if name == "get_stock":
                return {"ok": True, "product": _product(inventory.resolve_product(args["product"]))}
            if name == "get_low_stock":
                return {"ok": True, "products": [_product(p) for p in inventory.low_stock()]}
            if name == "create_bill_draft":
                bill = billing.create_draft(context.owner_id, context.chat_id)
                default_payment = prefs.get(context.owner_id, "default_payment")
                if default_payment and not bill.payment_mode:
                    billing.set_payment(bill.id, context.owner_id, default_payment)
                return {"ok": True, "bill": _bill(billing.preview(bill.id, context.owner_id))}
            if name == "get_active_bill":
                bill = billing.active_draft(context.owner_id, context.chat_id)
                return {"ok": True, "bill": _bill(bill) if bill else None}
            if name == "add_bill_item":
                bill = _active_or_error(billing, context)
                return {
                    "ok": True,
                    "bill": _bill(
                        billing.add_item(
                            bill.id, context.owner_id, args["product"], args["quantity"]
                        )
                    ),
                }
            if name == "update_bill_item":
                bill = _active_or_error(billing, context)
                return {
                    "ok": True,
                    "bill": _bill(
                        billing.update_item(
                            bill.id, context.owner_id, args["product"], args["quantity"]
                        )
                    ),
                }
            if name == "remove_bill_item":
                bill = _active_or_error(billing, context)
                return {
                    "ok": True,
                    "bill": _bill(billing.remove_item(bill.id, context.owner_id, args["product"])),
                }
            if name == "set_bill_payment":
                bill = _active_or_error(billing, context)
                updated = billing.set_payment(
                    bill.id, context.owner_id, args["payment_mode"], args.get("payment_reference")
                )
                return {"ok": True, "bill": _bill(billing.preview(updated.id, context.owner_id))}
            if name == "preview_bill":
                bill = _active_or_error(billing, context)
                return {"ok": True, "bill": _bill(billing.preview(bill.id, context.owner_id))}
            if name == "finalize_bill":
                bill = _active_or_error(billing, context)
                finalized = billing.finalize(
                    bill.id,
                    context.owner_id,
                    idempotency_key=f"telegram:{context.update_id}:finalize:{bill.id}",
                    customer_name=args.get("customer_name"),
                )
                return {"ok": True, "bill": _bill(finalized)}
            if name == "khata_add_credit":
                balance = khata.add_credit(
                    context.owner_id, args["customer_name"], args["amount"], args.get("note")
                )
                return {"ok": True, "customer": args["customer_name"], "balance": str(balance)}
            if name == "khata_payment":
                balance = khata.record_payment(
                    context.owner_id, args["customer_name"], args["amount"], args.get("note")
                )
                return {"ok": True, "customer": args["customer_name"], "balance": str(balance)}
            if name == "khata_balance":
                balance = khata.balance(context.owner_id, args["customer_name"])
                return {"ok": True, "customer": args["customer_name"], "balance": str(balance)}
            if name == "sales_summary":
                return {
                    "ok": True,
                    "summary": AnalyticsService(session).summary(int(args.get("days", 1))),
                }
            if name == "daily_close":
                return {"ok": True, "summary": AnalyticsService(session).today_summary()}
            if name == "set_preference":
                pref = prefs.set(context.owner_id, args["key"], args["value"])
                return {"ok": True, "key": pref.key, "value": pref.value}
            if name == "get_preferences":
                return {"ok": True, "preferences": prefs.all(context.owner_id)}
            if name == "generate_invoice_pdf":
                stmt = select(Bill).where(
                    Bill.owner_id == context.owner_id, Bill.status == "FINALIZED"
                )
                if args.get("invoice_number"):
                    stmt = stmt.where(Bill.invoice_number == args["invoice_number"])
                bill = session.scalar(
                    stmt.order_by(Bill.finalized_at.desc()).options(selectinload(Bill.items))
                )
                if not bill:
                    raise DomainError("No matching finalized bill found")
                path = generate_invoice_pdf(
                    session, context.owner_id, bill.id, Path("artifacts/invoices")
                )
                context.pending_files.append(path)
                return {"ok": True, "invoice_number": bill.invoice_number, "file": path.name}
            if name == "generate_analysis_deck":
                days = int(args.get("days", 7))
                path = generate_analysis_deck(
                    session, context.owner_id, days, Path("artifacts/decks")
                )
                context.pending_files.append(path)
                return {"ok": True, "days": days, "file": path.name}
            return {"ok": False, "error": f"Unknown tool '{name}'"}
    except AmbiguousProductError as exc:
        return {
            "ok": False,
            "error_type": "AMBIGUOUS_PRODUCT",
            "message": str(exc),
            "matches": exc.matches,
        }
    except DomainError as exc:
        return {"ok": False, "error_type": exc.__class__.__name__, "message": str(exc)}
    except (ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "error_type": "INVALID_ARGUMENT", "message": str(exc)}
