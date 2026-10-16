from odoo import api, models


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def dac_global_search(self, query="", category="all", limit=8):
        """Search the main operational records while preserving normal ACLs."""
        query = (query or "").strip()
        category = category if category in {"all", "order", "customer", "conversation", "task"} else "all"
        per_type = max(1, min(int(limit or 8), 12))
        results = []

        def add_records(kind, label, icon, model_name, domain, fields, subtitle):
            if category not in ("all", kind) or model_name not in self.env:
                return
            model = self.env[model_name]
            available = [field for field in fields if field in model._fields]
            try:
                records = model.search_read(domain, available, limit=per_type)
            except Exception:
                return
            for record in records:
                title = record.get("display_name") or record.get("name") or f"{label} #{record['id']}"
                results.append({
                    "key": f"{kind}-{record['id']}",
                    "kind": kind,
                    "typeLabel": label,
                    "icon": icon,
                    "title": title,
                    "subtitle": subtitle(record),
                    "model": model_name,
                    "resId": record["id"],
                })

        def text_domain(model_name, candidates):
            model = self.env[model_name]
            fields = [name for name in candidates if name in model._fields]
            if not query or not fields:
                return []
            domain = [(fields[0], "ilike", query)]
            for field in fields[1:]:
                domain = ["|"] + domain + [(field, "ilike", query)]
            return domain

        if "sale.order" in self.env:
            add_records(
                "order", "Đơn hàng", "ĐH", "sale.order",
                text_domain("sale.order", ["name", "order_title", "partner_id.name", "partner_id.phone"]),
                ["display_name", "name", "order_title", "partner_id", "amount_total", "currency_id"],
                lambda r: " · ".join(filter(None, [
                    r.get("order_title"),
                    r.get("partner_id") and r["partner_id"][1],
                ])),
            )

        if "res.partner" in self.env:
            customer_domain = text_domain("res.partner", ["name", "phone", "mobile", "email"])
            if "customer_rank" in self.env["res.partner"]._fields:
                customer_domain = [("customer_rank", ">", 0)] + customer_domain
            add_records(
                "customer", "Khách hàng", "KH", "res.partner", customer_domain,
                ["display_name", "name", "phone", "mobile", "email"],
                lambda r: r.get("phone") or r.get("mobile") or r.get("email") or "Hồ sơ khách hàng",
            )

        if "page.fm.conversation" in self.env:
            add_records(
                "conversation", "Hội thoại", "HT", "page.fm.conversation",
                text_domain("page.fm.conversation", ["name", "customer_name_fm", "phone", "last_message_snippet"]),
                ["display_name", "name", "customer_name_fm", "phone", "last_message_snippet"],
                lambda r: r.get("last_message_snippet") or r.get("phone") or "Hội thoại khách hàng",
            )

        if "dac.work.task" in self.env:
            add_records(
                "task", "Công việc", "CV", "dac.work.task",
                text_domain("dac.work.task", ["name", "description", "order_id.name"]),
                ["display_name", "name", "state", "deadline", "order_id"],
                lambda r: " · ".join(filter(None, [
                    dict(self.env["dac.work.task"]._fields["state"].selection).get(r.get("state")),
                    r.get("order_id") and r["order_id"][1],
                ])),
            )

        return results[: per_type * (4 if category == "all" else 1)]
