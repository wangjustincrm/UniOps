"""Writes meta.json: the list /meta bodies the e2e mock serves, built from the code (no database, no network).
Run from the repository root (any time a list's fields or views change):
  docker run --rm --network none -e JWT_SECRET_KEY=placeholder-for-metadata-only -v "$PWD":/repo:ro -w /repo/sales-api \\
    -e PYTHONPATH=/repo/sales-api orders-api-test:p6 python /repo/sales/e2e/fixtures/gen_meta.py > sales/e2e/fixtures/meta.json
"""
import json, logging
logging.disable(logging.CRITICAL)
from app.services.payment_list import PaymentRowService
from app.services.stock import StockItemService, StockLotService
from app.services.order_views import OrderService
out = {}
for k, cls in (("orders", OrderService), ("payments", PaymentRowService), ("inventory/items", StockItemService), ("inventory/lots", StockLotService)):
    repo = cls.repo_cls
    out[k] = {"meta": {"version": cls.META_VERSION, "fields": repo.field_meta(cls.read_schema), "searchable": bool(repo.search_fields),
              "default_sort": list(repo.default_sort), "client_columns": list(repo.client_columns), "views": [p.as_dict() for p in repo.presets]},
              }
print(json.dumps({k: v["meta"] for k, v in out.items()}, default=str, indent=1))
