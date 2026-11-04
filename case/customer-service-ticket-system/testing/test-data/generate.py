"""三阶段共享基准数据生成器（schema v2）。

单一事实源：三个架构阶段（Single Agent / Multi-Agent / Workflow+RAG）共用本目录
数据，通过各自适配层消费（口径见 MANIFEST.md），保证跨阶段同任务集、同验收口径。

产物（写入脚本所在 test-data/ 目录，幂等、固定 seed 可复现）：
- tickets.json        245 条工单（标准 100 / 复杂 40 / 异常 30 / 模糊 20 / 并发 10 / 扩展 45）
- orders.json         订单快照（工单引用的全部订单）
- logistics.json      物流快照（已发货订单对应的物流轨迹）
- knowledge-base/     知识库语料（FAQ / products / policies）

v2.1.0 相对 v2.0.0 的变化：
- 扩展段新增 40 条知识库咨询工单（X006-X045）：return_policy 15 / exchange_policy 15 /
  contact_info 10，知识意图占比 2/205 → 42/245，让 RAG 检索链路在压测流量中获得足量覆盖
- 意图仅用三阶段意图检测器已支持的 return_policy / exchange_policy / contact_info，
  不改变服务端行为；关键词取自知识库语料原文，保证三阶段答复判定可命中

v2 相对 v1 的变化：
- 每条工单新增 order_id / expected_facts / expected_agents / expected_path / expected_branch
- expected_answer_keywords 语义收紧为「答复必须全部命中的业务要点词」，
  结构化事实由 expected_facts 承担（订单状态、当前位置、预计送达、金额）
- 并入原 Multi-Agent 阶段 5 条扩展业务域工单（金额核对 / 退款咨询 / 退货 / 综合投诉）

运行：python test-data/generate.py
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
KB_DIR = DATA_DIR / "knowledge-base"

SEED = 42
DATE = "20240101"
SCHEMA_VERSION = "v2.1.0"

# 订单状态 -> 答复中的中文表述（与服务端 mock 答复模板保持同一映射）
STATUS_ZH = {"pending": "待发货", "shipped": "已发货", "exception": "异常"}

# 意图 -> 阶段二 Multi-Agent 期望参与的 Agent（supervisor 编排 + 专职 Agent + 答复校验）
AGENTS_BY_INTENT: dict[str, list[str]] = {
    "logistics_query": ["supervisor", "order_agent", "logistics_agent", "validation_agent"],
    "delivery_time": ["supervisor", "order_agent", "logistics_agent", "validation_agent"],
    "order_info": ["supervisor", "order_agent", "validation_agent"],
    "refund_status": ["supervisor", "order_agent", "validation_agent"],
    "return_policy": ["supervisor", "knowledge_agent", "validation_agent"],
    "exchange_policy": ["supervisor", "knowledge_agent", "validation_agent"],
    "contact_info": ["supervisor", "knowledge_agent", "validation_agent"],
    "complaint": ["supervisor", "validation_agent"],
    "clarification": ["supervisor"],
}

# 意图 -> 阶段三 Workflow 期望路径与分支（语义节点名，阶段三实现时对齐节点定义）
PATH_BY_INTENT: dict[str, tuple[list[str], str]] = {
    "logistics_query": (["intent_recognition", "data_retrieval", "response_generation", "confidence_check"], "auto_reply"),
    "delivery_time": (["intent_recognition", "data_retrieval", "response_generation", "confidence_check"], "auto_reply"),
    "order_info": (["intent_recognition", "data_retrieval", "response_generation", "confidence_check"], "auto_reply"),
    "refund_status": (["intent_recognition", "data_retrieval", "response_generation", "confidence_check"], "auto_reply"),
    "return_policy": (["intent_recognition", "knowledge_retrieval", "response_generation", "confidence_check"], "auto_reply"),
    "exchange_policy": (["intent_recognition", "knowledge_retrieval", "response_generation", "confidence_check"], "auto_reply"),
    "contact_info": (["intent_recognition", "knowledge_retrieval", "response_generation", "confidence_check"], "auto_reply"),
    "complaint": (["intent_recognition", "confidence_check"], "human_transfer"),
    "clarification": (["intent_recognition", "clarification"], "clarification"),
}


def _order_id(seq: int) -> str:
    return f"{DATE}{seq:05d}"


def _tracking_no(seq: int, carrier: str = "SF") -> str:
    return f"{carrier}{seq:08d}"


PRODUCTS = [
    ("P001", "iPhone 15 Pro", 8999),
    ("P002", "MacBook Pro 14", 14999),
    ("P003", "AirPods Pro 2", 1799),
    ("P004", "iPad Air 5", 4399),
    ("P005", "Apple Watch S9", 2999),
    ("P006", "HomePod mini", 749),
]
CITIES = [
    ("北京市", "朝阳区"),
    ("上海市", "浦东新区"),
    ("广东省", "深圳市"),
    ("浙江省", "杭州市"),
    ("四川省", "成都市"),
    ("湖北省", "武汉市"),
]
HUBS = ["北京中转站", "上海分拨中心", "广州转运中心", "成都集散仓", "武汉网关站"]


def _address(i: int) -> dict:
    prov, city = CITIES[i % len(CITIES)]
    return {"province": prov, "city": city, "district": "示例区", "address": "示例街道例号"}


def _trajectory(origin: str, dest: str, current: str) -> list[dict]:
    return [
        {"time": f"{DATE} 08:00:00", "location": origin, "status": "揽收"},
        {"time": f"{DATE} 12:00:00", "location": current, "status": "运输中"},
        {"time": f"{DATE} 18:00:00", "location": dest, "status": "派送中"},
    ]


def _build_order(seq: int, status: str, rng: random.Random) -> dict:
    n_items = 1 if status in ("pending", "exception") else rng.randint(1, 2)
    items = []
    amount = 0
    for _ in range(n_items):
        pid, name, price = PRODUCTS[rng.randrange(len(PRODUCTS))]
        qty = 1
        items.append(
            {"product_id": pid, "product_name": name, "quantity": qty, "price": price}
        )
        amount += price * qty
    prov, city = CITIES[seq % len(CITIES)]
    order = {
        "order_id": _order_id(seq),
        "user_id": f"U{seq:05d}",
        "status": status,
        "items": items,
        "total_amount": amount,
        "shipping_address": _address(seq),
        "tracking_no": None,
        "carrier": None,
        "create_time": f"{DATE} 10:{seq % 60:02d}:00",
        "update_time": f"{DATE} 15:{seq % 60:02d}:00",
    }
    if status in ("shipped", "exception"):
        order["tracking_no"] = _tracking_no(seq)
        order["carrier"] = "顺丰速运"
    return order


CONCURRENT_SEQ = 10  # 并发工单共用同一订单，验证并发独立处理


def generate() -> None:
    rng = random.Random(SEED)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    KB_DIR.mkdir(parents=True, exist_ok=True)

    tickets: list[dict] = []
    orders: list[dict] = []
    logistics: list[dict] = []
    order_by_id: dict[str, dict] = {}
    logistics_by_order: dict[str, dict] = {}

    def ensure_order(seq: int, status: str):
        oid = _order_id(seq)
        if oid not in order_by_id:
            order = _build_order(seq, status, rng)
            order_by_id[oid] = order
            orders.append(order)
        return oid

    def ensure_logistics(seq: int, status: str = "运输中"):
        oid = _order_id(seq)
        if oid in order_by_id and not any(l["order_id"] == oid for l in logistics):
            order = order_by_id[oid]
            origin = order["shipping_address"]["province"] + "仓"
            dest = "收货地分拨中心"
            current = HUBS[seq % len(HUBS)]
            row = {
                "tracking_no": order["tracking_no"],
                "order_id": oid,
                "carrier": order["carrier"],
                "status": status,
                "current_location": current,
                "origin": origin,
                "destination": dest,
                "trajectory": _trajectory(origin, dest, current),
                "estimated_delivery": "明天 20:00 前",
            }
            logistics.append(row)
            logistics_by_order[oid] = row

    def facts_for(order_id: str, with_logistics: bool = True) -> dict:
        """从关联订单/物流快照提取结构化事实（判定：每项取值须出现在答复中）。"""
        order = order_by_id[order_id]
        facts = {"order_id": order_id, "status": STATUS_ZH[order["status"]]}
        if with_logistics:
            lo = logistics_by_order.get(order_id)
            if lo:
                facts["current_location"] = lo["current_location"]
                facts["estimated_delivery"] = lo["estimated_delivery"]
        return facts

    def stage_fields(intent: str, facts: dict) -> dict:
        """为工单附加三阶段消费字段（阶段二 Agent 期望、阶段三路径与分支）。"""
        path, branch = PATH_BY_INTENT.get(
            intent, (["intent_recognition", "confidence_check"], "human_transfer")
        )
        return {
            "expected_facts": facts,
            "expected_agents": list(AGENTS_BY_INTENT.get(intent, ["supervisor"])),
            "expected_path": path,
            "expected_branch": branch,
        }

    # ---- 标准工单 100 条：正常订单 + 正常物流 ----
    std_seq = 1
    while len(tickets) < 100:
        oid = ensure_order(std_seq, "shipped")
        ensure_logistics(std_seq)
        tno = order_by_id[oid]["tracking_no"]
        # 关键词 = 答复必须全部命中的业务要点词；位置/时效等结构化事实由 expected_facts 校验
        templates = [
            ("我的订单 {oid} 现在到哪了？", "logistics_query", ["query_order", "query_logistics"], ["已发货", "预计", "送达"]),
            ("帮我查一下订单 {oid} 的物流状态", "logistics_query", ["query_order", "query_logistics"], ["物流信息", "预计"]),
            ("订单 {oid} 什么时候能到？", "delivery_time", ["query_order", "query_logistics"], ["预计", "送达"]),
            ("查询订单 {oid} 的配送进度", "logistics_query", ["query_order", "query_logistics"], ["物流信息", "预计"]),
            ("我的件 {tno} 派送了吗？", "logistics_query", ["query_logistics"], ["预计", "送达"]),
        ]
        t = templates[len(tickets) % len(templates)]
        tickets.append(
            {
                "ticket_id": f"T{std_seq:05d}",
                "user_id": f"U{std_seq:05d}",
                "user_query": t[0].format(oid=oid, tno=tno),
                "order_id": oid,
                "expected_intent": t[1],
                "expected_tools": t[2],
                "expected_answer_keywords": t[3],
                "difficulty": "easy",
                **stage_fields(t[1], facts_for(oid)),
            }
        )
        std_seq += 1

    # ---- 复杂工单 40 条：多商品 + 多状态 ----
    base = 200
    for i in range(40):
        seq = base + i
        status = "pending" if i % 3 == 1 else "shipped"
        oid = ensure_order(seq, status)
        if status == "shipped":
            ensure_logistics(seq)
        tickets.append(
            {
                "ticket_id": f"C{i+1:03d}",
                "user_id": f"U{seq:05d}",
                "user_query": f"订单 {oid} 购物车里有多件商品，现在分别到哪了？",
                "order_id": oid,
                "expected_intent": "logistics_query",
                "expected_tools": ["query_order", "query_logistics"]
                if status == "shipped"
                else ["query_order"],
                "expected_answer_keywords": ["已发货", "预计"] if status == "shipped" else ["待发货"],
                "difficulty": "medium",
                **stage_fields("logistics_query", facts_for(oid, with_logistics=status == "shipped")),
            }
        )

    # ---- 异常工单 30 条：订单不存在 / 物流异常 / 状态异常 ----
    for i in range(30):
        oid = ensure_order(base + 500 + i, "shipped")
        ensure_logistics(base + 500 + i, "异常件退回")
        if i < 10:
            query = f"订单 9999999999999 现在到哪了？"
            tools = ["query_order"]
            kw = ["没有查询到"]
            facts: dict = {}
            used_oid = ""
        elif i < 20:
            query = f"运单 {_tracking_no(90000000+i)} 派送进度如何？"
            tools = ["query_logistics"]
            kw = ["没有查询到"]
            facts = {}
            used_oid = ""
        else:
            query = f"订单 {oid} 的物流显示异常退回，是什么原因？"
            tools = ["query_order", "query_logistics"]
            kw = ["物流信息"]
            facts = facts_for(oid)
            used_oid = oid
        tickets.append(
            {
                "ticket_id": f"E{i+1:03d}",
                "user_id": f"U9000{i:04d}",
                "user_query": query,
                "order_id": used_oid,
                "expected_intent": "logistics_query",
                "expected_tools": tools,
                "expected_answer_keywords": kw,
                "difficulty": "medium",
                **stage_fields("logistics_query", facts),
            }
        )

    # ---- 模糊工单 20 条：表述不清，需澄清（无关键词/事实，按 expected_behavior 判定） ----
    fuzzy_queries = [
        "那个东西到哪了",
        "帮我看看物流",
        "快递怎么回事",
        "我买的啥时候到",
        "查一下我的单",
        "订单状态帮我看看",
    ]
    for i in range(20):
        tickets.append(
            {
                "ticket_id": f"M{i+1:03d}",
                "user_id": f"U9900{i:04d}",
                "user_query": fuzzy_queries[i % len(fuzzy_queries)],
                "order_id": "",
                "expected_intent": "clarification",
                "expected_tools": [],
                "expected_answer_keywords": [],
                # 行为特征文本：判定要求该文本出现在答复中（澄清答复须含“提供订单号”）
                "expected_behavior": "提供订单号",
                "difficulty": "hard",
                **stage_fields("clarification", {}),
            }
        )

    # ---- 并发工单 10 条：相同订单 ID，验证并发独立处理 ----
    for i in range(10):
        oid = ensure_order(CONCURRENT_SEQ + 10, "shipped")
        ensure_logistics(CONCURRENT_SEQ + 10)
        tickets.append(
            {
                "ticket_id": f"V{i+1:03d}",
                "user_id": f"U9500{i:04d}",
                "user_query": f"查询订单 {oid} 的物流信息",
                "order_id": oid,
                "expected_intent": "logistics_query",
                "expected_tools": ["query_order", "query_logistics"],
                "expected_answer_keywords": ["物流信息", "预计"],
                "difficulty": "medium",
                **stage_fields("logistics_query", facts_for(oid)),
            }
        )

    # ---- 扩展业务域工单 5 条（并入自原 Multi-Agent 阶段数据集）----
    # X001/X002 金额核对：仅订单查询，答复需给出订单金额事实
    x_seq = base + 610
    for i, suffix in enumerate(
        ["的金额好像不对，帮我核对一下实际支付金额。", "我付的钱和订单显示的不一致，请帮我确认订单金额。"]
    ):
        seq = x_seq + i
        oid = ensure_order(seq, "shipped")  # 不生成物流记录：金额核对走纯订单分支
        facts = facts_for(oid, with_logistics=False)
        facts["total_amount"] = str(order_by_id[oid]["total_amount"])
        tickets.append(
            {
                "ticket_id": f"X{i+1:03d}",
                "user_id": f"U{seq:05d}",
                "user_query": f"订单 {oid} {suffix}",
                "order_id": oid,
                "expected_intent": "order_info",
                "expected_tools": ["query_order"],
                "expected_answer_keywords": ["订单金额"],
                "difficulty": "medium",
                **stage_fields("order_info", facts),
            }
        )

    # X003/X004 知识库咨询：退款 / 退货
    for i, (query, kw) in enumerate(
        [("如何申请退款？", ["退款"]), ("商品有质量问题，我要退货。", ["退货"])]
    ):
        tickets.append(
            {
                "ticket_id": f"X{i+3:03d}",
                "user_id": f"U{base + 620 + i:05d}",
                "user_query": query,
                "order_id": "",
                "expected_intent": "return_policy",
                "expected_tools": ["search_knowledge"],
                "expected_answer_keywords": kw,
                "difficulty": "medium",
                **stage_fields("return_policy", {}),
            }
        )

    # X005 综合投诉：金额 + 物流双问题，走订单与物流查询路径
    seq5 = x_seq + 4
    oid5 = ensure_order(seq5, "shipped")
    ensure_logistics(seq5)
    tickets.append(
        {
            "ticket_id": "X005",
            "user_id": f"U{seq5:05d}",
            "user_query": f"订单 {oid5} 金额不对，物流也一直没更新，到底怎么回事？",
            "order_id": oid5,
            "expected_intent": "logistics_query",
            "expected_tools": ["query_order", "query_logistics"],
            "expected_answer_keywords": ["物流信息", "预计"],
            "difficulty": "medium",
            **stage_fields("logistics_query", facts_for(oid5)),
        }
    )

    # X006-X045 知识库咨询扩充（v2.1.0）：知识意图占比 2/205 -> 42/245，
    # 让 RAG 检索链路（knowledge_retrieval 节点）在压测流量中获得足量覆盖。
    # - 意图只用三阶段检测器已支持的 return_policy / exchange_policy / contact_info
    # - 退货/退款措辞避开“退款…到哪/进度/状态/到账”的 refund_status 数据意图正则
    # - 措辞避开“投诉”（complaint 分支）与“无理由”（退换货语料不含“退货”子串，排序不稳）
    # - 关键词取自知识库语料 content 原文（三阶段知识答复均引用命中语料原文）
    return_policy_templates: list[tuple[str, list[str]]] = [
        ("商品不想要了，怎么申请退货？", ["退货"]),
        ("退货流程麻烦说一下。", ["退货"]),
        ("我想退货，具体怎么操作？", ["退货"]),
        ("下单后还能退货吗？退货步骤是什么？", ["退货"]),
        ("帮我介绍下退货的办理方式。", ["退货"]),
        ("收到货不满意想退回去，退货怎么申请？", ["退货"]),
        ("退货需要满足什么条件？", ["退货"]),
        ("申请退货后多久能有结果？", ["退货"]),
        ("我要退货，请告知退货注意事项。", ["退货"]),
        ("退货申请提交后商品要寄到哪里？", ["退货"]),
        ("退款要怎么申请？", ["退款"]),
        ("申请退款的条件是什么？", ["退款"]),
        ("退款一般多久能处理完？", ["退款"]),
        ("我不想要了，帮我说明退款流程。", ["退款"]),
        ("退款申请提交后需要注意什么？", ["退款"]),
    ]
    exchange_policy_templates: list[tuple[str, list[str]]] = [
        ("收到的尺码不合适，怎么申请换货？", ["换货"]),
        ("换货流程是什么？", ["换货"]),
        ("我想换货，具体怎么操作？", ["换货"]),
        ("商品有瑕疵想换货，麻烦说下换货步骤。", ["换货"]),
        ("帮我介绍下换货的办理方式。", ["换货"]),
        ("衣服尺码买错了可以换货吗？", ["换货"]),
        ("换货需要运费吗？", ["换货"]),
        ("换货一般多久能收到新商品？", ["换货"]),
        ("我要换货，请告知换货注意事项。", ["换货"]),
        ("质量有问题，能申请换货吗？", ["换货"]),
        ("换货的处理周期是几天？", ["换货"]),
        ("申请换货后怎么寄回商品？", ["换货"]),
        ("大小不合适，帮我说下换货办法。", ["换货"]),
        ("换货支持哪些原因？", ["换货"]),
        ("收到的东西坏了，我要换货。", ["换货"]),
    ]
    contact_info_templates: list[tuple[str, list[str]]] = [
        ("人工客服电话是多少？", ["400"]),
        ("你们的联系方式是什么？", ["客服电话"]),
        ("客服电话多少？", ["400"]),
        ("怎么联系客服？请提供电话。", ["400"]),
        ("想先电话咨询再决定，客服电话号码是什么？", ["客服电话"]),
        ("请告诉我客服联系方式。", ["客服电话"]),
        ("打哪个电话能找到人工客服？", ["400"]),
        ("400 电话是免费的吗？", ["400"]),
        ("客服在线时间外怎么联系你们？电话有人接吗？", ["400"]),
        ("帮我查下官方客服的联系电话。", ["客服电话"]),
    ]
    knowledge_tickets: list[tuple[str, str, list[str]]] = (
        [(q, "return_policy", kw) for q, kw in return_policy_templates]
        + [(q, "exchange_policy", kw) for q, kw in exchange_policy_templates]
        + [(q, "contact_info", kw) for q, kw in contact_info_templates]
    )
    for i, (query, intent, kw) in enumerate(knowledge_tickets):
        tickets.append(
            {
                "ticket_id": f"X{i + 6:03d}",
                "user_id": f"U{base + 630 + i:05d}",
                "user_query": query,
                "order_id": "",
                "expected_intent": intent,
                "expected_tools": ["search_knowledge"],
                "expected_answer_keywords": kw,
                "difficulty": "medium",
                **stage_fields(intent, {}),
            }
        )

    # ---- 知识库语料 ----
    knowledge = _knowledge_docs()
    (DATA_DIR / "tickets.json").write_text(
        json.dumps(tickets, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DATA_DIR / "orders.json").write_text(
        json.dumps(orders, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DATA_DIR / "logistics.json").write_text(
        json.dumps(logistics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for category, docs in knowledge.items():
        (KB_DIR / f"{category}.json").write_text(
            json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"生成完成（schema {SCHEMA_VERSION}）：tickets={len(tickets)} orders={len(orders)} "
          f"logistics={len(logistics)} knowledge={sum(len(v) for v in knowledge.values())}")


def _knowledge_docs() -> dict[str, list[dict]]:
    return {
        "faq": [
            {
                "category": "faq",
                "title": "如何申请退货",
                "content": "退货流程：1. 在订单详情页点击申请退货；2. 选择退货原因并提交；3. 审核通过后寄回商品；4. 收到商品后退款原路返回。",
                "keywords": ["退货", "申请退货", "退货流程"],
            },
            {
                "category": "faq",
                "title": "退款到账时间",
                "content": "退款将在仓库确认收货后的 1-3 个工作日内原路退回，银行卡支付一般 2-5 个工作日到账。",
                "keywords": ["退款", "退款到账"],
            },
            {
                "category": "faq",
                "title": "客服联系电话",
                "content": "官方客服电话为 400-800-8000，服务时间 9:00-21:00，全年无休。",
                "keywords": ["电话", "400", "联系方式", "客服电话"],
            },
            {
                "category": "faq",
                "title": "如何申请换货",
                "content": "换货流程：在订单详情选择申请换货，填写换货原因与尺码。换货运费由平台承担，处理周期 3-5 个工作日。",
                "keywords": ["换货", "换货流程"],
            },
            {
                "category": "faq",
                "title": "发票开具说明",
                "content": "支持电子发票，可在订单完成后 30 天内在线申请，电子发票将发送至用户邮箱。",
                "keywords": ["发票", "电子发票"],
            },
            {
                "category": "faq",
                "title": "物流配送范围",
                "content": "全国大部分地区支持配送，偏远地区配送时效为 3-7 个工作日，具体以物流信息为准。",
                "keywords": ["配送", "区域", "偏远"],
            },
        ],
        "products": [
            {
                "category": "products",
                "title": "iPhone 15 Pro 退换说明",
                "content": "iPhone 15 Pro 支持 7 天无理由退货，激活后不影响退货权益，仅限全新未损坏商品。",
                "keywords": ["iPhone", "手机", "退换"],
            },
            {
                "category": "products",
                "title": "AirPods Pro 2 保修政策",
                "content": "AirPods Pro 2 享受 1 年有限保修，含电池与充电盒故障维修。",
                "keywords": ["AirPods", "耳机", "保修"],
            },
        ],
        "policies": [
            {
                "category": "policies",
                "title": "售后服务政策",
                "content": "自签收之日起 7 天内享受无理由退换货，15 天内质量问题可退换，保修期内维修免费。",
                "keywords": ["售后", "保修", "政策"],
            },
            {
                "category": "policies",
                "title": "隐私保护承诺",
                "content": "平台承诺对用户订单、物流与个人信息进行加密存储，绝不向第三方泄露。",
                "keywords": ["隐私", "安全", "保密"],
            },
        ],
    }


if __name__ == "__main__":
    generate()
    sys.exit(0)
