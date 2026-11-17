from .audit import history
from .catalog import Catalog
from .db import connect
from .delivery import Delivery
from .grants import Grants
from .lineage import Lineage
from .policy import Policies
from .products import Products


class Exchange:
    def __init__(self, database):
        self.connection = connect(database)
        self.catalog = Catalog(self.connection)
        self.grants = Grants(self.connection)
        self.policies = Policies(self.connection)
        self.products = Products(self.connection)
        self.delivery = Delivery(self.connection)
        self.lineage = Lineage(self.connection)

    def impact(self, asset_id):
        return self.lineage.impact(asset_id)

    def history(self, subject):
        return history(self.connection, subject)

    def close(self):
        self.connection.close()
