import hashlib
import json

from .audit import write


class CorrectionError(ValueError):
    """更正链无法给出可信输入。"""


class NoEffectiveVersion(CorrectionError):
    """记录已失效且没有可用的更正版本。"""


class CorrectionFork(CorrectionError):
    """更正链分叉，存在多个候选有效版本。"""


class Catalog:
    def __init__(self, connection):
        self.connection = connection

    def register(self, source_id, owner, jurisdiction, classification, fields, at):
        if not fields or len({field[0] for field in fields}) != len(fields):
            raise ValueError("来源字段缺失或重复")
        with self.connection:
            self.connection.execute(
                "INSERT INTO sources(id,owner,jurisdiction,classification) VALUES(?,?,?,?)",
                (source_id, owner, jurisdiction, classification),
            )
            self.connection.executemany(
                "INSERT INTO fields(source_id,name,meaning,kind,sensitive) VALUES(?,?,?,?,?)",
                [(source_id, name, meaning, kind, int(sensitive)) for name, meaning, kind, sensitive in fields],
            )
            write(self.connection, at, owner, "source.register", source_id, {"jurisdiction": jurisdiction})

    def ingest(self, asset_id, source_id, payload, at):
        source = self.connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if source is None or not source["active"]:
            raise ValueError("来源不可用")
        return self._store(asset_id, source, payload, at, None, "asset.ingest", {})

    def correct(self, asset_id, corrects, payload, at):
        # 更正件必须指向同一来源下已存在且未被否决的记录
        target = self.connection.execute("SELECT * FROM assets WHERE id=?", (corrects,)).fetchone()
        if target is None:
            raise KeyError(corrects)
        if corrects == asset_id:
            raise ValueError("更正件不能更正自身")
        if target["state"] == "rejected":
            raise ValueError("被否决的记录不可更正")
        source = self.connection.execute("SELECT * FROM sources WHERE id=?", (target["source_id"],)).fetchone()
        if not source["active"]:
            raise ValueError("来源不可用")
        return self._store(asset_id, source, payload, at, corrects, "asset.correct", {"corrects": corrects})

    def _store(self, asset_id, source, payload, at, corrects, action, details):
        allowed = {row[0] for row in self.connection.execute("SELECT name FROM fields WHERE source_id=?", (source["id"],))}
        if not isinstance(payload, dict) or not payload or set(payload) - allowed:
            raise ValueError("记录字段不符合来源目录")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self.connection:
            self.connection.execute(
                "INSERT INTO assets(id,source_id,schema_version,payload,digest,received_at,corrects) VALUES(?,?,?,?,?,?,?)",
                (asset_id, source["id"], source["schema_version"], canonical, digest, at, corrects),
            )
            write(self.connection, at, source["owner"], action, asset_id, {"source": source["id"], "digest": digest, **details})
        return digest

    def withdraw(self, asset_id, at, actor):
        self._transition(asset_id, "withdrawn", "asset.withdraw", at, actor)

    def reject(self, asset_id, at, actor):
        self._transition(asset_id, "rejected", "asset.reject", at, actor)

    def _transition(self, asset_id, state, action, at, actor):
        row = self.connection.execute("SELECT state FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
        if row["state"] != "active":
            raise ValueError("记录当前状态不允许该变更: " + asset_id)
        with self.connection:
            self.connection.execute("UPDATE assets SET state=? WHERE id=?", (state, asset_id))
            write(self.connection, at, actor, action, asset_id, {"state": state})

    def effective(self, asset_id):
        # 沿更正关系解析当前唯一可信版本；分叉或无有效版本时拒绝静默选定
        return self._head(asset_id, set())

    def _head(self, asset_id, seen):
        if asset_id in seen:
            raise CorrectionError("更正链存在循环: " + asset_id)
        seen.add(asset_id)
        row = self.connection.execute("SELECT state FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
        heads = []
        for successor in self.connection.execute("SELECT id FROM assets WHERE corrects=? AND state<>'rejected'", (asset_id,)):
            try:
                head = self._head(successor["id"], seen)
            except NoEffectiveVersion:
                continue  # 枯枝没有可信版本，剪除即可
            if head not in heads:
                heads.append(head)
        if len(heads) > 1:
            raise CorrectionFork("更正链分叉，候选有效版本: " + ", ".join(sorted(heads)))
        if heads:
            return heads[0]
        if row["state"] == "active":
            return asset_id
        raise NoEffectiveVersion("记录已失效且无有效更正: " + asset_id)

    def chain(self, asset_id):
        # 列出该记录所在更正族的全部节点与当前有效版本，供业务人员核查
        self.get(asset_id)
        root = asset_id
        seen = {asset_id}
        while True:
            parent = self.get(root)["corrects"]
            if parent is None:
                break
            if parent in seen:
                raise CorrectionError("更正链存在循环: " + parent)
            seen.add(parent)
            root = parent
        nodes, stack, visited = [], [root], set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            row = self.get(current)
            nodes.append({
                "id": row["id"],
                "source_id": row["source_id"],
                "state": row["state"],
                "corrects": row["corrects"],
                "received_at": row["received_at"],
                "digest": row["digest"],
            })
            stack.extend(r[0] for r in self.connection.execute("SELECT id FROM assets WHERE corrects=?", (current,)))
        nodes.sort(key=lambda node: (node["received_at"], node["id"]))
        try:
            effective, issue = self.effective(root), None
        except CorrectionError as exc:
            effective, issue = None, str(exc)
        return {"asset": asset_id, "root": root, "effective": effective, "issue": issue, "nodes": nodes}

    def get(self, asset_id):
        row = self.connection.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return dict(row)
