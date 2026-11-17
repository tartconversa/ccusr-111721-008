import argparse
import json

from .service import Exchange


def main():
    parser = argparse.ArgumentParser(description="本地协作台账演练")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    service = Exchange(args.database)
    try:
        service.catalog.register("flow", "机构甲", "甲地", "business", [("count", "通行计数", "integer", False)], "2026-11-17")
        service.catalog.ingest("item-1", "flow", {"count": 7}, "2026-11-17")
        service.grants.issue("g-1", "flow", "机构乙", "协作统计", "2026-12-31", "2026-11-17")
        service.policies.set("甲地", "business", "协作统计", "allow", "2026-11-17", "机构甲")
        first = service.products.assemble("report", "通行汇总", ["item-1"], "2026-11-17", "机构甲")
        service.delivery.send("d-1", "report", first, "机构乙", "协作统计", "2026-11-17")
        # 合作方撤回原件并以新编号提交更正件
        service.catalog.withdraw("item-1", "2026-11-18", "机构甲")
        service.catalog.correct("item-2", "item-1", {"count": 9}, "2026-11-18")
        # 后续编排仍按旧编号申请，台账自动转向有效版本
        second = service.products.assemble("report", "通行汇总", ["item-1"], "2026-11-18", "机构甲")
        service.delivery.send("d-2", "report", second, "机构乙", "协作统计", "2026-11-18")
        summary = {
            "deliveries": [service.delivery.receipt("d-1"), service.delivery.receipt("d-2")],
            "impact": service.impact("item-1"),
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    finally:
        service.close()


if __name__ == "__main__":
    main()
