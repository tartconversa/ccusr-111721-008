from .audit import write
from .catalog import Catalog


class Products:
    def __init__(self, connection):
        self.connection = connection
        self.catalog = Catalog(connection)

    def assemble(self, product_id, name, asset_ids, at, actor):
        requested = list(dict.fromkeys(asset_ids))
        if not requested:
            raise ValueError("数据产品缺少输入")
        # 编排一律转向各输入的当前有效版本；更正链异常时在此失败而不是静默沿用旧编号
        resolution = {}
        for asset_id in requested:
            try:
                resolution[asset_id] = self.catalog.effective(asset_id)
            except KeyError:
                raise ValueError("输入记录不可用: " + asset_id) from None
        resolved = list(dict.fromkeys(resolution.values()))
        revision = self.connection.execute("SELECT COALESCE(MAX(revision),0)+1 FROM products WHERE id=?", (product_id,)).fetchone()[0]
        with self.connection:
            self.connection.execute("INSERT INTO products(id,revision,name,created_at) VALUES(?,?,?,?)", (product_id, revision, name, at))
            self.connection.executemany(
                "INSERT INTO product_inputs(product_id,product_revision,asset_id,requested_asset_id) VALUES(?,?,?,?)",
                [
                    (product_id, revision, head, next(asset_id for asset_id in requested if resolution[asset_id] == head))
                    for head in resolved
                ],
            )
            write(self.connection, at, actor, "product.assemble", product_id, {"revision": revision, "inputs": resolved, "resolution": resolution})
        return revision

    def inputs(self, product_id, revision):
        rows = self.connection.execute(
            "SELECT a.*,s.owner,s.jurisdiction,s.classification,s.active AS source_active,p.requested_asset_id FROM product_inputs p "
            "JOIN assets a ON a.id=p.asset_id JOIN sources s ON s.id=a.source_id "
            "WHERE p.product_id=? AND p.product_revision=? ORDER BY a.id",
            (product_id, revision),
        ).fetchall()
        return [dict(row) for row in rows]
