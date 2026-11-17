import hashlib
import json

from .audit import write
from .catalog import Catalog, CorrectionError
from .grants import Grants
from .policy import Policies
from .products import Products


class Delivery:
    def __init__(self, connection):
        self.connection = connection
        self.catalog = Catalog(connection)
        self.grants = Grants(connection)
        self.policies = Policies(connection)
        self.products = Products(connection)

    def send(self, delivery_id, product_id, revision, recipient, purpose, at):
        existing = self.connection.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
        if existing:
            if (existing["product_id"], existing["product_revision"], existing["recipient"], existing["purpose"]) != (product_id, revision, recipient, purpose):
                raise ValueError("交付编号已用于其他请求")
            return dict(existing)
        product = self.connection.execute("SELECT state FROM products WHERE id=? AND revision=?", (product_id, revision)).fetchone()
        if product is None or product["state"] != "draft":
            raise ValueError("数据产品修订不可交付")
        inputs = self.products.inputs(product_id, revision)
        if not inputs:
            raise ValueError("数据产品缺少输入")
        reasons, revisions = [], {}
        for asset in inputs:
            source_id = asset["source_id"]
            if not asset["source_active"]:
                reasons.append({"source": source_id, "reason": "inactive"})
            # 输入必须仍是当前有效版本；被更正或更正链异常的旧编号不得再交付
            try:
                head = self.catalog.effective(asset["id"])
            except CorrectionError as exc:
                reasons.append({"source": source_id, "asset": asset["id"], "reason": "unresolved", "detail": str(exc)})
            else:
                if head != asset["id"]:
                    reasons.append({"source": source_id, "asset": asset["id"], "reason": "superseded", "effective": head})
            if not self.grants.permits(source_id, recipient, purpose, at):
                reasons.append({"source": source_id, "reason": "grant"})
            decision, policy_revision = self.policies.decision(asset["jurisdiction"], asset["classification"], purpose)
            revisions[source_id] = policy_revision
            if decision != "allow":
                reasons.append({"source": source_id, "reason": "policy"})
        if reasons:
            with self.connection:
                write(self.connection, at, recipient, "delivery.reject", product_id, {"revision": revision, "reasons": reasons})
            raise PermissionError(reasons)
        digest = hashlib.sha256("|".join(asset["digest"] for asset in inputs).encode()).hexdigest()
        with self.connection:
            self.connection.execute(
                "INSERT INTO deliveries(id,product_id,product_revision,recipient,purpose,created_at,input_digest,policy_revisions) VALUES(?,?,?,?,?,?,?,?)",
                (delivery_id, product_id, revision, recipient, purpose, at, digest, json.dumps(revisions, sort_keys=True)),
            )
            write(self.connection, at, recipient, "delivery.accept", delivery_id, {"product": product_id, "revision": revision, "inputs": [a["id"] for a in inputs]})
        return dict(self.connection.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone())

    def receipt(self, delivery_id):
        # 凭据保持原始输入不变，逐项标示交付后是否被更正、当前有效版本指向何处
        row = self.connection.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
        if row is None:
            raise KeyError(delivery_id)
        inputs = []
        for asset in self.products.inputs(row["product_id"], row["product_revision"]):
            try:
                head = self.catalog.effective(asset["id"])
                issue = None
            except CorrectionError as exc:
                head, issue = None, str(exc)
            inputs.append({
                "asset": asset["id"],
                "requested": asset["requested_asset_id"],
                "digest": asset["digest"],
                "state": asset["state"],
                "corrects": asset["corrects"],
                "effective": head,
                "later_corrected": head is not None and head != asset["id"],
                "issue": issue,
            })
        return {"delivery": dict(row), "inputs": inputs}
