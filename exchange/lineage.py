from .catalog import Catalog


class Lineage:
    """沿记录、产品、交付追溯影响范围。"""

    def __init__(self, connection):
        self.connection = connection
        self.catalog = Catalog(connection)

    def impact(self, asset_id):
        asset = self.catalog.get(asset_id)
        chain = self.catalog.chain(asset_id)
        products = []
        for row in self.connection.execute(
            "SELECT p.id,p.revision,p.name,p.state,i.asset_id,i.requested_asset_id FROM product_inputs i "
            "JOIN products p ON p.id=i.product_id AND p.revision=i.product_revision "
            "WHERE i.asset_id=? OR i.requested_asset_id=? ORDER BY p.id,p.revision",
            (asset_id, asset_id),
        ):
            products.append({
                "product_id": row["id"],
                "revision": row["revision"],
                "name": row["name"],
                "state": row["state"],
                "role": "input" if row["asset_id"] == asset_id else "requested",
            })
        deliveries = []
        for product in products:
            for row in self.connection.execute(
                "SELECT * FROM deliveries WHERE product_id=? AND product_revision=? ORDER BY created_at,id",
                (product["product_id"], product["revision"]),
            ):
                deliveries.append(dict(row))
        return {"asset": asset, "chain": chain, "products": products, "deliveries": deliveries}
