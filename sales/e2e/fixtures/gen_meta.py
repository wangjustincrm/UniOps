"""Writes meta.json: the list /meta bodies (and the status help texts) the e2e mock serves, built from the code (no database, no network).
Run from the repository root (any time a list's fields or views change):
  docker run --rm --network none -e JWT_SECRET_KEY=placeholder-for-metadata-only -v "$PWD":/repo:ro -w /repo/sales-api \\
    -e PYTHONPATH=/repo/sales-api orders-api-test:p6 python /repo/sales/e2e/fixtures/gen_meta.py > sales/e2e/fixtures/meta.json
"""
import json, logging
logging.disable(logging.CRITICAL)
from app.services.payment_list import PaymentRowService
from app.services.stock import StockItemService, StockLotService
from app.services.order_views import OrderService
from app.services.lots import LotService
from app.services.items import ItemService
from app.domain.status_help import payload as status_help
from app.platform.relative_dates import as_meta as relative_dates
out = {}
for k, cls in (("orders", OrderService), ("payments", PaymentRowService), ("inventory/items", StockItemService), ("inventory/lots", StockLotService),
               ("lots", LotService), ("config/items", ItemService)):
    repo = cls.repo_cls
    out[k] = {"meta": {"version": cls.META_VERSION, "fields": repo.field_meta(cls.read_schema), "searchable": bool(repo.search_fields),
              "default_sort": list(repo.default_sort), "client_columns": list(repo.client_columns), "views": [p.as_dict() for p in repo.presets],
              "groups": repo.group_labels(), "relative_dates": relative_dates(),
              # the e2e person may read every record type its fields (and its detail) name
              "refs": {t: {"readable": True} for t in sorted({r.type for r in repo.refs.values()} | set(repo.detail_refs))}},
              }
print(json.dumps({**{k: v["meta"] for k, v in out.items()}, "help/texts": status_help()}, default=str, indent=1))
